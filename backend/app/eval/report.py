"""Score a run -> eval/results/<date>_<system>_<split>.json, and the results table. Phase 5,
finalized in Phase 13.

score_run() sends every answer of a run folder (run_herb.py) to the judge (judge.py), writes
judged.jsonl next to the answers, and summarizes into one results file. table() rebuilds the
§7.4 table from all results files, so the README numbers always come from committed files.

Scales: content is a 0-100 Likert score and the other categories are F1 in 0-1; the table
shows F1 x 100, and Avg is the mean of the four category scores, as in the HERB paper ("the
average score across all four query types"). Unanswerable = % of unanswerable questions left
unanswered (higher is better). evaluate.py's own printed average mixes the two scales; it is
kept as herb_script_avg for reference only. Spec: docs/CONTEXT.md §7.2, §7.4.
"""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.db import percentile
from app.eval import judge
from app.eval.run_herb import read_jsonl, write_json, write_jsonl

CATEGORIES = (
    ("content", "Content"),
    ("person", "People"),
    ("company", "Customer"),
    ("url", "Artifact"),
)
# Published reference point (CONTEXT §7.1): different model and setup, context only.
HERB_REF = {
    "system": "Ref: HERB product-specific ReAct GPT-4o",
    "avg": 43.41,
    "unanswerable": 29.33,
}


def judge_row(row: dict[str, Any]) -> dict[str, Any]:
    if row["error"]:  # never credited: not even as a correct abstention (D035)
        verdict = (
            {"bucket": judge.BUCKET[row["type"]], "score": 0.0}
            if row["kind"] == "ans"
            else {"unanswered": False}
        )
        return {"id": row["id"], **verdict, "reason": "answer step failed", "judge_cost_usd": 0.0}
    if row["kind"] == "drift":
        v = judge.score_drift(row["question"], row["ground_truth"], row["answer"])
    elif row["kind"] == "ans":
        v = judge.score_answerable(row["question"], row["type"], row["ground_truth"], row["answer"])
    else:
        v = judge.score_unanswerable(row["question"], row["answer"])
    return {"id": row["id"], **v}


VARIANTS = ("unvalidated",)


def score_run(run: Path, workers: int = 4, variant: str | None = None) -> Path:
    """Judge a run folder and write its results file. Returns the results path.

    `variant="unvalidated"` scores the model's first final answers, before the citation
    validator's autocite, repair and dropping: the Lore − citation validator ablation from the
    same run, for the judge's cost only (R2). Its system is named `<system>-unvalidated`.
    """
    meta = json.loads((run / "meta.json").read_text(encoding="utf-8"))
    rows = read_jsonl(run / "answers.jsonl")
    if variant is not None:
        if variant not in VARIANTS:
            raise ValueError(f"unknown variant {variant!r}: {VARIANTS}")
        missing = [r["id"] for r in rows if not r["error"] and not r.get(variant)]
        if missing:
            raise ValueError(f"{len(missing)} answers have no {variant!r} field, e.g. {missing[0]}")
        rows = [
            {**r, "answer": r[variant]["answer_markdown"], "abstain": r[variant]["abstain"]}
            if r.get(variant)
            else r
            for r in rows
        ]
        meta = {**meta, "system": f"{meta['system']}-{variant}"}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        verdicts = list(pool.map(judge_row, rows))
    write_jsonl(run / f"judged{'-' + variant if variant else ''}.jsonl", verdicts)
    summary = (summarize_drift if meta["split"] == "drift" else summarize)(meta, rows, verdicts)
    if variant is not None:  # its citations were never checked: the validator is what's removed
        summary["citations"] = None
    name = f"{meta['started_utc'][:8]}_{meta['system']}_{meta['split']}.json"
    # Partial runs (--limit, --products) stay in their run folder, out of the results table.
    out = run / "results.json" if meta.get("partial") else results_dir() / name
    out.parent.mkdir(parents=True, exist_ok=True)
    write_json(out, summary)
    return out


def summarize(meta: dict, rows: list[dict], verdicts: list[dict]) -> dict[str, Any]:
    s = get_settings()
    by_id = {v["id"]: v for v in verdicts}
    ans = [r for r in rows if r["kind"] == "ans"]
    unans = [r for r in rows if r["kind"] == "unans"]

    categories = {}
    for key, _ in CATEGORIES:
        scores = [by_id[r["id"]]["score"] for r in ans if by_id[r["id"]]["bucket"] == key]
        scale = 1 if key == "content" else 100
        categories[key] = {
            "n": len(scores),
            "score": round(mean(scores) * scale, 2) if scores else None,
        }
    present = [c["score"] for c in categories.values() if c["score"] is not None]
    unanswered = [by_id[r["id"]]["unanswered"] for r in unans]
    raw = [by_id[r["id"]]["score"] for r in ans]

    answered = [r for r in rows if not r["error"] and r["llm"]]
    fresh = [r for r in answered if not r["llm"]["cached"]]  # cached answers have no latency
    citations = [c for r in rows for c in r["citations"]]
    return {
        "run_id": meta["run_id"],
        "system": meta["system"],
        "split": meta["split"],
        "products": meta["products"],
        "questions": {"answerable": len(ans), "unanswerable": len(unans)},
        "categories": categories,
        "avg": round(mean(present), 2) if present else None,
        "unanswerable": round(100 * mean(unanswered), 2) if unanswered else None,
        "herb_script_avg": round(mean(raw), 2) if raw else None,
        "abstain_rate_answerable": round(100 * mean([bool(r["abstain"]) for r in ans]), 2)
        if ans
        else None,
        "errors": sum(1 for r in rows if r["error"]),
        "citations": {
            "total": len(citations),
            "retrieved_pct": pct([c["retrieved"] for c in citations]),
            "quote_ok_pct": pct([c["quote_ok"] for c in citations]),
        },
        "latency_s": {
            "n_uncached": len(fresh),
            "p50": round(percentile([r["answer_ms"] for r in fresh], 50) / 1000, 2),
            "p95": round(percentile([r["answer_ms"] for r in fresh], 95) / 1000, 2),
            "search_p50_ms": round(percentile([r["search_ms"] for r in rows], 50), 1),
        },
        "validator": validator_stats(rows),
        "tool_latency_ms": tool_latency(rows),
        "query_latency": meta.get("query_latency"),
        "tokens_per_answer": round(
            mean([r["llm"]["input_tokens"] + r["llm"]["output_tokens"] for r in answered]), 1
        )
        if answered
        else None,
        "cost_usd": {
            "answers_this_run": round(sum(r["llm"]["cost_usd"] for r in answered), 4),
            "judge_this_run": round(sum(v["judge_cost_usd"] for v in verdicts), 4),
        },
        "models": {
            "answer": meta["answer_model"],
            "answer_effort": meta["answer_effort"],
            "judge": s.lore_judge_model,
            "judge_effort": s.lore_judge_effort,
        },
        "deviations": [
            f"judge {s.lore_judge_model} instead of gpt-4o (D035)",
            "dev split: 3 products, product-scoped retrieval",
        ],
    }


def summarize_drift(meta: dict, rows: list[dict], verdicts: list[dict]) -> dict[str, Any]:
    """HERB-Drift (13.1): correct %, stale-answer rate (stale / answered, CONTEXT §7.2), and
    abstentions, overall and per question kind (current status, as-of status, why)."""
    by_id = {v["id"]: v for v in verdicts}

    def block(subset: list[dict]) -> dict[str, Any]:
        vs = [by_id[r["id"]] for r in subset]
        answered = [v for v in vs if v.get("verdict") != "abstained"]
        return {
            "n": len(vs),
            "correct_pct": pct([v.get("correct", False) for v in vs]),
            "stale_answer_rate": pct([v.get("stale", False) for v in answered]),
            "abstained": len(vs) - len(answered),
            "wrong": sum(1 for v in vs if v.get("verdict") == "wrong"),
        }

    kinds = sorted({r["type"] for r in rows})
    answered = [r for r in rows if not r["error"] and r["llm"]]
    return {
        "run_id": meta["run_id"],
        "system": meta["system"],
        "split": "drift",
        "products": meta["products"],
        "questions": {"drift": len(rows)},
        **block(rows),
        "by_kind": {k: block([r for r in rows if r["type"] == k]) for k in kinds},
        "errors": sum(1 for r in rows if r["error"]),
        "tokens_per_answer": round(
            mean([r["llm"]["input_tokens"] + r["llm"]["output_tokens"] for r in answered]), 1
        )
        if answered
        else None,
        "cost_usd": {
            "answers_this_run": round(sum(r["llm"]["cost_usd"] for r in answered), 4),
            "judge_this_run": round(sum(v.get("judge_cost_usd", 0.0) for v in verdicts), 4),
        },
        "models": {"answer": meta["answer_model"], "judge": get_settings().lore_judge_model},
    }


def validator_stats(rows: list[dict]) -> dict[str, Any] | None:
    """How often the citation validator acted: first answers with a problem, repaired, items
    autocited, sentences dropped, and answers it turned into abstentions."""
    checked = [r["validator"] for r in rows if r.get("validator")]
    if not checked:
        return None
    return {
        "answers": len(checked),
        "first_answer_problems_pct": pct([bool(v["first_problems"]) for v in checked]),
        "repaired_pct": pct([v["repaired"] for v in checked]),
        "dropped_sentences": sum(len(v["dropped"]) for v in checked),
        "autocited": sum(len(v["autocited"]) for v in checked),
        "turned_into_abstention": sum(
            1
            for r in rows
            if r.get("validator") and r["abstain"] and not r["validator"]["model_abstained"]
        ),
    }


def tool_latency(rows: list[dict]) -> dict[str, dict[str, float]] | None:
    """Per agent tool: calls, p50 / p95 wall-clock ms (graph queries + Python), from traces."""
    by_tool: dict[str, list[float]] = {}
    for r in rows:
        for step in r.get("trace") or []:
            by_tool.setdefault(step["tool"], []).append(step["ms"])
    if not by_tool:
        return None
    return {
        tool: {"calls": len(ms), "p50": percentile(ms, 50), "p95": percentile(ms, 95)}
        for tool, ms in sorted(by_tool.items())
    }


def results_dir() -> Path:
    return get_settings().eval_dir / "results"


def table(paths: list[Path] | None = None) -> str:
    """The §7.4 markdown table from every results file (newest file per system and split)."""
    paths = paths or sorted(results_dir().glob("*.json"))
    latest: dict[tuple[str, str], dict] = {}
    drift: dict[str, dict] = {}  # system -> its latest HERB-Drift result (the stale column)
    for p in paths:  # names start with the date, so a later file replaces an earlier one
        r = json.loads(p.read_text(encoding="utf-8"))
        if "system" not in r:  # e.g. experts_dev.json (10.8), not a system run
            continue
        if r["split"] == "drift":
            drift[r["system"]] = r
        else:
            latest[(r["system"], r["split"])] = r
    head = [
        "System",
        *[label for _, label in CATEGORIES],
        "Avg",
        "Unanswerable",
        "Stale-answer rate",
        "p50 answer s",
        "tokens/answer",
    ]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for (system, split), r in sorted(latest.items()):
        cells = [
            f"{system} ({split}, {r['questions']['answerable']}+{r['questions']['unanswerable']} q)"
        ]
        cells += [fmt(r["categories"][k]["score"]) for k, _ in CATEGORIES]
        cells += [
            fmt(r["avg"]),
            fmt(r["unanswerable"]),
            stale_cell(drift.get(system)),
            fmt(r["latency_s"]["p50"]),
            fmt(r["tokens_per_answer"], 0),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append(
        f"| {HERB_REF['system']} | — | — | — | — | {HERB_REF['avg']} | "
        f"{HERB_REF['unanswerable']} | n/a | n/a | n/a |"
    )
    judges = sorted({r["models"]["judge"] for r in latest.values()})
    lines += [
        "",
        f"Judge: {', '.join(judges)} with HERB's prompts, standing in for gpt-4o (D035). "
        "Content is a 0-100 Likert score; People, Customer and Artifact are F1 x 100; Avg is "
        "their mean; Unanswerable is the % of unanswerable questions left unanswered. The "
        "reference row is HERB's published product-specific result: a different answer "
        "model and setup, so context only.",
        "",
        "Run-to-run spread: answers are sampled (Haiku 5.5 takes no temperature), so one dev run "
        "of the same system moves by a few Avg points. The baseline scored 16.4 and 13.8 on two "
        "13 Oct runs; between them only the search changed (4.9). Per question, scores moved "
        "both ways and followed the model's choice to answer partly or abstain, not what search "
        "found (D043).",
    ]
    return "\n".join(lines) + "\n"


def stale_cell(r: dict | None) -> str:
    if r is None or r["stale_answer_rate"] is None:
        return "n/a"
    return f"{r['stale_answer_rate']:.1f}% ({r['correct_pct']:.0f}% right, n={r['n']})"


def write_table() -> Path:
    path = results_dir() / "table.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Results\n\nGenerated by `lore eval-report` from the JSON files here; "
        "don't edit by hand.\n\n" + table(),
        encoding="utf-8",
        newline="\n",
    )
    return path


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def pct(flags: list[bool]) -> float | None:
    return round(100 * mean([bool(f) for f in flags]), 1) if flags else None


def fmt(value: float | None, digits: int = 2, none: str = "—") -> str:
    return none if value is None else f"{value:.{digits}f}"
