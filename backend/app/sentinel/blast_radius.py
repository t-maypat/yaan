"""Blast radius of a decision change (11.2, tool 11). Spec: docs/CONTEXT.md §9.5, D040.

One graph read (queries/impact_of.cypher) gathers what a state change touches. For an issue,
that is the PR that FIXES it with its author and reviewers, the REPORTED_BY customers and
the companies they WORK_AT, the assignees, and the other issues on the same component. For a
PR, it is the author, the reviewers and the issues it fixes. Both also get the artifacts and
the person behind the state the decision replaced (e.g. the message that said "fixed"), the
teams WORKS_ON the product, and CAUSED decisions up to 3 hops. There are no ASSUMES edges
(R1-4: P2).

Every item says why it is in the radius (`why`) and what it hangs off (`via`). A person who
is in for several reasons (e.g. the fix's author is also the assignee) appears once in
`people`, with all of them.
"""

from typing import Any

from app import db

# Open issues on the same component may share the root cause; resolved ones may need a recheck.
OPEN = ("reported", "reopened")


def impact_of(decision_id: str, *, graph: str | None = None, limit: int = 25) -> dict[str, Any]:
    """The blast radius of `decision_id`, grouped by kind; `people` merges each person's
    reasons. Empty sections are kept so a caller can show "none"."""
    rows = db.run("impact_of", {"decision_id": decision_id, "limit": limit}, graph=graph).rows
    by_why: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_why.setdefault(r["why"], []).append(r)
    people: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r["kind"] != "person":
            continue
        p = people.setdefault(
            r["id"], {"id": r["id"], "name": r["name"], "role": r["role"], "reasons": []}
        )
        if r["why"] not in p["reasons"]:
            p["reasons"].append(r["why"])
    component = by_why.get("same_component", [])
    return {
        "decision_id": decision_id,
        "prs": by_why.get("fixes_issue", []),
        "people": list(people.values()),
        "customers": by_why.get("reported_issue", []),
        "companies": by_why.get("reporter_works_at", []),
        "open_issues_same_component": [i for i in component if i["detail"] in OPEN],
        "other_issues_same_component": [i for i in component if i["detail"] not in OPEN],
        "issues_fixed_by_pr": by_why.get("fixed_by_pr", []),
        "evidence_old_state": by_why.get("evidenced_old_state", []),
        "evidence_this_state": by_why.get("evidenced_this_state", []),
        "teams": by_why.get("works_on_product", []),
        "caused": by_why.get("caused", []),
        "size": len({(r["kind"], r["id"]) for r in rows}),
        "rows": rows,  # one per item with `via`: the Inbox draws the radius from them
        "cypher": ["impact_of.cypher"],
    }
