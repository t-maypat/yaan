"""Sentinel engine (11.1): decision changes -> Alert nodes. Spec: docs/CONTEXT.md §9.4-9.5, D054.

Runs after every fold (batch `yaan ingest --stages fold derive sentinel`, or the live path),
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

The live path judges only the decision chains its fold changed (`topics`, D112): the same
rules, found from the topic index, and only those topics' alerts can be withdrawn. Key-person
and bus-factor alerts read the derived layer, so they come with the full run, which the
periodic job (`live.derive_if_due`) and the batch make.
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
BULK_ROWS = 1_000_000  # the full run's whole-graph reads: fail loudly past this, never cut (D115)
BULK_TIMEOUT_MS = 120_000
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
    if change["change"] == "outside_group":  # dismissed outside the area's working group (D064)
        return ("outside_group", "medium")
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
    if kind == "outside_group":
        return (
            f"'{title}' was dismissed by {who}{role}, who is outside the working group "
            "that does the work in its area."
        )
    return (
        f"Conflict: {who}{role} says '{title}' is {c['to_value']}, but may not change it; "
        f"it stays {c['from_value']}."
    )


def run(
    graph: str | None = None, *, now_ms: int | None = None, topics: list[str] | None = None
) -> dict[str, Any]:
    """Judge every decision change in `graph` (or only `topics`' chains), write the alerts,
    and report what changed."""
    now_ms = now_ms or int(time.time() * 1000)
    now_iso = datetime.fromtimestamp(now_ms / 1000, UTC).isoformat().replace("+00:00", "Z")
    if topics is not None:
        changes = []
        if topics:
            given = {"topics": topics}
            changes = db.run_all("sentinel_changes_for", given, cap=50_000, graph=graph)
            changes += db.run_all("outside_decisions_for", given, cap=10_000, graph=graph)
    else:
        full = {"cap": BULK_ROWS, "graph": graph, "timeout_ms": BULK_TIMEOUT_MS}
        changes = db.run_all("sentinel_changes", {}, **full)
        # dismissals by someone outside the area's working group (CDLP, D064); none before derive
        changes += db.run_all("outside_decisions", {}, **full)
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
    for alert, radius in key_person_alerts(graph, now_ms, now_iso) if topics is None else []:
        seen.add(alert["id"])
        rows["upsert_alerts"].append(alert)
        for action in drafts.key_person_drafts(alert, radius):
            rows["upsert_actions"].append({**action, "observed_ms": now_ms})
            rows["link_action_for"].append({"id": action["id"], "alert_id": alert["id"]})
            rows[TARGET_QUERY[action["target_kind"]]].append(
                {"id": action["id"], "target_id": action["target_id"]}
            )
        rows["link_notifies"] += [
            {"alert_id": alert["id"], "person_id": p["id"], "reasons": p["reasons"]}
            for p in radius["people"]
            if "manager" in p["reasons"]
        ] + [{"alert_id": alert["id"], "person_id": alert["subject_id"], "reasons": ["key_person"]}]
    report: dict[str, dict[str, Any]] = {}
    for name in LOAD_ORDER:
        if rows[name]:
            result = db.run(name, {"rows": rows[name]}, graph=graph, readonly=False)
            report[name] = {"rows": len(rows[name]), **result.stats}
    # a rebuild recreates the flagged decisions: re-link CAUSED to the responses (11.4)
    report["link_alert_caused"] = {
        "rows": 0,
        **db.run("link_alert_caused", {}, graph=graph, readonly=False).stats,
    }
    if topics is None:
        withdrawn = db.run("withdraw_alerts", {"ids": sorted(seen)}, graph=graph, readonly=False)
    else:
        withdrawn = db.run(
            "withdraw_alerts_for",
            {"ids": sorted(seen), "topics": topics},
            graph=graph,
            readonly=False,
        )
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


MIN_SOLE_AREAS = 3  # a key person is the only author of at least this many areas


def key_person_alerts(graph: str | None, now_ms: int, now_iso: str) -> list[tuple[dict, dict]]:
    """11.5 (D062): one `key_person` alert per person who is first in a product's truck-factor
    order and the only author of >= MIN_SOLE_AREAS areas; `bus_factor` (high) when that
    product's truck factor is 1. Dated on HERB's clock (the products' last artifact)."""
    rows = db.run_all("key_people", {}, cap=BULK_ROWS, graph=graph, timeout_ms=BULK_TIMEOUT_MS)
    clock = {
        r["product_id"]: r["last_ms"]
        for r in db.run_all("product_clock", {}, cap=1000, graph=graph, timeout_ms=30_000)
    }
    people: dict[str, dict[str, Any]] = {}
    for r in rows:
        p = people.setdefault(
            r["person_id"],
            {"name": r["name"], "role": r["role"], "flagged": {}, "areas": {}},
        )
        p["flagged"][r["flagged_in"]] = r["tf"]
        p["areas"][r["area_id"]] = r["area_product"]
    out = []
    for pid, p in sorted(people.items()):
        if len(p["areas"]) < MIN_SOLE_AREAS:
            continue
        flagged = sorted(p["flagged"])
        products = sorted(set(p["areas"].values()))
        tf1 = any(tf == 1 for tf in p["flagged"].values())
        kind = "bus_factor" if tf1 else "key_person"
        where = " and ".join(products)
        place = (
            ("in both" if len(flagged) == 2 else "in each")
            if flagged == products and len(flagged) > 1
            else "there"
            if flagged == products
            else f"in {' and '.join(flagged)}"
        )
        first_in = f"the first person the truck factor removes {place}"
        summary = (
            f"{p['name']} ({p['role']}) is the only expert on {len(p['areas'])} "
            f"area{'s' if len(p['areas']) != 1 else ''} in "
            f"{where}, and {first_in}."
        )
        radius = blast_radius.person_radius(pid, graph=graph)
        ts = max((clock.get(x) or 0) for x in flagged)
        alert = {
            "id": f"alert:{kind}:{pid}",
            "kind": kind,
            "severity": "high" if tf1 else "medium",
            "change": "derived",
            "summary": summary,
            "decision_id": None,
            "trigger_id": pid,
            "topic": f"person:{pid}/areas",
            "subject_id": pid,
            "subject_title": p["name"],
            "product_id": flagged[0],
            "from_value": None,
            "to_value": None,
            "from_ms": None,
            "ts_ms": ts,
            "ts_iso": datetime.fromtimestamp(ts / 1000, UTC).isoformat().replace("+00:00", "Z"),
            "artifact_id": None,
            "quote": None,
            "actor_id": pid,
            "actor_name": p["name"],
            "actor_role": p["role"],
            "reason": "truck factor "
            + ", ".join(f"{k} {v}" for k, v in sorted(p["flagged"].items())),
            "radius_size": radius["size"],
            "radius_people": len(radius["people"]),
            "radius_companies": [],
            "observed_ms": now_ms,
            "observed_iso": now_iso,
        }
        out.append((alert, radius))
    return out


def list_alerts(
    status: str | None = None,
    product_id: str | None = None,
    *,
    graph: str | None = None,
    alert_id: str | None = None,
    artifact_id: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """The Inbox's alerts, newest change first (the newest `limit`). `alert_id` and
    `artifact_id` find one alert, or an artifact's alerts, at any age (D098)."""
    params = {
        "status": status,
        "product_id": product_id,
        "alert_id": alert_id,
        "artifact_id": artifact_id,
        "limit": limit,
    }
    out = []
    for r in db.run("list_alerts", params, graph=graph).rows:
        r["notifies"] = [p for p in r["notifies"] if p["id"]]
        out.append(r)
    return out


def alert_actions(alert_id: str, *, graph: str | None = None) -> list[dict[str, Any]]:
    """The drafted actions for one alert (11.3)."""
    return db.run("alert_actions", {"alert_id": alert_id, "limit": 50}, graph=graph).rows
