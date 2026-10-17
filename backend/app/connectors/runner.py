"""One sync round for every connected tool (17.2, D074): new items in, approved actions out.

`sync` asks each connector for what changed since its cursor, feeds every new or newer item
to the live path (S1-S8 + Sentinel on the demo graph), then hands the approved, unsent Actions
to the connectors until one delivers each. The cursor and the version seen per item live in
data/live/<graph>/connectors.json, next to the live artifacts, so `lore demo reset` clears both.
"""

import json
import time
from typing import Any

from app import db
from app.connectors.base import Connector, ConnectorError, labeled
from app.sentinel import live

STATE = "connectors.json"


def load_state(graph: str) -> dict[str, Any]:
    path = live.live_dir(graph) / STATE
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def save_state(graph: str, state: dict[str, Any]) -> None:
    folder = live.live_dir(graph)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / STATE).write_text(json.dumps(state, indent=1), encoding="utf-8", newline="\n")


def sync(connectors: list[Connector], graph: str | None = None) -> dict[str, Any]:
    """One round: ingest what changed, then deliver approved actions. Fails loudly per item:
    an item the live path refuses is reported, never silently skipped."""
    graph = live.demo_graph(graph)
    state = load_state(graph)
    report: dict[str, Any] = {"ingested": [], "refused": [], "sent": [], "unsent": []}
    for c in connectors:
        mine = state.setdefault(c.name, {"since_ms": 0, "seen": {}})
        items = c.fetch(mine["since_ms"])
        items.sort(key=lambda i: (i.artifact.ts_ms or 0, i.artifact.id))
        for item in items:
            a = item.artifact
            if mine["seen"].get(a.id, -1) >= item.updated_ms:
                continue
            if not labeled(a.text):  # rule 5: the sandboxes hold labeled synthetic items only
                report["refused"].append({"id": a.id, "why": "not labeled [synthetic demo]"})
                mine["seen"][a.id] = item.updated_ms
                continue
            try:
                again = a.id in mine["seen"]
                out = live.ingest_artifact(a, graph, events=item.events, replace=again)
            except live.LiveError as e:
                report["refused"].append({"id": a.id, "why": str(e)})
            else:
                report["ingested"].append(
                    {
                        "id": a.id,
                        "source": c.name,
                        "alerts": [x["id"] for x in out["alerts"]],
                        "freshness_lag_ms": out["freshness_lag_ms"],
                    }
                )
            mine["seen"][a.id] = item.updated_ms
            mine["since_ms"] = max(mine["since_ms"], item.updated_ms)
        save_state(graph, state)
    report["sent"], report["unsent"] = deliver(connectors, graph)
    return report


def deliver(connectors: list[Connector], graph: str) -> tuple[list[dict], list[dict]]:
    """Each approved, unsent Action to the first connector that takes it; record where."""
    sent, unsent = [], []
    pending = db.run("actions_to_send", {"limit": 100}, graph=graph).rows
    for action in pending:
        errors = []
        for c in connectors:
            try:
                ref = c.post(action)
            except ConnectorError as e:  # reported, and the next connector may still take it
                errors.append(f"{c.name}: {e}")
                continue
            if ref:
                row = {"id": action["id"], "ref": ref, "ms": int(time.time() * 1000), "via": c.name}
                db.run("mark_action_sent", {"rows": [row]}, graph=graph, readonly=False)
                sent.append(row)
                break
        else:
            why = "; ".join(errors) or "no connected tool holds its target"
            unsent.append({"id": action["id"], "kind": action["kind"], "why": why})
    return sent, unsent
