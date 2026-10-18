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
    # From the first share to the share of the newer version; the later chat is out.
    assert [m["id"] for m in doc["slack_discussion"]] == ["msg:m1", "msg:m2", "msg:m3", "msg:m4"]
    assert doc["slack_discussion"][1]["role"] == "Engineering Lead"
    [meeting] = doc["meetings"]
    assert meeting["id"] == "mtg:wd_1" and {s["id"] for s in meeting["speakers"]} == {
        "eid_a1",
        "eid_b2",
    }
    assert tools.doc_discussion("Roadmap", "Widget", graph=company)["documents"] == []


def test_issue_reports_join_customers_to_companies(company):
    [report] = tools.issue_reports("Widget", graph=company)["reports"]
    assert report["id"] == "msg:r1" and report["author"] == "eid_b2"
    assert report["customers"] == [{"id": "CUST-0001", "name": "Dee Fox", "company": "Blue Wave"}]
    assert report["mentioned"] == ["eid_c3"]  # exactly one customer: the A18 shape, fixed


def test_list_prs_leaves_out_external_prs_and_shows_the_chain(company):
    [pr] = tools.list_prs("Widget", graph=company)["prs"]
    assert pr["id"] == "pr:w1" and pr["chain"] == ["open", "approved"]
    assert pr["authors"] == ["eid_b2"] and pr["reviews"] == [{"id": "eid_c3", "state": "APPROVED"}]
    assert tools.list_prs("Widget", status="merged", graph=company)["prs"] == []


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


INDEXED = {
    "get_artifact": {"id": "x"},
    "artifact_people_detail": {"id": "x"},
    "artifact_texts": {"ids": ["x"]},
    "find_docs": {"product_id": "W", "doc": "x"},
    "doc_discussion": {"doc_id": "x", "window_ms": 1, "limit": 1},
    "doc_meetings": {"doc_id": "x"},
    "issue_reports": {"product_id": "W", "limit": 1},
    "product_prs": {"product_id": "W", "status": None, "limit": 1},
    "links_shared": {"product_id": "W", "limit": 1},
    "people_by_role": {"product_id": "W", "role": "x", "k": 1},
}


@pytest.mark.parametrize("name", sorted(INDEXED))
def test_tool_queries_start_from_an_index(empty_graph, name):
    plan = " / ".join(db.explain(name, INDEXED[name], graph=empty_graph))
    assert "Node By Index Scan" in plan and "Label Scan" not in plan, plan
