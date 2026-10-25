"""`yaan snapshot` and `yaan seed` (Phase 15.2, R2 W6): Yaan without LLM calls or an API key.

A snapshot is one file, `snapshots/lore-snapshot.tar.gz`:
- `graphs/<graph>.dump`: the graph as FalkorDB's own DUMP payload (nodes, edges, properties,
  indexes, vectors; 38.5k nodes ≈ 37 MB, DUMP 1 s, RESTORE 11 s);
- `data/normalized/` and `data/extract/`: what the live path folds over (demo injections and
  connectors), so a seeded copy keeps working live: HERB's normalized rows and the extracted
  events (the only LLM output, ~0.6 MB);
- `manifest.json`: per graph its node and edge counts and the payload's sha256, the FalkorDB tag.

`seed` restores it (from a path or a URL, e.g. a release asset), checks the sha256, the counts
and the declared indexes, and copies the HERB graph to the demo graph. DUMP/RESTORE run as the
admin user: the writer's ACL has neither command. A DUMP payload carries FalkorDB's encoding
version, so it restores on the pinned tag (FALKORDB_TAG); the manifest says which one made it.

The manifest's sha256s only catch a damaged file: whoever made the file wrote them too. So the
whole file's sha256 is also pinned outside it (H2, D111): `snapshot` writes it to SHA256SUMS
next to the file, the release's line is committed in snapshots/SHA256SUMS, and `seed` refuses
a file no pin names, unless told `unpinned` (a snapshot you made yourself elsewhere). The
graph payload is RESTOREd as the Redis admin, so an unchecked file must not get that far.
"""

import hashlib
import io
import json
import tarfile
import time
import urllib.request
from pathlib import Path
from typing import Any

import redis

from app.config import REPO_ROOT, get_settings

SNAPSHOT = REPO_ROOT / "snapshots" / "lore-snapshot.tar.gz"
SUMS = REPO_ROOT / "snapshots" / "SHA256SUMS"  # the released snapshots' pins, committed
DATA_DIRS = ("normalized", "extract")


class SnapshotError(RuntimeError):
    pass


def admin() -> redis.Redis:
    s = get_settings()
    if not s.falkordb_admin_password:
        raise SnapshotError("FALKORDB_ADMIN_PASSWORD is not set (see .env.example)")
    return redis.Redis(
        host=s.falkordb_host,
        port=s.falkordb_port,
        username="default",
        password=s.falkordb_admin_password,
        socket_timeout=600,  # a 37 MB RESTORE takes ~11 s; never cut it off
    )


def counts(r: redis.Redis, graph: str) -> dict[str, int]:
    def one(q: str) -> int:
        return int(r.execute_command("GRAPH.RO_QUERY", graph, q)[1][0][0])

    return {
        "nodes": one("MATCH (n) RETURN count(n)"),
        "edges": one("MATCH ()-[e]->() RETURN count(e)"),
    }


def snapshot(graphs: tuple[str, ...] | None = None, out: Path = SNAPSHOT) -> dict[str, Any]:
    """Write the snapshot file; returns its manifest."""
    s = get_settings()
    graphs = graphs or (s.yaan_graph,)
    r = admin()
    manifest: dict[str, Any] = {
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "falkordb_tag": _falkordb_tag(),
        "graphs": {},
        "data": list(DATA_DIRS),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out, "w:gz") as tar:
        for g in graphs:
            payload = r.dump(g)
            if payload is None:
                raise SnapshotError(f"graph {g!r} doesn't exist")
            manifest["graphs"][g] = {
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                **counts(r, g),
            }
            _add(tar, f"graphs/{g}.dump", payload)
        for d in DATA_DIRS:
            folder = s.data_dir / d
            if not folder.is_dir():
                raise SnapshotError(f"{folder} is missing: run the pipeline first")
            for path in sorted(folder.rglob("*")):
                if path.is_file():
                    tar.add(path, arcname=f"data/{d}/{path.relative_to(folder).as_posix()}")
        _add(tar, "manifest.json", json.dumps(manifest, indent=1).encode())
    manifest["file"] = str(out)
    manifest["file_mb"] = round(out.stat().st_size / 1e6, 1)
    manifest["file_sha256"] = pin(out)
    return manifest


def pin(path: Path) -> str:
    """Record the file's sha256 in SHA256SUMS beside it (one line per file name)."""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    sums = path.with_name("SHA256SUMS")
    lines = [ln for ln in _lines(sums) if not ln.endswith(f"  {path.name}")]
    body = "\n".join([*lines, f"{digest}  {path.name}"]) + "\n"
    sums.write_text(body, encoding="utf-8", newline="\n")  # LF: `sha256sum -c` reads it anywhere
    return digest


def _lines(sums: Path) -> list[str]:
    if not sums.is_file():
        return []
    return [ln.strip() for ln in sums.read_text(encoding="utf-8").splitlines()]


def pinned(source: str | Path) -> set[str]:
    """The sha256s a snapshot may have: the committed pins, and for a local file the
    SHA256SUMS `snapshot` wrote beside it."""
    files = [SUMS]
    if not str(source).startswith(("http://", "https://")):
        files.append(Path(source).with_name("SHA256SUMS"))
    return {ln.split()[0] for f in files for ln in _lines(f) if ln}


def seed(source: str | Path = SNAPSHOT, force: bool = False, unpinned: bool = False) -> dict:
    """Restore a snapshot (path or http(s) URL): graphs, then data folders, then the demo copy.
    The file's sha256 must be pinned (see the module docstring) unless `unpinned`."""
    s = get_settings()
    blob = _read(source)
    digest = hashlib.sha256(blob).hexdigest()
    if not unpinned and digest not in pinned(source):
        raise SnapshotError(
            f"this snapshot's sha256 ({digest[:16]}…) is pinned in neither snapshots/SHA256SUMS "
            "nor a SHA256SUMS beside it: it isn't the one this version of Yaan was released "
            "with. Use the release that matches your checkout, or pass --unpinned for a "
            "snapshot you made yourself."
        )
    r = admin()
    report: dict[str, Any] = {"source": str(source), "graphs": {}}
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        manifest = json.loads(tar.extractfile("manifest.json").read())
        if manifest["falkordb_tag"] != _falkordb_tag():
            report["warning"] = (
                f"snapshot made on FalkorDB {manifest['falkordb_tag']}, running {_falkordb_tag()}"
            )
        for g, meta in manifest["graphs"].items():
            if r.exists(g) and not force:
                raise SnapshotError(f"graph {g!r} already exists: pass --force to replace it")
            payload = tar.extractfile(f"graphs/{g}.dump").read()
            if hashlib.sha256(payload).hexdigest() != meta["sha256"]:
                raise SnapshotError(f"{g}.dump doesn't match its sha256: the file is damaged")
            t0 = time.perf_counter()
            r.restore(g, 0, payload, replace=True)
            got = counts(r, g)
            if got != {"nodes": meta["nodes"], "edges": meta["edges"]}:
                raise SnapshotError(
                    f"{g}: restored {got}, expected {meta['nodes']}/{meta['edges']}"
                )
            report["graphs"][g] = {**got, "ms": round((time.perf_counter() - t0) * 1000)}
        written = 0
        for member in tar.getmembers():
            if not member.isfile() or not member.name.startswith("data/"):
                continue
            target = (s.data_dir / member.name.removeprefix("data/")).resolve()
            if s.data_dir.resolve() not in target.parents:  # no path tricks in the archive
                raise SnapshotError(f"unsafe path in snapshot: {member.name}")
            if target.exists() and not force:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(tar.extractfile(member).read())
            written += 1
        report["data_files_written"] = written
    from app import db, schema
    from app.sentinel import live

    for g in manifest["graphs"]:
        problems = schema.problems(schema.declared(), db.run("list_indexes", graph=g).rows)
        if problems:
            raise SnapshotError(f"{g}: indexes differ from 00_schema.cypher: {problems}")
    if s.yaan_graph in manifest["graphs"]:
        report["demo"] = live.reset_demo(s.yaan_demo_graph)
    return report


def _read(source: str | Path) -> bytes:
    text = str(source)
    if text.startswith(("http://", "https://")):
        with urllib.request.urlopen(text, timeout=300) as resp:  # noqa: S310 (a URL the user gave)
            return resp.read()
    path = Path(source)
    if not path.is_file():
        raise SnapshotError(f"no snapshot at {path}: run `yaan snapshot` or pass --from <url>")
    return path.read_bytes()


def _add(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mtime = int(time.time())
    tar.addfile(info, io.BytesIO(data))


def _falkordb_tag() -> str:
    return get_settings().falkordb_tag
