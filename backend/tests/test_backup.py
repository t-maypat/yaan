"""`yaan backup` / `yaan restore` (22.2, F16): a graph and every state file of its deployment
round-trip through one checked file, read in an order that loses nothing. On the real server,
as the admin user, with data/ and the config files in a temp folder."""

import io
import json
import tarfile
import time
from pathlib import Path

import pytest

from app import backup, db, snapshot
from app.config import get_settings
from app.connectors import runner

pytestmark = pytest.mark.db
FIXTURE = Path(__file__).parent / "fixtures" / "agent_company.cypher"


@pytest.fixture
def deployment(empty_graph, tmp_path, monkeypatch):
    """A graph with the fixture company, and its state files: a cursor, people met, tokens,
    the ledger, an audit month, plus the transient files a backup leaves out."""
    db.run_text(FIXTURE.read_text(encoding="utf-8"), label="f", graph=empty_graph, readonly=False)
    data, conf = tmp_path / "data", tmp_path / "repo"
    monkeypatch.setattr(get_settings(), "data_dir", data)
    monkeypatch.setattr(get_settings(), "audit_dir", None)  # the audit log under data/ here
    monkeypatch.setattr(backup, "REPO_ROOT", conf)
    live = data / "live" / empty_graph
    files = {
        data / "tokens.json": '{"t1": {"person": "eid_a1"}}',
        data / "session.key": "k" * 32,
        data / "cache" / "llm_spend.json": '{"2026-10-23": 0.12}',
        live / "connectors.json": json.dumps({"slack": {"since_ms": 10, "seen": {"m1": 10}}}),
        live / "people.json": '{"eid_00000000000000ab": {"name": "Ann"}}',
        data / "audit" / empty_graph / "2026-10.jsonl": '{"viewer": "eid_a1"}\n',
        live / "heartbeat.json": json.dumps({"at_ms": 1, "healthy": True, "next_by_ms": 2}),
        live / "connectors.json.lock": "",
        conf / "people.yaml": "people: []\n",
        conf / "connectors.yaml": "product: Widget\n",
    }
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return empty_graph, tmp_path / "backups" / "b.tar.gz", data, conf


def lock_holder(graph):
    rows = db.run_text(
        "MATCH (l:Lock {id: 'derived'}) RETURN l.holder AS holder LIMIT 1", label="t", graph=graph
    ).rows
    return rows[0]["holder"] if rows else None


def test_a_deployment_round_trips_through_one_file(deployment):
    graph, out, data, conf = deployment
    with db.writer_lock(graph):  # a live graph has its :Lock node from the first writer on
        pass
    before = snapshot.counts(snapshot.admin(), graph)
    report = backup.backup(graph, out)
    assert report["nodes"] == before["nodes"] > 0 and report["files"] == 8  # 6 state, 2 config
    with tarfile.open(out) as tar:
        names = set(tar.getnames())
    assert {"graph.dump", "manifest.json", "data/tokens.json", "data/session.key",
            "data/cache/llm_spend.json", f"data/live/{graph}/connectors.json",
            f"data/live/{graph}/people.json", f"data/audit/{graph}/2026-10.jsonl",
            "config/people.yaml", "config/connectors.yaml"} == names  # fmt: skip
    assert backup.sidecar(out).read_text(encoding="utf-8") == f"{report['sha256']}  {out.name}\n"
    assert lock_holder(graph) is None  # the live graph's lock was given back

    # the next round moves on: a new cursor and a new node; config changes; a file vanishes
    live = data / "live" / graph
    (live / "connectors.json").write_text('{"slack": {"since_ms": 99}}', encoding="utf-8")
    db.run_text("CREATE (:Tombstone {id: 'tomb:x'})", label="t", graph=graph, readonly=False)
    (conf / "connectors.yaml").write_text("product: Other\n", encoding="utf-8")
    (conf / "people.yaml").unlink()
    (data / "tokens.json").unlink()
    with pytest.raises(backup.BackupError, match="exists: pass --force"):
        backup.restore(out)
    got = backup.restore(out, force=True)
    assert {"nodes": got["nodes"], "edges": got["edges"]} == before
    assert json.loads((live / "connectors.json").read_text(encoding="utf-8")) == {
        "slack": {"since_ms": 10, "seen": {"m1": 10}}
    }
    assert (data / "tokens.json").is_file() and not (live / "heartbeat.json").exists()
    assert got["config"] == {"connectors.yaml": "differs: kept", "people.yaml": "written"}
    assert (conf / "connectors.yaml").read_text(encoding="utf-8") == "product: Other\n"
    assert lock_holder(graph) is None  # restored with the backup's own lock released


def test_files_are_read_before_the_graph_and_under_its_writer_lock(deployment, monkeypatch):
    graph, out, _, _ = deployment
    seen = []
    files = backup.state_files
    monkeypatch.setattr(backup, "state_files", lambda g: seen.append("files") or files(g))
    admin = snapshot.admin()
    real_dump = admin.dump

    def dump(name):
        params = {"name": "derived", "holder": "someone else", "now_ms": int(time.time() * 1000),
                  "ttl_ms": 1000}  # fmt: skip
        seen.append("dump, lock free" if db.run("acquire_lock", params, graph=graph,
                    readonly=False).rows else "dump, lock held")  # fmt: skip
        return real_dump(name)

    monkeypatch.setattr(admin, "dump", dump)
    monkeypatch.setattr(snapshot, "admin", lambda: admin)
    backup.backup(graph, out)
    assert seen == ["files", "dump, lock held"]


def test_a_damaged_moved_or_foreign_file_is_refused(deployment, monkeypatch):
    graph, out, _, _ = deployment
    backup.backup(graph, out)
    blob = out.read_bytes()
    moved = out.with_name("moved.tar.gz")
    moved.write_bytes(blob)
    with pytest.raises(backup.BackupError, match=r"\.sha256 is missing"):
        backup.restore(moved, force=True)
    backup.sidecar(moved).write_text(f"{'0' * 64}  moved.tar.gz\n", encoding="utf-8")
    with pytest.raises(backup.BackupError, match="damaged or changed"):
        backup.restore(moved, force=True)
    # a member that doesn't match the manifest, even with the outer check skipped
    with tarfile.open(out) as tar:
        members = {m.name: tar.extractfile(m).read() for m in tar.getmembers()}
    members["data/tokens.json"] = b"{}"
    bad = out.with_name("bad.tar.gz")
    with tarfile.open(bad, "w:gz") as tar:
        for name, body in members.items():
            snapshot._add(tar, name, body)
    with pytest.raises(backup.BackupError, match="tokens.json doesn't match its sha256"):
        backup.restore(bad, force=True, unpinned=True)
    # another FalkorDB version: a DUMP payload restores only on its own
    monkeypatch.setattr(get_settings(), "falkordb_tag", "v9.9.9")
    with pytest.raises(backup.BackupError, match="restores only on the version"):
        backup.restore(out, force=True)
    # a snapshot is not a backup
    snap = io.BytesIO()
    with tarfile.open(fileobj=snap, mode="w:gz") as tar:
        snapshot._add(tar, "manifest.json", b'{"graphs": {}}')
    other = out.with_name("snap.tar.gz")
    other.write_bytes(snap.getvalue())
    with pytest.raises(backup.BackupError, match="not a yaan backup"):
        backup.restore(other, unpinned=True)


def test_a_running_worker_or_differing_files_stop_a_restore(deployment):
    graph, out, data, _ = deployment
    backup.backup(graph, out)
    snapshot.admin().delete(graph)  # the graph is gone, the files stay
    runner.beat(graph, healthy=True, pause_s=30)  # a worker is running here
    with pytest.raises(backup.BackupError, match="worker is running"):
        backup.restore(out)
    (data / "live" / graph / "heartbeat.json").unlink()  # stopped
    (data / "tokens.json").write_text("{}", encoding="utf-8")
    with pytest.raises(backup.BackupError, match="1 files differ"):
        backup.restore(out)
    (data / "tokens.json").unlink()
    assert backup.restore(out)["files_written"] == 6


def test_keep_deletes_the_oldest_backups_of_that_graph_only(tmp_path):
    folder = tmp_path / "backups"
    folder.mkdir()
    for name in ("g-20261001T000000Z", "g-20261002T000000Z", "g-20261003T000000Z",
                 "h-20261001T000000Z"):  # fmt: skip
        (folder / f"{name}.tar.gz").write_bytes(b"x")
        (folder / f"{name}.tar.gz.sha256").write_text("x", encoding="utf-8")
    assert backup.prune(folder, "g", keep=2) == ["g-20261001T000000Z.tar.gz"]
    assert sorted(p.name for p in folder.glob("*.tar.gz")) == [
        "g-20261002T000000Z.tar.gz", "g-20261003T000000Z.tar.gz", "h-20261001T000000Z.tar.gz"
    ]  # fmt: skip
    assert not (folder / "g-20261001T000000Z.tar.gz.sha256").exists()


def test_a_trial_restore_brings_the_graph_back_under_a_new_name_and_writes_no_file(deployment):
    graph, out, data, _ = deployment
    backup.backup(graph, out)
    (data / "tokens.json").write_text("{}", encoding="utf-8")  # differs: a trial doesn't care
    trial = f"{graph}_trial"
    try:
        got = backup.restore(out, as_graph=trial)
        assert got["graph"] == trial and got["files_written"] == 0 and got["nodes"] > 0
        assert snapshot.counts(snapshot.admin(), trial) == snapshot.counts(snapshot.admin(), graph)
        assert (data / "tokens.json").read_text(encoding="utf-8") == "{}"
        assert lock_holder(trial) is None
        with pytest.raises(backup.BackupError, match="needs a new name"):
            backup.restore(out, as_graph=trial)
    finally:
        snapshot.admin().delete(trial)


def test_a_dump_that_would_block_the_server_is_refused_unless_asked(deployment, monkeypatch):
    graph, out, _, _ = deployment
    monkeypatch.setattr(backup, "dump_seconds", lambda r: 120.0)
    with pytest.raises(backup.BackupError, match="about 120 s. Use --files-only"):
        backup.backup(graph, out)
    assert backup.backup(graph, out, block_ok=True)["nodes"] > 0


def test_a_files_only_backup_saves_the_graph_after_the_files_and_restores_beside_it(
    deployment, monkeypatch
):
    """D141: a big graph's DUMP would block FalkorDB for minutes. The files are read under
    the lock, then a fresh background save runs, so the volume's dump.rdb is never behind."""
    graph, out, data, _ = deployment
    seen = []
    files = backup.state_files
    monkeypatch.setattr(backup, "state_files", lambda g: seen.append("files") or files(g))
    saved = "2026-10-23T03:00:00Z"
    monkeypatch.setattr(backup, "fresh_save", lambda r: seen.append("save") or saved)
    report = backup.backup(graph, out, files_only=True)
    assert seen == ["files", "save"] and "dump.rdb" in report["next"]
    with tarfile.open(out) as tar:
        assert "graph.dump" not in tar.getnames()
    with pytest.raises(backup.BackupError, match="holds no graph to try"):
        backup.restore(out, as_graph=f"{graph}_x")
    (data / "tokens.json").unlink()
    got = backup.restore(out)  # the graph is there (its volume came back): only the files
    assert got["files_written"] == 6 and (data / "tokens.json").is_file()
    snapshot.admin().delete(graph)
    with pytest.raises(backup.BackupError, match="restore its volume"):
        backup.restore(out, force=True)


def test_a_fresh_save_waits_out_one_already_running_then_starts_its_own():
    class Server:
        def __init__(self):
            self.calls, self.running, self.last = [], 2, 100  # someone's save, 2 polls long

        def info(self, section):
            if self.running:
                self.running -= 1
                return {"rdb_bgsave_in_progress": 1, "rdb_last_save_time": self.last}
            return {"rdb_bgsave_in_progress": 0, "rdb_last_save_time": self.last,
                    "rdb_last_bgsave_status": "ok"}  # fmt: skip

        def bgsave(self):
            self.calls.append("bgsave")
            self.running, self.last = 1, int(time.time()) + 1

    s = Server()
    assert backup.fresh_save(s, wait_s=30).startswith("20")
    assert s.calls == ["bgsave"]  # not before the running one ended, and once
