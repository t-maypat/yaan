"""The eval harness (Phase 5): HERB's scoring rules, the run folder, the results summary.

No LLM is called: the judge runs on test_llm's fake Anthropic client, and run_herb on a
fake system. Expected numbers come from running HERB's own evaluate.py on a made-up output
with a stub judge (docs/decisions.md D035).
"""

import json

import pytest
from test_llm import gateway, response  # noqa: F401  (pytest fixture + reply builder)

from app import llm
from app.eval import judge, report, run_herb


# --- HERB's text rules ----------------------------------------------------------------------
def test_normalize_and_f1_match_herb():
    assert judge.normalize("The Acme Corp.") == "acme corp"
    assert judge.f1_sets(["Acme Corp"], ["The Acme Corp."]) == 1.0
    assert judge.f1_sets(["eid_aaa", "eid_ccc"], ["eid_aaa", "eid_bbb"]) == 0.5
    assert judge.f1_sets(["https://u.example/1"], []) == 0.0
    # punctuation goes before articles are removed, as in HERB
    assert judge.normalize("https://github.com/salesforce/VizForce/pull/14") == (
        "httpsgithubcomsalesforcevizforcepull14"
    )


def test_missing_answers_and_think_tags_are_handled_as_in_herb():
    assert judge.answer_text(None) == "I don't know."
    assert judge.answer_text("<think>hm</think> eid_1") == "eid_1"


def test_prompts_render_like_herb():
    likert = judge.LIKERT.format(question="Q", reference="R", candidate="C")
    assert likert.endswith(
        "Candidate Answer: C\nRespond in json format: {'score': 'Your overall rating between "
        "0-100', 'reason': 'a brief justification'}'."
    )
    prs = judge.PRS.format(question="Q", content="A")
    assert "steps.- If the question asks which PRs were reverted" in prs  # HERB's missing \n
    assert judge.BUCKET["pr"] == "url"


# --- the judge on the fake client ------------------------------------------------------------
def test_content_is_a_likert_score(gateway):  # noqa: F811
    gateway.replies.append(response(parsed=judge.Likert(score=70, reason="ok")))
    v = judge.score_answerable("Q?", "content", "ref", "answer")
    assert (v["bucket"], v["score"]) == ("content", 70)
    assert gateway.calls[0]["model"] == gateway.settings.lore_judge_model
    assert gateway.calls[0]["output_config"] == {"effort": gateway.settings.lore_judge_effort}


def test_out_of_range_likert_fails_loudly(gateway):  # noqa: F811
    gateway.replies.append(response(parsed=judge.Likert(score=170, reason="?")))
    with pytest.raises(llm.LLMError, match="0-100"):
        judge.score_answerable("Q?", "content", "ref", "answer")


def test_lists_are_extracted_then_scored_by_f1(gateway):  # noqa: F811
    gateway.replies.append(response(parsed=judge.Links(links=["https://x/pull/1"])))
    v = judge.score_answerable("Which PRs?", "pr", ["https://x/pull/1", "https://x/pull/2"], "a")
    assert v["bucket"] == "url" and v["score"] == pytest.approx(2 / 3)
    assert "pull request links" in gateway.calls[0]["messages"][0]["content"]


def test_unanswerable_needs_no_call_without_an_answer(gateway):  # noqa: F811
    assert judge.score_unanswerable("Q?", None)["unanswered"] and gateway.calls == []
    gateway.replies.append(response(parsed=judge.Done(done="no", reason="says not found")))
    assert judge.score_unanswerable("Q?", "The artifacts don't say.")["unanswered"]
    prompt = gateway.calls[0]["messages"][0]["content"]
    assert prompt.endswith('{"done": "yes/no", "reason": "justification for the decision"}')


# --- the run folder ----------------------------------------------------------------------------
ROWS = [
    {
        "id": "P:ans:0",
        "product": "P",
        "kind": "ans",
        "question": "Who?",
        "type": "person",
        "ground_truth": ["eid_1"],
    },
    {
        "id": "P:ans:1",
        "product": "P",
        "kind": "ans",
        "question": "What?",
        "type": "content",
        "ground_truth": "ref",
    },
    {
        "id": "P:unans:0",
        "product": "P",
        "kind": "unans",
        "question": "U0?",
        "type": "unanswerable",
        "ground_truth": None,
    },
    {
        "id": "P:unans:1",
        "product": "P",
        "kind": "unans",
        "question": "U1?",
        "type": "unanswerable",
        "ground_truth": None,
    },
]


@pytest.fixture
def fake_run(tmp_path, monkeypatch):
    settings = run_herb.get_settings().model_copy(update={"eval_dir": tmp_path / "eval"})
    for mod in (run_herb, report):
        monkeypatch.setattr(mod, "get_settings", lambda: settings)
    monkeypatch.setattr(run_herb, "questions", lambda split, products: [dict(r) for r in ROWS])

    def respond(question, context):
        if question == "U1?":
            raise llm.Refused("declined")
        usage = {
            "model": "m",
            "input_tokens": 100,
            "output_tokens": 20,
            "cost_usd": 0.001,
            "cached": False,
        }
        return {
            "answer_markdown": f"answer to {question} from {context}",
            "abstain": False,
            "citations": [{"retrieved": True, "quote_ok": True}],
            "retrieved": ["a"],
            "llm": usage,
        }

    system = run_herb.System(prepare=lambda q, p: f"ctx:{p}", respond=respond)
    monkeypatch.setattr(run_herb, "get_system", lambda name: system)
    return run_herb.run("baseline", "dev", workers=2, run_id="20261013T0000Z_baseline_dev")


def test_a_run_writes_answers_and_herb_format(fake_run):
    rows = run_herb.read_jsonl(fake_run / "answers.jsonl")
    assert [r["id"] for r in rows] == [r["id"] for r in ROWS]  # input order, not finish order
    assert rows[0]["answer"] == "answer to Who? from ctx:P"
    assert rows[3]["answer"] is None and rows[3]["error"].startswith("Refused")
    herb_ans = json.loads((fake_run / "herb-ans.json").read_text("utf-8"))
    assert set(herb_ans) == {"question", "answer", "ground_truth", "type"}
    assert herb_ans["ground_truth"] == [["eid_1"], "ref"]
    assert set(json.loads((fake_run / "herb-unans.json").read_text("utf-8"))) == {
        "question",
        "answer",
    }


def test_a_partial_run_keeps_its_results_out_of_the_table(fake_run, monkeypatch):
    smoke = run_herb.run("baseline", "dev", limit=2, run_id="20261013T0001Z_baseline_dev")
    monkeypatch.setattr(
        report,
        "judge_row",
        lambda row: {"id": row["id"], "bucket": "person", "score": 1.0, "judge_cost_usd": 0.0},
    )
    assert report.score_run(smoke, workers=1) == smoke / "results.json"
    assert not report.results_dir().exists()


def test_a_check_run_answers_only_the_given_ids(fake_run):
    ids = [ROWS[2]["id"], ROWS[0]["id"]]
    check = run_herb.run("baseline", "dev", ids=ids, run_id="20261013T0002Z_baseline_dev")
    rows = run_herb.read_jsonl(check / "answers.jsonl")
    assert [r["id"] for r in rows] == [ROWS[0]["id"], ROWS[2]["id"]]  # split order
    assert json.loads((check / "meta.json").read_text("utf-8"))["partial"] is True
    with pytest.raises(ValueError, match="aren't in the dev split"):
        run_herb.run("baseline", "dev", ids=["Nope:ans:0"], run_id="20261013T0003Z_baseline_dev")


def test_spend_cap_stops_the_whole_run(fake_run, monkeypatch):
    def broke(question, context):
        raise llm.SpendCapReached("cap")

    monkeypatch.setattr(
        run_herb,
        "get_system",
        lambda name: run_herb.System(prepare=lambda q, p: None, respond=broke),
    )
    with pytest.raises(llm.SpendCapReached):
        run_herb.run("baseline", "dev", run_id="x_baseline_dev")


def test_summary_scales_and_never_credits_errors(fake_run, monkeypatch):
    verdicts = {
        "P:ans:0": {"bucket": "person", "score": 0.5},
        "P:ans:1": {"bucket": "content", "score": 70},
        "P:unans:0": {"unanswered": True},
    }

    real_judge_row = report.judge_row  # errored rows never reach the judge

    def fake_judge(row):
        if row["error"]:
            return real_judge_row(row)
        return {"id": row["id"], **verdicts[row["id"]], "judge_cost_usd": 0.0002}

    monkeypatch.setattr(report, "judge_row", fake_judge)
    results = json.loads(report.score_run(fake_run, workers=1).read_text("utf-8"))
    assert results["categories"]["person"] == {"n": 1, "score": 50.0}  # F1 x 100
    assert results["categories"]["content"] == {"n": 1, "score": 70.0}
    assert results["avg"] == 60.0  # mean of the categories present
    assert results["herb_script_avg"] == 35.25  # evaluate.py's mixed-scale average
    assert results["unanswerable"] == 50.0  # the refused one is not a correct abstention
    assert results["errors"] == 1
    table = report.table()
    assert "| baseline (dev, 2+2 q) | 70.00 | 50.00 | — | — | 60.00 | 50.00 |" in table
    assert "43.41" in table
