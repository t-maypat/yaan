"""Re-validate a stored eval run offline (D099): what would today's citation validator drop?

Each answer in eval/runs/<run>/answers.jsonl keeps its tool trace. The tools are deterministic
reads of the graph, so replaying the trace rebuilds exactly what the validator saw (the
artifacts returned, their texts, urls and records); the stored final answer is then checked
again. No LLM call: the repair round a new problem would trigger isn't replayed, so this
measures the worst case (everything newly failing is dropped).

For answers scored by F1 (person, company, url, pr) the ground-truth items still present after
the drops are counted, an offline stand-in for the judge's extraction.

    uv run --project backend python -m app.eval.revalidate eval/runs/<run>_dev

Dev runs only: held-out answers are never read (rule 3).
"""

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from app import db
from app.agent import loop, tools, validator


def replay(row: dict[str, Any], graph: str | None = None) -> dict[str, Any]:
    """The validator's inputs for one stored answer, rebuilt by re-running its tool calls."""
    state: dict[str, Any] = {"seen": set(), "texts": {}, "urls": {}, "related": {}, "facts": {}}
    scope = tools.Scope(row["product"], None, graph)
    for entry in row["trace"]:
        if entry.get("error"):
            continue
        try:
            out = loop.call_tool(entry["tool"], entry["input"], scope)
        except (loop.ToolError, db.QueryError):
            continue
        loop.collect(
            out,
            state["seen"],
            state["texts"],
            state["urls"],
            state["related"],
            facts=state["facts"],
        )
        loop.row_facts(out, state["facts"])
    return state


def revalidate(row: dict[str, Any], graph: str | None = None) -> dict[str, Any]:
    """Today's validator on one stored final answer: its problems and what it would drop."""
    if row.get("error") or row["abstain"] or not row["citations"]:
        return {"id": row["id"], "skipped": True}
    state = replay(row, graph)
    answer = {
        "answer_markdown": row["answer"],
        "citations": [{k: c[k] for k in ("n", "artifact_id", "quote")} for c in row["citations"]],
        "abstain": False,
    }
    texts = {k: list(v) for k, v in state["texts"].items()}
    cited = {c["artifact_id"] for c in answer["citations"]} & state["seen"]
    for aid, more in loop.graph_texts(sorted(cited), graph).items():
        texts[aid] = texts.get(aid, []) + more
    report = validator.check(answer, state["seen"], texts, state["facts"], state["related"])
    cleaned, dropped = validator.clean(answer, report)
    out = {
        "id": row["id"],
        "type": row["type"],
        "problems": report.problems,
        "dropped": dropped,
        "abstain_now": cleaned["abstain"],
    }
    truth = row.get("ground_truth")
    if row["type"] != "content" and isinstance(truth, list) and truth:
        out["truth_before"] = hits(truth, row["answer"])
        out["truth_after"] = hits(truth, "" if cleaned["abstain"] else cleaned["answer_markdown"])
        out["truth_total"] = len(truth)
    return out


def hits(truth: list[Any], text: str) -> int:
    """How many ground-truth items (ids, links, company names) appear in the text."""
    t = text.casefold()
    return sum(1 for item in truth if str(item).casefold().rstrip("/") in t)


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    checked = [r for r in results if not r.get("skipped")]
    kinds: Counter[str] = Counter()
    for r in checked:
        for p in r["problems"]:
            kinds[problem_kind(p)] += 1
    scored = [r for r in checked if "truth_total" in r]
    return {
        "answers_checked": len(checked),
        "answers_with_new_problems": sum(1 for r in checked if r["problems"]),
        "sentences_dropped": sum(len(r["dropped"]) for r in checked),
        "answers_now_abstaining": sum(1 for r in checked if r["abstain_now"]),
        "problems_by_rule": dict(kinds.most_common()),
        "truth_items_lost": sum(r["truth_before"] - r["truth_after"] for r in scored),
        "truth_items_found_before": sum(r["truth_before"] for r in scored),
    }


def problem_kind(problem: str) -> str:
    for key, kind in (
        ("too short", "4 quote too short"),
        ("share no word", "5 no overlap"),
        ("is in none of", "6 identifier not in cited"),
        ("not word for word", "2 quote not verbatim"),
        ("no tool returned", "1 not retrieved"),
        ("no valid citation", "3 uncited"),
    ):
        if key in problem:
            return kind
    return "other"


def main(run_dir: str) -> None:
    folder = Path(run_dir)
    if "_test" in folder.name or "heldout" in folder.name:
        raise SystemExit("held-out runs are never read (rule 3): pass a dev run")
    rows = [json.loads(line) for line in (folder / "answers.jsonl").open(encoding="utf-8")]
    results = [revalidate(r) for r in rows]
    summary = summarize(results)
    path = folder / "revalidate.json"
    path.write_text(
        json.dumps({"summary": summary, "answers": results}, indent=1, ensure_ascii=False),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=1))
    print(f"details: {path}")


if __name__ == "__main__":
    main(sys.argv[1])
