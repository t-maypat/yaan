"""The access audit log (22.5): who was shown what, when, and through which door.

Each answer (web, MCP, Slack), each MCP tool call and each API read of items (an artifact, an
entity, a decision's lineage or belief, alerts) appends one JSON line to
data/audit/<graph>/<YYYY-MM>.jsonl: the time, who (the signed-in person; nobody in the demo),
`via` (web, mcp, slack-dm, slack-channel, api), what (answer, a tool's name, a route), and the
ids of the items in what they were shown, after permissions.

It names items and never copies them: no question, no words. So forgetting an item needs
nothing here (its id names a tombstone from then on), and erasing a person drops the lines
that name them as the viewer (`scrub`, from forget.erase_person). Whole months older than
LORE_AUDIT_MONTHS are deleted by the retention job. Yaan only appends; admins read it with
`yaan audit` or on the Admin page. `yaan backup` carries it (22.2).
"""

import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app import filestate
from app.config import get_settings

MAX_IDS = 500  # per line: an answer reads at most a few hundred items; the rest are counted


def folder(graph: str) -> Path:
    s = get_settings()
    return (s.audit_dir or s.data_dir / "audit") / graph


def record(
    graph: str, what: str, viewer: str | None, via: str, ids: Any = (), **fields: Any
) -> None:
    """Append one line. `ids`: the item ids shown (any iterable; sorted, capped)."""
    now = time.time()
    shown = sorted({i for i in ids if isinstance(i, str)})
    line: dict[str, Any] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "ts_ms": int(now * 1000),
        "viewer": viewer,
        "via": via,
        "what": what,
        "ids": shown[:MAX_IDS],
    }
    if len(shown) > MAX_IDS:
        line["ids_more"] = len(shown) - MAX_IDS
    line.update({k: v for k, v in fields.items() if v is not None})
    path = folder(graph) / f"{time.strftime('%Y-%m', time.gmtime(now))}.jsonl"
    with filestate.locked(path):  # the API's threads and the worker (Slack answers) both write
        with path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")


def safely(write: Callable[[], None]) -> None:
    """Write an audit line; a failure is logged and counted (`yaan_audit_failures_total`),
    never the reader's error: what they were shown has already been shown."""
    from app import obs

    try:
        write()
    except Exception as e:
        obs.METRICS.inc("yaan_audit_failures_total")
        obs.event("audit", f"audit line not written: {type(e).__name__}: {e}", logging.ERROR)


def answered(graph: str, out: dict[str, Any], viewer: str | None, via: str, **fields) -> None:
    """An answer's line: what its tools returned to the asker (`retrieved`, after the viewer's
    permissions), what it cited, which tools ran, whether it abstained."""
    record(
        graph,
        "answer",
        viewer,
        via,
        out.get("retrieved") or [],
        cited=sorted({c["artifact_id"] for c in out.get("citations") or []}),
        tools=[t["tool"] for t in out.get("trace") or []],
        abstained=bool(out.get("abstain")),
        hidden=out.get("hidden_sources") or None,
        **fields,
    )


def months(graph: str) -> list[Path]:
    return sorted(folder(graph).glob("????-??.jsonl"))


def read(
    graph: str, viewer: str | None = None, item: str | None = None, limit: int = 200
) -> list[dict[str, Any]]:
    """The newest lines first, optionally only one viewer's or those that showed one item."""
    out: list[dict[str, Any]] = []
    for path in reversed(months(graph)):
        lines = path.read_text(encoding="utf-8").splitlines()
        for raw in reversed(lines):
            if not raw.strip():
                continue
            row = json.loads(raw)
            if viewer and row.get("viewer") != viewer:
                continue
            if item and item not in row.get("ids", []) and item not in row.get("cited", []):
                continue
            out.append(row)
            if len(out) >= limit:
                return out
    return out


def scrub(graph: str, person: str) -> int:
    """Erasure: drop every line where `person` was the viewer, and their id from the items
    others were shown (an /entity read names a person). Returns how many lines went."""
    gone = 0
    for path in months(graph):
        with filestate.locked(path):
            rows = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln]
            keep = [r for r in rows if r.get("viewer") != person]
            named = [r for r in keep if person in r.get("ids", [])]
            for r in named:
                r["ids"] = [i for i in r["ids"] if i != person]
            if len(keep) < len(rows) or named:
                gone += len(rows) - len(keep)
                text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in keep)
                filestate.write_text(path, text)
    return gone


def prune(graph: str, keep_months: int | None = None, now: float | None = None) -> list[str]:
    """Delete whole months older than the last `keep_months` (this month included)."""
    keep_months = keep_months or get_settings().lore_audit_months
    t = time.gmtime(now or time.time())
    index = t.tm_year * 12 + t.tm_mon - 1
    oldest = index - keep_months + 1
    gone = []
    for path in months(graph):
        year, month = (int(x) for x in path.stem.split("-"))
        if year * 12 + month - 1 < oldest:
            path.unlink()
            gone.append(path.name)
    return gone
