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


def pairwise_precedents(rows, vecs, min_sim):
    """The loop derive.precedents ran before D112, kept as the reference: every pair."""
    links = []
    for i, d in enumerate(rows):
        scored = []
        for j, p in enumerate(rows):
            if (
                (p["kind"], p["value"], p["product_id"]) == (d["kind"], d["value"], d["product_id"])
                and p["subject_id"] != d["subject_id"]
                and (p["ts_ms"] or 0) < (d["ts_ms"] or 0)
            ):
                sim = sum(a * b for a, b in zip(vecs[i], vecs[j], strict=True))
                if sim >= min_sim:
                    scored.append((sim, p["id"]))
        for sim, pid in sorted(scored, reverse=True)[: derive.PRECEDENTS_PER_DECISION]:
            links.append({"precedent_id": pid, "decision_id": d["id"], "similarity": round(sim, 4)})
    return links


@pytest.mark.parametrize("seed", range(5))
def test_precedents_by_matrix_find_exactly_what_every_pair_did(seed, monkeypatch):
    """D112: grouped, blocked matrix products in place of the all-pairs loop, same links. Small
    blocks so a group spans several; near-duplicate subjects so some pairs pass min_sim."""
    import random

    monkeypatch.setattr(derive, "PRECEDENT_BLOCK", 7)
    rng = random.Random(seed)
    bases = [[rng.gauss(0, 1) for _ in range(16)] for _ in range(6)]
    rows, vecs = [], []
    for n in range(60):
        base = bases[rng.randrange(len(bases))]
        v = [x + rng.gauss(0, 0.35) for x in base]
        norm = sum(x * x for x in v) ** 0.5
        vecs.append([x / norm for x in v])
        rows.append(
            {
                "id": f"dec:{n:03d}",
                "kind": rng.choice(["issue_status", "pr_action"]),
                "value": rng.choice(["resolved", "dismissed"]),
                "product_id": rng.choice(["A", "B"]),
                "subject_id": f"iss:{rng.randrange(25)}",
                "ts_ms": rng.choice([None, *range(1000, 1040)]),
            }
        )
    want = pairwise_precedents(rows, vecs, 0.8)
    got = derive.similar_earlier(rows, vecs, 0.8)
    assert len(want) > 20  # the case has real precedents to find
    key = lambda x: (x["decision_id"], x["precedent_id"])  # noqa: E731
    assert sorted(got, key=key) == sorted(want, key=key)
