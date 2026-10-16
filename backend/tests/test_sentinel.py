"""The Change Sentinel (Phase 11) on an invented graph (fixtures/sentinel_company.cypher).

11.2: the blast radius of a decision (impact_of.cypher, D040), for an issue's reopening and
for a PR's stated action.
"""

from pathlib import Path

import pytest

from app import db
from app.sentinel import blast_radius, engine

pytestmark = pytest.mark.db
FIXTURE = Path(__file__).parent / "fixtures" / "sentinel_company.cypher"
REOPENED = "dec:iss:Widget:a1/status:5000"
NOW = 1_800_000_000_000


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


def alerts_by_id(graph):
    return {a["id"]: a for a in engine.list_alerts(graph=graph)}


def test_judge_alerts_on_regressions_reversals_and_conflicts_not_on_progress():
    change = {"change": "supersession", "kind": "issue_status"}
    assert engine.judge({**change, "from_value": "resolved", "to_value": "reopened"}) == (
        "regression",
        "high",
    )
    assert engine.judge({**change, "from_value": "reported", "to_value": "resolved"}) is None
    assert engine.judge({"change": "conflict", "kind": "pr_action"}) == ("conflict", "high")
    assert engine.day(1804546200000) == "8 Mar 2027"


def test_run_raises_one_alert_per_change_worth_one(company):
    out = engine.run(company, now_ms=NOW)
    assert out["by_kind"] == {"regression": 1, "conflict": 1}
    assert (out["progress"], out["new"], out["withdrawn"]) == (1, 2, 0)
    alerts = alerts_by_id(company)
    reg = alerts[f"alert:{REOPENED}"]
    assert reg["status"] == "open" and reg["severity"] == "high"
    assert reg["summary"] == (
        "Regression: 'Slow export' was marked resolved on 1 Jan 1970 and reopened on 1 Jan 1970."
    )
    assert (reg["artifact_id"], reg["quote"]) == ("msg:r3", "CUST-0001 has reopened Slow export.")
    notified = {p["id"]: sorted(p["reasons"]) for p in reg["notifies"]}
    assert notified["eid_c3"] == ["assigned", "authored_fix", "decided_old_state"]
    con = alerts["alert:clm:k1"]
    assert con["decision_id"] == "dec:pr:w1/action:2500" and con["actor_id"] == "eid_e5"
    assert con["summary"].startswith("Conflict: Ed Moss (Marketing Manager) says 'Speed up export'")


def test_a_rerun_keeps_status_and_creates_nothing(company):
    engine.run(company, now_ms=NOW)
    db.run_text(
        "MATCH (a:Alert {id: $id}) SET a.status = 'dismissed'",
        {"id": "alert:clm:k1"},
        label="dismiss",
        graph=company,
        readonly=False,
    )
    out = engine.run(company, now_ms=NOW + 1)
    assert out["new"] == 0 and out["withdrawn"] == 0
    alerts = alerts_by_id(company)
    assert alerts["alert:clm:k1"]["status"] == "dismissed"
    assert alerts[f"alert:{REOPENED}"]["created_iso"].startswith("2027-01-15")


def test_a_rebuild_withdraws_a_vanished_change_and_relinks_a_returning_one(company):
    engine.run(company, now_ms=NOW)
    flags = "MATCH (:Alert {id: $id})-[:FLAGS]->(d:Decision) RETURN d.id AS id LIMIT 5"
    params = {"id": f"alert:{REOPENED}"}
    removed = db.run_text(
        "MATCH (d:Decision {id: $id}) WITH d, properties(d) AS props DETACH DELETE d "
        "RETURN props LIMIT 1",
        {"id": REOPENED},
        label="rm",
        graph=company,
        readonly=False,
    ).rows[0]["props"]
    assert engine.run(company, now_ms=NOW)["withdrawn"] == 1
    assert alerts_by_id(company)[f"alert:{REOPENED}"]["status"] == "withdrawn"
    # the fold recreates the decision under the same id: the alert reopens and re-links
    db.run_text(
        "MATCH (o:Decision {id: 'dec:iss:Widget:a1/status:3000'}), "
        "(i:Issue {id: 'iss:Widget:a1'}), (m:Artifact {id: 'msg:r3'}) "
        "CREATE (d:Decision)-[:SUPERSEDES]->(o) SET d = $props "
        "CREATE (d)-[:AFFECTS]->(i) CREATE (d)-[:EVIDENCED_BY {quote: 'reopened'}]->(m)",
        {"props": removed},
        label="restore",
        graph=company,
        readonly=False,
    )
    assert engine.run(company, now_ms=NOW)["new"] == 0
    assert alerts_by_id(company)[f"alert:{REOPENED}"]["status"] == "open"
    assert db.run_text(flags, params, label="flags", graph=company).rows == [{"id": REOPENED}]


def test_list_alerts_filters_by_status_and_sorts_newest_change_first(company):
    engine.run(company, now_ms=NOW)
    assert [a["id"] for a in engine.list_alerts(graph=company)] == [
        "alert:clm:k1",
        f"alert:{REOPENED}",
    ]
    assert engine.list_alerts("dismissed", graph=company) == []
    assert len(engine.list_alerts(product_id="Widget", graph=company)) == 2


@pytest.mark.parametrize("name", ["list_alerts", "sentinel_changes"])
def test_sentinel_reads_run_on_an_empty_graph(empty_graph, name):
    params = {"status": None, "product_id": None, "limit": 1}
    assert db.run(name, params, graph=empty_graph).rows == []
