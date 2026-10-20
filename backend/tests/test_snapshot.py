"""`lore snapshot` / `lore seed` (15.2): a graph and the live-path data round-trip through one
file, checked by sha256, counts and indexes. On the real server, as the admin user."""

import json
import tarfile
from pathlib import Path

import pytest

from app import db, schema, snapshot
from app.config import get_settings

pytestmark = pytest.mark.db
FIXTURE = Path(__file__).parent / "fixtures" / "agent_company.cypher"


@pytest.fixture
def setup(empty_graph, tmp_path, monkeypatch):
    db.run_text(FIXTURE.read_text(encoding="utf-8"), label="f", graph=empty_graph, readonly=False)
    data = tmp_path / "data"
    for d in snapshot.DATA_DIRS:
        (data / d / "Widget").mkdir(parents=True)
        (data / d / "Widget" / "rows.jsonl").write_text('{"id": "x"}\n', encoding="utf-8")
    monkeypatch.setattr(get_settings(), "data_dir", data)
    return empty_graph, tmp_path / "snap.tar.gz", data


def test_a_graph_and_its_data_round_trip(setup):
    graph, out, data = setup
    before = snapshot.counts(snapshot.admin(), graph)
    manifest = snapshot.snapshot((graph,), out)
    assert manifest["graphs"][graph]["nodes"] == before["nodes"] > 0
    with tarfile.open(out) as tar:
        names = tar.getnames()
    assert f"graphs/{graph}.dump" in names and "data/extract/Widget/rows.jsonl" in names
    # refuses to overwrite, then restores after the graph and files are gone
    with pytest.raises(snapshot.SnapshotError, match="already exists"):
        snapshot.seed(out)
    snapshot.admin().delete(graph)
    for d in snapshot.DATA_DIRS:
        (data / d / "Widget" / "rows.jsonl").unlink()
    report = snapshot.seed(out)
    assert report["graphs"][graph]["nodes"] == before["nodes"]
    assert report["data_files_written"] == 2 and "demo" not in report  # not the HERB graph
    assert (data / "normalized" / "Widget" / "rows.jsonl").read_text(encoding="utf-8")
    assert schema.problems(schema.declared(), db.run("list_indexes", graph=graph).rows) == []


def test_a_damaged_snapshot_is_refused(setup):
    graph, out, _ = setup
    snapshot.snapshot((graph,), out)
    with tarfile.open(out) as tar:
        members = {m.name: tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}
    manifest = json.loads(members["manifest.json"])
    manifest["graphs"][graph]["sha256"] = "0" * 64
    members["manifest.json"] = json.dumps(manifest).encode()
    bad = out.with_name("bad.tar.gz")
    with tarfile.open(bad, "w:gz") as tar:
        for name, data in members.items():
            snapshot._add(tar, name, data)
    snapshot.admin().delete(graph)
    with pytest.raises(snapshot.SnapshotError, match="pinned in neither"):
        snapshot.seed(bad)  # no pin names it: refused before anything is restored
    with pytest.raises(snapshot.SnapshotError, match="doesn't match its sha256"):
        snapshot.seed(bad, unpinned=True)  # and the manifest's own check still holds


def test_only_a_pinned_snapshot_is_loaded(setup, monkeypatch):
    graph, out, _ = setup
    manifest = snapshot.snapshot((graph,), out)
    line = out.with_name("SHA256SUMS").read_text(encoding="utf-8")
    assert line == f"{manifest['file_sha256']}  {out.name}\n"  # written beside the file
    moved = out.with_name("elsewhere") / out.name
    moved.parent.mkdir()
    moved.write_bytes(out.read_bytes())
    monkeypatch.setattr(snapshot, "SUMS", out.with_name("none"))
    snapshot.admin().delete(graph)
    with pytest.raises(snapshot.SnapshotError, match="pinned in neither"):
        snapshot.seed(moved)  # a copy without its SHA256SUMS
    monkeypatch.setattr(snapshot, "SUMS", out.with_name("SHA256SUMS"))  # the committed pins
    assert snapshot.seed(moved)["graphs"][graph]["nodes"] > 0
