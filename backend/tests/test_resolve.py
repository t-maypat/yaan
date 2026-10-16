"""Phase 8.2 (R1-5): issues keyed by title, matched through fuzzy steps; PR references."""

from app.ingest import resolve
from app.ingest.normalize import Artifact


def ev(type_, issue=None, ts=0, channel="bug-ann-W", quote="q", **kw):
    return {
        "type": type_,
        "issue": issue,
        "ts_ms": ts,
        "channel": channel,
        "quote": quote,
        "product": "W",
        "artifact_id": f"msg:{ts}",
        **kw,
    }


def pr(number, repo="salesforce/W", ts=0):
    return Artifact(
        id=f"pr:{repo}:{number}",
        kind="pr",
        product_hint="W",
        ts_ms=ts,
        ts_iso=None,
        text="",
        raw_ref="W:prs[0]",
        meta={"repo": repo, "number": number},
    )


def test_issue_ids_ignore_quotes_case_and_filler():
    assert resolve.issue_id("W", "'Slow Charts'") == resolve.issue_id("W", "the slow charts bug")
    assert resolve.issue_id("W", "Slow Charts") != resolve.issue_id("V", "Slow Charts")


def test_lifecycle_events_match_reported_issues_step_by_step():
    events = [
        ev(
            "issue_reported",
            "Cache Invalidation Failure",
            1,
            quote="stale data in the UI",
            person="eid_a",
        ),
        ev("issue_reported", "Cache Invalidation Issue", 2, channel="bug-bob-W"),
        ev(
            "issue_reported",
            "OAuth Scope Misconfiguration",
            3,
            quote="due to incorrect OAuth scope assignments",
            person="eid_b",
        ),
        ev("issue_resolved", "cache invalidation failure", 4),  # exact (case)
        ev("issue_resolved", "incorrect cache invalidation", 5, channel="bug-bob-W"),  # tie
        ev("issue_dismissed", "incorrect OAuth scope assignments in W", 6),  # description
        ev("issue_reopened", "Totally Unrelated Thing", 7),
    ]
    r = resolve.resolve(events, {})
    titles = {e["ts_ms"]: r.issues[e["issue_id"]].title for e in r.events if e.get("issue_id")}
    assert titles[4] == "Cache Invalidation Failure"
    assert titles[5] == "Cache Invalidation Issue"  # the same channel breaks the tie
    assert titles[6] == "OAuth Scope Misconfiguration"
    assert [u["ts_ms"] for u in r.unresolved] == [7]


def test_a_fix_with_no_report_becomes_an_issue_without_a_customer():
    r = resolve.resolve([ev("issue_resolved", "Broken Export", 1)], {})
    (issue,) = r.issues.values()
    assert issue.title == "Broken Export" and r.events[0]["issue_how"] == "first_seen_resolved"


def test_pr_references_resolve_by_url_number_or_quote():
    prs = {"W": [pr(2), pr(15), pr(7, "salesforce/Wx", ts=1), pr(7, ts=5)]}
    events = [
        ev("pr_reverted", pr="PR# 2", ts=9),
        ev("pr_to_merge", pr="https://github.com/salesforce/W/pull/15", ts=10),
        ev("pr_to_close", pr=None, quote="could you hold off on merging PR# 15?", ts=11),
        ev("pr_on_hold", pr="PR#7", ts=3),  # two repos share 7: the one opened before
        ev("pr_to_merge", pr="PR#99", ts=12),
    ]
    r = resolve.resolve(events, prs)
    got = {e["ts_ms"]: e["pr_id"] for e in r.events}
    assert got == {
        9: "pr:salesforce/W:2",
        10: "pr:salesforce/W:15",
        11: "pr:salesforce/W:15",
        3: "pr:salesforce/Wx:7",
        12: None,
    }
    assert len(r.unresolved) == 1
