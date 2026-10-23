"""Retention (22.5): how long Yaan keeps what each tool brought, in org mode.

connectors.yaml may say `retention_days: N` at the top (every tool) and under a tool (that
tool; `retention_days: null` keeps it forever). Each day the worker forgets the items older
than that, through `forget` (D125): their words go everywhere, a tombstone keeps each id, and
the decisions are rebuilt without them. So a decision whose every source has expired leaves
the timeline, as when the source tool deletes it: Yaan states nothing it can no longer quote
(D138). Choose a retention longer than the decision history you want to ask about.

Items already older than the retention are refused at the door (runner.sync), so a sweep, an
edit of an old message or a backfill can't bring one back; a backfill reaches no further than
the retention. A run forgets at most PER_RUN items (each forget holds the writer lock while it
refolds); what is left goes on the next round. The audit log's old months go in the same run.
The demo keeps everything: its data is synthetic and the eval reads it.
"""

import time
from typing import Any

from app import audit, db, forget, org
from app.connectors import base

DAY_MS = 86_400_000
EVERY_MS = DAY_MS
PER_RUN = 2_000
KINDS = ("msg", "pr", "doc", "mtg", "chat")  # the id kinds connectors make (base.synthetic_id)
JOB = "retention"


class RetentionError(ValueError):
    pass


def policy(maps: base.Maps | None = None) -> dict[str, float]:
    """{tool: days kept} for every tool with a retention; a tool without one is kept."""
    maps = maps or base.maps()
    default = _days(maps.retention_days, "retention_days")
    out = {}
    for name, conf in maps.sources.items():
        days = _days(conf.get("retention_days", default), f"{name}.retention_days")
        if days is not None:
            out[name] = days
    return out


def _days(value: Any, where: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        raise RetentionError(f"connectors.yaml: {where} must be a number of days above 0")
    return float(value)


def cutoff_ms(source: str, now_ms: int | None = None, maps: base.Maps | None = None) -> int | None:
    """Items of `source` older than this are past its retention (org mode), else None."""
    if not org.active():
        return None
    days = policy(maps).get(source)
    if days is None:
        return None
    return (now_ms or int(time.time() * 1000)) - int(days * DAY_MS)


def due(graph: str, now_ms: int | None = None, limit: int = PER_RUN) -> dict[str, list[str]]:
    """{tool: ids past its retention}, oldest first, at most `limit` in all."""
    now_ms = now_ms or int(time.time() * 1000)
    out: dict[str, list[str]] = {}
    left = limit
    for source, days in sorted(policy().items()):
        if left <= 0:
            break
        params = {
            "prefixes": [base.id_prefix(k, source) for k in KINDS],
            "before_ms": now_ms - int(days * DAY_MS),
            "limit": left,
        }
        rows = db.run("retention_due", params, graph=graph, timeout_ms=60_000).rows
        if rows:
            out[source] = [r["id"] for r in rows]
            left -= len(rows)
    return out


def run(graph: str, now_ms: int | None = None, apply: bool = True) -> dict[str, Any]:
    """Forget what is past each tool's retention (a dry run unless `apply`)."""
    if not org.active():
        raise RetentionError("retention runs in org mode only: the demo keeps its data")
    now_ms = now_ms or int(time.time() * 1000)
    rules = policy()
    found = due(graph, now_ms)
    report: dict[str, Any] = {
        "graph": graph,
        "policy_days": rules,
        "due": {s: len(ids) for s, ids in found.items()},
        "more_next_run": sum(len(ids) for ids in found.values()) >= PER_RUN,
    }
    if not apply:
        return report
    report["forgotten"] = {}
    for source, ids in found.items():
        why = f"retention: {source} keeps {rules[source]:g} days"
        receipt = forget.forget(ids, graph, reason=why, now_ms=now_ms)
        report["forgotten"][source] = receipt["artifacts"]
    report["audit_months_deleted"] = audit.prune(graph, now=now_ms / 1000)
    return report


def run_if_due(graph: str, now_ms: int | None = None) -> dict[str, Any] | None:
    """The worker's daily turn (after a round): org mode, a policy, a day since the last full
    run. A run cut at PER_RUN isn't marked done, so the next round goes on."""
    if not org.active():
        return None
    now_ms = now_ms or int(time.time() * 1000)
    if not policy():  # items are kept; the audit log's own months still go (a folder glob)
        months = audit.prune(graph, now=now_ms / 1000)
        return {"graph": graph, "audit_months_deleted": months} if months else None
    rows = db.run("job_state", {"id": JOB}, graph=graph).rows
    done = (rows[0]["done_ms"] if rows else None) or 0
    if now_ms - done < EVERY_MS:
        return None
    report = run(graph, now_ms)
    if not report.get("more_next_run"):
        db.run("mark_job", {"id": JOB, "dirty_ms": None, "done_ms": now_ms}, graph=graph,
               readonly=False)  # fmt: skip
    return report
