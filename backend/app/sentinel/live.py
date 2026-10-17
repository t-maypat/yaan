"""Live ingestion (11.6): one new message -> S1-S8 -> Sentinel, on the demo graph only. D057.

`ingest` takes one Slack message and runs the same pipeline as the batch, scoped to it:
- S1 validate it as an Artifact;
- S2 write it and its AUTHORED / IN_CHANNEL / IN_PRODUCT edges (its channel already exists);
- S3 chunk + embed it;
- S4 link its mentions;
- S5-S6 extract its events (one unit, one cached LLM call) and verify each quote;
- S7-S8 resolve + fold over every product's events plus the live ones, so the new claim
  lands in the right chain with the same rules as the batch;
- the Sentinel judges the result.

It returns the freshness lag: wall-clock time from receiving the message to its alert being
in the graph, per stage. Live artifacts and events are kept in data/live/<graph>/ so the next
live message folds over them too; `reset_demo` copies lore_herb into lore_demo (GRAPH.COPY)
and clears that folder.

Writes go to the demo graph only: `ingest` and `reset_demo` refuse the main graph.
"""

import json
import shutil
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from app import db
from app.config import get_settings
from app.ingest import chunk_embed, extract, fold, linker, resolve, structural
from app.ingest.normalize import Artifact, normalized_dir, parse_ts, read_jsonl
from app.sentinel import engine

SYNTHETIC_PREFIX = "msg:synthetic-"


class LiveError(ValueError):
    """The message can't be ingested live (wrong graph, bad fields, unknown channel...)."""


def demo_graph(graph: str | None) -> str:
    s = get_settings()
    graph = graph or s.lore_demo_graph
    if graph == s.lore_graph:
        raise LiveError(f"live writes go to the demo graph only, never {graph!r}")
    return graph


def live_dir(graph: str) -> Path:
    return get_settings().data_dir / "live" / graph


def reset_demo(graph: str | None = None) -> dict[str, Any]:
    """lore_demo := a copy of lore_herb (nodes, edges, indexes), and no live messages."""
    graph = demo_graph(graph)
    t0 = time.perf_counter()
    writer = db.client(readonly=False)
    if graph in writer.list_graphs():
        writer.select_graph(graph).delete()
    writer.select_graph(get_settings().lore_graph).copy(graph)
    shutil.rmtree(live_dir(graph), ignore_errors=True)
    return {"graph": graph, "copied_from": get_settings().lore_graph, "ms": _ms(t0)}


def artifact(msg: dict[str, Any]) -> Artifact:
    """S1 for one message (an exports/inject/*.json body or a POST /ingest/artifact body)."""
    missing = {"id", "product", "channel_id", "channel_name", "author_eid", "ts_iso", "text"} - set(
        msg
    )
    if missing:
        raise LiveError(f"message lacks {sorted(missing)}")
    if not str(msg["id"]).startswith(SYNTHETIC_PREFIX):
        raise LiveError(f"live message ids must start with {SYNTHETIC_PREFIX!r} (rule 5)")
    ts_ms, ts_iso = parse_ts(str(msg["ts_iso"]).removesuffix("Z"))
    return Artifact(
        id=msg["id"],
        kind="message",
        product_hint=msg["product"],
        channel_id=msg["channel_id"],
        ts_ms=ts_ms,
        ts_iso=ts_iso,
        author_eid=msg["author_eid"],
        text=msg["text"],
        raw_ref=f"synthetic:{msg['id']}",
        meta={"channel_name": msg["channel_name"], "sender": msg["author_eid"], "synthetic": True},
    )


def ingest(msg: dict[str, Any], graph: str | None = None) -> dict[str, Any]:
    """Run one message through S1-S8 and the Sentinel; return the new alerts and the lag."""
    graph = demo_graph(graph)
    received_ms = int(time.time() * 1000)
    t0 = time.perf_counter()
    stages: dict[str, float] = {}

    a = artifact(msg)
    product = a.product_hint
    meta = _metadata()
    if a.author_eid not in meta.employees:
        raise LiveError(f"unknown author {a.author_eid!r}")
    if a.id in injected_ids(graph):
        raise LiveError(f"{a.id!r} is already in {graph!r}: run `lore demo reset` to replay it")
    if not db.run("get_channel", {"id": a.channel_id}, graph=graph).rows:
        raise LiveError(f"channel {a.channel_id!r} is not in {graph!r}: run `lore demo reset`")
    stages["s1_validate"] = _ms(t0)

    t = time.perf_counter()
    rows = structural.product_rows(product, [a], set(meta.employees), received_ms)
    rows["upsert_products"], rows["upsert_channels"] = [], []  # both exist; don't overwrite
    structural.load(rows, graph)
    stages["s2_structural"] = _ms(t)

    t = time.perf_counter()
    chunk_embed.load(chunk_embed.chunk_artifact(a), graph)
    stages["s3_chunk_embed"] = _ms(t)

    t = time.perf_counter()
    link_rows, _ = linker.build_rows(meta, {product: [a]})
    linker.load(link_rows, graph)
    stages["s4_link"] = _ms(t)

    t = time.perf_counter()
    units = extract.units(product, [a])
    kept, dropped, xstats = extract.run(units, workers=1) if units else ([], [], {"cost_usd": 0})
    _append(graph, "artifacts.jsonl", [a.model_dump()])
    _append(graph, "events.jsonl", kept)
    stages["s5_s6_extract_verify"] = _ms(t)

    t = time.perf_counter()
    events, arts = _base_events_and_artifacts()
    live_arts = [Artifact(**r) for r in _read(graph, "artifacts.jsonl")]
    arts = {p: list(v) for p, v in arts.items()}
    for la in live_arts:
        arts.setdefault(la.product_hint, []).append(la)
    res = resolve.resolve(
        events + _read(graph, "events.jsonl"),
        {p: [x for x in v if x.kind == "pr"] for p, v in arts.items()},
    )
    fold_rows, fstats = fold.rebuild(res, fold.Context.build(meta, arts))
    fold.load(fold_rows, graph)
    stages["s7_s8_resolve_fold"] = _ms(t)

    # S9: the fold recreated the issues and decisions, so re-derive what hangs off them
    # (areas, KNOWS, truck factor, similarity precedents); COLLAB and communities are unchanged
    from app.ingest import derive

    t = time.perf_counter()
    derive.expertise(graph)
    derive.precedents(graph)
    derive.uses(graph)
    stages["s9_derive"] = _ms(t)

    t = time.perf_counter()
    sentinel = engine.run(graph)
    sentinel.pop("report")
    stages["sentinel"] = _ms(t)

    alerts = [x for x in engine.list_alerts(graph=graph) if x["artifact_id"] == a.id]
    lag_ms = _ms(t0)
    out = {
        "graph": graph,
        "artifact_id": a.id,
        "received_ms": received_ms,
        "events": kept,
        "dropped": dropped,
        "llm_cost_usd": xstats.get("cost_usd", 0),
        "fold": {k: v for k, v in fstats.items() if k.startswith(("claims_", "authority_"))},
        "sentinel": sentinel,
        "alerts": alerts,
        "freshness_lag_ms": lag_ms,
        "stages_ms": stages,
    }
    log = {k: out[k] for k in ("artifact_id", "freshness_lag_ms", "stages_ms", "llm_cost_usd")}
    _append(graph, "log.jsonl", [log | {"alerts": [x["id"] for x in alerts]}])
    return out


@lru_cache
def _metadata():
    return structural.load_metadata()


@lru_cache
def _base_events_and_artifacts() -> tuple[list[dict[str, Any]], dict[str, list[Artifact]]]:
    """Every product's extracted events and normalized artifacts, as `lore ingest` folds them."""
    have = sorted(p.name for p in extract.out_dir("x").parent.glob("*") if p.name[0] != "_")
    events = [e for p in have for e in extract.read_events(p)]
    arts = {p: read_jsonl(normalized_dir(p) / "artifacts.jsonl") for p in have}
    return events, arts


def injected_ids(graph: str) -> set[str]:
    """Ids of the live messages already in `graph` since its last reset."""
    return {r["id"] for r in _read(graph, "artifacts.jsonl")}


def _read(graph: str, name: str) -> list[dict[str, Any]]:
    path = live_dir(graph) / name
    if not path.is_file():
        return []
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _append(graph: str, name: str, rows: list[dict[str, Any]]) -> None:
    folder = live_dir(graph)
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / name).open("a", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 1)
