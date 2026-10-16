"""The Change Sentinel (Phase 11) on an invented graph (fixtures/sentinel_company.cypher).

11.2: the blast radius of a decision (impact_of.cypher, D040), for an issue's reopening and
for a PR's stated action.
"""

from pathlib import Path

import pytest

from app import db
from app.sentinel import blast_radius

pytestmark = pytest.mark.db
FIXTURE = Path(__file__).parent / "fixtures" / "sentinel_company.cypher"
REOPENED = "dec:iss:Widget:a1/status:5000"


@pytest.fixture
def company(empty_graph):
    text = FIXTURE.read_text(encoding="utf-8")
    db.run_text(text, label="sentinel_company", graph=empty_graph, readonly=False)
    return empty_graph


def ids(items):
    return sorted(i["id"] for i in items)


def test_reopening_reaches_the_fix_its_people_and_the_customers_company(company):
    out = blast_radius.impact_of(REOPENED, graph=company)
    assert ids(out["prs"]) == ["pr:w1"]
    people = {p["id"]: p["reasons"] for p in out["people"]}
    # The fix's author is also the assignee and the one who said "fixed": one entry, 3 reasons.
    assert sorted(people["eid_c3"]) == ["assigned", "authored_fix", "decided_old_state"]
    assert people["eid_b2"] == ["reviewed_fix"] and people["eid_d4"] == ["reviewed_fix"]
    assert ids(out["customers"]) == ["CUST-0001"] and ids(out["companies"]) == ["co:blue-wave"]


def test_a_reviewers_latest_review_is_the_one_reported(company):
    out = blast_radius.impact_of(REOPENED, graph=company)
    reviews = {
        r["id"]: (r["detail"], r["quote"])
        for r in db.run("impact_of", {"decision_id": REOPENED, "limit": 25}, graph=company).rows
        if r["why"] == "reviewed_fix"
    }
    assert reviews == {"eid_b2": ("APPROVED", "LGTM now."), "eid_d4": ("APPROVED", "Works for me.")}
    assert out["size"] > 0


def test_same_component_issues_split_open_from_closed_and_skip_other_components(company):
    out = blast_radius.impact_of(REOPENED, graph=company)
    assert ids(out["open_issues_same_component"]) == ["iss:Widget:b2"]
    assert ids(out["other_issues_same_component"]) == ["iss:Widget:c3"]


def test_the_replaced_state_brings_its_message_and_quote(company):
    out = blast_radius.impact_of(REOPENED, graph=company)
    [old] = out["evidence_old_state"]
    assert (old["id"], old["detail"], old["quote"]) == (
        "msg:r2",
        "resolved",
        "Slow export is fixed",
    )
    [new] = out["evidence_this_state"]
    assert (new["id"], new["detail"]) == ("msg:r3", "reopened")


def test_teams_once_each_and_caused_stops_at_three_hops(company):
    out = blast_radius.impact_of(REOPENED, graph=company)
    assert {(t["id"], t["detail"]) for t in out["teams"]} == {
        ("team:eid_b2", "8"),
        ("team:eid_d4", "2"),
    }
    assert ids(out["caused"]) == ["dec:c1", "dec:c2", "dec:c3"]


def test_a_pr_decision_reaches_its_author_reviewers_and_the_issue_it_fixes(company):
    out = blast_radius.impact_of("dec:pr:w1/action:2500", graph=company)
    people = {p["id"]: p["reasons"] for p in out["people"]}
    assert people == {
        "eid_c3": ["authored_pr"],
        "eid_b2": ["reviewed_pr"],
        "eid_d4": ["reviewed_pr"],
    }
    assert ids(out["issues_fixed_by_pr"]) == ["iss:Widget:a1"]
    assert out["prs"] == [] and out["evidence_old_state"] == []


def test_an_unknown_decision_has_an_empty_radius(company):
    out = blast_radius.impact_of("dec:none", graph=company)
    assert out["size"] == 0 and out["people"] == []


def test_impact_of_starts_from_the_decision_index(empty_graph):
    plan = " / ".join(db.explain("impact_of", {"decision_id": "x", "limit": 1}, graph=empty_graph))
    assert "Node By Index Scan" in plan and "Label Scan" not in plan, plan
