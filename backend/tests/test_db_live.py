"""Lore's own database code (app/db.py, app/schema.py, list_indexes.cypher) on the real server.

FalkorDB's own behavior (the A-table in decisions.md) is pinned separately by the contract
tests; these check that our code uses it correctly.
"""

import pytest

from app import db, schema

pytestmark = pytest.mark.db


def read(graph, text, **params):
    return db.run_text(text, params, label="test_read", graph=graph).rows


def test_the_tiny_company_answers_what_is_true_now(graph):
    current = read(
        graph,
        "MATCH (d:Decision {topic: $topic}) WHERE d.valid_to_ms IS NULL "
        "MATCH (d)-[e:EVIDENCED_BY]->(a:Artifact) MATCH (d)-[:DECIDED_BY]->(p:Person) "
        "RETURN d.value AS value, p.name AS by, e.quote AS quote LIMIT 5",
        topic="pr:42/status",
    )
    assert current == [
        {"value": "reverted", "by": "Linus", "quote": "Reverting PR 42 until it is fixed."}
    ]
    chain = read(
        graph,
        "MATCH (new:Decision)-[:SUPERSEDES]->(old:Decision) RETURN old.value AS old, "
        "new.value AS new LIMIT 5",
    )
    assert chain == [{"old": "merged", "new": "reverted"}]


def test_list_indexes_matches_the_schema_file(empty_graph):
    rows = db.run("list_indexes", graph=empty_graph).rows
    assert schema.problems(schema.declared(), rows) == []


def test_an_id_lookup_uses_the_index(graph):
    plan = db.explain_text(
        "MATCH (p:Person {id: $id}) RETURN p.name AS name LIMIT 1",
        {"id": "eid_ada"},
        label="test_plan",
        graph=graph,
    )
    assert any("Node By Index Scan" in line for line in plan), plan


def test_rerunning_the_schema_is_safe(graph):
    assert {outcome for _, outcome in schema.apply(graph)} == {"exists"}
    assert schema.verify(graph) == []


def test_batched_writes_are_idempotent(empty_graph):
    upsert = "UNWIND $rows AS r MERGE (p:Person:Entity {id: r.id}) SET p.n = r.n"
    rows = [{"id": f"eid_{i}", "n": i} for i in range(1201)]
    first = db.run_text(
        upsert, {"rows": rows}, label="t", graph=empty_graph, readonly=False, batch_rows=500
    )
    again = db.run_text(upsert, {"rows": rows}, label="t", graph=empty_graph, readonly=False)
    assert (first.batches, first.stats["nodes_created"]) == (3, 1201)
    assert again.stats["nodes_created"] == 0
    assert read(empty_graph, "MATCH (p:Person) RETURN count(p) AS n LIMIT 1") == [{"n": 1201}]


def test_a_slow_read_raises_query_timeout(empty_graph):
    with pytest.raises(db.QueryTimeout, match="slow"):
        db.run_text(
            "UNWIND range(1, 300000000) AS i RETURN count(i) AS c LIMIT 1",
            label="slow",
            graph=empty_graph,
            timeout_ms=100,
        )


def test_the_reader_cannot_write_through_either_lock(graph):
    # Lock 2: through db.run_text, a write becomes GRAPH.RO_QUERY and FalkorDB refuses it.
    with pytest.raises(db.QueryError, match="read-only queries"):
        db.run_text("MATCH (p:Person) SET p.hacked = true", label="t", graph=graph)
    # Lock 1: even sent as GRAPH.QUERY, the reader's ACL user isn't allowed the command.
    with pytest.raises(Exception, match="no permissions to run the 'graph.QUERY' command"):
        db.client(readonly=True).select_graph(graph).query("MATCH (p:Person) SET p.hacked = true")
    assert read(graph, "MATCH (p:Person) WHERE p.hacked RETURN count(p) AS n LIMIT 1") == [{"n": 0}]
