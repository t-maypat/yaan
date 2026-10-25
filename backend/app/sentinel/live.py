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
(YAAN_DATA_MODE=org, app/org.py, D097) they go to the org graph instead: no rule-5 id check,
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

from app import db, forget, llm, org
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
        graph = graph or s.yaan_org_graph
        if graph in (s.yaan_graph, s.yaan_demo_graph):
            raise LiveError(f"org mode writes to your own graph, never the HERB graph {graph!r}")
        return graph
    graph = graph or s.yaan_demo_graph
    if graph == s.yaan_graph:
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
    writer.select_graph(get_settings().yaan_graph).copy(graph)
    shutil.rmtree(live_dir(graph), ignore_errors=True)
    fold.forget_memo(graph)  # a new derived layer: the next fold writes in full (D142)
    return {"graph": graph, "copied_from": get_settings().yaan_graph, "ms": _ms(t0)}


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
    (out,) = ingest_batch([(a, events, replace)], graph)
    if isinstance(out, Exception):
        raise out
    return out


Batch = list[tuple[Artifact, list[dict[str, Any]] | None, bool]]


def ingest_batch(
    items: Batch, graph: str | None = None, workers: int = 1
) -> list[dict[str, Any] | Exception]:
    """Many artifacts, one graph pass (D112): each is checked and extracted on its own (S1,
    S5-S6, outside the writer lock), then, under one lock, each one's S2-S4 and events, ONE
    fold per product they touch and ONE Sentinel pass over every chain those folds changed.
    A connector round's cost no longer grows with the number of items in it. Returns, in
    order, each item's result or the exception that stopped it; an exception in the shared
    fold or Sentinel is every written item's (the runner retries them). `workers`: items
    extracted at once (a backfill); the spend cap holds across them (D098)."""
    from concurrent.futures import ThreadPoolExecutor

    graph = demo_graph(graph)
    artifacts = [a for a, _, _ in items]

    def prepare(n: int) -> dict[str, Any] | Exception:
        a, events, replace = items[n]
        try:  # an item's context: the batch's items before it, whatever happens to them
            return _prepare(a, graph, events, replace, artifacts[:n])
        except Exception as e:  # this item only
            return e

    if workers > 1 and len(items) > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            prepared = list(pool.map(prepare, range(len(items))))
    else:
        prepared = [prepare(n) for n in range(len(items))]
    ready = [p for p in prepared if not isinstance(p, Exception)]
    if ready:
        with db.writer_lock(graph):
            upgrade(graph)
            _write_batch(ready, graph)
    out: list[dict[str, Any] | Exception] = []
    for p in prepared:
        if isinstance(p, Exception) or isinstance(p.get("error"), Exception):
            out.append(p if isinstance(p, Exception) else p["error"])
            continue
        result = p["result"] | {
            "events": p["kept"],
            "dropped": p["dropped"],
            "llm_cost_usd": p["xstats"].get("cost_usd", 0),
            "freshness_lag_ms": _ms(p["t0"]),
        }
        keys = ("artifact_id", "freshness_lag_ms", "stages_ms", "llm_cost_usd")
        log = {k: result[k] for k in keys} | {"alerts": [x["id"] for x in result["alerts"]]}
        _append(graph, "log.jsonl", [log])
        out.append(result)
    return out


def _prepare(
    a: Artifact,
    graph: str,
    events: list[dict[str, Any]] | None,
    replace: bool,
    earlier: list[Artifact],
) -> dict[str, Any]:
    """S1 and S5-S6 for one artifact, before any graph write. `earlier`: the batch's items
    before it, read as conversation context like messages already in the graph."""
    received_ms = int(time.time() * 1000)
    t0 = time.perf_counter()
    stages: dict[str, float] = {}
    # forgotten, or by an erased person: never in again, and nobody registered for it (D125)
    if why := forget.blocked([a], graph).get(a.id):
        raise LiveError(f"{a.id!r} is not taken: {why}")
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
        raise LiveError(f"{a.id!r} is already in {graph!r}: run `yaan demo reset` to replay it")
    a, found = with_channel_kind(a, graph)
    if a.channel_id and not found:
        raise LiveError(f"channel {a.channel_id!r} is not in {graph!r}: run `yaan demo reset`")
    if a.kind == "message" and not a.channel_id:
        raise LiveError("a live message needs its channel")
    stages["s1_validate"] = _ms(t0)

    t = time.perf_counter()
    if events is None:
        units = live_units(a, graph, earlier)
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
    return {
        "a": a,
        "meta": meta,
        "kept": kept,
        "dropped": dropped,
        "xstats": xstats,
        "replace": replace,
        "received_ms": received_ms,
        "t0": t0,
        "stages": stages,
    }


def _write_batch(ready: list[dict[str, Any]], graph: str) -> None:
    """The graph writes of a batch (under the writer lock): each item's S2-S4 and events,
    then one fold per product and one Sentinel pass. Sets each item's `result` or `error`."""
    written = []
    for p in ready:
        try:
            _write_one(p, graph)
            written.append(p)
        except Exception as e:  # this item only: the others still go in
            p["error"] = e
    if not written:
        return
    t = time.perf_counter()
    folds = {}
    try:
        for product in sorted({p["a"].product_hint for p in written}):
            folds[product] = fold.fold_product(product, graph)
        fold_ms = _ms(t)
        # S9 (areas, KNOWS, truck factor, precedents, USES) reads the whole graph, and
        # minutes-old expertise is fine: marked due here, rebuilt by derive_if_due at most
        # every DERIVE_EVERY_S, not on every message (D112). The Sentinel judges the chains
        # these folds changed.
        mark_due(graph)  # now, under the lock
        t = time.perf_counter()
        topics = sorted({x for f in folds.values() for x in f["write"]["topics"]})
        sentinel = engine.run(graph, topics=topics)
        sentinel.pop("report")
        sentinel_ms = _ms(t)
    except Exception as e:  # the shared part failed: every written item retries
        for p in written:
            p["error"] = e
        return
    for p in written:
        a, folded = p["a"], folds[p["a"].product_hint]
        p["stages"] |= {"s7_s8_resolve_fold": fold_ms, "sentinel": sentinel_ms}
        p["result"] = {
            "graph": graph,
            "artifact_id": a.id,
            "received_ms": p["received_ms"],
            "fold": {
                k: v for k, v in folded["stats"].items() if k.startswith(("claims_", "authority_"))
            },
            "fold_write": folded["write"],
            "sentinel": sentinel,
            "alerts": engine.list_alerts(graph=graph, artifact_id=a.id),
            "stages_ms": p["stages"],
            "batch": len(ready),
        }


def _write_one(p: dict[str, Any], graph: str) -> None:
    """One artifact's S2-S4 and events."""
    a, meta, stages, received_ms = p["a"], p["meta"], p["stages"], p["received_ms"]
    product = a.product_hint
    again = bool(present_ids(graph, [a.id]))
    if again and not p["replace"]:  # another writer took it while the model read it
        raise LiveError(f"{a.id!r} is already in {graph!r}: run `yaan demo reset` to replay it")
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
    # linked against what is already loaded, looked up in the graph for this artifact (D113):
    # a url in the text finds its PR, doc or link, "Revert PR# N" finds N, a codename its
    # product, whichever came first
    known, aliases = link_context(a, graph)
    link_rows, _ = linker.build_rows(meta, {product: [a]}, {product: known}, aliases)
    linker.load(link_rows, graph)
    stages["s4_link"] = _ms(t)

    # the events become graph facts; a newer version of the artifact closes the ones it no
    # longer states (never deletes them)
    event_store.load(p["kept"], graph, observed_ms=received_ms)
    if again:
        event_store.retract(a.id, [event_store.event_id(e) for e in p["kept"]], graph, received_ms)


def link_context(a: Artifact, graph: str) -> tuple[list[Artifact], dict[str, list[str]]]:
    """What the linker needs to know about what is already loaded, for one new artifact
    (D113): the PRs, docs and links its urls name, the PR a "Revert PR# N" title undoes, and
    the product's codenames. Indexed lookups for this artifact only, where the live path used
    to read every live artifact so far from a log that only grew."""
    product = a.product_hint
    text = "\n".join(t for t in (a.title, a.text) if t)
    found = linker.urls(text)
    variants = sorted({v for u in found for v in (u, u + "/")})
    rows = []
    if variants:
        rows += db.run("link_context_urls", {"urls": variants, "limit": 200}, graph=graph).rows
    if a.kind == "pr" and (m := linker.REVERT.match(a.title or "")) and a.meta.get("repo"):
        refs = [{"repo": a.meta["repo"], "number": int(m.group(1))}]
        prs = db.run("link_context_prs", {"refs": refs, "limit": 5}, graph=graph).rows
        rows += [r | {"kind": "pr"} for r in prs]
    known = [
        Artifact(
            id=r["id"],
            kind=r["kind"],
            product_hint=product,
            ts_ms=None,
            ts_iso=None,
            text="",
            url=r["url"],
            raw_ref=r["id"],
            meta={"repo": r["repo"], "number": r["number"]} if r["kind"] == "pr" else {},
        )
        for r in rows
        if r["id"] != a.id
    ]
    alias_rows = db.run("product_aliases", {"product_id": product}, graph=graph).rows
    aliases = {product: list((alias_rows[0]["aliases"] if alias_rows else None) or [])}
    return known, aliases


def with_channel_kind(a: Artifact, graph: str) -> tuple[Artifact, bool]:
    """`a` carrying its channel's declared kind (what extraction reads from it, D116), and
    whether the channel is in the graph at all."""
    if not a.channel_id:
        return a, False
    rows = db.run("get_channel", {"id": a.channel_id}, graph=graph).rows
    if rows and rows[0]["kind"]:
        a = a.model_copy(update={"meta": {**a.meta, "channel_kind": rows[0]["kind"]}})
    return a, bool(rows)


EXPECTED_OUTPUT_TOKENS = 130  # an extraction's typical answer (D102: 109-137 per unit)


def estimate_extraction(items: list[Artifact], graph: str | None = None) -> dict[str, Any]:
    """What extracting `items` would cost, before anything is spent (a backfill's dry run,
    D114): how many need the model, and the worst case (every output token the call may use)
    and the expected cost (a typical answer), from each one's real extraction unit."""
    graph = demo_graph(graph)
    model = get_settings().yaan_extract_model
    need = worst = expected = 0.0
    chars = 0
    for n, a in enumerate(items):
        for unit in live_units(with_channel_kind(a, graph)[0], graph, items[:n]):
            prompt = len(extract.SYSTEM) + len(unit.render())
            need += 1
            chars += prompt
            worst += llm.worst_case(model, prompt, 8000)
            tokens = int(prompt / llm.CHARS_PER_TOKEN_WORST)
            expected += llm.cost_usd(model, tokens, EXPECTED_OUTPUT_TOKENS)
    return {
        "items": len(items),
        "need_the_model": int(need),
        "prompt_chars": chars,
        "expected_usd": round(expected, 4),
        "worst_case_usd": round(worst, 4),
        "model": model,
    }


DERIVE_EVERY_S = 300  # the derived layer is at most this stale after a live write (D112)
ALGORITHMS_EVERY_S = 3600  # org mode's collaboration graph: who works with whom moves slowly
JOBS = ("derive", "algorithms")


def mark_due(graph: str, now_ms: int | None = None) -> None:
    """A write changed the graph: the periodic jobs are due at their next turn."""
    now_ms = now_ms or int(time.time() * 1000)
    for job in JOBS:
        due = {"id": job, "dirty_ms": now_ms, "done_ms": None}
        db.run("mark_job", due, graph=graph, readonly=False)


def algorithms_if_due(
    graph: str | None = None, every_s: float = ALGORITHMS_EVERY_S, force: bool = False
):
    """Org mode's graph algorithms (F4, D127), which the live path never ran, so the README's
    PageRank, communities and teams worked only in the demo: WORKS_ON from people.yaml's teams,
    COLLAB from real conversations in what everyone may read, PageRank, label-propagation
    communities and betweenness, then the full Sentinel (outside-group alerts, team impact).
    Hourly at most, after a write made it due. The demo's come from `yaan ingest`, as before.
    Returns what ran, or None."""
    from app.ingest import derive

    graph = demo_graph(graph)
    if not org.active():
        return None
    rows = db.run("job_state", {"id": "algorithms"}, graph=graph).rows
    dirty = rows[0]["dirty_ms"] if rows else None
    done = (rows[0]["done_ms"] if rows else None) or 0
    now = int(time.time() * 1000)
    if not force and (dirty is None or dirty <= done or now - done < every_s * 1000):
        return None
    t0 = time.perf_counter()
    with db.writer_lock(graph):
        derive.works_on(graph)
        collab = derive.collab(graph)
        groups = derive.communities(graph)
        sentinel = engine.run(graph)
        db.run("mark_job", {"id": "algorithms", "dirty_ms": None, "done_ms": now}, graph=graph,
               readonly=False)  # fmt: skip
    sentinel.pop("report")
    return {
        "graph": graph,
        "ms": _ms(t0),
        "pairs": collab["pairs"],
        "communities": groups["communities"],
        "sentinel": sentinel,
    }


def derive_if_due(graph: str | None = None, every_s: float = DERIVE_EVERY_S, force: bool = False):
    """Rebuild the derived layer (S9) and run the full Sentinel (key-person and bus-factor
    alerts, withdrawals) when a live write made it due and the last run is `every_s` old.
    Called after every connector round and after the API's live writes, so per-message cost
    no longer grows with the whole graph (D112). Returns what ran, or None."""
    from app.ingest import derive

    graph = demo_graph(graph)
    rows = db.run("job_state", {"id": "derive"}, graph=graph).rows
    dirty = rows[0]["dirty_ms"] if rows else None
    done = (rows[0]["done_ms"] if rows else None) or 0
    now = int(time.time() * 1000)
    if not force and (dirty is None or dirty <= done or now - done < every_s * 1000):
        return None
    t0 = time.perf_counter()
    with db.writer_lock(graph):
        derive.expertise(graph)
        derive.precedents(graph)
        derive.uses(graph)
        sentinel = engine.run(graph)
        done_row = {"id": "derive", "dirty_ms": None, "done_ms": now}
        db.run("mark_job", done_row, graph=graph, readonly=False)
    sentinel.pop("report")
    return {"graph": graph, "ms": _ms(t0), "sentinel": sentinel}


CONTEXT_MESSAGES = 8  # earlier messages a live message is read with
CONTEXT_WINDOW_MS = 7 * 86_400_000  # within the week before it


def conversation(a: Artifact, graph: str, earlier: list[Artifact] | None = None) -> list[Artifact]:
    """The messages before `a` in its conversation, oldest first (D102). The batch extraction
    reads whole conversations, so "the PR" and "that bug" resolve to what was said earlier; a
    live message read alone could not, and org mode, which runs only the live path, extracted
    less than the evaluated batch did. A thread reply's conversation is its thread: the root,
    however old, then the replies before it ("fixed it" means nothing without the root); a
    top-level message's is the channel's top-level messages (D126). `earlier`: a live batch's
    items before `a`, not yet in the graph (D112)."""
    if a.kind != "message" or not a.channel_id or a.ts_ms is None:
        return []
    thread = a.meta.get("thread_id")
    params = {
        "channel_id": a.channel_id,
        "before_ms": a.ts_ms,
        "since_ms": a.ts_ms - CONTEXT_WINDOW_MS,
        "limit": CONTEXT_MESSAGES,
    }
    if thread:
        rows = db.run("thread_context", params | {"thread_id": thread}, graph=graph).rows
        rows += db.run("message_context", {"id": thread}, graph=graph).rows  # the root
    else:
        rows = db.run("channel_context", params, graph=graph).rows
    rows.sort(key=lambda r: (r["ts_ms"] or 0, r["id"]), reverse=True)
    found = [
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
    pending = [
        m
        for m in earlier or []
        if m.kind == "message"
        and m.channel_id == a.channel_id
        and m.ts_ms is not None
        and m.ts_ms < a.ts_ms
        and (
            (m.id == thread or m.meta.get("thread_id") == thread)
            if thread
            else not m.meta.get("thread_id") and params["since_ms"] <= m.ts_ms
        )
    ]
    merged = {m.id: m for m in [*found, *pending] if m.id != a.id}
    ordered = sorted(merged.values(), key=lambda m: (m.ts_ms or 0, m.id))
    root = [m for m in ordered if m.id == thread]
    rest = [m for m in ordered if m.id != thread]
    return root + rest[-(CONTEXT_MESSAGES - len(root)) :]


def live_units(
    a: Artifact, graph: str, earlier: list[Artifact] | None = None
) -> list[extract.Unit]:
    """One extraction unit: the new message after its conversation, oldest messages dropped
    first to stay within extract.UNIT_CHARS. None for what extraction doesn't read (PRs,
    docs, system messages, planning channels)."""
    if a.kind != "message" or a.system or extract.channel_kind(a) not in extract.CHANNEL_KINDS:
        return []
    context = [m for m in conversation(a, graph, earlier) if not m.system]
    budget = extract.UNIT_CHARS - len(a.text)
    keep = 1 if context and context[0].id == a.meta.get("thread_id") else 0  # a thread's root
    while len(context) > keep and sum(len(m.text) + 20 for m in context) > budget:
        context.pop(keep)
    if context and sum(len(m.text) + 20 for m in context) > budget:
        context.pop(0)  # a root too long to fit with the reply
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
    """Every product's extracted events and normalized artifacts, as `yaan ingest` folds them.
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


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 1)
