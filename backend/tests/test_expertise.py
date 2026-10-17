"""Phase 10 expertise (D059): areas, degree-of-authorship, truck factor. Pure functions first,
checked against hand-computed values; then the derive step on the Sentinel's invented company."""

import math
from pathlib import Path

import pytest

from app import db
from app.agent import tools
from app.ingest import derive
from app.ingest import expertise as ex

DAY = ex.DAY_MS


def test_doa_matches_the_published_formula_by_hand():
    # first author with 2 of 3 deliveries: 3.293 + 1.098 + 2(0.164) - 0.321 ln(1 + 1)
    assert ex.doa(True, 2, 1) == pytest.approx(4.719 - 0.321 * math.log(2))
    assert ex.doa(False, 1, 2) == pytest.approx(3.457 - 0.321 * math.log(3))


def test_knows_marks_authors_scores_recency_and_gives_reviewers_no_doa():
    area = ex.Area("area:W:c:x", "X", "component", "W")
    area.deliveries = [("a", 1 * DAY, "i1"), ("a", 2 * DAY, "i2"), ("b", 3 * DAY, "i3")]
    area.talk = [("c", 3 * DAY), ("c", 3 * DAY), ("b", 3 * DAY)]
    rows = {r["person_id"]: r for r in ex.knows(area, now_ms=92 * DAY)}
    a, b, c = rows["a"], rows["b"], rows["c"]
    assert a["author"] and a["first"] and a["deliveries"] == 2 and a["doa_norm"] == 1.0
    assert b["doa_norm"] == pytest.approx(ex.doa(False, 1, 2) / ex.doa(True, 2, 1), abs=1e-4)
    assert not b["author"]  # 0.69 of the top, under the 0.75 bar
    assert a["score"] == pytest.approx(math.exp(-90 / 90), abs=1e-4)  # last delivery 90 days ago
    assert c["doa_abs"] is None and c["score"] == pytest.approx(0.2) and not c["author"]
    assert b["discussion"] == 0.5 and a["refs"] == ["i1", "i2"]


def test_truck_factor_removes_top_authors_until_most_areas_are_orphaned():
    assert ex.truck_factor({"x": {"a"}, "y": {"a"}, "z": {"b"}}) == (1, ["a"])
    # removing a orphans exactly half (not more), so b goes too
    assert ex.truck_factor({"x": {"a"}, "y": {"a"}, "z": {"b"}, "w": {"a", "b"}}) == (2, ["a", "b"])
    assert ex.truck_factor({"x": set()}) == (0, [])


def test_a_compound_component_splits_only_into_known_components():
    known = {"backend server": "Backend Server", "visualization module": "Visualization Module"}
    assert ex.component_areas("Backend Server & Visualization Module", known) == [
        "backend server",
        "visualization module",
    ]
    assert ex.component_areas("Database & User Interface", known) == ["database and user interface"]
    assert ex.component_areas("AI suggestion engine", {}) == ["ai suggestion engine"]
    assert ex.component_areas(None, known) == []


def pr(id, title, ts, author, reverts=None, fixes=None, product="Widget"):
    return {
        "id": id,
        "product_id": product,
        "title": title,
        "number": int(id[-1]),
        "ts_ms": ts,
        "author": author,
        "reverts": reverts,
        "fixes": fixes,
    }


def test_work_items_group_retries_and_reverts_and_bug_fixes_join_their_component():
    issues = [{"id": "iss:1", "product_id": "Widget", "component": "Export", "reported_ms": 0}]
    people = [{"issue_id": "iss:1", "person_id": "e", "how": "fix", "ref_id": "pr:5", "ts_ms": 9}]
    prs = [
        pr("pr:1", "Implement GraphQL Schema for Widget", 1, "a"),
        pr("pr:2", "Implement GraphQL schema for Widget!", 2, "b"),
        pr("pr:3", "Revert PR# 2: GraphQL changes", 3, "c", reverts="pr:2"),
        pr("pr:4", "Add Caching", 4, "a"),
        pr("pr:5", "Fix export", 5, "e", fixes="iss:1"),
    ]
    reviews = [{"person_id": "r", "pr_id": "pr:3", "ts_ms": 3}]
    areas = ex.build_areas(issues, people, prs, reviews)
    gql = areas["area:Widget:w:implement-graphql-schema-for"]
    assert gql.name == "Implement GraphQL Schema for Widget" and gql.prs == {"pr:1", "pr:2", "pr:3"}
    assert [d[0] for d in gql.deliveries] == ["a", "b", "c"] and gql.talk == [("r", 3)]
    export = areas["area:Widget:c:export"]
    assert export.deliveries == [("e", 9, "iss:1")] and export.prs == {"pr:5"}
    assert {a.kind for a in areas.values()} == {"work_item", "component"} and len(areas) == 3


FIXTURE = Path(__file__).parent / "fixtures" / "sentinel_company.cypher"


@pytest.mark.db
def test_derive_writes_areas_knows_and_the_truck_factor(empty_graph):
    db.run_text(FIXTURE.read_text(encoding="utf-8"), label="f", graph=empty_graph, readonly=False)
    out = derive.expertise(empty_graph)
    assert out["truck"] == [
        {
            "product_id": "Widget",
            "truck_factor": 1,
            "truck_people": ["eid_c3"],
            "areas_with_author": 1,
            "areas_orphaned": 1,
        }
    ]
    rows = db.run_text(
        "MATCH (p:Person)-[k:KNOWS]->(a:Area {name: 'Export Module'}) "
        "RETURN p.id AS id, k.author AS author, k.deliveries AS d, k.discussion AS disc "
        "ORDER BY id LIMIT 10",
        label="k",
        graph=empty_graph,
    ).rows
    assert rows == [
        {"id": "eid_b2", "author": False, "d": 0, "disc": 1.0},  # reviewed PR 1 twice
        {"id": "eid_c3", "author": True, "d": 1, "disc": 0.0},  # fixed 'Slow export'
        {"id": "eid_d4", "author": False, "d": 0, "disc": 0.5},
    ]
    issues = db.run_text(
        "MATCH (i:Issue)-[:IN_AREA]->(a:Area {name: 'Export Module'}) RETURN count(i) AS n LIMIT 1",
        label="n",
        graph=empty_graph,
    ).rows
    assert issues == [{"n": 3}]
    again = derive.expertise(empty_graph)  # idempotent: cleared and rewritten
    assert again["report"]["upsert_areas"]["rows"] == out["report"]["upsert_areas"]["rows"]


def test_collab_weights_split_a_meeting_and_count_each_review():
    meetings = [{"meeting_id": "m1", "person_id": p} for p in ("a", "b", "c")]
    messages = [
        {"message_id": "msg:20270101-0-x", "channel_id": "ch", "person_id": "a"},
        {"message_id": "msg:20270101-1-y", "channel_id": "ch", "person_id": "b"},
        {"message_id": "msg:20270102-0-z", "channel_id": "ch", "person_id": "c"},
    ]
    prs = [{"id": "pr:1", "author": "a"}]
    reviews = [{"person_id": "b", "pr_id": "pr:1"}, {"person_id": "b", "pr_id": "pr:1"}]
    w = derive.collab_weights(meetings, messages, prs, reviews)
    assert w[("a", "b")] == {"w": 0.5 + 1 + 2, "reviews": 2, "meetings": 1, "chats": 1}
    assert w[("a", "c")] == {"w": 0.5, "reviews": 0, "meetings": 1, "chats": 0}  # c chatted alone


@pytest.mark.db
def test_collab_and_pagerank_on_the_invented_company(empty_graph):
    db.run_text(FIXTURE.read_text(encoding="utf-8"), label="f", graph=empty_graph, readonly=False)
    out = derive.collab(empty_graph, min_w=0.5)
    assert out["pairs"] == 2  # Bo and Di each reviewed Cy's PR
    rows = db.run_text(
        "MATCH (p:Person) WHERE p.pagerank_rank IS NOT NULL "
        "RETURN p.id AS id, p.pagerank_rank AS rank ORDER BY rank LIMIT 10",
        label="pr",
        graph=empty_graph,
    ).rows
    assert rows[0]["id"] == "eid_c3"  # the author both reviewers link to
    edges = db.run_text(
        "MATCH (:Person {id: 'eid_b2'})-[c:COLLAB]->(:Person {id: 'eid_c3'}) "
        "RETURN c.w AS w, c.reviews AS r LIMIT 1",
        label="c",
        graph=empty_graph,
    ).rows
    assert edges == [{"w": 2.0, "r": 2}]


def test_match_areas_prefers_the_exact_name_over_a_subset():
    areas = [
        {"id": "area:V:c:api", "name": "API", "aliases": ["API"]},
        {"id": "area:V:c:api-data", "name": "API & Data Integration Layer", "aliases": []},
        {"id": "area:V:c:ui", "name": "User Interface", "aliases": []},
    ]
    got = tools.match_areas("API & Data Integration Layer", areas)
    assert got[0]["id"] == "area:V:c:api-data" and got[0]["match"] == 100
    assert tools.match_areas("quantum billing", areas) == []


@pytest.mark.db
def test_who_knows_names_the_expert_the_owner_and_the_evidence(empty_graph):
    db.run_text(FIXTURE.read_text(encoding="utf-8"), label="f", graph=empty_graph, readonly=False)
    derive.collab(empty_graph, min_w=0.5)
    derive.expertise(empty_graph)
    out = tools.who_knows("export module", "Widget", graph=empty_graph)
    [area] = [a for a in out["areas"] if a["name"] == "Export Module"]
    top = area["experts"][0]
    assert (top["id"], top["author"], top["key_person"]) == ("eid_c3", True, True)
    assert top["evidence"] == [
        {
            "artifact_id": "pr:w1",
            "number": 1,
            "title": "Speed up export",
            "ts_iso": "2026-05-01T00:00:02Z",
        }
    ]
    assert {o["id"] for o in area["listed_owners"]} == {"eid_c3"} and area["single_expert"]
    assert area["owner_is_expert"] and "owner_to_expert" not in area
    assert out["truck_factor"]["value"] == 1 and "connectors" not in out
    assert tools.who_knows("quantum billing", "Widget", graph=empty_graph)["areas"] == []


@pytest.mark.db
def test_communities_and_betweenness_on_the_invented_company(empty_graph):
    db.run_text(FIXTURE.read_text(encoding="utf-8"), label="f", graph=empty_graph, readonly=False)
    derive.collab(empty_graph, min_w=0.5)  # Bo-Cy and Di-Cy: a chain of three
    bridges = derive.communities(empty_graph)["bridges"]
    assert bridges[0]["id"] == "eid_c3"  # the only path between Bo and Di runs through Cy
    # close the triangle (Bo and Di also work together): one working group of three
    db.run_text(
        "MATCH (b:Person {id: 'eid_b2'}), (d:Person {id: 'eid_d4'}) "
        "CREATE (b)-[:COLLAB {w: 1.0}]->(d), (d)-[:COLLAB {w: 1.0}]->(b)",
        label="tri",
        graph=empty_graph,
        readonly=False,
    )
    out = derive.communities(empty_graph)
    assert out["communities"] == [3]
    rows = db.run_text(
        "MATCH (p:Person)-[:IN_COMMUNITY]->(c:Community) "
        "RETURN c.size AS size, c.team_count AS teams, collect(p.id) AS ids LIMIT 5",
        label="c",
        graph=empty_graph,
    ).rows
    assert rows[0]["size"] == 3 and sorted(rows[0]["ids"]) == ["eid_b2", "eid_c3", "eid_d4"]


@pytest.mark.db
def test_collab_paths_find_the_shortest_chain_and_report_no_tie(empty_graph):
    db.run_text(FIXTURE.read_text(encoding="utf-8"), label="f", graph=empty_graph, readonly=False)
    derive.collab(empty_graph, min_w=0.5)  # Bo-Cy-Di
    paths = tools.collab_paths([("eid_b2", "eid_d4"), ("eid_b2", "eid_e5")], graph=empty_graph)
    assert paths[("eid_b2", "eid_d4")] == ["Bo Chen", "Cy Diaz", "Di Park"]
    assert paths[("eid_b2", "eid_e5")] == []  # Ed never worked with anyone
    assert tools.collab_paths([], graph=empty_graph) == {}


@pytest.mark.db
def test_uses_and_similarity_precedents_on_the_invented_company(empty_graph):
    db.run_text(FIXTURE.read_text(encoding="utf-8"), label="f", graph=empty_graph, readonly=False)
    derive.uses(empty_graph)
    derive.uses(empty_graph)  # rebuilt, not doubled
    rows = db.run_text(
        "MATCH (c:Company)-[u:USES]->(p:Product) "
        "RETURN c.id AS c, p.id AS p, u.issues AS n LIMIT 5",
        label="u",
        graph=empty_graph,
    ).rows
    assert rows == [{"c": "co:blue-wave", "p": "Widget", "n": 1}]
    # two dismissals of near-identical issues: the earlier one is a precedent for the later one
    db.run_text(
        "MATCH (w:Product {id: 'Widget'}) "
        "CREATE (i1:Issue:Entity {id: 'iss:Widget:p1', title: 'Export button freezes', "
        "description: 'Clicking export freezes the page.', product_id: 'Widget'}), "
        "(i2:Issue:Entity {id: 'iss:Widget:p2', title: 'Export button freezes the page', "
        "description: 'The page freezes after clicking export.', product_id: 'Widget'}), "
        "(:Decision {id: 'dec:p1', kind: 'issue_status', value: 'dismissed', "
        "origin: 'extracted', valid_from_ms: 100})-[:AFFECTS]->(i1), "
        "(:Decision {id: 'dec:p2', kind: 'issue_status', value: 'dismissed', "
        "origin: 'extracted', valid_from_ms: 200})-[:AFFECTS]->(i2)",
        label="p",
        graph=empty_graph,
        readonly=False,
    )
    out = derive.precedents(empty_graph)
    got = db.run(
        "decision_precedents", {"decision_id": "dec:p2", "limit": 5}, graph=empty_graph
    ).rows
    assert [r["id"] for r in got] == ["dec:p1"] and got[0]["similarity"] >= 0.8
    assert (
        db.run("decision_precedents", {"decision_id": "dec:p1", "limit": 5}, graph=empty_graph).rows
        == []
    )
    assert out["links"] >= 1
