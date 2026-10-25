"""Load harness for the live path (H3, D112): how long each stage of one live message takes, on a
throwaway copy of a graph. No LLM: every message carries deterministic events (the way a Jira
status change does), labeled synthetic (rule 5), in a bug channel of one product.

    uv run --project backend python scripts/bench_live.py --n 40
    uv run --project backend python scripts/bench_live.py --n 40 --batch 10   # micro-batches
    uv run --project backend python scripts/bench_live.py --grow 5            # 5x the events first

`--grow K` first adds K x the product's issue lifecycles (report, fix, reopen) as events, so a
fold, the derived layer and the Sentinel have K times as much history to go over. The copy
(`yaan_bench`) and its live folder are deleted at the end unless `--keep`.
"""

import argparse
import shutil
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import db  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.connectors import base  # noqa: E402
from app.ingest.normalize import Artifact  # noqa: E402
from app.sentinel import live  # noqa: E402

BENCH = "yaan_bench"
CHANNEL, CHANNEL_NAME = "ch-force-d79648", "bug-davidgarcia-VizForce"
PRODUCT, AUTHOR = "VizForce", "eid_bd791efc"
KINDS = ("issue_reported", "issue_resolved", "issue_reopened")


def message(n: int, kind: str, issue: str, ts_ms: int) -> tuple[Artifact, list[dict]]:
    verb = {"issue_reported": "is new", "issue_resolved": "is fixed", "issue_reopened": "is back"}
    quote = f"the bug titled '{issue}' {verb[kind]}"
    a = Artifact(
        id=f"msg:synthetic-bench-{n}",
        kind="message",
        product_hint=PRODUCT,
        channel_id=CHANNEL,
        ts_ms=ts_ms,
        ts_iso=time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts_ms / 1000)),
        author_eid=AUTHOR,
        text=f"[synthetic demo] Heads up: {quote}.",
        raw_ref=f"bench:{n}",
        meta={"channel_name": CHANNEL_NAME, "sender": AUTHOR, "synthetic": True},
    )
    return a, [base.structured_event(kind, a, quote, AUTHOR, issue=issue)]


def grow(k: int, start_ms: int) -> int:
    """Add k x (report, fix, reopen) lifecycles per existing issue of the product: messages
    and events written in bulk, then one fold, derive and Sentinel pass (not timed below)."""
    from app.ingest import derive, fold, structural
    from app.ingest import events as event_store
    from app.sentinel import engine

    issues = db.run_text(
        "MATCH (i:Issue {product_id: $p}) RETURN i.title AS t LIMIT 10000",
        {"p": PRODUCT},
        label="bench",
        graph=BENCH,
    ).rows
    arts, events, n = [], [], 0
    for round_ in range(k):
        for row in issues:
            for kind in KINDS:
                n += 1
                a, ev = message(10_000_000 + n, kind, f"{row['t']} (copy {round_})", start_ms + n)
                arts.append(a)
                events += ev
    rows = structural.product_rows(PRODUCT, arts, set(live._metadata().employees), start_ms)
    rows["upsert_channels"], rows["upsert_products"] = [], []
    structural.load(rows, BENCH)
    event_store.load(events, BENCH)
    fold.fold_product(PRODUCT, BENCH)
    derive.expertise(BENCH)
    derive.precedents(BENCH)
    derive.uses(BENCH)
    engine.run(BENCH)
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--n", type=int, default=30, help="messages to time")
    ap.add_argument("--batch", type=int, default=1, help="messages per live batch")
    ap.add_argument("--grow", type=int, default=0, help="first add K x the issue lifecycles")
    ap.add_argument("--source", default=None, help="graph to copy (default: YAAN_GRAPH)")
    ap.add_argument("--keep", action="store_true", help="keep yaan_bench afterwards")
    args = ap.parse_args()
    s = get_settings()
    writer = db.client(readonly=False)
    if BENCH in writer.list_graphs():
        writer.select_graph(BENCH).delete()
    writer.select_graph(args.source or s.yaan_graph).copy(BENCH)
    shutil.rmtree(live.live_dir(BENCH), ignore_errors=True)
    t0 = int(time.time() * 1000) - 86_400_000
    try:
        if args.grow:
            started = time.perf_counter()
            added = grow(args.grow, t0)
            print(f"grew the product by {added} events in {time.perf_counter() - started:.0f} s")
        stages: dict[str, list[float]] = {}
        totals = []
        ingest_batch = getattr(live, "ingest_batch", None)
        pending = []
        for n in range(args.n):
            kind = KINDS[n % 3]
            a, ev = message(n, kind, f"Bench issue {n // 3}", t0 + 3_600_000 + n * 1000)
            pending.append((a, ev))
            if len(pending) < args.batch and n < args.n - 1:
                continue
            started = time.perf_counter()
            if ingest_batch and args.batch > 1:
                outs = [o for o in ingest_batch([(a, ev, False) for a, ev in pending], BENCH)]
            else:
                outs = [live.ingest_artifact(a, BENCH, events=ev) for a, ev in pending]
            totals.append((time.perf_counter() - started) * 1000 / len(pending))
            for out in outs:
                if isinstance(out, Exception):
                    raise out
                for k, v in out["stages_ms"].items():
                    stages.setdefault(k, []).append(v / len(pending) if args.batch > 1 else v)
            pending = []
        nodes = db.run_text("MATCH (n) RETURN count(n) AS n", label="bench", graph=BENCH).rows
        print(f"graph {BENCH}: {nodes[0]['n']:,} nodes; {args.n} messages, batches of {args.batch}")
        print(f"{'stage':<26}{'p50 ms':>10}{'p95 ms':>10}")
        for k, v in stages.items():
            v = sorted(v)
            print(f"{k:<26}{statistics.median(v):>10.1f}{v[int(0.95 * (len(v) - 1))]:>10.1f}")
        totals.sort()
        print(f"{'per message, wall':<26}{statistics.median(totals):>10.1f}{totals[-1]:>10.1f} (max)")
    finally:
        if not args.keep:
            writer.select_graph(BENCH).delete()
            shutil.rmtree(live.live_dir(BENCH), ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
