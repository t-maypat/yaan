"""Phase 8.4/8.7: the deterministic fold (§9.4) on invented events. No LLM, no DB."""

from app.ingest import fold
from app.ingest.resolve import Issue, Resolution

ROLES = {
    "eid_a1": "Engineering Lead",
    "eid_b2": "Software Engineer",
    "eid_c3": "Software Engineer",
    "eid_d4": "Marketing Manager",
}


def ctx():
    return fold.Context(
        roles=ROLES,
        customers={"CUST-0001"},
        active={"W": {"eid_a1", "eid_b2", "eid_c3"}},
        prs={},
        observed_ms=1,
    )


def ev(type_, ts, speaker, **kw):
    base = {
        "type": type_,
        "ts_ms": ts,
        "ts_iso": f"t{ts}",
        "speaker": speaker,
        "artifact_id": f"msg:{ts}",
        "quote": f"quote {ts}",
        "product": "W",
        "decisive": True,
        "confidence": 0.9,
    }
    if type_.startswith("issue_"):
        base |= {"issue_id": "iss:W:1", "issue": "Slow export"}
    return base | kw


def res(*events):
    issue = Issue("iss:W:1", "W", "Slow export", "bug-W", 0, "desc", "eid_b2")
    return Resolution({"iss:W:1": issue}, list(events), [])


def chain(rows, topic="iss:W:1/status"):
    ds = sorted(
        (d for d in rows["upsert_decisions"] if d["topic"] == topic),
        key=lambda d: d["valid_from_ms"],
    )
    return [(d["value"], d["valid_from_ms"], d["valid_to_ms"], d["status"]) for d in ds]


REPORT = ev("issue_reported", 10, "eid_a1", person="eid_b2", customer="CUST-0001")


def test_claims_fold_in_time_order_with_closed_windows():
    rows, stats = fold.rebuild(
        res(ev("issue_reopened", 30, "eid_a1"), REPORT, ev("issue_resolved", 20, "eid_b2")),
        ctx(),
    )
    assert chain(rows) == [
        ("reported", 10, 20, "superseded"),
        ("resolved", 20, 30, "superseded"),
        ("reopened", 30, None, "active"),
    ]
    assert len(rows["link_supersedes"]) == 2
    [issue] = rows["upsert_issues"]
    assert issue["current_status"] == "reopened" and issue["reopen_count"] == 1
    assert rows["link_reported_by"] == [{"issue_id": "iss:W:1", "customer_id": "CUST-0001"}]
    assert stats["claims_supersedes"] == 2


def test_a_repeated_value_supports_the_active_decision():
    rows, _ = fold.rebuild(
        res(REPORT, ev("issue_resolved", 20, "eid_b2"), ev("issue_resolved", 25, "eid_a1")),
        ctx(),
    )
    assert [c[0] for c in chain(rows)] == ["reported", "resolved"]
    outcomes = {c["ts_ms"]: c["outcome"] for c in rows["upsert_claims"]}
    assert outcomes == {10: "supports", 20: "supersedes", 25: "supports"}


def test_an_unauthorized_claim_conflicts_and_changes_nothing():
    rows, stats = fold.rebuild(res(REPORT, ev("issue_dismissed", 20, "eid_c3")), ctx())
    assert [c[0] for c in chain(rows)] == ["reported"]
    [conflict] = rows["link_conflicts_with"]
    assert conflict["decision_id"] == "dec:iss:W:1/status:10" and "eid_c3" in conflict["reason"]
    assert stats["claims_conflict"] == 1 and stats["authority_unauthorized"] == 1


def test_a_non_decisive_pr_intent_stays_pending():
    pr = {"pr_id": "pr:w1", "pr": "PR#1"}
    rows, _ = fold.rebuild(
        res(
            ev("pr_reverted", 10, "eid_b2", decisive=False, **pr),
            ev("pr_reverted", 20, "eid_a1", reason="product_direction", **pr),
        ),
        ctx(),
    )
    assert chain(rows, "pr:w1/action") == [("reverted", 20, None, "active")]
    assert [c["outcome"] for c in rows["upsert_claims"]] == ["pending", "supports"]
    assert rows["set_pr_action"] == [
        {"pr_id": "pr:w1", "action": "reverted", "reason": "product_direction", "since_ms": 20}
    ]


def test_the_fold_is_idempotent_and_a_late_claim_just_rebuilds():
    events = [REPORT, ev("issue_resolved", 30, "eid_b2")]
    first, _ = fold.rebuild(res(*events), ctx())
    again, _ = fold.rebuild(res(*reversed(events)), ctx())
    assert first == again
    late, _ = fold.rebuild(res(*events, ev("issue_dismissed", 20, "eid_b2")), ctx())
    assert [c[0] for c in chain(late)] == ["reported", "dismissed", "resolved"]


def test_an_unknown_speaker_applies_with_lower_confidence():
    rows, stats = fold.rebuild(res(REPORT, ev("issue_resolved", 20, "eid_e5")), ctx())
    resolved = [d for d in rows["upsert_decisions"] if d["value"] == "resolved"]
    assert resolved[0]["confidence"] == 0.6 and stats["authority_unverified"] == 1


def test_a_reassignment_keeps_a_reopened_issue_reopened():
    rows, _ = fold.rebuild(
        res(
            REPORT,
            ev("issue_dismissed", 20, "eid_b2"),
            ev("issue_reopened", 30, "eid_a1"),
            ev("issue_reported", 40, "eid_a1", person="eid_c3"),  # "I'll reassign the bug to you"
        ),
        ctx(),
    )
    assert [c[0] for c in chain(rows)] == ["reported", "dismissed", "reopened"]
    [issue] = rows["upsert_issues"]
    assert issue["current_status"] == "reopened" and issue["current_assignee"] == "eid_c3"
    spans = sorted((a["person_id"], a["from_ms"], a["to_ms"]) for a in rows["link_assigned_to"])
    assert spans == [("eid_b2", 10, 40), ("eid_c3", 40, None)]


def test_holding_off_after_a_decision_not_to_merge_supports_it():
    pr = {"pr_id": "pr:w1", "pr": "PR#1"}
    rows, _ = fold.rebuild(
        res(ev("pr_to_close", 10, "eid_a1", **pr), ev("pr_on_hold", 20, "eid_b2", **pr)), ctx()
    )
    assert chain(rows, "pr:w1/action") == [("to_close", 10, None, "active")]
