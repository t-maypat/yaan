"""Scoring: a faithful port of HERB's code/evaluate.py with the judge model swapped (D035).

HERB (SalesforceAIResearch/HERB, CC BY-NC 4.0) scores with gpt-4o. We can't pay for
OpenAI, so the same prompts, copied word for word below, go to LORE_JUDGE_MODEL through
app/llm.py. Everything else follows evaluate.py:
- content questions: a 0-100 Likert score against the reference answer;
- person / pr / url / company: the judge extracts the ids, links or names that answer the
  question, then set F1 against ground truth after HERB's normalize(); pr counts as url;
- unanswerable questions: a missing answer, or a judge "no" (the answer doesn't address the
  question), counts as correctly unanswered;
- a missing answer to an answerable question is scored as "I don't know.".

Deviations (declared in D035): the judge model; JSON comes back as structured output
instead of gpt-4o's JSON mode; temperature can't be set on Haiku 5.5 (the API rejects
non-default sampling), so repeat runs are pinned by the gateway's cache instead; a short
system line, since HERB sends only a user message.

The stale-answer judge (CONTEXT §7.2, §7.3) is ours, for the HERB-Drift set: it compares an
answer with the state that holds at the asked moment and the states that held at other
times, and calls an answer stale when it presents one of those as the answer.
"""

import re
import string
from typing import Any, Literal

from pydantic import BaseModel

from app import llm

SYSTEM = "You are an evaluation judge. Follow the instructions in the user message exactly."
MAX_TOKENS = 4096  # thinking counts toward it on Haiku 5.5
# HERB answer type -> scoring bucket. evaluate.py folds pr into url.
BUCKET = {"content": "content", "person": "person", "company": "company", "url": "url", "pr": "url"}

# --- HERB's prompts, verbatim (code/evaluate.py) --------------------------------------------
UNANSWERABLE = """Given a question and its corresponding answer, determine whether the answer provides sufficient information to fully or even partially respond to the question.

- Reply "Yes" if the answer directly addresses the question, either fully or partially, using information relevant to the question.
- Reply "No" if the answer does not address the question due to missing, vague, or insufficient information — even if it explicitly states that the information is not available.

Question: Which products were affected by the bug in March?
Answer:  The context does not provide the specific information required to determine the products.
Output: No

Question: Find all authors and reviewers of the PRD?
Answer: John authored the PRD
Output: Yes

Question: Find all authors and reviewers of the PRD?
Answer: John authored the PRD. Yuan and Jessica reviewed the PRD.
Output: Yes
"""  # noqa: E501

PRS = (
    "The following is a question and its answer. The answer may include reasoning and a final answer. Extract only the pull request links that directly answer the question—that is, the links that refer to the specific PRs being asked about."  # noqa: E501
    "\n#Instructions:\n"
    "- Ignore PR links mentioned in reasoning or intermediate steps."
    "- If the question asks which PRs were reverted, extract the original PRs that were reverted, not the PRs that reverted them."  # noqa: E501
    "- If no pull request links directly answer the question, {{'links': []}}."
    "\n\nQuestion: {question}"
    "\n\nAnswer: {content}"
    "\n\nRespond in json format: {{'links': ['list of links']}}"
)

URLS = (
    "The following is a question and its answer. The answer may include reasoning and a final answer. Extract only the URLs that directly answer the question—ignore any links mentioned in the reasoning or intermediate steps. If no URLs are part of the final answer, return {{'links': []}}."  # noqa: E501
    "\n\nQuestion: {question}"
    "\n\nAnswer: {content}"
    "\n\nRespond in json format: {{'links': ['list of links']}}"
)

COMPANIES = (
    "The following is a question and its answer. The answer may include reasoning and a final answer. Extract only the company names that directly answer the question—ignore any names mentioned in the reasoning or intermediate steps. If no company names are part of the final answer, return {{'names': []}}."  # noqa: E501
    "\n\nQuestion: {question}"
    "\n\nAnswer: {content}"
    "\n\nRespond in JSON format: {{'names': ['list of company names']}}"
)

EMPLOYEE_IDS = (
    "The following is a question and its answer. The answer may include reasoning and a final answer. Extract only the employee IDs that directly answer the question—ignore any IDs mentioned in the reasoning or intermediate steps. If no employee IDs are part of the final answer, return {{'ids': []}}."  # noqa: E501
    "\n\nQuestion: {question}"
    "\n\nAnswer: {content}"
    "\n\nRespond in json format: {{'ids': ['list of employee IDs']}}"
)

LIKERT = (
    "You are an expert evaluator. Given a question, a reference answer, "
    "and a candidate answer, your task is to evaluate how well the candidate "
    "answer aligns with the reference.\n\nFor your evaluation:"
    "\nFocus on accuracy, completeness, and relevance."
    "\nIf the candidate includes extra information, check if it's correct and appropriate."
    "\nIf it omits key points from the reference, mention that."
    "\nQuestion: {question}\nReference Answer: {reference}\nCandidate Answer: {candidate}"
    "\nRespond in json format: {{'score': 'Your overall rating between 0-100', "
    "'reason': 'a brief justification'}}'."
)


# --- structured outputs: the keys HERB's prompts ask for --------------------------------------
class Done(BaseModel):
    done: Literal["yes", "no"]
    reason: str


class Links(BaseModel):
    links: list[str]


class Names(BaseModel):
    names: list[str]


class Ids(BaseModel):
    ids: list[str]


class Likert(BaseModel):
    score: int
    reason: str


class DriftVerdict(BaseModel):
    verdict: Literal["correct", "stale", "abstained", "wrong"]
    reason: str


DRIFT = (
    "You judge whether an answer gets the state of a company record right for the moment the "
    "question asks about (now, or a date the question names).\n\n"
    "Question: {question}\n"
    "Reference answer: {reference}\n"
    "The correct state at that moment: {correct}\n"
    "Stale states (true at another time, wrong for this moment): {stale}\n"
    "Candidate answer: {candidate}\n\n"
    "Classify the candidate answer:\n"
    "- correct: it gives the correct state as its answer (extra true detail is fine);\n"
    "- stale: it presents one of the stale states as the state at the asked moment;\n"
    "- abstained: it says it can't find or can't answer;\n"
    "- wrong: anything else (another wrong state, or no clear state).\n"
    'Respond in JSON: {{"verdict": "correct|stale|abstained|wrong", "reason": "one sentence"}}'
)


EXTRACTORS: dict[str, tuple[str, type[BaseModel], str]] = {
    "person": (EMPLOYEE_IDS, Ids, "ids"),
    "url": (URLS, Links, "links"),
    "pr": (PRS, Links, "links"),
    "company": (COMPANIES, Names, "names"),
}


# --- HERB's text handling ---------------------------------------------------------------------
def normalize(answer: str) -> str:
    """HERB's normalize(): lowercase, drop punctuation, then articles, collapse whitespace."""
    text = "".join(ch for ch in answer.lower() if ch not in set(string.punctuation))
    text = re.sub(r"\b(a|an|the)\b", " ", text)
    return " ".join(text.split())


def f1_sets(truth: list[str], predicted: list[str]) -> float:
    """HERB's f1_score_sets(): F1 over the sets of normalized strings."""
    t = {normalize(e) for e in truth}
    p = {normalize(e) for e in predicted}
    tp = len(t & p)
    precision = tp / len(p) if p else 0.0
    recall = tp / len(t) if t else 0.0
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def answer_text(answer: str | None) -> str:
    """What evaluate.py scores for an answerable question: None -> "I don't know."."""
    text = "I don't know." if answer is None else answer
    return text.split("</think>")[-1].strip() if "</think>" in text else text


# --- the judge calls --------------------------------------------------------------------------
def ask(user: str, output: type[BaseModel]) -> llm.LLMResult:
    return llm.complete(
        role="judge",
        system=SYSTEM,
        user=user,
        output=output,
        purpose="judge",
        max_tokens=MAX_TOKENS,
    )


def score_answerable(question: str, qtype: str, reference: Any, answer: str | None) -> dict:
    """One answerable question -> {bucket, score (0-100 or F1 0-1), extracted, judge_cost_usd}."""
    if qtype not in BUCKET:
        raise ValueError(f"unknown HERB question type {qtype!r}")
    text = answer_text(answer)
    if qtype == "content":
        out = ask(LIKERT.format(question=question, reference=reference, candidate=text), Likert)
        score = out.parsed["score"]
        if not 0 <= score <= 100:
            raise llm.LLMError(f"judge gave a Likert score outside 0-100: {score}")
        return {
            "bucket": "content",
            "score": score,
            "reason": out.parsed["reason"],
            "judge_cost_usd": out.cost_usd,
        }
    template, model, key = EXTRACTORS[qtype]
    out = ask(template.format(question=question, content=text), model)
    extracted = out.parsed[key]
    return {
        "bucket": BUCKET[qtype],
        "score": f1_sets(reference, extracted),
        "extracted": extracted,
        "judge_cost_usd": out.cost_usd,
    }


def score_drift(question: str, item: dict[str, Any], answer: str | None) -> dict:
    """One HERB-Drift question -> {verdict, correct, stale}. A missing answer abstains."""
    if answer is None:
        return {"verdict": "abstained", "correct": False, "stale": False, "judge_cost_usd": 0.0}
    user = DRIFT.format(
        question=question,
        reference=item["answer"],
        correct=item["correct"],
        stale="; ".join(item["stale"]),
        candidate=answer_text(answer),
    )
    out = ask(user, DriftVerdict)
    verdict = out.parsed["verdict"]
    return {
        "verdict": verdict,
        "correct": verdict == "correct",
        "stale": verdict == "stale",
        "reason": out.parsed["reason"],
        "judge_cost_usd": out.cost_usd,
    }


def score_unanswerable(question: str, answer: str | None) -> dict:
    """One unanswerable question -> {unanswered: bool}. True is the right outcome."""
    if answer is None:
        return {"unanswered": True, "reason": "no answer", "judge_cost_usd": 0.0}
    user = UNANSWERABLE + (
        f"\n\nQuestion: {question}\n\nAnswer: {answer}\n\nRespond using the below json format:"
        '\n{"done": "yes/no", "reason": "justification for the decision"}'
    )
    out = ask(user, Done)
    return {
        "unanswered": out.parsed["done"] == "no",
        "reason": out.parsed["reason"],
        "judge_cost_usd": out.cost_usd,
    }
