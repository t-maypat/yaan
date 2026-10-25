"""The R2 tools (decision_lineage, as_of, rank, find_missing, impact_of, open_alerts,
person_profile, connection_path, graph_query) and the registry, on the invented graph of
fixtures/agent_company.cypher plus fixtures/agent_company_r2.cypher.
"""

from pathlib import Path

import pytest

from app import db
from app.agent import graph_query, loop, prompts, tools

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def company(empty_graph):
    for name in ("agent_company", "agent_company_r2"):
        text = (FIXTURES / f"{name}.cypher").read_text(encoding="utf-8")
        db.run_text(text, label=name, graph=empty_graph, readonly=False)
    return empty_graph


# --- no database -----------------------------------------------------------------------------
def test_parse_as_of_reads_a_date_as_the_end_of_that_day():
    end = tools.parse_as_of("2027-01-15")
    assert tools.iso(end) == "2027-01-15T23:59:59Z"
    assert tools.parse_as_of("2027-01-15T10:00:00Z") == end - 50_399_999
    assert tools.parse_as_of(None) is None and tools.parse_as_of("") is None
    with pytest.raises(ValueError, match="ISO date"):
        tools.parse_as_of("last tuesday")


@pytest.mark.parametrize(
    ("cypher", "reason"),
    [
        ("MATCH (n) DETACH DELETE n", "DETACH"),
        ("CREATE (:Person {id: 'x'})", "CREATE"),
        ("MATCH (n) SET n.x = 1 RETURN n", "SET"),
        ("MATCH (a)-[*]->(b) RETURN a LIMIT 5", "upper bound"),
        ("MATCH (a)-[*..9]->(b) RETURN a LIMIT 5", "6 hops"),
        ("MATCH (n) RETURN n LIMIT 500", "at most 200"),
        ("MATCH (n) RETURN n; MATCH (m) RETURN m", "one statement"),
        ("CALL db.idx.fulltext.createNodeIndex('X', 'y')", "read-only list"),
        ("MATCH (n) WHERE n.name =~ 'A.*' RETURN n LIMIT 1", "regular expressions"),
    ],
)
def test_graph_query_guard_refuses_writes_and_unbounded_reads(cypher, reason):
    with pytest.raises(graph_query.Refused, match=reason):
        graph_query.check(cypher)


def test_graph_query_guard_adds_a_limit_and_ignores_keywords_in_strings():
    assert graph_query.check("MATCH (n:Issue) RETURN n.title").endswith("LIMIT 200")
    text = "MATCH (i:Issue) WHERE i.title CONTAINS 'Create SET delete' RETURN i.id LIMIT 3"
    assert graph_query.check(text) == text
    assert "Decision" in graph_query.schema_card() and "COLLAB" in graph_query.schema_card()


def test_registry_holds_every_spec_and_the_search_only_prompt_drops_graph_lines():
    names = [t["name"] for t in tools.TOOL_SPECS]
    assert names == list(tools.REGISTRY) and len(names) == len(set(names)) == 17
    full = prompts.system_prompt(names)
    lean = prompts.system_prompt(tools.SEARCH_ONLY)
    assert "decision_lineage" in full and "who_knows" in full
    assert "decision_lineage" not in lean and "list_issues" not in lean
    assert "search_artifacts" in lean and lean.endswith(full[full.index("Rules for") :])


def test_strict_schemas_stay_inside_the_apis_shared_budget():
    """The API refuses (400) a request whose strict schemas, tools and answer format together,
    pass 20 strict tools, 24 optional or 16 union-typed parameters (D068)."""
    import json

    from app.agent.contract import SCHEMA

    text = json.dumps([*tools.TOOL_SPECS, SCHEMA])
    unions = text.count('"anyOf"') + text.count('"type": [')
    optional = sum(
        len(t["input_schema"]["properties"]) - len(t["input_schema"]["required"])
        for t in tools.TOOL_SPECS
    )
    assert sum(t["strict"] for t in tools.TOOL_SPECS) <= 20
    assert unions <= 16 and optional <= 24, (unions, optional)
    assert "x-yaan" not in text  # the optional marker never reaches the API


def test_call_tool_refuses_tools_outside_the_toolset():
    scope = tools.Scope("Widget")
    with pytest.raises(loop.ToolError, match="unknown tool"):
        loop.call_tool("list_issues", {}, scope, frozenset(tools.SEARCH_ONLY))
    with pytest.raises(loop.ToolError, match="needs a product"):
        loop.call_tool("rank", {"metric": "prs_authored"}, tools.Scope(None))


# --- on the fixture graph --------------------------------------------------------------------
@pytest.mark.db
def test_decision_lineage_gives_the_chain_with_deciders_evidence_and_alerts(company):
    out = tools.decision_lineage("slow export", "Widget", graph=company)
    [s] = out["subjects"]
    assert s["id"] == "iss:Widget:abc" and s["status_now"] == "resolved"
    first, second = s["status_chain"]
    assert (first["value"], first["in_force"], second["value"], second["in_force"]) == (
        "reported",
        False,
        "resolved",
        True,
    )
    assert second["decided_by"] == [{"id": "eid_c3", "name": "Cy Diaz", "role": "Engineering Lead"}]
    [evidence] = second["evidence"]
    assert (evidence["artifact_id"], evidence["quote"]) == ("msg:r2", "on it")
    assert [c["artifact_id"] for c in second["said"]] == ["msg:r2"]
    assert [a["id"] for a in second["alerts"]] == ["alert:x"]
    past = tools.decision_lineage("Slow export", "Widget", as_of_ms=8050, graph=company)
    assert past["subjects"][0]["status_as_of"] == "reported"


@pytest.mark.db
def test_decision_lineage_reads_pr_numbers_and_both_pr_chains(company):
    [s] = tools.decision_lineage("PR #1", "Widget", graph=company)["subjects"]
    assert s["id"] == "pr:w1"
    assert [c["value"] for c in s["status_chain"]] == ["open", "approved"]
    assert [c["value"] for c in s["action_chain"]] == ["to_close"]
    assert tools.decision_lineage("Quantum teleporter", "Widget", graph=company)["subjects"] == []


@pytest.mark.db
def test_as_of_shows_the_state_in_force_at_that_moment(company):
    then = tools.list_issues("Widget", graph=company, as_of_ms=8050)
    [issue] = then["issues"]  # the second issue wasn't reported yet
    assert issue["status"] == "reported" and [h["status"] for h in issue["history"]] == ["reported"]
    assert then["as_of"] == "1970-01-01T00:00:08Z"
    now = tools.list_issues("Widget", graph=company)
    assert {i["status"] for i in now["issues"]} == {"resolved", "reported"}
    early = tools.list_prs("Widget", graph=company, as_of_ms=4050)["prs"]
    [pr] = [p for p in early if p["id"] == "pr:w1"]
    assert pr["chain"] == ["open"] and pr["status"] == "open" and pr["said"] == []
    assert pr["team_action"] is None
    assert pr["reverted_by"] == []  # the revert PR came at 4500: no leak from the future
    later = tools.list_prs("Widget", graph=company, as_of_ms=4600)["prs"]
    assert [u["number"] for p in later if p["id"] == "pr:w1" for u in p["reverted_by"]] == [3]
    assert tools.list_prs("Widget", graph=company, as_of_ms=3000)["prs"] == []


@pytest.mark.db
def test_rank_counts_in_the_graph_with_shared_ranks(company):
    authored = tools.rank("prs_authored", "Widget", graph=company)["ranking"]
    assert [(r["id"], r["n"], r["rank"]) for r in authored] == [("eid_b2", 1, 1)]
    assert authored[0]["evidence"] == [{"artifact_id": "pr:w1", "title": "Add export"}]
    fixed = tools.rank("bugs_fixed", "Widget", graph=company)["ranking"]
    assert [(r["id"], r["n"]) for r in fixed] == [("eid_b2", 1)]
    companies = tools.rank("issues_reported", "Widget", graph=company)["ranking"]
    assert [(r["name"], r["n"]) for r in companies] == [("Blue Wave", 2)]
    open_ = tools.rank("issues_unresolved", "Widget", by="customer", graph=company)["ranking"]
    assert [(r["name"], r["n"]) for r in open_] == [("Dee Fox", 1)]
    with pytest.raises(ValueError):
        tools.rank("vibes", "Widget", graph=company)


@pytest.mark.db
def test_find_missing_is_absence_of_a_path(company):
    def ids(kind):
        return [r["id"] for r in tools.find_missing(kind, "Widget", graph=company)["items"]]

    assert ids("open_without_fix") == ["iss:Widget:def"]
    assert ids("resolved_without_fix") == []  # PR 1 FIXES it
    assert ids("issue_without_assignee") == ["iss:Widget:def"]
    assert ids("pr_without_review") == ["pr:w3"]
    assert ids("area_single_expert") == ["area:Widget:c:export-module"]


@pytest.mark.db
def test_impact_of_finds_the_active_decision_and_its_radius(company):
    out = tools.impact_of("Slow export", "Widget", graph=company)
    assert out["decision_id"] == "dec:iss:Widget:abc/status:8100"
    assert [p["id"] for p in out["prs"]] == ["pr:w1"]
    people = {p["id"]: set(p["reasons"]) for p in out["people"]}
    assert people["eid_b2"] >= {"authored_fix"} and people["eid_c3"] >= {"reviewed_fix", "assigned"}
    assert [c["name"] for c in out["companies"]] == ["Blue Wave"]
    assert [i["id"] for i in out["open_issues_same_component"]] == ["iss:Widget:def"]
    assert "rows" not in out


@pytest.mark.db
def test_open_alerts_lists_what_the_sentinel_raised(company):
    out = tools.open_alerts("Widget", graph=company)
    [a] = out["alerts"]
    assert a["id"] == "alert:x" and a["notifies"] == ["Cy Diaz"] and a["artifact_id"] == "msg:r2"
    assert out["counts_by_kind"] == {"regression": 1}
    assert tools.open_alerts("Widget", kind="reversal", graph=company)["alerts"] == []


@pytest.mark.db
def test_person_profile_resolves_namesakes_and_gathers_the_person(company):
    out = tools.person_profile("Bo Chen", "Widget", graph=company)
    assert out["id"] == "eid_b2" and [n["id"] for n in out["namesakes"]] == ["eid_b9"]
    assert out["teams"] == ["Widget Team"] and out["managers"][0]["id"] == "eid_c3"
    assert [k["name"] for k in out["knows"]] == ["Export Module"]
    assert [c["id"] for c in out["collaborators"]] == ["eid_c3", "eid_a1"]
    assert out["activity_by_product"]["Widget"]["authored_pr"] == 1
    assert {a["artifact_id"] for a in out["recent_artifacts"]} >= {"pr:w1", "msg:r1"}


@pytest.mark.db
def test_connection_path_is_the_shortest_collab_chain(company):
    out = tools.connection_path("Ann Lee", "Cy Diaz", "Widget", graph=company)
    assert out["connected"] and out["hops"] == 2
    assert [p["id"] for p in out["path"]] == ["eid_a1", "eid_b2", "eid_c3"]
    assert out["ties"][0]["w"] == 0.5
    assert "note" in tools.connection_path("Ann Lee", "Nobody Here", "Widget", graph=company)


@pytest.mark.db
def test_graph_query_runs_read_only_and_returns_plain_rows(company):
    out = graph_query.graph_query(
        "MATCH (i:Issue)-[:IN_PRODUCT]->(:Product {id: 'Widget'}) RETURN i ORDER BY i.id",
        graph=company,
    )
    assert out["cypher_run"].endswith("LIMIT 200")
    assert [r["i"]["id"] for r in out["rows"]] == ["iss:Widget:abc", "iss:Widget:def"]
    assert "Issue" in out["rows"][0]["i"]["labels"]
    with pytest.raises(db.QueryError):
        graph_query.graph_query("MATCH (n RETURN n", graph=company)


R2_INDEXED = {
    "decision_chain": {"topics": ["x"], "limit": 1},
    "lineage_subjects": {"product_id": "W", "limit": 1},
    "states_at": {"product_id": "W", "as_of_ms": 1, "limit": 1},
    "rank_people": {"product_id": "W", "metric": "prs_authored", "limit": 1},
    "rank_companies": {"product_id": "W", "entity": "company", "limit": 1},
    "find_missing": {"product_id": "W", "kind": "open_without_fix", "limit": 1},
    "person_profile": {"id": "x"},
    "person_activity": {"id": "x", "limit": 1},
    "person_links": {"id": "x", "product_id": "W"},
    "connection_path": {"a": "x", "b": "y"},
}


@pytest.mark.db
@pytest.mark.parametrize("name", sorted(R2_INDEXED))
def test_r2_queries_start_from_an_index(empty_graph, name):
    plan = " / ".join(db.explain(name, R2_INDEXED[name], graph=empty_graph))
    assert "Node By Index Scan" in plan, plan
