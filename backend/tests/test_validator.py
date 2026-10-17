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


def test_autocite_cites_lines_that_name_a_returned_artifacts_url():
    from app.agent.validator import autocite, check

    url = "https://github.com/salesforce/W/pull/12"
    answer = {
        "answer_markdown": f"Reverted PRs:\n- PR #12: {url}.\n- PR #99: https://example.com/x\n"
        "  Its revert says it broke the API [1].",
        "citations": [{"n": 1, "artifact_id": "msg:a", "quote": "broke the API"}],
        "abstain": False,
    }
    out, added = autocite(answer, {url: "pr:w12"})
    assert added == [2]
    assert out["answer_markdown"].splitlines()[1] == f"- PR #12: {url} [2]."
    assert out["citations"][-1] == {"n": 2, "artifact_id": "pr:w12", "quote": url}
    assert "example.com/x" in out["answer_markdown"].splitlines()[2]  # unknown url: untouched
    report = check(out, {"msg:a", "pr:w12"}, {"msg:a": ["It broke the API."], "pr:w12": [url]})
    assert not any("PR #12" in p for p in report.problems)
    assert autocite({**answer, "abstain": True}, {url: "pr:w12"})[1] == []


def test_autocite_works_per_sentence_when_another_sentence_on_the_line_is_cited():
    from app.agent.validator import autocite

    url = "https://github.com/salesforce/W/pull/12"
    answer = {
        "answer_markdown": f"  - PR #12: {url}. Its revert says it broke the API [1].",
        "citations": [{"n": 1, "artifact_id": "msg:a", "quote": "broke the API"}],
        "abstain": False,
    }
    out, added = autocite(answer, {url: "pr:w12"})
    assert added == [2]
    assert out["answer_markdown"] == f"  - PR #12: {url} [2]. Its revert says it broke the API [1]."


def test_reattribute_moves_a_quote_to_the_one_related_artifact_that_holds_it():
    """D070: a revert PR's words cited on the PR it reverted."""
    texts = {
        "pr:3": ["Add AES-256 encryption at rest"],
        "pr:5": ["Revert PR# 3. This revert is necessary due to a change in product direction."],
        "pr:14": ["Revert PR# 12. This revert is necessary due to a change in product direction."],
    }
    related = {"pr:3": {"pr:5"}, "pr:5": {"pr:3"}, "pr:12": {"pr:14"}, "pr:14": {"pr:12"}}
    a = answer(
        "- PR #3 was reverted for the product direction [1].",
        [(1, "pr:3", "This revert is necessary due to a change in product direction.")],
    )
    out, moved = validator.reattribute(a, related, texts)
    assert moved == [1] and out["citations"][0]["artifact_id"] == "pr:5"
    assert validator.check(out, {"pr:3", "pr:5", "pr:14"}, texts).ok
    # two related artifacts hold the quote: no guess, the citation keeps failing
    both = {"pr:3": {"pr:5", "pr:14"}}
    assert validator.reattribute(a, both, texts) == (a, [])
    # a quote already in its artifact, an unrelated holder, an abstention: untouched
    ok = answer("PR #3 adds encryption [1].", [(1, "pr:3", "AES-256 encryption")])
    assert validator.reattribute(ok, related, texts) == (ok, [])
    assert validator.reattribute(a, {}, texts) == (a, [])
    assert validator.reattribute({**a, "abstain": True}, related, texts)[1] == []
