"""The deterministic citation validator (9.0): three rules, then clean-up or abstention."""

from app.agent import validator
from app.agent.contract import SCHEMA, Answer

TEXTS = {
    "msg:1": ["Let's revert PR 26 given the new product direction."],
    "pr:26": ["Optimize GraphQL Queries", "https://github.com/salesforce/ainAIX/pull/26"],
}
SEEN = {"msg:1", "pr:26"}


def answer(markdown, citations, abstain=False):
    return {
        "answer_markdown": markdown,
        "citations": [{"n": n, "artifact_id": a, "quote": q} for n, a, q in citations],
        "abstain": abstain,
    }


def test_a_fully_cited_answer_passes():
    a = answer(
        "PR 26 was reverted for the new product direction [1].\n"
        "- https://github.com/salesforce/ainAIX/pull/26 [2]",
        [(1, "msg:1", "revert PR 26 given the new  PRODUCT direction"), (2, "pr:26", "pull/26")],
    )
    assert validator.check(a, SEEN, TEXTS).ok


def test_each_rule_is_reported():
    a = answer(
        "Reverted [1]. Merged later [2]. Nobody objected. Unknown [3].",
        [(1, "msg:1", "we reverted it"), (2, "msg:9", "merged")],
    )
    report = validator.check(a, SEEN, TEXTS)
    assert report.bad == {1, 2}
    text = " ".join(report.problems)
    assert "not word for word in msg:1" in text  # rule 2
    assert "msg:9, which no tool returned" in text  # rule 1
    assert "[3] has no entry in citations" in text
    assert "no valid citation for: 'Nobody objected.'" in text  # rule 3


def test_headings_and_list_intros_need_no_citation():
    a = answer("## PRs\nThe reverted PRs are:\n- pull/26 [1]", [(1, "pr:26", "pull/26")])
    assert validator.check(a, SEEN, TEXTS).ok


def test_a_marker_after_the_full_stop_still_counts():
    a = answer("PR 26 was reverted. [1]", [(1, "msg:1", "revert PR 26")])
    assert validator.check(a, SEEN, TEXTS).ok


def test_clean_drops_what_fails_and_keeps_the_rest():
    a = answer(
        "PR 26 was reverted [1]. It shipped in May [2].\n- pull/26 [3]\n- pull/99 [2]",
        [(1, "msg:1", "revert PR 26"), (2, "msg:1", "shipped in May"), (3, "pr:26", "pull/26")],
    )
    report = validator.check(a, SEEN, TEXTS)
    cleaned, dropped = validator.clean(a, report)
    assert cleaned["answer_markdown"] == "PR 26 was reverted [1].\n- pull/26 [3]"
    assert [c["n"] for c in cleaned["citations"]] == [1, 3] and not cleaned["abstain"]
    assert dropped == ["It shipped in May [2].", "- pull/99 [2]"]


def test_nothing_valid_left_means_abstain():
    a = answer("It shipped in May [1].", [(1, "msg:1", "shipped in May")])
    cleaned, _ = validator.clean(a, validator.check(a, SEEN, TEXTS))
    assert cleaned["abstain"] and cleaned["citations"] == []


def test_an_abstention_is_not_checked():
    a = answer("Not found.", [], abstain=True)
    assert validator.check(a, set(), {}).ok


def test_the_schema_matches_the_model():
    assert set(SCHEMA["properties"]) == set(Answer.model_fields)
    assert SCHEMA["additionalProperties"] is False


def test_a_list_item_is_backed_by_its_cited_sub_items():
    a = answer(
        "Reverted PRs:\n"
        "- https://github.com/salesforce/ainAIX/pull/26\n"
        "  - Reverted for the new product direction [1].\n"
        "- https://github.com/salesforce/ainAIX/pull/99\n"
        "  - No source for this one.",
        [(1, "msg:1", "revert PR 26 given the new product direction")],
    )
    report = validator.check(a, SEEN, TEXTS)
    assert report.uncited == [
        "- https://github.com/salesforce/ainAIX/pull/99",
        "- No source for this one.",
    ]
    cleaned, _ = validator.clean(a, report)
    assert cleaned["answer_markdown"] == (
        "Reverted PRs:\n- https://github.com/salesforce/ainAIX/pull/26\n"
        "  - Reverted for the new product direction [1]."
    )
