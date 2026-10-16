"""Phase 8.0: the WORKS_ON timeline, computed in FalkorDB."""

import pytest

from app import db
from app.ingest import derive

pytestmark = pytest.mark.db


def test_works_on_spans_each_teams_activity_per_stage(empty_graph):
    db.run_text(
        "CREATE (p:Product:Entity {id: 'W'}), (t:Team:Entity {id: 'team:a'}),"
        " (u:Team:Entity {id: 'team:b'}),"
        " (ann:Person:Entity {id: 'eid_a'})-[:MEMBER_OF]->(t),"
        " (bo:Person:Entity {id: 'eid_b'})-[:MEMBER_OF]->(u),"
        " (plan:Channel {id: 'c1', stage: 'planning'}), (dev:Channel {id: 'c2', stage: 'dev'})"
        " WITH p, ann, bo, plan, dev"
        " UNWIND [[1, 'a', 'c1'], [5, 'a', 'c1'], [9, 'a', 'c1'], [20, 'a', 'c2'],"
        "         [30, 'b', 'c2'], [31, 'b', 'c2'], [40, 'b', 'c2']] AS r"
        " CREATE (m:Artifact:Message {id: 'msg:' + toString(r[0]), kind: 'message', ts_ms: r[0]})"
        " CREATE (m)-[:IN_PRODUCT]->(p)"
        " FOREACH (_ IN CASE WHEN r[1] = 'a' THEN [1] ELSE [] END | CREATE (ann)-[:AUTHORED]->(m))"
        " FOREACH (_ IN CASE WHEN r[1] = 'b' THEN [1] ELSE [] END | CREATE (bo)-[:AUTHORED]->(m))"
        " FOREACH (_ IN CASE WHEN r[2] = 'c1' THEN [1] ELSE [] END |"
        " CREATE (m)-[:IN_CHANNEL]->(plan))"
        " FOREACH (_ IN CASE WHEN r[2] = 'c2' THEN [1] ELSE [] END |"
        " CREATE (m)-[:IN_CHANNEL]->(dev))",
        label="works_on_fixture",
        graph=empty_graph,
        readonly=False,
    )
    derive.works_on(empty_graph)
    derive.works_on(empty_graph)  # idempotent
    rows = db.run_text(
        "MATCH (t:Team)-[w:WORKS_ON]->(:Product {id: 'W'}) "
        "RETURN t.id AS team, w.stage AS stage, w.from_ms AS f, w.to_ms AS l, w.artifacts AS n "
        "ORDER BY team, stage LIMIT 10",
        label="works_on_check",
        graph=empty_graph,
    ).rows
    # team a's single dev message is under the 3-artifact floor
    assert rows == [
        {"team": "team:a", "stage": "planning", "f": 1, "l": 9, "n": 3},
        {"team": "team:b", "stage": "dev", "f": 30, "l": 40, "n": 3},
    ]
