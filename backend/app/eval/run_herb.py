"""Run a system (baseline | lore | lore-search) over a split's HERB questions. Phase 5.

Per run, eval/runs/<run_id>/ (gitignored) gets:
- answers.jsonl: one row per question: the answer, abstain flag, citations, retrieved ids,
  search and LLM latency, tokens and cost;
- herb-ans.json / herb-unans.json: the column format HERB's evaluate.py reads
  ({question, answer, ground_truth, type} / {question, answer}), so anyone with an OpenAI
  key can score the same answers with HERB's own script, unchanged;
- meta.json: system, split, products, models.
Questions load only through the held-out guard (app.eval.split). Retrieval runs in this
thread (the graph client and the embedder stay single-threaded); the LLM step runs in a
small thread pool. Spec: docs/CONTEXT.md §7.
"""

import json
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app import db, llm
from app.config import get_settings
from app.eval.split import load_questions, read_split


@dataclass(frozen=True)
class System:
    """Two steps: `prepare` touches the graph (one thread), `respond` calls the LLM (pooled)."""

    prepare: Callable[[str, str], Any]  # (question, product) -> context
    respond: Callable[[str, Any], dict[str, Any]]  # (question, context) -> answer dict


def get_system(name: str) -> System:
    if name == "baseline":
        from app.eval import baseline_rag

        return System(
            prepare=lambda q, product: baseline_rag.retrieve(q, product_id=product),
            respond=baseline_rag.answer_from,
        )
    if name == "lore":
        from app.agent import loop

        # The agent reads the graph between LLM turns, so all of it runs in the pool:
        # the graph client is thread-safe and the embedder is behind a lock (tools.py).
        return System(
            prepare=lambda q, product: product,
            respond=lambda q, product: loop.answer(q, product_id=product),
        )
    if name == "lore-search":  # Lore − graph tools (9.8, R1-7): search and reading only
        from app.agent import loop, tools

        return System(
            prepare=lambda q, product: product,
            respond=lambda q, product: loop.answer(
                q, product_id=product, toolset=tools.SEARCH_ONLY
            ),
        )
    raise ValueError(f"unknown system {name!r}: 'baseline', 'lore' or 'lore-search'")


def questions(split: str = "dev", products: list[str] | None = None) -> list[dict[str, Any]]:
    """The split's questions as rows with a stable id: <product>:ans:<i> / <product>:unans:<i>.

    `drift` is the hand-labeled HERB-Drift set on the dev products (13.1, eval/drift_set.json):
    one row per item, kind "drift", with the item itself as the ground truth."""
    if split == "drift":
        items = json.loads((get_settings().eval_dir / "drift_set.json").read_text("utf-8"))
        return [
            {
                "id": q["id"],
                "product": q["product"],
                "kind": "drift",
                "question": q["question"],
                "type": q["kind"],
                "ground_truth": {k: q[k] for k in ("answer", "correct", "stale")},
            }
            for q in items["questions"]
            if not products or q["product"] in products
        ]
    if split != "dev":
        raise ValueError("only the dev split runs here; the held-out run is Phase 13")
    rows = []
    for product in products or read_split()["dev"]:
        q = load_questions(product)
        for i, a in enumerate(q["answerable_questions"]):
            rows.append(
                {
                    "id": f"{product}:ans:{i}",
                    "product": product,
                    "kind": "ans",
                    "question": a["question"],
                    "type": a["type"],
                    "ground_truth": a["ground_truth"],
                }
            )
        for i, text in enumerate(q["unanswerable_questions"]):
            rows.append(
                {
                    "id": f"{product}:unans:{i}",
                    "product": product,
                    "kind": "unans",
                    "question": text,
                    "type": "unanswerable",
                    "ground_truth": None,
                }
            )
    return rows


def run_dir(run_id: str) -> Path:
    return get_settings().eval_dir / "runs" / run_id


def run(
    system: str = "baseline",
    split: str = "dev",
    products: list[str] | None = None,
    limit: int | None = None,
    workers: int = 4,
    run_id: str | None = None,
    progress: Callable[[str], None] = lambda _: None,
    ids: list[str] | None = None,
) -> Path:
    """Answer every question and write the run folder. Returns its path. `ids` limits it to
    those questions (a targeted check after a fix, 9.8); an unknown id is an error."""
    sys_ = get_system(system)
    rows = questions(split, products)
    if ids is not None:
        unknown = set(ids) - {r["id"] for r in rows}
        if unknown:
            raise ValueError(f"{len(unknown)} ids aren't in the {split} split, e.g. {min(unknown)}")
        rows = [r for r in rows if r["id"] in set(ids)]
    rows = rows[:limit] if limit else rows
    partial = bool(limit or products or ids)  # a smoke or subset run: never a headline number
    products = sorted({r["product"] for r in rows})
    s = get_settings()
    run_id = run_id or f"{datetime.now(UTC):%Y%m%dT%H%MZ}_{system}_{split}"
    out_dir = run_dir(run_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    def respond(row: dict[str, Any], context: Any, search_ms: float) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            out = sys_.respond(row["question"], context)
            error = None
        except llm.SpendCapReached:
            raise  # stop the whole run, loudly
        except llm.LLMError as e:  # refusal, cut-off: recorded per question and counted
            out, error = {}, f"{type(e).__name__}: {e}"
        return {
            **row,
            "answer": out.get("answer_markdown"),
            "abstain": out.get("abstain"),
            "citations": out.get("citations", []),
            "retrieved": out.get("retrieved", []),
            "llm": out.get("llm"),
            **{k: out[k] for k in ("trace", "validator", "unvalidated") if k in out},
            "search_ms": round(search_ms, 1),
            "answer_ms": round(search_ms + (time.perf_counter() - started) * 1000, 1),
            "error": error,
        }

    results: dict[str, dict[str, Any]] = {}
    db.LATENCY.clear()  # this run's graph calls only: per-query p50/p95 go into meta.json
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = []
        for n, row in enumerate(rows, 1):
            started = time.perf_counter()
            context = sys_.prepare(row["question"], row["product"])
            futures.append(
                pool.submit(respond, row, context, (time.perf_counter() - started) * 1000)
            )
            progress(f"searched {n}/{len(rows)}")
        for n, f in enumerate(futures, 1):
            r = f.result()
            results[r["id"]] = r
            progress(f"answered {n}/{len(rows)}")

    ordered = [results[r["id"]] for r in rows]
    write_jsonl(out_dir / "answers.jsonl", ordered)
    ans = [r for r in ordered if r["kind"] == "ans"]
    unans = [r for r in ordered if r["kind"] == "unans"]
    write_json(
        out_dir / "herb-ans.json",
        {
            "question": [r["question"] for r in ans],
            "answer": [r["answer"] for r in ans],
            "ground_truth": [r["ground_truth"] for r in ans],
            "type": [r["type"] for r in ans],
        },
    )
    write_json(
        out_dir / "herb-unans.json",
        {
            "question": [r["question"] for r in unans],
            "answer": [r["answer"] for r in unans],
        },
    )
    write_json(
        out_dir / "meta.json",
        {
            "run_id": run_id,
            "system": system,
            "split": split,
            "products": products,
            "questions": len(rows),
            "limit": limit,
            "partial": partial,
            "answer_model": s.lore_answer_model,
            "answer_effort": s.lore_answer_effort,
            "started_utc": run_id.split("_")[0],
            "query_latency": db.latency_stats(),
        },
    )
    return out_dir


def write_json(path: Path, data: Any) -> None:
    path.write_text(
        json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
