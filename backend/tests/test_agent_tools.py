"""The agent's slice-A tools (9.0) on an invented graph (fixtures/agent_company.cypher).

One test per tool, plus index use for every query the tools run. The fixture includes the
shapes that broke FalkorDB 6.0.1 (A18, A19), so a regression shows up here first.
"""

from pathlib import Path

import pytest

from app import db
from app.agent import loop, tools

pytestmark = pytest.mark.db
FIXTURE = Path(__file__).parent / "fixtures" / "agent_company.cypher"


@pytest.fixture
def company(empty_graph):
    text = FIXTURE.read_text(encoding="utf-8")
    db.run_text(text, label="agent_company", graph=empty_graph, readonly=False)
    return empty_graph


def test_resolve_entity_maps_codenames_namesakes_and_roles(company):
    assert [p["id"] for p in tools.resolve_entity("wdAI", graph=company)["products"]] == ["Widget"]
    bo = tools.resolve_entity("Bo Chen", "Widget", graph=company)
    assert [p["id"] for p in bo["people"]] == ["eid_b2", "eid_b9"]  # the active one first
    assert bo["people"][0]["activity"] > bo["people"][1]["activity"] == 0
    leads = tools.resolve_entity("engineering lead", "Widget", graph=company)["people_with_role"]
    assert {p["id"] for p in leads} == {"eid_b2", "eid_c3"}
    company_hit = tools.resolve_entity("Blue Wave", graph=company)["companies"]
    assert [c["id"] for c in company_hit] == ["co:blue-wave"]


def test_get_artifact_returns_quotable_fields_and_people(company):
    pr = tools.get_artifact("pr:w1", graph=company)
    assert pr["fields"] == [{"field": "reviews[0].comment", "text": "LGTM, approved!"}]
    relations = {(p["id"], p["relation"], p["state"]) for p in pr["people"]}
    assert ("eid_c3", "REVIEWED", "APPROVED") in relations and (
        "eid_b2",
        "AUTHORED",
        None,
    ) in relations
    message = tools.get_artifact("msg:r1", graph=company)  # no title or url: the A19 shape
    assert message["channel"] == "bug-Widget" and message["fields"] == []
    assert {p["relation"] for p in message["people"]} == {"AUTHORED", "MENTIONS"}
    assert "error" in tools.get_artifact("msg:nope", graph=company)


def test_doc_discussion_is_the_share_window_plus_meetings(company):
    out = tools.doc_discussion("Product Vision Document", "Widget", graph=company)
    [doc] = out["documents"]
    assert doc["base_id"] == "doc:wdai_vision" and doc["authors"] == ["eid_a1"]
    assert [v["version"] for v in doc["versions"]] == ["base", "final"]
    # From the first share to the share of the newer version; the later chat is out. The
    # author's own messages are kept apart: their to-do list is not their suggestion (D047).
    assert [m["id"] for m in doc["feedback"]] == ["msg:m2", "msg:m3"]
    assert [m["id"] for m in doc["author_messages"]] == ["msg:m1", "msg:m4"]
    assert doc["feedback"][0]["role"] == "Engineering Lead"
    [meeting] = doc["meetings"]
    assert meeting["id"] == "mtg:wd_1"
    speakers = {(s["id"], s["is_author"]) for s in meeting["speakers"]}
    assert speakers == {("eid_a1", True), ("eid_b2", False)}
    assert tools.doc_discussion("Roadmap", "Widget", graph=company)["documents"] == []


def test_issue_reports_join_customers_to_companies(company):
    [report] = tools.issue_reports("Widget", graph=company)["reports"]
    assert report["id"] == "msg:r1" and report["author"] == "eid_b2"
    assert report["customers"] == [{"id": "CUST-0001", "name": "Dee Fox", "company": "Blue Wave"}]
    assert report["mentioned"] == ["eid_c3"]  # exactly one customer: the A18 shape, fixed


def test_list_prs_leaves_out_external_prs_and_shows_the_chain(company):
    prs = {p["id"]: p for p in tools.list_prs("Widget", graph=company)["prs"]}
    assert set(prs) == {"pr:w1", "pr:w3"}  # the external PR is left out
    pr = prs["pr:w1"]
    assert pr["chain"] == ["open", "approved"] and pr["authors"] == ["eid_b2"]
    assert pr["reviews"] == [{"id": "eid_c3", "state": "APPROVED"}]
    [undo] = pr["reverted_by"]  # "Revert PR# 1" names the original (D047)
    assert undo["number"] == 3 and "product direction" in undo["reason"]
    assert undo["id"] == "pr:w3"  # citable on its own, and related to pr:w1 (D070)
    assert prs["pr:w3"]["reverts"] == [1] and prs["pr:w3"]["reverted_by"] == []
    merged = tools.list_prs("Widget", status="merged", graph=company)["prs"]
    assert [p["id"] for p in merged] == ["pr:w3"]


def test_list_prs_shows_what_the_team_said_apart_from_the_record(company):
    [pr] = [p for p in tools.list_prs("Widget", graph=company)["prs"] if p["id"] == "pr:w1"]
    assert pr["chain"] == ["open", "approved"]  # the pr_action decision stays out (A20 shape)
    assert pr["team_action"] == "to_close" and pr["action_reason"] == "product_direction"
    [said] = pr["said"]
    assert said["artifact_id"] == "msg:r3" and said["quote"] == "Hold off on merging PR# 1"
    to_close = tools.list_prs("Widget", graph=company, action="to_close")["prs"]
    assert [p["id"] for p in to_close] == ["pr:w1"]


def test_list_issues_gives_status_history_customers_and_fixes(company):
    out = tools.list_issues("Widget", graph=company)
    [issue] = out["issues"]
    assert issue["title"] == "Slow export" and issue["status"] == "resolved"
    assert out["counts_by_status"] == {"resolved": 1}
    assert issue["customers"] == [{"id": "CUST-0001", "name": "Dee Fox", "company": "Blue Wave"}]
    assert [a["id"] for a in issue["assignees"]] == ["eid_c3"]
    assert [p["number"] for p in issue["fixed_by"]] == [1]
    assert [(h["status"], h["by"], h["artifact_id"]) for h in issue["history"]] == [
        ("reported", "eid_b2", "msg:r1"),
        ("resolved", "eid_c3", "msg:r2"),
    ]
    assert tools.list_issues("Widget", "unresolved", graph=company)["issues"] == []
    assert len(tools.list_issues("Widget", company="bluewave", graph=company)["issues"]) == 1
    assert tools.list_issues("Widget", company="Other Co", graph=company)["issues"] == []


def test_links_shared_counts_only_this_products_sharers(company):
    [link] = tools.links_shared("Widget", graph=company)["links"]
    assert link["url"] == "https://www.rival.com/demo"
    assert link["shared_by"] == ["eid_c3"] and link["shared_in"] == ["msg:m6"]


def test_collect_records_what_tools_returned_for_the_validator(company):
    seen, texts = set(), {}
    loop.collect(
        tools.doc_discussion("Product Vision Document", "Widget", graph=company), seen, texts
    )
    assert {"doc:wdai_vision", "doc:final_wdai_vision", "msg:m2", "mtg:wd_1"} <= seen
    assert "Add numbers to the summary." in texts["msg:m2"]


def test_collect_relates_a_pr_to_the_pr_that_reverted_it(company):
    seen, texts, related = set(), {}, {}
    loop.collect(tools.list_prs("Widget", graph=company), seen, texts, None, related)
    assert "pr:w3" in related["pr:w1"] and "pr:w1" in related["pr:w3"]
    assert "pr:w3" not in related.get("pr:w2", set())  # rows aren't related to each other


def test_call_tool_refuses_a_product_id_the_graph_lacks(company):
    scope = tools.Scope("Widget", None, company)
    with pytest.raises(loop.ToolError, match="unknown product_id.*Gadget, Widget"):
        loop.call_tool("list_prs", {"product_id": '">'}, scope)
    assert loop.call_tool("list_prs", {"product_id": "Widget"}, scope)["prs"]
    assert loop.call_tool("list_prs", {"product_id": ""}, scope)["prs"]  # "" = the question's


def test_subject_product_names_an_issues_or_prs_product(company):
    one = lambda i: db.run("subject_product", {"id": i}, graph=company).rows[0]["product_id"]  # noqa: E731
    assert one("iss:Widget:abc") == "Widget" and one("pr:w1") == "Widget"
    assert one("pr:nope") is None


def test_entity_card_finds_any_entity_kind_by_id(company):
    card = lambda i: db.run("entity_card", {"id": i}, graph=company).rows  # noqa: E731
    [person] = card("eid_b2")
    assert "Person" in person["labels"] and person["props"]["id"] == "eid_b2"
    assert "AUTHORED" in person["rels"]
    [co] = card("co:blue-wave")
    assert co["props"]["name"] == "Blue Wave" and "Company" in co["labels"]
    assert card("iss:Widget:abc")[0]["props"]["title"] == "Slow export"
    assert card("nobody") == []


def test_list_products_gives_codenames_and_counts_for_the_picker(company):
    rows = db.run("list_products", {"limit": 10}, graph=company).rows
    assert [r["id"] for r in rows] == ["Gadget", "Widget"]
    widget = rows[1]
    assert widget["aliases"] == ["wdAI"] and widget["artifacts"] > 0
    assert rows[0]["aliases"] == [] and rows[0]["artifacts"] >= 0


INDEXED = {
    "get_artifact": {"id": "x"},
    "artifact_people_detail": {"id": "x"},
    "artifact_texts": {"ids": ["x"]},
    "find_docs": {"product_id": "W", "doc": "x"},
    "doc_discussion": {"doc_id": "x", "window_ms": 1, "limit": 1},
    "doc_meetings": {"doc_id": "x"},
    "issue_reports": {"product_id": "W", "limit": 1},
    "product_prs": {"product_id": "W", "status": None, "action": None, "limit": 1},
    "product_issues": {"product_id": "W", "status": None, "limit": 1},
    "links_shared": {"product_id": "W", "limit": 1},
    "people_by_role": {"product_id": "W", "role": "x", "k": 1},
    "subject_product": {"id": "x"},
    "entity_card": {"id": "x"},
}


@pytest.mark.parametrize("name", sorted(INDEXED))
def test_tool_queries_start_from_an_index(empty_graph, name):
    plan = " / ".join(db.explain(name, INDEXED[name], graph=empty_graph))
    assert "Node By Index Scan" in plan and "Label Scan" not in plan, plan
