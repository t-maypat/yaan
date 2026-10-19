"""S8 decision fold (Phase 8.4; Phase 7.5 writes the claims it folds).

Deterministic and idempotent: the same events always give the same graph. Each verified,
resolved event (extract -> verify -> resolve) becomes a Claim on a topic:
- `iss:<product>:<hash>/status`: reported -> resolved | dismissed -> reopened -> ...
- `pr:<herb id>/action`: what the team said to do with a PR: to_merge, to_close, on_hold,
  reverted. It is kept apart from the record-derived `pr:<id>/status` chain (D049).

Per topic, claims are applied in time order with the §9.4 rules:
1. same value as the active decision -> the claim SUPPORTS it;
2. otherwise the policy decides: `unauthorized` -> CONFLICTS_WITH the active decision, state
   unchanged; `authorized` / `unverified` and decisive -> a new Decision that SUPERSEDES the
   old one, whose window closes at the claim's time (nothing is deleted);
3. not decisive -> the claim stays `pending`.
Issue events are decisive by nature (a report, a fix, a dismissal, a reopening); PR intents
keep the model's flag, since "should we revert?" and "please revert" differ.

`rebuild` turns a whole extraction into rows for the queries in LOAD_ORDER; `load` clears the
derived layer and writes them, so a late claim just means re-running the fold.
Spec: docs/CONTEXT.md §9.3 (S8), §9.4; PLAN.md 7.5, 8.3-8.5.
"""

import hashlib
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from app import db, policy
from app.ingest.normalize import Artifact
from app.ingest.resolve import Issue, Resolution, norm_title

ISSUE_VALUE = {
    "issue_reported": "reported",
    "issue_resolved": "resolved",
    "issue_dismissed": "dismissed",
    "issue_reopened": "reopened",
}
PR_VALUE = {
    "pr_to_merge": "to_merge",
    "pr_to_close": "to_close",
    "pr_on_hold": "on_hold",
    "pr_reverted": "reverted",
}
# (active value, claim value) pairs where the claim restates the active decision, so it
# SUPPORTS it instead of replacing it (8.8 hand check, D051):
# - a later "report" of a known issue is a (re)assignment ("I'll reassign the bug to you"),
#   not a state change: a reopened bug stays reopened;
# - "I'll hold off on merging" after the team agreed not to merge is compliance, not a pause.
RESTATES = {(v, "reported") for v in ISSUE_VALUE.values()} | {("to_close", "on_hold")}
# ...except a fresh report of a bug the team had settled (D098): when the report names the
# issue ("there's a 'Cache Invalidation Issue'") after it was resolved or dismissed, the bug is
# back, so the claim reopens it and the Sentinel sees a regression. A reassignment refers to
# "the bug" without naming it and still restates.
SETTLED = ("resolved", "dismissed")
_CUST = re.compile(r"CUST-\d+")
_EID = re.compile(r"eid_[0-9a-f]+")

LOAD_ORDER = (
    "upsert_issues",
    "upsert_claims",
    "upsert_decisions",
    "link_claim_from",
    "link_asserted_by",
    "link_claim_about_issue",
    "link_claim_about_pr",
    "link_supports",
    "link_conflicts_with",
    "link_affects_issue",
    "link_affects",
    "link_decided_by",
    "link_evidenced_by",
    "link_supersedes",
    "link_issue_in_product",
    "link_reported_by",
    "link_reported_in",
    "link_assigned_to",
    "link_fixes",
    "set_pr_action",
)


@dataclass
class Context:
    """Facts the fold reads: people's roles, who is active where, the products' PRs."""

    roles: dict[str, str]  # eid -> role
    customers: set[str]
    active: dict[str, set[str]]  # product -> eids that authored anything there
    prs: dict[str, Artifact]  # pr id -> PR
    observed_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    @classmethod
    def build(cls, meta, products: dict[str, list[Artifact]], observed_ms: int | None = None):
        active: dict[str, set[str]] = defaultdict(set)
        prs: dict[str, Artifact] = {}
        for product, arts in products.items():
            for a in arts:
                if a.author_eid:
                    active[product].add(a.author_eid)
                if a.kind == "pr" and not a.external:
                    prs[a.id] = a
                    for r in a.meta.get("reviews", []):
                        if r.get("reviewer_eid"):
                            active[product].add(r["reviewer_eid"])
        return cls(
            roles={eid: e["role"] for eid, e in meta.employees.items()},
            customers={c["id"] for c in meta.customers},
            active=dict(active),
            prs=prs,
            observed_ms=observed_ms if observed_ms is not None else int(time.time() * 1000),
        )


def claim_id(e: dict[str, Any]) -> str:
    target = e.get("issue_id") or e.get("pr_id") or ""
    key = f"{e['artifact_id']}|{e['type']}|{target}"
    return f"clm:{hashlib.sha1(key.encode('utf-8')).hexdigest()[:16]}"


def eid(text: str | None) -> str | None:
    m = _EID.search(text or "")
    return m.group(0) if m else None


def rebuild(res: Resolution, ctx: Context) -> tuple[dict[str, list[dict]], dict[str, Any]]:
    """All rows for the extracted layer, plus fold statistics (conflicts, pending, ...)."""
    rows: dict[str, list[dict[str, Any]]] = {name: [] for name in LOAD_ORDER}
    topics: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in res.events:
        if e["type"] in ISSUE_VALUE and e.get("issue_id"):
            topics[f"{e['issue_id']}/status"].append(e)
        elif e["type"] in PR_VALUE and e.get("pr_id"):
            topics[f"{e['pr_id']}/action"].append(e)
    stats: dict[str, int] = defaultdict(int)
    assigned = assignments(res, ctx)
    finals: dict[str, dict[str, Any]] = {}
    for topic in sorted(topics):
        claims = sorted(topics[topic], key=lambda e: (e["ts_ms"] or 0, e["artifact_id"]))
        chain = fold_topic(topic, claims, ctx, assigned, rows, stats)
        if chain:
            finals[topic] = chain[-1]
    reopens: dict[str, int] = defaultdict(int)  # every claim that the issue is back
    for c in rows["upsert_claims"]:
        if c["value"] == "reopened":
            reopens[c["topic"]] += 1
    for issue in res.issues.values():
        current = assigned.get(issue.id, [(0, None)])[-1][1]
        rows["upsert_issues"].append(
            _issue_row(issue, res, finals, current, reopens[f"{issue.id}/status"])
        )
        steps = assigned.get(issue.id, [])
        for (start, who), nxt in zip(steps, [*steps[1:], (None, None)], strict=False):
            rows["link_assigned_to"].append(
                {"issue_id": issue.id, "person_id": who, "from_ms": start, "to_ms": nxt[0]}
            )
        rows["link_issue_in_product"].append({"issue_id": issue.id, "product_id": issue.product})
    for e in res.events:
        if e["type"] == "issue_reported" and e.get("issue_id"):
            rows["link_reported_in"].append(
                {"issue_id": e["issue_id"], "artifact_id": e["artifact_id"]}
            )
            for cust in sorted(set(_CUST.findall(e.get("customer") or "")) & ctx.customers):
                rows["link_reported_by"].append({"issue_id": e["issue_id"], "customer_id": cust})
        if e["type"] == "issue_resolved" and e.get("issue_id") and e.get("pr_id"):
            rows["link_fixes"].append({"pr_id": e["pr_id"], "issue_id": e["issue_id"]})
    for topic, d in finals.items():
        if topic.endswith("/action"):
            rows["set_pr_action"].append(
                {
                    "pr_id": topic.removesuffix("/action"),
                    "action": d["value"],
                    "reason": d["_reason"],
                    "since_ms": d["valid_from_ms"],
                }
            )
    for name in ("link_reported_by", "link_assigned_to", "link_fixes", "link_reported_in"):
        rows[name] = _unique(rows[name])
    stats["issues"] = len(res.issues)
    stats["topics"] = len(topics)
    return rows, dict(stats)


def fold_topic(topic, claims, ctx, assigned, rows, stats) -> list[dict[str, Any]]:
    """Apply one topic's claims in time order (§9.4); append rows; return the chain."""
    is_issue = topic.startswith("iss:")
    kind = "issue_status" if is_issue else "pr_action"
    chain: list[dict[str, Any]] = []
    for e in claims:
        value = (ISSUE_VALUE if is_issue else PR_VALUE)[e["type"]]
        target = e["issue_id"] if is_issue else e["pr_id"]
        speaker = e.get("speaker")
        verdict = _verdict(kind, e, speaker, target, ctx, assigned)
        decisive = True if is_issue else bool(e.get("decisive"))
        active = chain[-1] if chain else None
        if value == "reported" and active and active["value"] in SETTLED and names_issue(e):
            value = "reopened"
        cid = claim_id(e)
        if active and (active["value"] == value or (active["value"], value) in RESTATES):
            outcome = "supports"
            rows["link_supports"].append({"claim_id": cid, "decision_id": active["id"]})
        elif verdict.outcome == "unauthorized":
            outcome = "conflict"
            if active:
                rows["link_conflicts_with"].append(
                    {"claim_id": cid, "decision_id": active["id"], "reason": verdict.reason}
                )
        elif not decisive:
            outcome = "pending"
        else:
            outcome = "supersedes" if active else "supports"
            ts = e["ts_ms"] or 0
            if active and ts <= active["valid_from_ms"]:
                ts = active["valid_from_ms"] + 1
            confidence = float(e.get("confidence") or 0.5)
            if verdict.outcome == "unverified":
                confidence = min(confidence, policy.UNVERIFIED_CONFIDENCE)
            d = {
                "id": f"dec:{topic}:{ts}",
                "topic": topic,
                "kind": kind,
                "value": value,
                "statement": _statement(topic, value, e, ctx),
                "valid_from_ms": ts,
                "valid_to_ms": None,
                "status": "active",
                "confidence": round(confidence, 3),
                "origin": "extracted",
                "ts_inferred": False,
                "observed_ms": ctx.observed_ms,
                "_reason": e.get("reason"),
            }
            if active:
                active["valid_to_ms"], active["status"] = ts, "superseded"
                rows["link_supersedes"].append({"new_id": d["id"], "old_id": active["id"]})
            chain.append(d)
            rows["link_supports"].append({"claim_id": cid, "decision_id": d["id"]})
            (rows["link_affects_issue"] if is_issue else rows["link_affects"]).append(
                {"decision_id": d["id"], ("issue_id" if is_issue else "artifact_id"): target}
            )
            if speaker in ctx.roles:
                rows["link_decided_by"].append({"decision_id": d["id"], "person_id": speaker})
            rows["link_evidenced_by"].append(
                {
                    "decision_id": d["id"],
                    "artifact_id": e["artifact_id"],
                    "field": "text",
                    "quote": e["quote"],
                    "quote_hash": hashlib.sha1(e["quote"].encode("utf-8")).hexdigest()[:12],
                    "start": e.get("start"),
                    "end": e.get("end"),
                }
            )
        stats[f"claims_{outcome}"] += 1
        stats[f"authority_{verdict.outcome}"] += 1
        rows["upsert_claims"].append(
            {
                "id": cid,
                "topic": topic,
                "kind": kind,
                "type": e["type"],
                "value": value,
                "quote": e["quote"],
                "ts_ms": e["ts_ms"],
                "ts_iso": e.get("ts_iso"),
                "decisive": decisive,
                "confidence": float(e.get("confidence") or 0.5),
                "outcome": outcome,
                "authority": verdict.outcome,
                "authority_rule": verdict.rule,
                "authority_reason": verdict.reason,
                "reason": e.get("reason"),
                "resolved_how": e.get("issue_how") or e.get("pr_how"),
                "product_id": e["product"],
                "observed_ms": ctx.observed_ms,
            }
        )
        rows["link_claim_from"].append(
            {
                "claim_id": cid,
                "artifact_id": e["artifact_id"],
                "quote": e["quote"],
                "start": e.get("start"),
                "end": e.get("end"),
            }
        )
        if speaker in ctx.roles:
            rows["link_asserted_by"].append({"claim_id": cid, "person_id": speaker})
        about = "link_claim_about_issue" if is_issue else "link_claim_about_pr"
        rows[about].append({"claim_id": cid, ("issue_id" if is_issue else "pr_id"): target})
    for d in chain:
        rows["upsert_decisions"].append({k: v for k, v in d.items() if not k.startswith("_")})
    return chain


def names_issue(e: dict[str, Any]) -> bool:
    """Does the claim's quote name its issue's title (a report, not "the bug")?"""
    title = norm_title(e.get("issue") or "")
    return bool(title) and title in norm_title(e.get("quote") or "")


def _verdict(kind, e, speaker, target, ctx: Context, assigned) -> policy.Verdict:
    if e["type"] == "issue_reported":
        return policy.Verdict("authorized", "report", "anyone may report an issue")
    pr = ctx.prs.get(target) if kind == "pr_action" else None
    facts = policy.Facts(
        speaker=speaker if speaker in ctx.roles else None,
        speaker_role=ctx.roles.get(speaker or ""),
        speaker_in_product=speaker in ctx.active.get(e["product"], set()),
        assignee=assignee_at(assigned.get(target, []), e["ts_ms"] or 0)
        if kind == "issue_status"
        else None,
        pr_author=pr.author_eid if pr else None,
        pr_reviewers=frozenset(
            r["reviewer_eid"]
            for r in (pr.meta.get("reviews", []) if pr else [])
            if r.get("reviewer_eid")
        ),
    )
    return policy.decide(kind, facts)


def assignments(res: Resolution, ctx: Context) -> dict[str, list[tuple[int, str]]]:
    """Per issue, who it was assigned to and from when, in time order: every report or
    reassignment that names a known person (the same person twice in a row counts once)."""
    out: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for e in sorted(res.events, key=lambda e: (e["ts_ms"] or 0, e["artifact_id"])):
        who = eid(e.get("person"))
        if e["type"] == "issue_reported" and e.get("issue_id") and who in ctx.roles:
            steps = out[e["issue_id"]]
            if not steps or steps[-1][1] != who:
                steps.append((e["ts_ms"] or 0, who))
    return dict(out)


def assignee_at(steps: list[tuple[int, str]], ts: int) -> str | None:
    """The assignee in force at `ts` (the policy judges a claim by the facts of its time)."""
    current = None
    for start, who in steps:
        if start <= ts:
            current = who
    return current


def _statement(topic: str, value: str, e: dict[str, Any], ctx: Context) -> str:
    if topic.startswith("iss:"):
        return f"Issue '{e.get('issue')}' in {e['product']} is {value}"
    pr = ctx.prs.get(e["pr_id"])
    label = f"PR #{pr.meta['number']} in {pr.meta['repo']}" if pr else e["pr_id"]
    words = {
        "to_merge": "to be merged",
        "to_close": "to be closed (not merged)",
        "on_hold": "on hold",
        "reverted": "to be reverted",
    }[value]
    why = f" ({e['reason']})" if e.get("reason") and value in ("reverted", "to_close") else ""
    return f"{label}: the team decided it is {words}{why}"


def _issue_row(
    issue: Issue, res: Resolution, finals, assignee: str | None, reopen_count: int
) -> dict[str, Any]:
    report = next(
        (e for e in res.events if e.get("issue_id") == issue.id and e["type"] == "issue_reported"),
        None,
    )
    final = finals.get(f"{issue.id}/status")
    return {
        "id": issue.id,
        "title": issue.title,
        "product_id": issue.product,
        "component": (report or {}).get("component"),
        "description": issue.description,
        "aliases": sorted(issue.aliases),
        "reported_ms": (report or {}).get("ts_ms"),
        "reported_iso": (report or {}).get("ts_iso"),
        "current_status": final["value"] if final else None,
        "status_since_ms": final["valid_from_ms"] if final else None,
        "current_assignee": assignee,
        "reopen_count": reopen_count,
        "first_seen": "reported" if report else "resolved",
    }


def _unique(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen, out = set(), []
    for r in rows:
        key = tuple(sorted((k, str(v)) for k, v in r.items()))
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def load(rows: dict[str, list[dict[str, Any]]], graph: str | None = None) -> dict[str, dict]:
    """Clear the derived extraction layer, then write every query's rows in LOAD_ORDER."""
    report = {
        "clear_extracted": {
            "rows": 0,
            **db.run("clear_extracted", {}, graph=graph, readonly=False).stats,
        }
    }
    for name in LOAD_ORDER:
        if rows.get(name):
            result = db.run(name, {"rows": rows[name]}, graph=graph, readonly=False)
            report[name] = {"rows": len(rows[name]), **result.stats}
    return report
