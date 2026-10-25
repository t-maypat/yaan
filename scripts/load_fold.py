"""Load test for the fold (Phase 21.1, F3): how long folding one product takes as its history
grows, on a throwaway graph of synthetic events. No LLM, no HERB data: events are written
straight to the graph, the way the live path stores them after extraction.

Sizing (the review's gate): HERB yields about 6 decision events and 2.5 issues per 100
messages, so a year of 2.5M messages is about 62k issues and 146k events. The worst single
product in that year is assumed to hold a quarter of them: ~15.5k issues.

    uv run --project backend python scripts/load_fold.py --memory  # resolve + rebuild only
    uv run --project backend python scripts/load_fold.py --issues 4000  # in a graph
    uv run --project backend python scripts/load_fold.py --issues 15500 --exact

Each run reports, per stage: a cold full fold (nothing resolved before), a steady-state batch
(5 new events after the newest), and a late edit (an event dated in the middle of the history:
everything after it is resolved again). Follow-ups name their issue loosely, as people do in
chat (`--exact`: by its title, as Jira does). The graph `yaan_load` is deleted unless `--keep`.
"""

import argparse
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import db, schema  # noqa: E402
from app.ingest import events as event_store  # noqa: E402
from app.ingest import fold, resolve  # noqa: E402

GRAPH, PRODUCT = "yaan_load", "LoadForce"
SPEAKER, REPORTER = "eid_cccccccc", "eid_aaaaaaaa"
WORDS = (
    "cache login export chart sync token oauth upload report dashboard filter search billing "
    "invoice email webhook timeout latency memory crash render mobile api permission audit "
    "import schema migration index queue retry session"
).split()
DAY_MS = 86_400_000


def history(n_issues: int, exact: bool, channels: int, seed: int = 7) -> list[dict]:
    """A report per issue, then three follow-ups each (resolved or reopened), a year's span."""
    rnd = random.Random(seed)
    start, step = 1_767_225_600_000, max(1, 365 * DAY_MS // (4 * n_issues))
    titles = [" ".join(rnd.sample(WORDS, 4)) + f" {i}" for i in range(n_issues)]
    out, t = [], start
    for i, title in enumerate(titles):
        t += step
        out.append(event(i, "r", "issue_reported", title, t, f"report {title}", i % channels))
    for i, title in enumerate(titles):
        for k in range(3):
            t += step
            words = title.split()[:-1]
            rnd.shuffle(words)
            named = title if exact else " ".join(words[:3])
            kind = rnd.choice(["issue_resolved", "issue_reopened"])
            out.append(event(i, k, kind, named, t, f"{named} is fixed", i % channels))
    return out


def event(i, k, kind, issue, ts, quote, channel) -> dict:
    return {
        "type": kind,
        "artifact_id": f"msg:synthetic-load-{i}-{k}",
        "product": PRODUCT,
        "channel": f"ch-load-{channel}",
        "issue": issue,
        "quote": quote,
        "person": REPORTER if kind == "issue_reported" else None,
        "speaker": SPEAKER,
        "ts_ms": ts,
        "ts_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts / 1000)),
        "confidence": 0.9,
        "decisive": True,
    }


def in_memory(sizes: list[int], exact: bool, channels: int) -> None:
    """resolve + rebuild with no graph: the review's benchmark, for comparison."""
    ctx = fold.Context(roles={SPEAKER: "member"}, customers=set(), active={}, prs={})
    for n in sizes:
        found = history(n, exact, channels)
        resolve.norm_title.cache_clear()
        resolve.core.cache_clear()
        t0 = time.perf_counter()
        res = resolve.resolve(found, {PRODUCT: []})
        t1 = time.perf_counter()
        fold.rebuild(res, ctx)
        t2 = time.perf_counter()
        print(
            f"issues={n:>6} events={len(found):>7}  resolve {t1 - t0:7.2f} s"
            f"  rebuild {t2 - t1:6.2f} s"
        )


def seed_graph(found: list[dict]) -> None:
    schema.apply(GRAPH)
    problems = schema.verify(GRAPH, wait_s=30)
    if problems:
        raise SystemExit(f"schema: {problems}")
    db.run_text(
        f"MERGE (:Product {{id: '{PRODUCT}', name: '{PRODUCT}'}})",
        label="load_product",
        graph=GRAPH,
        readonly=False,
    )
    people = [{"id": SPEAKER, "role": "member"}, {"id": REPORTER, "role": "member"}]
    db.run_text(
        "UNWIND $rows AS r MERGE (p:Person {id: r.id}) SET p.role = r.role",
        {"rows": people},
        label="load_people",
        graph=GRAPH,
        readonly=False,
    )
    messages = [
        {"id": e["artifact_id"], "ts_ms": e["ts_ms"], "channel": e["channel"]} for e in found
    ]
    for lo in range(0, len(messages), 2000):
        db.run_text(
            "UNWIND $rows AS r MERGE (m:Message:Artifact {id: r.id}) "
            "SET m.ts_ms = r.ts_ms, m.channel_id = r.channel, m.kind = 'message'",
            {"rows": messages[lo : lo + 2000]},
            label="load_messages",
            graph=GRAPH,
            readonly=False,
        )
    for lo in range(0, len(found), 500):  # writes must finish inside 10 s (A17)
        event_store.load(found[lo : lo + 500], GRAPH, observed_ms=found[0]["ts_ms"])


def timed_fold(label: str) -> dict:
    t0 = time.perf_counter()
    out = fold.fold_product(PRODUCT, GRAPH)
    total = time.perf_counter() - t0
    ms = out.get("ms", {})
    stages = "  ".join(f"{k} {v / 1000:6.2f}" for k, v in ms.items())
    w = out["write"]
    print(f"  {label:<12} {total:7.2f} s   {stages}   (changed {w['changed']}, stale {w['stale']})")
    return out


def used_memory() -> int:
    info = db.client(readonly=False).connection.info("memory")
    return int(info["used_memory"])


def in_graph(n: int, exact: bool, channels: int, keep: bool) -> None:
    writer = db.client(readonly=False)
    if GRAPH in writer.list_graphs():
        writer.select_graph(GRAPH).delete()
    found = history(n, exact, channels)
    before = used_memory()
    t0 = time.perf_counter()
    seed_graph(found)
    print(
        f"issues={n} events={len(found)} {'exact' if exact else 'chat'}-named, "
        f"{channels} channel(s): seeded in {time.perf_counter() - t0:.1f} s"
    )
    try:
        timed_fold("cold")
        timed_fold("unchanged")
        last = max(e["ts_ms"] for e in found)
        rnd = random.Random(11)
        batch = [
            event(
                n + j,
                "x",
                "issue_resolved",
                found[rnd.randrange(n)]["issue"],
                last + j + 1,
                "fixed now",
                0,
            )  # fmt: skip
            for j in range(5)
        ]
        event_store.load(batch, GRAPH)
        timed_fold("new batch")
        middle = sorted(e["ts_ms"] for e in found)[len(found) // 2]
        late = event(n + 9, "late", "issue_resolved", found[1]["issue"], middle, "late fix", 0)
        event_store.load([late], GRAPH)
        timed_fold("late edit")
        mb = (used_memory() - before) / 1e6
        print(f"  graph memory: +{mb:.0f} MB ({mb * 1e6 / len(found):.0f} B per event)")
    finally:
        if not keep:
            writer.select_graph(GRAPH).delete()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--memory", action="store_true", help="resolve + rebuild only, no graph")
    p.add_argument("--issues", type=int, default=4000)
    p.add_argument("--exact", action="store_true", help="follow-ups name the exact title")
    p.add_argument("--channels", type=int, default=1, help="spread issues over N channels")
    p.add_argument("--keep", action="store_true", help=f"keep the {GRAPH} graph")
    a = p.parse_args()
    if a.memory:
        in_memory([250, 500, 1000, 2000, 4000], a.exact, a.channels)
    else:
        in_graph(a.issues, a.exact, a.channels, a.keep)


if __name__ == "__main__":
    main()
