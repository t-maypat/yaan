"""Approve or dismiss an alert (11.4): the answer is written back as a decision trace.
Spec: docs/CONTEXT.md §9.5, D056.

A response is itself a Decision, so the next person (or the agent) can ask "what did we do
last time, and who decided?":
- `Decision {origin: 'approved_action' | 'dismissed_action', topic: '<alert id>/response'}`
  that AFFECTS the alert's issue or PR;
- DECIDED_BY the person who responded, EVIDENCED_BY the message that triggered the alert;
- the flagged decision CAUSED it; earlier responses to the same kind of alert in the product
  are PRECEDENT_FOR it.

Approving means "send the drafted follow-ups" for every alert kind, a conflict included: a
conflict's drafts ask the owner to confirm, and only an authorized claim (through the fold)
changes state. Each drafted Action becomes approved (optionally with edited text) or skipped;
a dismissal dismisses them all. Nothing is deleted. A second response to the same alert is
refused, in the database as well as here (set_alert_response matches only an open alert).
"""

import hashlib
import time
from datetime import UTC, datetime
from typing import Any

from app import db, policy
from app.sentinel import engine

VERDICTS = ("approved", "dismissed")


class AlertError(ValueError):
    """The response can't be recorded: unknown alert or person, or the alert isn't open."""


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat().replace("+00:00", "Z")


def respond(
    alert_id: str,
    verdict: str,
    person_id: str,
    *,
    note: str | None = None,
    action_ids: list[str] | None = None,
    edits: dict[str, str] | None = None,
    graph: str | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Record `person_id`'s verdict on an open alert. `action_ids` picks which drafts to send
    (default: all); `edits` maps an action id to the text actually sent."""
    if verdict not in VERDICTS:
        raise AlertError(f"verdict must be one of {VERDICTS}, not {verdict!r}")
    now_ms = now_ms or int(time.time() * 1000)
    rows = db.run("get_alert", {"alert_id": alert_id, "person_id": person_id}, graph=graph).rows
    if not rows:
        raise AlertError(f"no alert {alert_id!r}")
    alert = rows[0]
    if alert["status"] != "open":
        raise AlertError(f"alert {alert_id!r} is {alert['status']}, not open")
    if not alert["person_id"]:
        raise AlertError(f"no person {person_id!r} to record as the one who responded")
    # Who may answer for the team (D100): the people the alert notifies, or a lead. Approving
    # sends drafted text to the team's tools, so it is not open to anyone with an id.
    notified = {
        n["id"]
        for a in engine.list_alerts(graph=graph, alert_id=alert_id, limit=1)
        for n in a["notifies"]
    }
    lead = policy.is_lead(alert["person_role"], alert["person_authority"])
    if person_id not in notified and not lead:
        who = alert["person_name"] or person_id
        raise AlertError(
            f"{who} isn't notified by this alert and isn't a lead: only those may respond to it"
        )
    actions = engine.alert_actions(alert_id, graph=graph)
    known = {a["id"] for a in actions}
    chosen = known if action_ids is None else set(action_ids)
    edits = edits or {}
    if unknown := (chosen | set(edits)) - known:
        raise AlertError(f"actions not drafted for {alert_id!r}: {sorted(unknown)}")
    precedents = db.run(
        "alert_precedents",
        {
            "kind": alert["kind"],
            "product_id": alert["product_id"],
            "alert_id": alert_id,
            "limit": 3,
        },
        graph=graph,
    ).rows

    topic = f"{alert_id}/response"
    decision_id = f"dec:{topic}:{now_ms}"
    sent = sorted(chosen) if verdict == "approved" else []
    claimed = db.run(
        "set_alert_response",
        {
            "rows": [
                {
                    "alert_id": alert_id,
                    "status": verdict,
                    "decided_ms": now_ms,
                    "decided_iso": iso(now_ms),
                    "person_id": person_id,
                    "decision_id": decision_id,
                    "note": note,
                }
            ]
        },
        graph=graph,
        readonly=False,
    ).rows
    if not claimed:  # someone answered between our read and this write
        raise AlertError(f"alert {alert_id!r} was answered by someone else")

    name, title = alert["person_name"], alert["subject_title"]
    if verdict == "approved":
        statement = (
            f"{name} approved the Sentinel's follow-up on the {alert['kind']} of '{title}': "
            f"{len(sent)} of {len(actions)} drafted actions sent."
        )
    else:
        statement = f"{name} dismissed the Sentinel's {alert['kind']} alert on '{title}'" + (
            f": {note}" if note else "."
        )
    decision = {
        "id": decision_id,
        "topic": topic,
        "kind": "alert_response",
        "value": verdict,
        "statement": statement,
        "valid_from_ms": now_ms,
        "valid_to_ms": None,
        "status": "active",
        "confidence": 1.0,
        "origin": "approved_action" if verdict == "approved" else "dismissed_action",
        "ts_inferred": False,
        "observed_ms": now_ms,
    }
    subject = alert["subject_id"]
    writes: dict[str, list[dict[str, Any]]] = {
        "upsert_decisions": [decision],
        "link_decided_by": [{"decision_id": decision_id, "person_id": person_id}],
        "link_affects_issue" if subject.startswith("iss:") else "link_affects": [
            {
                "decision_id": decision_id,
                ("issue_id" if subject.startswith("iss:") else "artifact_id"): subject,
            }
        ]
        if subject.startswith(("iss:", "pr:"))  # a key_person alert is about a person
        else [],
        "link_caused": [{"cause_id": alert["decision_id"], "decision_id": decision_id}]
        if alert["decision_id"]
        else [],
        "link_precedent_for": [
            {"precedent_id": p["id"], "decision_id": decision_id} for p in precedents
        ],
        "set_action_status": [
            {
                "id": a["id"],
                "status": (
                    "dismissed"
                    if verdict == "dismissed"
                    else "approved"
                    if a["id"] in chosen
                    else "skipped"
                ),
                "decided_ms": now_ms,
                "person_id": person_id,
                "draft": edits.get(a["id"]) if verdict == "approved" else None,
            }
            for a in actions
        ],
    }
    if alert["artifact_id"] and alert["quote"]:
        writes["link_evidenced_by"] = [
            {
                "decision_id": decision_id,
                "artifact_id": alert["artifact_id"],
                "field": "text",
                "quote": alert["quote"],
                "quote_hash": hashlib.sha1(alert["quote"].encode("utf-8")).hexdigest()[:12],
                "start": None,
                "end": None,
            }
        ]
    for name_, rows_ in writes.items():
        if rows_:
            db.run(name_, {"rows": rows_}, graph=graph, readonly=False)
    return {
        "alert_id": alert_id,
        "status": verdict,
        "decision": decision,
        "decided_by": {"id": person_id, "name": name, "role": alert["person_role"]},
        "actions_sent": sent,
        "precedents": precedents,
        "cypher": ["get_alert.cypher", "alert_precedents.cypher", "set_alert_response.cypher"],
    }
