"""`yaan export` / `yaan import` (D120): a graph through plain JSONL and back, the same graph.
On the real server."""

import gzip
import json
import uuid
from pathlib import Path

import pytest

from app import db, export

pytestmark = pytest.mark.db
FIXTURE = Path(__file__).parent / "fixtures" / "agent_company.cypher"


def everything(graph: str) -> tuple[list, list]:
    nodes = sorted(
        (sorted(n["labels"]), json.dumps(n["props"], sort_keys=True))
        for n in export.nodes(graph, embeddings=True)
    )
    edges = sorted(json.dumps(e, sort_keys=True) for e in export.edges(graph))
    return nodes, edges


def test_a_graph_goes_out_as_jsonl_and_comes_back_the_same(empty_graph, tmp_path, monkeypatch):
    monkeypatch.setattr(export, "PAGE", 7)  # several pages of nodes and of edges
    db.run_text(FIXTURE.read_text(encoding="utf-8"), label="f", graph=empty_graph, readonly=False)
    vector = [round(0.001 * i, 3) for i in range(384)]
    db.run_text(
        "MATCH (m:Message {id: 'msg:m1'}) "
        "CREATE (c:Chunk {id: 'chk:m1', text: 'I shared the Vision doc.', artifact_id: 'msg:m1'}) "
        "SET c.embedding = vecf32($v) CREATE (m)-[:HAS_CHUNK]->(c)",
        {"v": vector},
        label="chunk",
        graph=empty_graph,
        readonly=False,
    )
    out = tmp_path / "company.jsonl.gz"
    header = export.export(empty_graph, out, embeddings=True)
    lines = gzip.open(out, "rt", encoding="utf-8").read().splitlines()
    assert json.loads(lines[0])["yaan_export"] == 1 and len(lines) == 1 + header["nodes"] + header[
        "edges"
    ]
    copy = f"yaan_test_{uuid.uuid4().hex[:12]}"
    try:
        loaded = export.load(out, copy)
        assert (loaded["nodes"], loaded["edges"]) == (header["nodes"], header["edges"])
        assert everything(copy) == everything(empty_graph)
        hit = db.run_text(
            "CALL db.idx.vector.queryNodes('Chunk', 'embedding', 1, vecf32($v)) "
            "YIELD node RETURN node.id AS id LIMIT 1",
            {"v": vector},
            label="vector",
            graph=copy,
        ).rows
        assert hit == [{"id": "chk:m1"}]  # the vector is back in the index
        with pytest.raises(export.ExportError, match="already exists"):
            export.load(out, copy)
    finally:
        if copy in db.client(readonly=False).list_graphs():
            db.client(readonly=False).select_graph(copy).delete()


def test_an_export_leaves_vectors_out_unless_asked(empty_graph, tmp_path):
    db.run_text(
        "CREATE (:Chunk {id: 'chk:1', text: 'x'})", label="c", graph=empty_graph, readonly=False
    )
    db.run_text(
        "MATCH (c:Chunk) SET c.embedding = vecf32($v)",
        {"v": [0.0] * 384},
        label="v",
        graph=empty_graph,
        readonly=False,
    )
    out = tmp_path / "plain.jsonl"
    export.export(empty_graph, out)
    node = json.loads(out.read_text(encoding="utf-8").splitlines()[1])["node"]
    assert "embedding" not in node["props"]
