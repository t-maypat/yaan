"""Verified events in the graph: the derived layer's system of record (D101).

Every event that passed S6 (a verbatim quote in its message) becomes an `:Event` node, from the
batch extraction (`yaan ingest --stages fold` loads data/extract) or the live path. Events are
never deleted: a newer version of an artifact that no longer states one closes it
(`retracted_ms`). Claims, decisions and issues are a deterministic projection of the live
events (fold.py), so they can be rebuilt from the graph alone, and the belief at any earlier
moment replayed by folding the events Yaan knew then (`read(..., known_at_ms=T)`).

`observed_ms` is when Yaan first learned an event (set once). Before D101 the fold stamped
every claim and decision with the time of the last rebuild, so the transaction-time axis said
nothing.
"""

import hashlib
import json
import time
from typing import Any

from app import db

FIELDS = (
    "type",
    "msg",
    "quote",
    "issue",
    "component",
    "customer",
    "person",
    "pr",
    "reason",
    "decisive",
    "confidence",
    "unit_id",
    "product",
    "channel",
    "moved",
    "artifact_id",
    "ts_ms",
    "ts_iso",
    "speaker",
    "start",
    "end",
)
READ_CAP = 200_000  # events in one product
RESOLVED = ("res_issue", "res_how", "res_v")  # the last pass's resolution, stored on it (D129)


def event_id(e: dict[str, Any]) -> str:
    """Stable across re-extraction of the same words: artifact, type and the quoted span."""
    key = f"{e['artifact_id']}|{e['type']}|{e.get('start')}|{e.get('end')}|{e['quote']}"
    return f"evt:{hashlib.sha1(key.encode('utf-8')).hexdigest()[:16]}"


def rows(events: list[dict[str, Any]], observed_ms: int) -> list[dict[str, Any]]:
    return [
        {"id": event_id(e), "observed_ms": observed_ms, **{k: e.get(k) for k in FIELDS}}
        for e in events
    ]


def load(events: list[dict[str, Any]], graph: str | None, observed_ms: int | None = None) -> dict:
    """MERGE events by id: a known event keeps its first observed_ms; a retracted one that is
    stated again is live again."""
    observed_ms = observed_ms if observed_ms is not None else int(time.time() * 1000)
    if not events:
        return {"rows": 0, **dict.fromkeys(db.STAT_FIELDS, 0)}
    result = db.run(
        "upsert_events", {"rows": rows(events, observed_ms)}, graph=graph, readonly=False
    )
    return {"rows": len(events), **result.stats}


def retract(artifact_id: str, keep: list[str], graph: str | None, now_ms: int | None = None) -> int:
    """Close the artifact's live events that aren't in `keep` (a newer version of it no longer
    states them). Returns how many were closed."""
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    params = {"artifact_id": artifact_id, "keep": keep, "now_ms": now_ms}
    return db.run("retract_events", params, graph=graph, readonly=False).rows[0]["retracted"]


def read(product: str, graph: str | None, known_at_ms: int | None = None) -> list[dict[str, Any]]:
    """The product's events as the fold takes them: the live ones, or the ones Yaan knew at
    `known_at_ms`. Fields the graph didn't store (nulls) come back as None."""
    params = {"product_id": product, "known_at_ms": known_at_ms}
    rows = db.run_all("product_events", params, cap=READ_CAP, graph=graph, timeout_ms=30_000)
    return [event(r["event"]) for r in rows]


def retracted_resolved(product: str, graph: str | None) -> list[dict[str, Any]]:
    """The product's retracted events that still carry the last pass's resolution (D129)."""
    rows = db.run_all(
        "retracted_resolved", {"product_id": product}, cap=READ_CAP, graph=graph, timeout_ms=30_000
    )
    return [event(r["event"]) for r in rows]


def event(props: dict[str, Any] | str) -> dict[str, Any]:
    """An event as the fold takes it, from its properties (or their JSON, A23)."""
    e = json.loads(props) if isinstance(props, str) else dict(props)
    event = {k: e.get(k) for k in FIELDS}
    event["product"] = e.get("product_id")
    event["observed_ms"] = e.get("observed_ms")
    event["id"] = e.get("id")
    event.update({k: e.get(k) for k in RESOLVED})
    return event


def products(graph: str | None) -> list[str]:
    rows = db.run_all("event_products", {}, cap=10_000, graph=graph, timeout_ms=30_000)
    return sorted(r["product_id"] for r in rows if r["product_id"])
