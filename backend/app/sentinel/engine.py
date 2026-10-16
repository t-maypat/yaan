"""Sentinel engine (11.1): decision changes -> Alert nodes. Spec: docs/CONTEXT.md §9.4-9.5, D054.

Runs after every fold (batch `lore ingest --stages fold derive sentinel`, or the live path),
over the whole extracted layer, so one code path serves both:
1. `sentinel_changes.cypher` lists every extracted supersession and every CONFLICTS_WITH claim;
2. RULES (code, not the LLM) say which changes deserve an alert. Normal progress
   (reported -> resolved, reported -> dismissed) does not;
3. each alert gets its blast radius (blast_radius.impact_of) and a summary from a template;
4. the alerts are MERGEd by a stable id (`alert:<decision id>` / `alert:<claim id>`), so a
   re-run updates them without resetting their status. The fold's rebuild deletes and
   recreates decisions and claims with the same ids, so the FLAGS, EVIDENCED_BY and NOTIFIES
   edges are re-linked on every run;
5. an open alert whose change is gone after a rebuild becomes `withdrawn`. Nothing is deleted.
"""

import time
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from app import db
from app.sentinel import blast_radius, drafts

# (decision kind, old value, new value) -> (alert kind, severity). Everything else is progress.
RULES: dict[tuple[str, str, str], tuple[str, str]] = {
    # a bug the team said was fixed is back: the customer was told "fixed"
    ("issue_status", "resolved", "reopened"): ("regression", "high"),
    # a bug the team dismissed is back: the dismissal was wrong
    ("issue_status", "dismissed", "reopened"): ("reopened", "medium"),
    # the team's stated call on a PR flipped
    ("pr_action", "to_merge", "to_close"): ("reversal", "medium"),
    ("pr_action", "to_merge", "reverted"): ("reversal", "medium"),
    ("pr_action", "to_close", "to_merge"): ("reversal", "medium"),
}
CONFLICT = ("conflict", "high")  # an unauthorized claim against an active decision (§9.4 rule 5)
LOAD_ORDER = (
    "upsert_alerts",
    "link_alert_flags",
    "link_alert_flags_claim",
    "link_alert_evidence",
    "link_notifies",
    "upsert_actions",
    "link_action_for",
    "link_action_targets_issue",
    "link_action_targets_artifact",
    "link_action_targets_person",
)
TARGET_QUERY = {
    "issue": "link_action_targets_issue",
    "artifact": "link_action_targets_artifact",
    "person": "link_action_targets_person",
}


def judge(change: dict[str, Any]) -> tuple[str, str] | None:
    """The alert kind and severity for one change, or None for normal progress."""
    if change["change"] == "conflict":
        return CONFLICT
    return RULES.get((change["kind"], change["from_value"], change["to_value"]))


def day(ms: int | None) -> str:
    if ms is None:
        return "an unknown date"
    dt = datetime.fromtimestamp(ms / 1000, UTC)
    return f"{dt.day} {dt:%b %Y}"  # no %-d: Windows strftime lacks it


def summary(kind: str, c: dict[str, Any]) -> str:
    """One sentence per alert, from the change's own fields (no LLM)."""
    title = c["subject_title"]
    if kind == "regression":
        return (
            f"Regression: '{title}' was marked resolved on {day(c['from_ms'])} "
            f"and reopened on {day(c['ts_ms'])}."
        )
    if kind == "reopened":
        return f"'{title}' was dismissed on {day(c['from_ms'])} and reopened on {day(c['ts_ms'])}."
    if kind == "reversal":
        return (
            f"PR '{title}': the team's call changed from {c['from_value']} to {c['to_value']} "
            f"on {day(c['ts_ms'])}."
        )
    who = c["actor_name"] or "an unknown speaker"
    role = f" ({c['actor_role']})" if c["actor_role"] else ""
    return (
        f"Conflict: {who}{role} says '{title}' is {c['to_value']}, but may not change it; "
        f"it stays {c['from_value']}."
    )


def run(graph: str | None = None, *, now_ms: int | None = None) -> dict[str, Any]:
    """Judge every decision change in `graph`, write the alerts, and report what changed."""
    now_ms = now_ms or int(time.time() * 1000)
    now_iso = datetime.fromtimestamp(now_ms / 1000, UTC).isoformat().replace("+00:00", "Z")
    changes = db.run("sentinel_changes", {"limit": 10_000}, graph=graph).rows
    rows: dict[str, list[dict[str, Any]]] = {name: [] for name in LOAD_ORDER}
    seen: set[str] = set()
    progress = 0
    for c in changes:
        rule = judge(c)
        if rule is None:
            progress += 1
            continue
        alert_id = f"alert:{c['trigger_id']}"
        if alert_id in seen:  # a decision with two evidence fields gives two rows
            continue
        seen.add(alert_id)
        kind, severity = rule
        radius = blast_radius.impact_of(c["decision_id"], graph=graph)
        alert = {
            **c,
            "id": alert_id,
            "kind": kind,
            "severity": severity,
            "summary": summary(kind, c),
            "ts_iso": datetime.fromtimestamp(c["ts_ms"] / 1000, UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            "radius_size": radius["size"],
            "radius_people": len(radius["people"]),
            "radius_companies": [co["name"] for co in radius["companies"]],
            "observed_ms": now_ms,
            "observed_iso": now_iso,
        }
        rows["upsert_alerts"].append(alert)
        for action in drafts.drafts_for(alert, radius):
            rows["upsert_actions"].append({**action, "observed_ms": now_ms})
            rows["link_action_for"].append({"id": action["id"], "alert_id": alert_id})
            rows[TARGET_QUERY[action["target_kind"]]].append(
                {"id": action["id"], "target_id": action["target_id"]}
            )
        rows["link_alert_flags"].append({"alert_id": alert_id, "decision_id": c["decision_id"]})
        if c["change"] == "conflict":
            rows["link_alert_flags_claim"].append(
                {"alert_id": alert_id, "claim_id": c["trigger_id"]}
            )
        if c["artifact_id"]:
            rows["link_alert_evidence"].append(
                {"alert_id": alert_id, "artifact_id": c["artifact_id"], "quote": c["quote"]}
            )
        rows["link_notifies"] += [
            {"alert_id": alert_id, "person_id": p["id"], "reasons": p["reasons"]}
            for p in radius["people"]
        ]
    report: dict[str, dict[str, Any]] = {}
    for name in LOAD_ORDER:
        if rows[name]:
            result = db.run(name, {"rows": rows[name]}, graph=graph, readonly=False)
            report[name] = {"rows": len(rows[name]), **result.stats}
    withdrawn = db.run("withdraw_alerts", {"ids": sorted(seen)}, graph=graph, readonly=False)
    report["withdraw_alerts"] = {"rows": withdrawn.rows[0]["withdrawn"], **withdrawn.stats}
    return {
        "changes": len(changes),
        "progress": progress,
        "alerts": len(seen),
        "new": report.get("upsert_alerts", {}).get("nodes_created", 0),
        "withdrawn": withdrawn.rows[0]["withdrawn"],
        "by_kind": dict(Counter(r["kind"] for r in rows["upsert_alerts"])),
        "actions": len(rows["upsert_actions"]),
        "report": report,
    }


def list_alerts(
    status: str | None = None, product_id: str | None = None, *, graph: str | None = None
) -> list[dict[str, Any]]:
    """The Inbox's alerts, newest change first."""
    params = {"status": status, "product_id": product_id, "limit": 200}
    out = []
    for r in db.run("list_alerts", params, graph=graph).rows:
        r["notifies"] = [p for p in r["notifies"] if p["id"]]
        out.append(r)
    return out


def alert_actions(alert_id: str, *, graph: str | None = None) -> list[dict[str, Any]]:
    """The drafted actions for one alert (11.3)."""
    return db.run("alert_actions", {"alert_id": alert_id, "limit": 50}, graph=graph).rows
