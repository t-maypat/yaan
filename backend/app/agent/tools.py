"""Agent tools 1-14. search_artifacts in Phase 4, the rest in Phases 9-11.

Read-only; accept names or ids; return compact JSON with evidence_ids and the .cypher
file used; explain the why (path/edges), not just values.
Spec: docs/CONTEXT.md §9.6 (tool table and design rules).
"""

import re
import time
from typing import Any, Literal

from app import db
from app.ingest.chunk_embed import embed_query

RRF_K = 60  # reciprocal rank fusion constant (Cormack et al.); damps the top ranks
FETCH_FACTOR = 5  # over-fetch per list so filters and per-artifact grouping still leave k
MAX_FETCH = 200
MAX_KEYWORDS = 12
SNIPPET_CHARS = 300
STOPWORDS = frozenset(
    """a about above after again against all am an and any are as at be because been before
    being below between both but by can could did do does doing down during each few for from
    further had has have having he her here hers him his how i if in into is it its itself just
    me more most my no nor not now of off on once only or other our ours out over own same she
    should so some such than that the their theirs them then there these they this those
    through to too under until up very was we were what when where which while who whom why
    will with would you your yours tell give list name show find please any was were""".split()
)
Mode = Literal["hybrid", "fulltext", "vector"]


def keywords(text: str) -> str:
    """A full-text query from free text: content words joined with OR (A11).

    FalkorDB full-text ANDs every word and has no stopword list, so a raw question would
    require "what", "the" and every other word to appear. Words are \\w+ tokens, so no
    query syntax (`|`, `-`, `%`, quotes) can come from the user's text.
    """
    words = [w for w in re.findall(r"\w+", text.lower()) if len(w) > 1 and w not in STOPWORDS]
    return " | ".join(list(dict.fromkeys(words))[:MAX_KEYWORDS])


def search_artifacts(
    query: str,
    kinds: list[str] | None = None,
    product_id: str | None = None,
    since_ms: int | None = None,
    until_ms: int | None = None,
    k: int = 10,
    *,
    mode: Mode = "hybrid",
    graph: str | None = None,
) -> dict[str, Any]:
    """Tool 1: hybrid search over chunks, fused with RRF, one result per artifact.

    `mode` is for evaluation and ablations: full-text only, vector only, or both fused.
    """
    fetch = min(MAX_FETCH, k * FETCH_FACTOR)
    filters = {"kinds": kinds, "product_id": product_id, "since_ms": since_ms, "until_ms": until_ms}
    lists: dict[str, list[dict[str, Any]]] = {}
    timings: dict[str, float] = {}
    used: list[str] = []
    q = keywords(query)
    if mode in ("hybrid", "fulltext") and q:
        started = time.perf_counter()
        lists["fulltext"] = db.run(
            "search_fulltext", {"q": q, "k": fetch, **filters}, graph=graph
        ).rows
        timings["fulltext_ms"] = ms_since(started)
        used.append("search_fulltext.cypher")
    if mode in ("hybrid", "vector"):
        started = time.perf_counter()
        vector = embed_query(query)
        timings["embed_ms"] = ms_since(started)
        started = time.perf_counter()
        params = {"qvec": vector, "fetch": fetch, "k": fetch, **filters}
        lists["vector"] = db.run("search_vector", params, graph=graph).rows
        timings["vector_ms"] = ms_since(started)
        used.append("search_vector.cypher")

    results = fuse(lists, k)
    return {
        "query": query,
        "keywords": q,
        "mode": mode,
        "filters": {key: value for key, value in filters.items() if value is not None},
        "results": results,
        "evidence_ids": [r["artifact_id"] for r in results],
        "cypher": used,
        "timings": timings,
    }


def fuse(lists: dict[str, list[dict[str, Any]]], k: int) -> list[dict[str, Any]]:
    """Reciprocal rank fusion over chunk lists, then the best chunk per artifact."""
    scores: dict[str, float] = {}
    best: dict[str, dict[str, Any]] = {}
    ranks: dict[str, dict[str, int]] = {}
    for name, rows in lists.items():
        for rank, row in enumerate(rows, start=1):
            chunk = row["chunk_id"]
            scores[chunk] = scores.get(chunk, 0.0) + 1.0 / (RRF_K + rank)
            best.setdefault(chunk, row)
            ranks.setdefault(chunk, {})[f"{name}_rank"] = rank
    per_artifact: dict[str, dict[str, Any]] = {}
    for chunk in sorted(scores, key=lambda c: -scores[c]):
        row = best[chunk]
        if row["artifact_id"] in per_artifact:
            continue
        per_artifact[row["artifact_id"]] = {
            "artifact_id": row["artifact_id"],
            "kind": row["kind"],
            "title": row["title"],
            "ts_iso": row["ts_iso"],
            "product_id": row["product_id"],
            "chunk_id": chunk,
            "field": row["field"],
            "snippet": row["text"][:SNIPPET_CHARS],
            "rrf": round(scores[chunk], 5),
            **ranks[chunk],
        }
        if len(per_artifact) == k:
            break
    return list(per_artifact.values())


def ms_since(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)
