"""Live ingestion (11.6): one new message -> S1-S8 -> Sentinel, on the demo graph only. D057.

`ingest` takes one Slack message and runs the same pipeline as the batch, scoped to it:
- S1 validate it as an Artifact;
- S2 write it and its AUTHORED / IN_CHANNEL / IN_PRODUCT edges (its channel already exists);
- S3 chunk + embed it;
- S4 link its mentions;
- S5-S6 extract its events (one unit, one cached LLM call) and verify each quote, before any
  graph write, so a slow model call never holds the writer lock;
- the kept events become `:Event` nodes (D101), and S7-S8 fold the artifact's product from
  the graph's events: only the derived nodes that changed are rewritten;
- the Sentinel judges the result.
Graph writes run under the graph's writer lock (db.writer_lock), shared with the connector loop
and batch ingests in other processes.

It returns the freshness lag: wall-clock time from receiving the message to its alert being
in the graph, per stage. Live artifacts and events are kept in data/live/<graph>/ so the next
live message folds over them too; `reset_demo` copies lore_herb into lore_demo (GRAPH.COPY)
and clears that folder.

Writes go to the demo graph only: `ingest` and `reset_demo` refuse the main graph. In org mode
(LORE_DATA_MODE=org, app/org.py, D097) they go to the org graph instead: no rule-5 id check,
people are registered on first sight, the fold reads only that graph's own live events, and
`reset_demo` refuses, so nothing from HERB ever reaches a team's graph.

`ingest_artifact` (17.2, D074) is the same path for any artifact a connector brings: a message,
a pull request, a document. A connector may pass deterministic `events` (a Jira status change
is a structured fact, so no LLM reads it; each quote is still checked against the text), and
`replace=True` re-ingests a newer version of an artifact already in the graph (a PR that got a
review): its old row and events are swapped out before the fold runs again.
"""

import json
import shutil
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from app import db, llm, org
from app.config import get_settings
from app.ingest import chunk_embed, extract, fold, linker, structural
from app.ingest import events as event_store
from app.ingest.normalize import Artifact, normalized_dir, parse_ts, read_jsonl
from app.sentinel import engine

SYNTHETIC_PREFIX = "msg:synthetic-"
LIVE_KINDS = ("message", "pr", "doc")


class LiveError(ValueError):
    """The message can't be ingested live (wrong graph, bad fields, unknown channel...)."""


def demo_graph(graph: str | None) -> str:
    """The graph live writes go to: the demo graph, or in org mode the org graph."""
    s = get_settings()
    if org.active():
        graph = graph or s.lore_org_graph
        if graph in (s.lore_graph, s.lore_demo_graph):
            raise LiveError(f"org mode writes to your own graph, never the HERB graph {graph!r}")
        return graph
    graph = graph or s.lore_demo_graph
    if graph == s.lore_graph:
        raise LiveError(f"live writes go to the demo graph only, never {graph!r}")
    return graph


def live_dir(graph: str) -> Path:
    return get_settings().data_dir / "live" / graph


def reset_demo(graph: str | None = None) -> dict[str, Any]:
    """lore_demo := a copy of lore_herb (nodes, edges, indexes), and no live messages."""
    if org.active():
        raise LiveError("org mode never copies the HERB demo into your graph: demo reset is off")
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
    if not org.active() and not str(msg["id"]).startswith(SYNTHETIC_PREFIX):
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
        raw_ref=f"{'api' if org.active() else 'synthetic'}:{msg['id']}",
        meta={
            "channel_name": msg["channel_name"],
            "sender": msg["author_eid"],
            "synthetic": not org.active(),
        },
    )


def ingest(msg: dict[str, Any], graph: str | None = None) -> dict[str, Any]:
    """Run one message through S1-S8 and the Sentinel; return the new alerts and the lag."""
    return ingest_artifact(artifact(msg), graph)


def check_synthetic(artifact_id: str) -> None:
    """Rule 5: everything live is a labeled synthetic item, id `<kind>:synthetic-…`."""
    if not artifact_id.partition(":")[2].startswith("synthetic-"):
        raise LiveError(f"live ids must look like '<kind>:synthetic-…' (rule 5): {artifact_id!r}")


def ingest_artifact(
    a: Artifact,
    graph: str | None = None,
    events: list[dict[str, Any]] | None = None,
    replace: bool = False,
) -> dict[str, Any]:
    """Run one artifact (message, PR or doc) through S1-S8 and the Sentinel; return the new
    alerts and the lag. `events` skips the LLM; `replace` swaps in a newer version."""
    graph = demo_graph(graph)
    received_ms = int(time.time() * 1000)
    t0 = time.perf_counter()
    stages: dict[str, float] = {}

    product = a.product_hint
    if org.active():  # a team's own item: register the people it involves, then read the org
        org.ensure_people(graph, org.people_in(a))
        meta = org.metadata(graph)
    else:
        meta = _metadata()
        check_synthetic(a.id)
    if a.kind not in LIVE_KINDS:
        raise LiveError(f"live ingestion takes {LIVE_KINDS}, not {a.kind!r}")
    if a.author_eid not in meta.employees:
        raise LiveError(f"unknown author {a.author_eid!r}")
    if present_ids(graph, [a.id]) and not replace:
        raise LiveError(f"{a.id!r} is already in {graph!r}: run `lore demo reset` to replay it")
    if a.channel_id and not db.run("get_channel", {"id": a.channel_id}, graph=graph).rows:
        raise LiveError(f"channel {a.channel_id!r} is not in {graph!r}: run `lore demo reset`")
    if a.kind == "message" and not a.channel_id:
        raise LiveError("a live message needs its channel")
    stages["s1_validate"] = _ms(t0)

    t = time.perf_counter()
    if events is None:
        units = live_units(a, graph)
        kept, dropped, xstats = (
            extract.run(units, workers=1) if units else ([], [], {"cost_usd": 0})
        )
        # the conversation is context: only what the new message states is new
        kept = [e for e in kept if e["artifact_id"] == a.id]
    else:  # structured facts from the source: no LLM, but every quote must be in the text
        kept = [e for e in events if llm.quote_in(e["quote"], a.text)]
        dropped = [e for e in events if not llm.quote_in(e["quote"], a.text)]
        xstats = {"cost_usd": 0}
    stages["s5_s6_extract_verify"] = _ms(t)

    with db.writer_lock(graph):
        upgrade(graph)
        out = _write(a, graph, product, meta, kept, replace, received_ms, stages)
    out |= {
        "events": kept,
        "dropped": dropped,
        "llm_cost_usd": xstats.get("cost_usd", 0),
        "freshness_lag_ms": _ms(t0),
    }
    log = {k: out[k] for k in ("artifact_id", "freshness_lag_ms", "stages_ms", "llm_cost_usd")}
    _append(graph, "log.jsonl", [log | {"alerts": [x["id"] for x in out["alerts"]]}])
    return out


def _write(a, graph, product, meta, kept, replace, received_ms, stages) -> dict[str, Any]:
    """The graph writes of one live artifact (under the writer lock): S2-S4, its events,
    the fold of its product, S9 and the Sentinel."""
    again = bool(present_ids(graph, [a.id]))
    if again and not replace:  # another writer took it while the model read it
        raise LiveError(f"{a.id!r} is already in {graph!r}: run `lore demo reset` to replay it")
    t = time.perf_counter()
    rows = structural.product_rows(product, [a], set(meta.employees), received_ms)
    rows["upsert_channels"] = []  # the channel exists (checked above); don't overwrite it
    if not org.active():  # HERB's product carries codenames; an org's is created here if new
        rows["upsert_products"] = []
    structural.load(rows, graph)
    stages["s2_structural"] = _ms(t)

    t = time.perf_counter()
    chunk_embed.load(chunk_embed.chunk_artifact(a), graph)
    stages["s3_chunk_embed"] = _ms(t)

    t = time.perf_counter()
    # linked against everything already loaded (HERB's and earlier live artifacts): a url in
    # the text finds its PR, doc or link, and "Revert PR# N" finds N, whichever came first
    known = [
        *_base(graph)[1].get(product, []),
        *(Artifact(**r) for r in _read(graph, "artifacts.jsonl") if r["id"] != a.id),
    ]
    link_rows, _ = linker.build_rows(meta, {product: [a]}, context={product: known})
    linker.load(link_rows, graph)
    stages["s4_link"] = _ms(t)

    t = time.perf_counter()
    # the events become graph facts; a newer version of the artifact closes the ones it no
    # longer states (never deletes them). The artifact log is only the linker's context now.
    event_store.load(kept, graph, observed_ms=received_ms)
    if again:
        event_store.retract(a.id, [event_store.event_id(e) for e in kept], graph, received_ms)
        _rewrite(graph, "artifacts.jsonl", lambda r: r["id"] != a.id)
    _append(graph, "artifacts.jsonl", [a.model_dump()])
    folded = fold.fold_product(product, graph)
    fstats = folded["stats"]
    stages["s7_s8_resolve_fold"] = _ms(t)

    # S9: re-derive what hangs off the issues and decisions (areas, KNOWS, truck factor,
    # similarity precedents); COLLAB and communities are unchanged. Still whole-graph: the
    # next step for scale (D101).
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

    return {
        "graph": graph,
        "artifact_id": a.id,
        "received_ms": received_ms,
        "fold": {k: v for k, v in fstats.items() if k.startswith(("claims_", "authority_"))},
        "fold_write": folded["write"],
        "sentinel": sentinel,
        "alerts": engine.list_alerts(graph=graph, artifact_id=a.id),
        "stages_ms": stages,
    }


CONTEXT_MESSAGES = 8  # earlier messages a live message is read with
CONTEXT_WINDOW_MS = 7 * 86_400_000  # within the week before it


def conversation(a: Artifact, graph: str) -> list[Artifact]:
    """The messages before `a` in its channel, oldest first (D102). The batch extraction reads
    whole conversations, so "the PR" and "that bug" resolve to what was said earlier; a live
    message read alone could not, and org mode, which runs only the live path, extracted less
    than the evaluated batch did."""
    if a.kind != "message" or not a.channel_id or a.ts_ms is None:
        return []
    params = {
        "channel_id": a.channel_id,
        "before_ms": a.ts_ms,
        "since_ms": a.ts_ms - CONTEXT_WINDOW_MS,
        "limit": CONTEXT_MESSAGES,
    }
    rows = db.run("channel_context", params, graph=graph).rows
    return [
        Artifact(
            id=r["id"],
            kind="message",
            product_hint=a.product_hint,
            channel_id=a.channel_id,
            ts_ms=r["ts_ms"],
            ts_iso=r["ts_iso"],
            author_eid=r["author"],
            text=r["text"] or "",
            raw_ref=r["id"],
            system=bool(r["system"]),
            meta={"channel_name": a.meta.get("channel_name")},
        )
        for r in reversed(rows)
        if r["id"] != a.id
    ]


def live_units(a: Artifact, graph: str) -> list[extract.Unit]:
    """One extraction unit: the new message after its conversation, oldest messages dropped
    first to stay within extract.UNIT_CHARS. None for what extraction doesn't read (PRs,
    docs, system messages, planning channels)."""
    if a.kind != "message" or a.system or extract.channel_kind(a) not in extract.CHANNEL_KINDS:
        return []
    context = [m for m in conversation(a, graph) if not m.system]
    budget = extract.UNIT_CHARS - len(a.text)
    while context and sum(len(m.text) + 20 for m in context) > budget:
        context.pop(0)
    channel = a.meta.get("channel_name") or a.channel_id
    unit_id = f"unit:{a.product_hint}:{channel}:live:{a.id}"
    return [extract.Unit(unit_id, a.product_hint, channel, (*context, a))]


def upgrade(graph: str) -> int:
    """A graph whose derived layer predates D101 has no `:Event` nodes: load the events its
    claims came from (HERB's extraction in the demo, the old live log in org mode), rewrite
    its derived layer once, and fold every product. Returns how many events were loaded."""
    if not db.run("legacy_derived", {}, graph=graph).rows[0]["n"]:
        return 0
    from app import schema

    schema.apply(graph)  # the Event and product_id indexes, before the bulk MERGE
    old = [*_base(graph)[0], *_read(graph, "events.jsonl")]
    event_store.load(old, graph)
    fold.migrate_legacy(graph)
    for product in event_store.products(graph):
        fold.fold_product(product, graph)
    return len(old)


@lru_cache
def _metadata():
    return structural.load_metadata()


def _base(graph: str) -> tuple[list[dict[str, Any]], dict[str, list[Artifact]]]:
    """What the live fold folds over besides the graph's own live items: HERB's batch data in
    demo mode, nothing in org mode (a team's graph holds only its own events)."""
    return ([], {}) if org.active() else _base_events_and_artifacts()


def _base_events_and_artifacts() -> tuple[list[dict[str, Any]], dict[str, list[Artifact]]]:
    """Every product's extracted events and normalized artifacts, as `lore ingest` folds them.
    Read once per version of the files: a batch re-extraction while the API runs is picked up
    on the next live message instead of folding over stale events (D098)."""
    have = sorted(p.name for p in extract.out_dir("x").parent.glob("*") if p.name[0] != "_")
    files = [extract.out_dir(p) / "events.jsonl" for p in have]
    files += [normalized_dir(p) / "artifacts.jsonl" for p in have]
    return _load_base(tuple((str(f), f.stat().st_mtime_ns if f.is_file() else 0) for f in files))


@lru_cache(maxsize=2)
def _load_base(version: tuple) -> tuple[list[dict[str, Any]], dict[str, list[Artifact]]]:
    have = sorted(p.name for p in extract.out_dir("x").parent.glob("*") if p.name[0] != "_")
    events = [e for p in have for e in extract.read_events(p)]
    arts = {p: read_jsonl(normalized_dir(p) / "artifacts.jsonl") for p in have}
    return events, arts


def present_ids(graph: str, ids: list[str]) -> set[str]:
    """Which of `ids` are artifacts in `graph`: the graph says, not a log file (D101)."""
    if not ids:
        return set()
    rows = db.run("artifacts_present", {"ids": ids, "limit": len(ids)}, graph=graph).rows
    return {r["id"] for r in rows}


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


def _rewrite(graph: str, name: str, keep) -> None:
    rows = [r for r in _read(graph, name) if keep(r)]
    path = live_dir(graph) / name
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 1)
