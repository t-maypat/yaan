"""Search quality and latency on the dev questions, without any LLM. Built in Phase 4.

For each answerable dev question, run search_artifacts (full-text only, vector only, and
hybrid) scoped to the question's product, and compare the returned artifacts with HERB's
`citations` (HERB artifact ids, 12-254 per question, so they mark relevant context
rather than a single answer):
- hit@k: at least one of the top k artifacts is cited;
- precision@k: the share of the top k artifacts that are cited.
Latency comes from db.LATENCY (4.7). Dev products only: questions load through the
held-out guard (app.eval.split).
"""

import time
from typing import Any

from app import db
from app.agent.tools import search_artifacts
from app.eval.split import load_questions, read_split
from app.ingest.normalize import normalized_dir, read_jsonl

MODES = ("fulltext", "vector", "hybrid")


def herb_to_artifact_ids(product: str) -> dict[str, str]:
    """HERB id (as questions cite it) -> Yaan artifact id."""
    mapping = {}
    for a in read_jsonl(normalized_dir(product) / "artifacts.jsonl"):
        herb_id = a.meta["herb_id"] if a.kind == "link" else a.id.split(":", 1)[1]
        mapping[herb_id] = a.id
    return mapping


def run(k: int = 10, products: list[str] | None = None, graph: str | None = None) -> dict:
    products = products or read_split()["dev"]
    db.LATENCY.clear()
    per_mode: dict[str, list[dict[str, Any]]] = {m: [] for m in MODES}
    for product in products:
        ids = herb_to_artifact_ids(product)
        for q in load_questions(product)["answerable_questions"]:
            cited = {ids[c] for c in q["citations"] if c in ids}
            for mode in MODES:
                started = time.perf_counter()
                found = search_artifacts(
                    q["question"], product_id=product, k=k, mode=mode, graph=graph
                )["evidence_ids"]
                took = (time.perf_counter() - started) * 1000
                hits = sum(a in cited for a in found)
                per_mode[mode].append(
                    {
                        "type": q["type"],
                        "hit": hits > 0,
                        "precision": hits / k,
                        "ms": took,
                    }
                )
    summary = {}
    for mode, rows in per_mode.items():
        types = sorted({r["type"] for r in rows})
        summary[mode] = {
            "questions": len(rows),
            f"hit@{k}": mean([r["hit"] for r in rows]),
            f"precision@{k}": mean([r["precision"] for r in rows]),
            "p50_ms": db.percentile([r["ms"] for r in rows], 50),
            "p95_ms": db.percentile([r["ms"] for r in rows], 95),
            "by_type": {t: mean([r["hit"] for r in rows if r["type"] == t]) for t in types},
        }
    return {"k": k, "products": products, "modes": summary, "latency": db.latency_stats("search_")}


def mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 3) if values else 0.0
