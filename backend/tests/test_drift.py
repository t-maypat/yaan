"""HERB-Drift (13.1) and the Lore − validator variant (R2): loading, summaries, table cells.
No LLM: verdicts are written by hand."""

import json

import pytest

from app.eval import judge, report, run_herb


def test_drift_set_loads_as_rows_with_their_ground_truth():
    rows = run_herb.questions("drift")
    assert len(rows) >= 20 and len({r["id"] for r in rows}) == len(rows)
    assert {r["product"] for r in rows} == {"ExplainabilityForce", "CollaborateForce", "VizForce"}
    for r in rows:
        assert r["kind"] == "drift" and set(r["ground_truth"]) == {"answer", "correct", "stale"}
        assert r["ground_truth"]["stale"] and r["ground_truth"]["correct"]
    only = run_herb.questions("drift", ["VizForce"])
    assert only and {r["product"] for r in only} == {"VizForce"}


def test_a_missing_drift_answer_abstains_without_a_judge_call():
    v = judge.score_drift("q", {"answer": "a", "correct": "x", "stale": ["y"]}, None)
    assert v == {"verdict": "abstained", "correct": False, "stale": False, "judge_cost_usd": 0.0}


def row(i, kind="current_status"):
    return {
        "id": i,
        "type": kind,
        "error": None,
        "llm": {"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.001},
    }


def test_summarize_drift_counts_stale_over_answered_only():
    rows = [row("a"), row("b"), row("c", "as_of_status"), row("d", "as_of_status")]
    verdicts = [
        {"id": "a", "verdict": "correct", "correct": True, "stale": False, "judge_cost_usd": 0.0},
        {"id": "b", "verdict": "stale", "correct": False, "stale": True, "judge_cost_usd": 0.0},
        {
            "id": "c",
            "verdict": "abstained",
            "correct": False,
            "stale": False,
            "judge_cost_usd": 0.0,
        },
        {"id": "d", "verdict": "wrong", "correct": False, "stale": False, "judge_cost_usd": 0.0},
    ]
    meta = {"run_id": "r", "system": "lore", "products": ["P"], "answer_model": "m"}
    s = report.summarize_drift(meta, rows, verdicts)
    assert (s["n"], s["correct_pct"], s["abstained"], s["wrong"]) == (4, 25.0, 1, 1)
    assert s["stale_answer_rate"] == pytest.approx(33.3)  # 1 of the 3 answered
    assert s["by_kind"]["as_of_status"]["stale_answer_rate"] == 0.0
    assert report.stale_cell(s) == "33.3% (25% right, n=4)" and report.stale_cell(None) == "n/a"


def test_the_unvalidated_variant_needs_the_stored_first_answers(tmp_path):
    (tmp_path / "meta.json").write_text(json.dumps({"system": "lore", "split": "dev"}))
    plain = {"id": "x", "error": None, "answer": "A [1]", "abstain": False}
    (tmp_path / "answers.jsonl").write_text(json.dumps(plain) + "\n")
    with pytest.raises(ValueError, match="no 'unvalidated' field"):
        report.score_run(tmp_path, variant="unvalidated")
    with pytest.raises(ValueError, match="unknown variant"):
        report.score_run(tmp_path, variant="raw")
