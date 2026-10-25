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

`rebuild` turns events into rows for the queries in LOAD_ORDER. Since D101 the events live in
the graph (events.py) and the fold runs per product (`fold_product`): its context (roles,
customers, PRs, who works where) is read from the graph too, and `write` compares each derived
node's rows with the hash stamped on it, rewrites only what changed and removes only what went
stale. Nothing is cleared wholesale, so readers never see an empty layer, and an interrupted
write is redone by the next fold (the hash is stamped last). A late claim still just means
folding its product again. `belief` replays the events known at an earlier moment.
Spec: docs/CONTEXT.md §9.3 (S8), §9.4; PLAN.md 7.5, 8.3-8.5.
"""

import hashlib
import json
import re
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from app import db, policy
from app.ingest import events as event_store
from app.ingest import resolve
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
    authority: dict[str, str | None] = field(default_factory=dict)  # people.yaml's (D102)
    unknown: str = "unverified"  # how a speaker with no role counts (people.yaml, D117)
    observed_ms: int = field(default_factory=lambda: int(time.time() * 1000))


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
    reports: dict[str, dict[str, Any]] = {}  # each issue's first report, found once (D129)
    for e in res.events:
        if e["type"] == "issue_reported" and e.get("issue_id"):
            reports.setdefault(e["issue_id"], e)
    for issue in res.issues.values():
        current = assigned.get(issue.id, [(0, None)])[-1][1]
        rows["upsert_issues"].append(
            _issue_row(issue, reports.get(issue.id), finals, current, reopens[f"{issue.id}/status"])
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
                "observed_ms": e.get("observed_ms") or ctx.observed_ms,  # when Yaan learned it
                "product_id": e["product"],
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
                "observed_ms": e.get("observed_ms") or ctx.observed_ms,
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
        speaker_authority=ctx.authority.get(speaker or ""),
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
    return policy.decide(kind, facts, ctx.unknown)


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
    issue: Issue, report: dict[str, Any] | None, finals, assignee: str | None, reopen_count: int
) -> dict[str, Any]:
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


# --- the fold over the graph (D101) -------------------------------------------------------
NODES = {"Issue": "upsert_issues", "Claim": "upsert_claims", "Decision": "upsert_decisions"}
# link query -> (the derived node that owns the edge, its id's key in the row). An edge belongs
# to the node it starts from, except FIXES (PR -> issue), which the issue owns.
OWNER = {
    "link_claim_from": ("Claim", "claim_id"),
    "link_asserted_by": ("Claim", "claim_id"),
    "link_claim_about_issue": ("Claim", "claim_id"),
    "link_claim_about_pr": ("Claim", "claim_id"),
    "link_supports": ("Claim", "claim_id"),
    "link_conflicts_with": ("Claim", "claim_id"),
    "link_affects_issue": ("Decision", "decision_id"),
    "link_affects": ("Decision", "decision_id"),
    "link_decided_by": ("Decision", "decision_id"),
    "link_evidenced_by": ("Decision", "decision_id"),
    "link_supersedes": ("Decision", "new_id"),
    "link_issue_in_product": ("Issue", "issue_id"),
    "link_reported_by": ("Issue", "issue_id"),
    "link_reported_in": ("Issue", "issue_id"),
    "link_assigned_to": ("Issue", "issue_id"),
    "link_fixes": ("Issue", "issue_id"),
}
CLEAR = {
    "Claim": ("clear_claim_edges",),
    "Decision": ("clear_decision_edges",),
    "Issue": ("clear_issue_edges", "clear_issue_fixes"),
}
DELETE = {"Claim": "delete_claims", "Issue": "delete_issues", "Decision": "delete_decisions"}
STAMP = {"Claim": "set_claim_hash", "Issue": "set_issue_hash", "Decision": "set_decision_hash"}
STATE_CAP = 500_000
WRITE_BATCH = 500  # rows per write statement


def topic_of(key: tuple[str, str], row: dict[str, Any]) -> str:
    """The decision topic a derived node belongs to: its own, or an issue's status topic."""
    return row.get("topic") or f"{key[1]}/status"


def desired(
    rows: dict[str, list[dict[str, Any]]], only: set[str] | None = None
) -> dict[tuple[str, str], dict[str, Any]]:
    """(label, id) -> the node's row, the link rows it owns, and a hash over both; with `only`,
    hashed only for the nodes of those topics (the others keep no hash: not compared)."""
    nodes: dict[tuple[str, str], dict[str, Any]] = {}
    for label, query in NODES.items():
        for r in rows[query]:  # a repeated id: the last row wins, as MERGE + SET did
            nodes[(label, r["id"])] = {"row": r, "links": defaultdict(list)}
    for query, (label, key) in OWNER.items():
        for r in rows[query]:
            nodes[(label, r[key])]["links"][query].append(r)
    for key, node in nodes.items():
        if only is not None and topic_of(key, node["row"]) not in only:
            continue
        links = {q: sorted(map(_LINK_JSON, rs)) for q, rs in node["links"].items()}
        blob = _BLOB_JSON({"row": node["row"], "links": links})
        node["hash"] = hashlib.sha1(blob.encode("utf-8")).hexdigest()
    return nodes


# The hashes' JSON, built once (22.7, D142): `json.dumps(..., sort_keys=True)` builds a new
# encoder on every call, ~430k a fold at a year's volume. The same options write the same
# bytes, so every stored hash stays valid; skipping the circular-reference check changes no
# output for these plain rows.
_LINK_JSON = json.JSONEncoder(sort_keys=True, check_circular=False).encode
_BLOB_JSON = json.JSONEncoder(sort_keys=True, default=str, check_circular=False).encode


def write(
    rows: dict[str, list[dict[str, Any]]],
    product: str,
    graph: str | None,
    only: set[str] | None = None,
) -> dict:
    """Bring one product's derived layer to `rows`: rewrite the nodes whose rows or owned edges
    changed, delete the ones no longer derived, leave the rest alone. `only` (22.7): the
    topics whose inputs changed since this process last wrote them; the others are left
    unread and unhashed (fold_product decides when that is safe)."""
    want = desired(rows, only)
    if only is None:
        params: dict[str, Any] = {"product_id": product}
        name = "derived_state"
    else:
        topics = sorted(only)
        issues = [t.removesuffix("/status") for t in topics if t.endswith("/status")]
        params = {"product_id": product, "topics": topics, "issue_ids": issues}
        name = "derived_state_topics"
    state = db.run_all(name, params, cap=STATE_CAP, graph=graph, timeout_ms=30_000)
    state = [json.loads(r["row"]) for r in state]  # [id, label, hash, topic] (A23)
    have = {(label, i): h for i, label, h, _ in state}
    topic_was = {(label, i): topic for i, label, _, topic in state}
    changed = {k for k, v in want.items() if "hash" in v and have.get(k) != v["hash"]}
    stale = sorted(k for k in have if k not in want)
    # the decision chains this write moved: the live Sentinel judges only these (D112)
    topics = {topic_was.get(k) for k in stale} | {
        want[k]["row"].get("topic") or f"{k[1]}/status" for k in changed
    }

    def ids(keys, label: str) -> list[dict[str, str]]:
        return [{"id": i} for lab, i in sorted(keys) if lab == label]

    def run(query: str, query_rows: list[dict[str, Any]]) -> None:
        if query_rows:
            db.run(query, {"rows": query_rows}, graph=graph, readonly=False)

    for label, query in DELETE.items():
        run(query, ids(stale, label))
    for label, queries in CLEAR.items():
        for query in queries:
            run(query, ids(changed & set(have), label))
    for label, query in NODES.items():
        run(query, [want[k]["row"] for k in sorted(changed) if k[0] == label])
    for query in LOAD_ORDER:
        if query in OWNER:
            run(query, [r for k in sorted(changed) for r in want[k]["links"].get(query, [])])
    actions = rows["set_pr_action"]
    run("set_pr_action", [r for r in actions if only is None or f"{r['pr_id']}/action" in only])
    keep = [r["pr_id"] for r in actions]  # every PR's: the clean ones keep their action
    cleared = db.run(
        "clear_pr_actions", {"product_id": product, "keep": keep}, graph=graph, readonly=False
    ).rows[0]["cleared"]
    for label, query in STAMP.items():  # last: an interrupted write leaves the old hash
        run(
            query,
            [
                {"id": i, "hash": want[(lab, i)]["hash"]}
                for lab, i in sorted(changed)
                if lab == label
            ],
        )
    return {
        "product": product,
        "nodes": len(want),
        "changed": len(changed),
        "stale": len(stale),
        "unchanged": len(want) - len(changed),
        "pr_actions_cleared": cleared,
        "topics": sorted(t for t in topics if t),
        "partial": only is not None,  # only the topics whose inputs changed were compared
        "compared": len(want) if only is None else sum("hash" in v for v in want.values()),
    }


# --- writing only what changed (22.7, D142) --------------------------------------------------
# A topic's rows are a function of its events (as resolved), its issue, and the context every
# topic reads. Each process remembers, per product, a fingerprint of those inputs from its last
# write; the next fold hashes, reads and writes only the topics whose fingerprint moved. A
# write by another process changes events too, so its topics move here as well. The memo is
# trusted only when the context and code are the same, the product's derived node count is
# what this process left, and a full write happened within FULL_EVERY_MS; else a full write.
MEMO_VERSION = 1  # bump when a topic's rows come to depend on anything new
FULL_EVERY_MS = 3_600_000


@dataclass
class Memo:
    version: str
    context: str
    topics: dict[str, str]
    nodes: int
    full_ms: int


_memo: dict[tuple[str, str], Memo] = {}
_memo_lock = threading.Lock()


def forget_memo(graph: str | None = None) -> None:
    """The derived layer changed without its events (a migration, a demo reset, a restore):
    the next fold of `graph` (None: every graph) writes in full."""
    with _memo_lock:
        for k in [k for k in _memo if graph is None or k[0] == (graph or "")]:
            del _memo[k]


def fingerprints(res: Resolution, ctx: Context, product: str) -> tuple[str, dict[str, str]]:
    """(the context's hash, {topic: hash of its inputs}): its events as this pass resolved them
    (their stored matches left out: rewritten after every write), and an issue's own fields."""

    def h(value: Any) -> str:
        return hashlib.sha1(_BLOB_JSON(value).encode("utf-8")).hexdigest()

    prs = sorted(
        [p.id, p.author_eid, p.ts_ms, p.ts_iso, p.external, p.meta] for p in ctx.prs.values()
    )
    active = sorted(ctx.active.get(product) or ())
    context = h([ctx.roles, ctx.authority, ctx.unknown, sorted(ctx.customers), active, prs])
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in res.events:  # the topics rebuild makes, the same way
        if e["type"] in ISSUE_VALUE and e.get("issue_id"):
            groups[f"{e['issue_id']}/status"].append(e)
        elif e["type"] in PR_VALUE and e.get("pr_id"):
            groups[f"{e['pr_id']}/action"].append(e)
    issues = {f"{i.id}/status": i for i in res.issues.values()}
    out = {}
    for topic in groups.keys() | issues.keys():
        evs = sorted(groups.get(topic, []), key=lambda e: (e["ts_ms"] or 0, e["artifact_id"]))
        evs = [{k: v for k, v in e.items() if not k.startswith("res_")} for e in evs]
        issue = issues.get(topic)
        doc = None if issue is None else {**vars(issue), "aliases": sorted(issue.aliases)}
        out[topic] = h([evs, doc])
    return context, out


def dirty(
    key: tuple[str, str], context: str, topics: dict[str, str], product: str, graph: str | None
) -> set[str] | None:
    """The topics to write, or None for a full write (no memo here, or it can't be trusted)."""
    with _memo_lock:
        memo = _memo.get(key)
    now = int(time.time() * 1000)
    if memo is None or memo.version != memo_version() or memo.context != context:
        return None
    if now - memo.full_ms >= FULL_EVERY_MS:
        return None
    rows = db.run("derived_count", {"product_id": product}, graph=graph).rows
    if sum(r["n"] for r in rows) != memo.nodes:  # someone else changed the layer: start over
        return None
    return {t for t in topics.keys() | memo.topics.keys() if topics.get(t) != memo.topics.get(t)}


def keep_memo(
    key: tuple[str, str], context: str, topics: dict[str, str], nodes: int, full: bool
) -> None:
    now = int(time.time() * 1000)
    with _memo_lock:
        last = _memo.get(key)
        full_ms = now if full or last is None else last.full_ms
        _memo[key] = Memo(memo_version(), context, topics, nodes, full_ms)


def memo_version() -> str:
    return f"{resolve.VERSION}:{MEMO_VERSION}"


def migrate_legacy(graph: str | None) -> int:
    """A graph whose derived layer predates D101 (extracted decisions without product_id): its
    claims, issues and extracted decisions are deleted once, in batches, so the next fold of
    each product writes them afresh. Returns how many nodes went."""
    n = db.run("legacy_derived", {}, graph=graph).rows[0]["n"]
    if not n:
        return 0
    forget_memo(graph)
    found = db.run_all("all_derived", {}, cap=STATE_CAP, graph=graph, timeout_ms=60_000)
    for label, query in DELETE.items():
        rows = [{"id": r["id"]} for r in found if r["label"] == label]
        if rows:
            db.run(query, {"rows": rows}, graph=graph, readonly=False)
    return len(found)


def graph_context(product: str, graph: str | None) -> tuple[Context, list[Artifact]]:
    """The fold's context for one product, read from the graph, and its PRs for resolution."""
    people = db.run_all("fold_people", {}, cap=STATE_CAP, graph=graph, timeout_ms=30_000)
    roles = {r["id"]: r["role"] for r in people}
    authority = {r["id"]: r["authority"] for r in people if r["authority"]}
    customers = {
        r["id"]
        for r in db.run_all("fold_customers", {}, cap=STATE_CAP, graph=graph, timeout_ms=30_000)
    }
    active = {
        r["id"]
        for r in db.run_all(
            "fold_product_active",
            {"product_id": product},
            cap=STATE_CAP,
            graph=graph,
            timeout_ms=30_000,
        )
    }
    prs = [
        Artifact(
            id=r["id"],
            kind="pr",
            product_hint=product,
            ts_ms=r["ts_ms"],
            ts_iso=r["ts_iso"],
            author_eid=r["author"],
            text="",
            raw_ref=r["id"],
            external=bool(r["external"]),
            meta={
                "repo": r["repo"],
                "number": r["number"],
                "reviews": [{"reviewer_eid": x} for x in r["reviewers"]],
            },
        )
        for r in db.run_all(
            "fold_product_prs",
            {"product_id": product},
            cap=STATE_CAP,
            graph=graph,
            timeout_ms=30_000,
        )
    ]
    from app import org

    ctx = Context(
        roles=roles,
        customers=customers,
        active={product: active},
        prs={p.id: p for p in prs if not p.external},
        authority=authority,
        unknown=org.unknown_speakers() if org.active() else "unverified",
    )
    return ctx, prs


def fold_product(
    product: str,
    graph: str | None,
    *,
    known_at_ms: int | None = None,
    apply: bool = True,
    recall: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Fold one product's events from the graph and (`apply`) write the result. With
    `known_at_ms`, only the events Yaan knew then: the belief at that moment (never written)."""
    ms: dict[str, float] = {}  # each stage's time: the load test and slow-fold warnings read it
    t = time.perf_counter()

    def lap(stage: str) -> None:
        nonlocal t
        now = time.perf_counter()
        ms[stage], t = round((now - t) * 1000, 1), now

    found = event_store.read(product, graph, known_at_ms)
    lap("read")
    ctx, prs = graph_context(product, graph)
    lap("context")
    prior, retracted = last_pass(product, graph, found, known_at_ms, recall)
    res = resolve.resolve(found, {product: prs}, prior)
    lap("resolve")
    rows, stats = rebuild(res, ctx)
    lap("rebuild")
    out: dict[str, Any] = {"rows": rows, "stats": stats, "unresolved": len(res.unresolved)}
    out["matched"] = res.matched  # issue events matched afresh; the rest reused (D129)
    if apply and known_at_ms is None:
        key = (graph or "", product)
        context, topics = fingerprints(res, ctx, product)
        only = dirty(key, context, topics, product, graph)
        out["write"] = write(rows, product, graph, only=only)
        remember(res, retracted, graph)  # after the layer: an interrupted fold redoes the work
        keep_memo(key, context, topics, out["write"]["nodes"], full=only is None)
        lap("write")
    out["ms"] = ms
    return out


def last_pass(
    product: str,
    graph: str | None,
    found: list[dict[str, Any]],
    known_at_ms: int | None,
    recall: list[dict[str, Any]] | None = None,
) -> tuple[resolve.Prior, list[str]]:
    """The last pass's stored resolutions (D129): every event that carries one of this version,
    live or retracted since, and the ones not in this pass (`gone`). Also the retracted events
    still carrying one, whose results are cleared once this pass is written. `recall`: events
    `forget` just blanked, as they were (in memory only), so the pass knows what they made."""
    retracted = event_store.retracted_resolved(product, graph)
    live = found if known_at_ms is None else event_store.read(product, graph)
    pool = {e["id"]: e for e in [*live, *retracted]}
    pool |= {e["id"]: e for e in recall or [] if e["id"] in pool}
    results = {
        i: (e["res_issue"], e["res_how"])
        for i, e in pool.items()
        if e.get("res_v") == resolve.VERSION and e.get("res_how")
    }
    here = {e["id"] for e in found}
    gone = [e for i, e in pool.items() if i in results and i not in here]
    return resolve.Prior(results, gone), [e["id"] for e in retracted]


def remember(res: resolve.Resolution, retracted: list[str], graph: str | None) -> None:
    """Store this pass's new or changed resolutions on their events, and clear the retracted
    ones', in batches a write finishes inside its time budget (A17)."""
    rows = [r | {"v": resolve.VERSION} for r in res.store]
    for lo in range(0, len(rows), WRITE_BATCH):
        db.run("set_event_resolutions", {"rows": rows[lo : lo + WRITE_BATCH]}, graph=graph,
               readonly=False)  # fmt: skip
    for lo in range(0, len(retracted), WRITE_BATCH):
        db.run("clear_event_resolutions", {"ids": retracted[lo : lo + WRITE_BATCH]}, graph=graph,
               readonly=False)  # fmt: skip


def forget_resolutions(product: str, graph: str | None) -> int:
    """Forget or erase rewrote the product's events: drop every stored resolution, so the
    next fold resolves the product afresh. Returns how many were dropped."""
    total = 0
    while n := db.run(
        "clear_product_resolutions",
        {"product_id": product, "limit": WRITE_BATCH * 10},
        graph=graph,
        readonly=False,
    ).rows[0]["cleared"]:
        total += n
    return total


def belief(
    subject_id: str, product: str, graph: str | None, known_at_ms: int | None
) -> dict[str, Any]:
    """What Yaan believed about an issue or PR at `known_at_ms` (None: now): its chains as the
    fold derives them from the events known then. Read-only: nothing is written."""
    rows = fold_product(product, graph, known_at_ms=known_at_ms, apply=False)["rows"]
    chains: dict[str, list[dict[str, Any]]] = {}
    for d in sorted(rows["upsert_decisions"], key=lambda d: d["valid_from_ms"]):
        if d["topic"].split("/")[0] == subject_id:
            facet = d["topic"].rsplit("/", 1)[1]
            chains.setdefault(facet, []).append(
                {
                    k: d[k]
                    for k in (
                        "id",
                        "value",
                        "statement",
                        "valid_from_ms",
                        "valid_to_ms",
                        "status",
                        "observed_ms",
                    )
                }
            )
    claims = [
        {k: c[k] for k in ("id", "value", "quote", "ts_iso", "outcome", "authority", "observed_ms")}
        for c in sorted(rows["upsert_claims"], key=lambda c: c["ts_ms"] or 0)
        if c["topic"].split("/")[0] == subject_id
    ]
    return {"subject": subject_id, "known_at_ms": known_at_ms, "chains": chains, "claims": claims}
