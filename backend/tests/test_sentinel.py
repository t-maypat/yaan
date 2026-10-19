"""The Change Sentinel (Phase 11) on an invented graph (fixtures/sentinel_company.cypher).

11.2: the blast radius of a decision (impact_of.cypher, D040), for an issue's reopening and
for a PR's stated action.
"""

from pathlib import Path

import pytest

from app import db
from app.sentinel import approve, blast_radius, engine

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


def actions(graph, alert_id):
    return {(a["kind"], a["target_id"]): a for a in engine.alert_actions(alert_id, graph=graph)}


def test_a_regression_drafts_a_comment_a_correction_and_one_note_per_person(company):
    out = engine.run(company, now_ms=NOW)
    got = actions(company, f"alert:{REOPENED}")
    assert sorted(got) == [
        ("doc_fix", "msg:r2"),
        ("notify_owner", "eid_b2"),
        ("notify_owner", "eid_c3"),
        ("notify_owner", "eid_d4"),
        ("ticket_comment", "iss:Widget:a1"),
    ]
    assert out["actions"] == len(got) + len(actions(company, "alert:clm:k1"))
    quote = "CUST-0001 has reopened Slow export."
    assert all(f'"{quote}" (msg:r3)' in a["draft"] for a in got.values())
    comment = got[("ticket_comment", "iss:Widget:a1")]["draft"]
    assert '"Slow export is fixed" (msg:r2)' in comment
    assert "Reviewed by Bo Chen and Di Park." in comment
    assert "Reported by Dee Fox (Blue Wave)." in comment
    assert "Export drops rows (reported); Export timeout (resolved)" in comment
    assert "Login flicker" not in comment
    note = got[("notify_owner", "eid_c3")]["draft"]
    assert (
        "because you wrote the fix, you are the assignee and you stated the earlier status" in note
    )


def test_a_conflict_asks_for_confirmation_and_corrects_nothing(company):
    engine.run(company, now_ms=NOW)
    got = actions(company, "alert:clm:k1")
    assert ("doc_fix", "msg:r2") not in got and all(k != "doc_fix" for k, _ in got)
    comment = got[("ticket_comment", "pr:w1")]["draft"]
    assert comment.startswith('Unconfirmed: Ed Moss (Marketing Manager) said "just close PR 1"')
    assert "stays 'to merge'" in comment
    assert "Please confirm or reject" in got[("notify_owner", "eid_c3")]["draft"]


def test_a_rerun_rewrites_only_drafts_still_proposed(company):
    engine.run(company, now_ms=NOW)
    target = f"action:alert:{REOPENED}:doc_fix:msg:r2"
    db.run_text(
        "MATCH (a:Action {id: $id}) SET a.status = 'approved', a.draft = 'sent as edited'",
        {"id": target},
        label="approve",
        graph=company,
        readonly=False,
    )
    engine.run(company, now_ms=NOW)
    assert actions(company, f"alert:{REOPENED}")[("doc_fix", "msg:r2")]["draft"] == "sent as edited"


def test_alert_actions_runs_on_an_empty_graph(empty_graph):
    assert db.run("alert_actions", {"alert_id": "x", "limit": 1}, graph=empty_graph).rows == []


TRACE = """
MATCH (r:Decision {id: $id})
OPTIONAL MATCH (r)-[:DECIDED_BY]->(p:Person)
OPTIONAL MATCH (c:Decision)-[:CAUSED]->(r)
OPTIONAL MATCH (r)-[e:EVIDENCED_BY]->(m:Artifact)
OPTIONAL MATCH (r)-[:AFFECTS]->(s)
RETURN r.origin AS origin, r.value AS value, r.statement AS statement, p.id AS by,
       c.id AS cause, m.id AS evidence, e.quote AS quote, s.id AS subject
LIMIT 1
"""


def trace(graph, decision_id):
    return db.run_text(TRACE, {"id": decision_id}, label="trace", graph=graph).rows[0]


def test_approving_writes_a_decision_trace_and_sends_the_chosen_drafts(company):
    engine.run(company, now_ms=NOW)
    alert_id = f"alert:{REOPENED}"
    note_b2 = f"action:{alert_id}:notify_owner:eid_b2"
    out = approve.respond(
        alert_id,
        "approved",
        "eid_b2",
        action_ids=[f"action:{alert_id}:ticket_comment:iss:Widget:a1", note_b2],
        edits={note_b2: "Bo, please re-review PR 1."},
        graph=company,
        now_ms=NOW + 5,
    )
    d = out["decision"]
    assert d["id"] == f"dec:{alert_id}/response:{NOW + 5}"
    got = trace(company, d["id"])
    assert got == {
        "origin": "approved_action",
        "value": "approved",
        "statement": "Bo Chen approved the Sentinel's follow-up on the regression of "
        "'Slow export': 2 of 5 drafted actions sent.",
        "by": "eid_b2",
        "cause": REOPENED,
        "evidence": "msg:r3",
        "quote": "CUST-0001 has reopened Slow export.",
        "subject": "iss:Widget:a1",
    }
    statuses = {
        a["id"]: (a["status"], a["draft"]) for a in engine.alert_actions(alert_id, graph=company)
    }
    assert statuses[note_b2] == ("approved", "Bo, please re-review PR 1.")
    assert sorted(s for s, _ in statuses.values()) == ["approved"] * 2 + ["skipped"] * 3
    assert alerts_by_id(company)[alert_id]["status"] == "approved"
    # the radius of the change now reaches the response (CAUSED)
    assert d["id"] in ids(blast_radius.impact_of(REOPENED, graph=company)["caused"])


def test_a_second_response_and_bad_input_are_refused(company):
    engine.run(company, now_ms=NOW)
    approve.respond("alert:clm:k1", "dismissed", "eid_c3", note="Ed was joking.", graph=company)
    with pytest.raises(approve.AlertError, match="is dismissed, not open"):
        approve.respond("alert:clm:k1", "approved", "eid_c3", graph=company)
    with pytest.raises(approve.AlertError, match="no alert"):
        approve.respond("alert:none", "approved", "eid_c3", graph=company)
    with pytest.raises(approve.AlertError, match="no person"):
        approve.respond(f"alert:{REOPENED}", "approved", "eid_zz", graph=company)
    with pytest.raises(approve.AlertError, match="not drafted"):
        approve.respond(f"alert:{REOPENED}", "approved", "eid_c3", action_ids=["x"], graph=company)
    with pytest.raises(approve.AlertError, match="verdict"):
        approve.respond(f"alert:{REOPENED}", "maybe", "eid_c3", graph=company)
    # D100: answering for the team takes being notified, or being a lead
    notified = {n["id"] for n in alerts_by_id(company)[f"alert:{REOPENED}"]["notifies"]}
    assert "eid_e5" not in notified  # Ed Moss, Marketing Manager
    with pytest.raises(approve.AlertError, match="Ed Moss isn't notified .* isn't a lead"):
        approve.respond(f"alert:{REOPENED}", "approved", "eid_e5", graph=company)
    assert alerts_by_id(company)[f"alert:{REOPENED}"]["status"] == "open"


def test_a_dismissal_is_recorded_too_and_dismisses_every_draft(company):
    engine.run(company, now_ms=NOW)
    out = approve.respond(
        "alert:clm:k1", "dismissed", "eid_c3", note="Ed was joking.", graph=company, now_ms=NOW
    )
    got = trace(company, out["decision"]["id"])
    assert (got["origin"], got["cause"], got["subject"]) == (
        "dismissed_action",
        "dec:pr:w1/action:2500",
        "pr:w1",
    )
    assert got["statement"].endswith("conflict alert on 'Speed up export': Ed was joking.")
    assert {a["status"] for a in engine.alert_actions("alert:clm:k1", graph=company)} == {
        "dismissed"
    }


def test_an_earlier_response_to_the_same_kind_is_a_precedent(company):
    engine.run(company, now_ms=NOW)
    first = approve.respond(f"alert:{REOPENED}", "approved", "eid_b2", graph=company, now_ms=NOW)
    db.run_text(
        "CREATE (:Alert {id: 'alert:two', kind: 'regression', status: 'open', product_id: 'Widget',"
        " decision_id: 'dec:iss:Widget:a1/status:3000', subject_id: 'iss:Widget:a1',"
        " subject_title: 'Slow export'})",
        label="second",
        graph=company,
        readonly=False,
    )
    second = approve.respond("alert:two", "dismissed", "eid_b2", graph=company, now_ms=NOW + 9)
    assert [p["id"] for p in second["precedents"]] == [first["decision"]["id"]]
    rows = db.run_text(
        "MATCH (a:Decision)-[:PRECEDENT_FOR]->(b:Decision) RETURN a.id AS a, b.id AS b LIMIT 5",
        label="prec",
        graph=company,
    ).rows
    assert rows == [{"a": first["decision"]["id"], "b": second["decision"]["id"]}]


def test_a_rebuild_relinks_caused_to_the_response(company):
    engine.run(company, now_ms=NOW)
    out = approve.respond(f"alert:{REOPENED}", "approved", "eid_b2", graph=company, now_ms=NOW)
    db.run_text(
        "MATCH (:Decision {id: $id})-[c:CAUSED]->(:Decision {origin: 'approved_action'}) DELETE c",
        {"id": REOPENED},
        label="cut",
        graph=company,
        readonly=False,
    )
    assert trace(company, out["decision"]["id"])["cause"] is None
    engine.run(company, now_ms=NOW)
    assert trace(company, out["decision"]["id"])["cause"] == REOPENED


@pytest.mark.parametrize(
    "name, params",
    [
        ("get_alert", {"alert_id": "x", "person_id": "y"}),
        ("alert_precedents", {"kind": "k", "product_id": "W", "alert_id": "x", "limit": 1}),
    ],
)
def test_response_reads_run_on_an_empty_graph(empty_graph, name, params):
    assert db.run(name, params, graph=empty_graph).rows == []


def test_a_sole_expert_whose_product_has_truck_factor_1_raises_a_bus_factor_alert(
    company, monkeypatch
):
    from app.ingest import derive

    derive.expertise(company)  # Cy is the only author of the Export Module: TF 1
    monkeypatch.setattr(engine, "MIN_SOLE_AREAS", 1)
    out = engine.run(company, now_ms=NOW)
    assert out["by_kind"]["bus_factor"] == 1
    alert = alerts_by_id(company)["alert:bus_factor:eid_c3"]
    assert alert["severity"] == "high" and alert["summary"] == (
        "Cy Diaz (Software Engineer) is the only expert on 1 area in Widget, "
        "and the first person the truck factor removes there."
    )
    assert {p["id"] for p in alert["notifies"]} == {"eid_c3"}  # no manager on record
    [note] = engine.alert_actions("alert:bus_factor:eid_c3", graph=company)
    assert note["target_id"] == "eid_c3" and "Export Module (Widget)" in note["draft"]
    # approving a person-level alert writes the decision without an issue or a cause
    out = approve.respond("alert:bus_factor:eid_c3", "approved", "eid_c3", graph=company)
    assert trace(company, out["decision"]["id"])["subject"] is None


def test_a_dismissal_by_someone_outside_the_areas_working_group_raises_an_alert(company):
    db.run_text(
        "MATCH (cy:Person {id: 'eid_c3'}), (ed:Person {id: 'eid_e5'}), "
        "(m:Artifact {id: 'msg:r4'}), "
        "(w:Product {id: 'Widget'}) "
        "CREATE (a:Area:Entity {id: 'area:W:c:auth', name: 'Auth Module', product_id: 'Widget'}), "
        "(i:Issue:Entity {id: 'iss:Widget:z9', title: 'Login loop', product_id: 'Widget'}), "
        "(d:Decision {id: 'dec:iss:Widget:z9/status:6000', topic: 'iss:Widget:z9/status', "
        "kind: 'issue_status', value: 'dismissed', status: 'active', origin: 'extracted', "
        "valid_from_ms: 6000}), "
        "(i)-[:IN_AREA]->(a), (i)-[:IN_PRODUCT]->(w), (d)-[:AFFECTS]->(i), "
        "(d)-[:DECIDED_BY]->(ed), "
        "(d)-[:EVIDENCED_BY {quote: 'Let us just close'}]->(m), "
        "(cy)-[:KNOWS {deliveries: 2}]->(a), "
        "(cy)-[:IN_COMMUNITY]->(:Community {id: 'comm:a', label: 'Working group of 3, mostly X'}), "
        "(ed)-[:IN_COMMUNITY]->(:Community {id: 'comm:b', label: 'Working group of 4, mostly Y'})",
        label="outside",
        graph=company,
        readonly=False,
    )
    out = engine.run(company, now_ms=NOW)
    assert out["by_kind"]["outside_group"] == 1
    alert = alerts_by_id(company)["alert:outside:dec:iss:Widget:z9/status:6000"]
    assert alert["summary"] == (
        "'Login loop' was dismissed by Ed Moss (Marketing Manager), who is outside the working "
        "group that does the work in its area."
    )
    assert alert["reason"].startswith("Auth Module work was done by Cy Diaz;")
    kinds = {a["kind"] for a in engine.alert_actions(alert["id"], graph=company)}
    assert "doc_fix" not in kinds and "ticket_comment" in kinds
