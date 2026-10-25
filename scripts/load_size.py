"""Sizing at the envelope (Phase 22.6, review F19 and "memory sizing"): what a year of an org's
items costs in FalkorDB memory, and how long product-scoped vector search takes as it grows.
No LLM and no HERB data: synthetic messages with random unit vectors, written into a FalkorDB
of its own (never the real server: its memory cap and the worker stay out of it).

    docker run -d --name falkordb-size -p 127.0.0.1:6394:6379 falkordb/falkordb:6.0.1
    uv run --project backend python scripts/load_size.py --steps 50000 100000 200000
    docker rm -f falkordb-size

D122's envelope: 250 people, 5k items a day, a year of backfill: ~1.8M artifacts. HERB holds
1.28 chunks per artifact. Each step adds artifacts (one product, a second small product for
`--other`), then reports used memory per artifact and the search timings: the exact scoped
scan (search_vector_scoped.cypher, what a product's search runs today) and the graph-wide
HNSW index with the same product filter (search_vector.cypher), and the index's recall@k
against the exact scan for queries near a stored chunk.
"""

import argparse
import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from falkordb import FalkorDB  # noqa: E402

from app import db, schema  # noqa: E402

GRAPH, PRODUCT, OTHER = "yaan_size", "SizeForce", "SideForce"
DIM, CHUNKS_PER_ARTIFACT, TEXT_CHARS = 384, 1.28, 400
WRITE = """
UNWIND $rows AS r
MERGE (p:Product {id: r.product})
CREATE (a:Artifact:Message {id: r.id, kind: 'message', text: r.text, ts_ms: r.ts,
        ts_iso: '2026-01-01T00:00:00', product_id: r.product})
CREATE (a)-[:IN_PRODUCT]->(p)
WITH a, r
UNWIND r.chunks AS c
CREATE (n:Chunk {id: c.id, artifact_id: r.id, kind: 'message', text: r.text, field: 'text',
        ts_ms: r.ts, embedding: vecf32(c.v)})
CREATE (a)-[:HAS_CHUNK]->(n)
"""


def unit(rnd: random.Random) -> list[float]:
    v = [rnd.gauss(0, 1) for _ in range(DIM)]
    norm = sum(x * x for x in v) ** 0.5
    return [round(x / norm, 5) for x in v]


def near(v: list[float], rnd: random.Random, noise: float = 0.35) -> list[float]:
    """A query close to a stored chunk, as a real question is to its answer's text."""
    w = [x + rnd.gauss(0, noise / DIM**0.5) for x in v]
    norm = sum(x * x for x in w) ** 0.5
    return [x / norm for x in w]


def fill(start: int, stop: int, other: float, rnd: random.Random, keep: list) -> None:
    batch = 250
    for lo in range(start, stop, batch):
        rows = []
        for i in range(lo, min(lo + batch, stop)):
            n = 2 if rnd.random() < CHUNKS_PER_ARTIFACT - 1 else 1
            chunks = [{"id": f"chunk:{i}:{j}", "v": unit(rnd)} for j in range(n)]
            product = OTHER if rnd.random() < other else PRODUCT
            if product == PRODUCT and len(keep) < 64 and rnd.random() < 0.001:
                keep.append(chunks[0]["v"])
            text = " ".join(rnd.choice(("export", "login", "chart", "sync", "fixed", "again"))
                            for _ in range(TEXT_CHARS // 7))  # fmt: skip
            rows.append({"id": f"msg:size-{i}", "product": product, "ts": 1_767_225_600_000 + i,
                         "text": text, "chunks": chunks})  # fmt: skip
        db.run_text(WRITE, {"rows": rows}, label="size_write", graph=GRAPH, readonly=False,
                    batch_rows=batch)  # fmt: skip


def search(name: str, qvec: list[float], k: int = 30, fetch: int = 150) -> tuple[float, list]:
    params = {"qvec": qvec, "k": k, "fetch": fetch, "product_id": PRODUCT, "kinds": None,
              "since_ms": None, "until_ms": None, "scoped": False, "reach_channels": [],
              "reach_meetings": None, "reach_unchanneled": True, "reach_sources": [],
              "reach_repos": []}  # fmt: skip
    t0 = time.perf_counter()
    rows = db.run(name, params, graph=GRAPH, timeout_ms=120_000).rows
    return time.perf_counter() - t0, [r["chunk_id"] for r in rows]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=6394)
    ap.add_argument("--steps", type=int, nargs="+", default=[50_000, 100_000, 200_000])
    ap.add_argument("--other", type=float, default=0.1, help="share of items in a second product")
    ap.add_argument("--queries", type=int, default=12)
    args = ap.parse_args()
    server = FalkorDB(host="127.0.0.1", port=args.port)
    db.client = lambda readonly: server  # the throwaway server, its default user
    conn = server.connection
    if GRAPH in server.list_graphs():
        server.select_graph(GRAPH).delete()
    schema.apply(GRAPH)
    base_mem = conn.info("memory")["used_memory"]
    rnd, keep, done = random.Random(22), [], 0
    print("artifacts  chunks  MB  KB/artifact  count_ms  scan_s  hnsw150_s  recall150  "
          "hnsw1000_s  recall1000   (p50 times; recall@30 vs the exact scan)")
    for step in args.steps:
        t0 = time.perf_counter()
        fill(done, step, args.other, rnd, keep)
        done = step
        wrote = time.perf_counter() - t0
        mem = conn.info("memory")["used_memory"] - base_mem
        t1 = time.perf_counter()
        chunks = db.run("count_chunks", {}, graph=GRAPH).rows[0]["n"]  # what search asks
        count_ms = (time.perf_counter() - t1) * 1000
        scans, shallow, deep, r150, r1000 = [], [], [], [], []
        for q in range(args.queries):
            qvec = near(keep[q % len(keep)], rnd)
            s, exact = search("search_vector_scoped", qvec)
            h, approx = search("search_vector", qvec)
            d, wide = search("search_vector", qvec, fetch=1000)
            scans.append(s)
            shallow.append(h)
            deep.append(d)
            r150.append(len(set(exact) & set(approx)) / max(len(exact), 1))
            r1000.append(len(set(exact) & set(wide)) / max(len(exact), 1))
        print(f"{done:>9}  {chunks:>6}  {mem / 2**20:>5.0f}  {mem / done / 1024:>10.2f}  "
              f"{count_ms:>8.1f}  {statistics.median(scans):>6.3f}  "
              f"{statistics.median(shallow):>9.3f}  {statistics.mean(r150):>9.2f}  "
              f"{statistics.median(deep):>10.3f}  {statistics.mean(r1000):>10.2f}   "
              f"(wrote in {wrote:.0f} s)")  # fmt: skip
        sys.stdout.flush()
    server.select_graph(GRAPH).delete()


if __name__ == "__main__":
    main()
