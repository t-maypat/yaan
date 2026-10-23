"""`yaan backup` and `yaan restore` (22.2, review F16): one file that brings a deployment back.

A deployment's state is split between the graph and files under data/: sign-in tokens and the
session key, the people met in the tools, the connector cursors, the synced access lists, the
spend ledger, the access audit log. `snapshot` saves HERB's graph and its pipeline files, not
these, so a lost disk lost every cursor (a full re-read, or a gap) and every token. A backup is
one `.tar.gz`:
- `graph.dump`: the graph as FalkorDB's DUMP payload (as in snapshot.py);
- `data/...`: tokens.json, session.key, cache/llm_spend.json, every file in
  live/<graph>/ and audit/<graph>/ except the heartbeat, pokes, locks and temp files;
- `config/...`: people.yaml, connectors.yaml, permissions.yaml as they were;
- `manifest.json`: the graph, its counts, the FalkorDB tag, each member's sha256, and the
  holder of the writer lock the backup held.

Consistency: the backup holds the graph's writer lock, reads every file, then DUMPs the graph.
A batch writes the graph before it saves its cursor, so a cursor read first is never ahead of
the graph: after a restore the worker re-reads at most the last batch (an update of items it
has, which is idempotent) and loses nothing. people.json is written before its Person nodes
are merged, so a person in the graph but not in the file is merged again on the next sight.

`restore` checks the file's sha256 against the `.sha256` beside it (written by `backup`), each
member's sha256 and the FalkorDB tag (a DUMP payload restores only on the encoding that made
it), refuses to replace a graph or files that exist (or to run beside a live worker) unless
told `force`, RESTOREs the graph as the admin, gives back the lock the backup held, and writes
the files. Config files are written only where none exists (a mounted, read-only people.yaml
is the operator's); a differing one is reported.

DUMP is one Redis command: the server answers nothing else while it serialises (~1 s per
37 MB measured, D075). Fine to ~1 GB; beyond, schedule it off-hours (22.6 sizes it).
A backup holds token hashes, the session key and every word Yaan has read: keep it like the
database itself (written owner-only where the OS supports it).
"""

import hashlib
import io
import json
import os
import shutil
import tarfile
import time
from pathlib import Path
from typing import Any

from app import db, snapshot
from app.config import REPO_ROOT, get_settings

FILES = ("tokens.json", "session.key", "cache/llm_spend.json")  # relative to data/
GRAPH_DIRS = ("live", "audit")  # data/<dir>/<graph>/, every file but the transient ones
TRANSIENT = ("heartbeat.json", "poke.json")
CONFIG = ("people.yaml", "connectors.yaml", "permissions.yaml")  # relative to the repo root
LOCK = "derived"  # db.writer_lock's default name


class BackupError(RuntimeError):
    pass


def default_out(graph: str) -> Path:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return get_settings().data_dir / "backups" / f"{graph}-{stamp}.tar.gz"


def state_files(graph: str) -> dict[str, Path]:
    """Archive name -> path of every state file the graph's deployment has now."""
    data = get_settings().data_dir
    found = {f"data/{name}": data / name for name in FILES if (data / name).is_file()}
    for d in GRAPH_DIRS:
        folder = data / d / graph
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*")):
            if path.is_file() and not _transient(path):
                found[f"data/{path.relative_to(data).as_posix()}"] = path
    return found


def _transient(path: Path) -> bool:
    return path.name in TRANSIENT or path.suffix in (".lock", ".tmp")


def backup(graph: str | None = None, out: Path | None = None, keep: int | None = None) -> dict:
    """Write one backup of `graph` (default: the graph the API serves); returns its manifest.
    `keep`: then delete this graph's oldest backups in the same folder beyond that many."""
    graph = graph or get_settings().api_graph()
    out = out or default_out(graph)
    r = snapshot.admin()
    if not r.exists(graph):
        raise BackupError(f"graph {graph!r} doesn't exist")
    members: dict[str, bytes] = {}
    with db.writer_lock(graph, name=LOCK) as holder:  # no batch half-written while we read
        for name, path in state_files(graph).items():  # files first, then the graph (above)
            members[name] = path.read_bytes()
        payload = r.dump(graph)
        found = snapshot.counts(r, graph)
    for name in CONFIG:
        if (REPO_ROOT / name).is_file():
            members[f"config/{name}"] = (REPO_ROOT / name).read_bytes()
    manifest: dict[str, Any] = {
        "kind": "yaan-backup",
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "graph": graph,
        "falkordb_tag": get_settings().falkordb_tag,
        "lock_holder": holder,
        **found,
        "members": {"graph.dump": _sha(payload)} | {n: _sha(b) for n, b in members.items()},
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".part")
    with tarfile.open(tmp, "w:gz") as tar:
        snapshot._add(tar, "graph.dump", payload)
        for name, blob in members.items():
            snapshot._add(tar, name, blob)
        snapshot._add(tar, "manifest.json", json.dumps(manifest, indent=1).encode())
    _owner_only(tmp)
    os.replace(tmp, out)
    digest = _sha(out.read_bytes())
    sidecar(out).write_text(f"{digest}  {out.name}\n", encoding="utf-8", newline="\n")
    report = {
        "file": str(out),
        "file_mb": round(out.stat().st_size / 1e6, 1),
        "sha256": digest,
        "graph": graph,
        **found,
        "files": len(members),
    }
    if keep:
        report["deleted"] = prune(out.parent, graph, keep)
    return report


def sidecar(path: Path) -> Path:
    return path.with_name(path.name + ".sha256")


def prune(folder: Path, graph: str, keep: int) -> list[str]:
    """Delete this graph's oldest backups beyond `keep` (names sort by their UTC stamp)."""
    mine = sorted(folder.glob(f"{graph}-*.tar.gz"))
    gone = mine[: max(len(mine) - keep, 0)]
    for path in gone:
        path.unlink()
        sidecar(path).unlink(missing_ok=True)
    return [p.name for p in gone]


def restore(
    source: Path, force: bool = False, unpinned: bool = False, as_graph: str | None = None
) -> dict[str, Any]:
    """Bring a backup back: the graph, then the state files (see the module docstring).
    `as_graph`: a trial restore of the graph alone under another name, no files written (a
    backup no one has restored is a hope, not a backup)."""
    from app.connectors import runner

    blob = _checked(source, unpinned)
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        files = {m.name: tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}
    manifest = json.loads(files.pop("manifest.json", b"{}") or b"{}")
    if manifest.get("kind") != "yaan-backup":
        raise BackupError(f"{source} is not a yaan backup (a snapshot? use `yaan seed`)")
    for name, digest in manifest["members"].items():
        if name not in files or _sha(files[name]) != digest:
            raise BackupError(f"{name} doesn't match its sha256: the file is damaged")
    tag = get_settings().falkordb_tag
    if manifest["falkordb_tag"] != tag:
        raise BackupError(
            f"made on FalkorDB {manifest['falkordb_tag']}, running {tag}: a DUMP payload "
            "restores only on the version that wrote it. Run that FALKORDB_TAG, restore, then "
            "upgrade (or move the data with `yaan export` / `yaan import`)."
        )
    graph = as_graph or manifest["graph"]
    s = get_settings()
    r = snapshot.admin()
    if as_graph:
        if r.exists(graph):
            raise BackupError(f"graph {graph!r} exists: a trial restore needs a new name")
        files = {"graph.dump": files["graph.dump"]}
    elif not force:
        if r.exists(graph):
            raise BackupError(f"graph {graph!r} exists: pass --force to replace it")
        if runner.worker_health(graph)["status"] in ("ok", "waiting"):
            raise BackupError(
                f"a connector worker is running on {graph!r}: stop it first (it would write "
                "its own cursors over the restored ones), or pass --force"
            )
        clash = [
            n
            for n, body in files.items()
            if n.startswith("data/")
            and (t := s.data_dir / n.removeprefix("data/")).is_file()
            and t.read_bytes() != body
        ]
        if clash:
            raise BackupError(f"{len(clash)} files differ from the backup ({clash[0]}, …): --force")
    t0 = time.perf_counter()
    r.restore(graph, 0, files.pop("graph.dump"), replace=True)
    got = snapshot.counts(r, graph)
    want = {"nodes": manifest["nodes"], "edges": manifest["edges"]}
    if got != want:
        raise BackupError(f"{graph}: restored {got}, expected {want}")
    # the lock the backup held while it dumped comes back taken: give it back
    db.run("release_lock", {"name": LOCK, "holder": manifest["lock_holder"]}, graph=graph,
           readonly=False)  # fmt: skip
    graph_ms = round((time.perf_counter() - t0) * 1000)
    if not as_graph:
        for d in GRAPH_DIRS:  # the graph's own folders become the backup's, not a mix of both
            shutil.rmtree(s.data_dir / d / graph, ignore_errors=True)
    written, config = 0, {}
    for name, body in sorted(files.items()):
        if name.startswith("data/"):
            target = _inside(s.data_dir, name.removeprefix("data/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
            written += 1
        elif name.startswith("config/"):
            target = _inside(REPO_ROOT, name.removeprefix("config/"))
            if not target.exists():
                target.write_bytes(body)
                config[target.name] = "written"
            else:
                config[target.name] = "same" if target.read_bytes() == body else "differs: kept"
    return {
        "source": str(source),
        "graph": graph,
        **got,
        "graph_ms": graph_ms,
        "files_written": written,
        "config": config,
        "made": manifest["created_utc"],
    }


def _checked(source: Path, unpinned: bool) -> bytes:
    if not source.is_file():
        raise BackupError(f"no backup at {source}")
    blob = source.read_bytes()
    pin = sidecar(source)
    if pin.is_file():
        want = pin.read_text(encoding="utf-8").split()[0]
        if _sha(blob) != want:
            raise BackupError(f"{source.name} doesn't match {pin.name}: damaged or changed")
    elif not unpinned:
        raise BackupError(
            f"{pin.name} is missing beside the backup: copy it along, or pass --unpinned"
        )
    return blob


def _inside(root: Path, rel: str) -> Path:
    target = (root / rel).resolve()
    if root.resolve() not in target.parents:  # no path tricks in the archive
        raise BackupError(f"unsafe path in backup: {rel}")
    return target


def _owner_only(path: Path) -> None:
    try:
        path.chmod(0o600)
    except OSError:  # Windows keeps its own ACLs
        pass


def _sha(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()
