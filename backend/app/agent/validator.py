"""Citation validator, deterministic (Phase 9.0; spec docs/CONTEXT.md §9.6).

Three rules, checked in code (no LLM):
1. a cited artifact must have come back from a tool in this turn;
2. its quote must appear word for word (ignoring case and whitespace) in that artifact;
3. every sentence or list item that states something needs at least one [n] marker that
   points at a valid citation.
The loop gives the model one repair round with the problems listed; `clean` then drops
whatever still fails, and an answer with nothing left becomes an abstention.
"""

import re
from dataclasses import dataclass, field
from typing import Any

from app import llm

MARKER = re.compile(r"\[(\d+)\]")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")


@dataclass
class Report:
    problems: list[str] = field(default_factory=list)
    bad: set[int] = field(default_factory=set)  # citation numbers that failed rule 1 or 2
    uncited: list[str] = field(default_factory=list)  # units that break rule 3

    @property
    def ok(self) -> bool:
        return not self.problems


def units(line: str) -> list[str]:
    """A line's sentences. A marker-only fragment ("… merged. [1]") joins the sentence before."""
    parts: list[str] = []
    for piece in SENTENCE_END.split(line.strip()):
        if parts and not re.sub(r"[\s\[\]\d.,;]", "", piece):
            parts[-1] = f"{parts[-1]} {piece}"
        elif piece:
            parts.append(piece)
    return parts


def needs_citation(unit: str) -> bool:
    """Headings, list intros ending in ':' and bare punctuation state nothing."""
    text = BULLET.sub("", MARKER.sub("", unit)).strip()
    if not text or text.startswith("#") or text.endswith(":"):
        return False
    return bool(re.search(r"\w", text))


def check(answer: dict[str, Any], seen: set[str], texts: dict[str, list[str]]) -> Report:
    """Validate an answer against what tools returned (`seen`) and the artifacts' texts."""
    report = Report()
    if answer["abstain"]:
        return report
    numbers = set()
    for c in answer["citations"]:
        numbers.add(c["n"])
        if c["artifact_id"] not in seen:
            report.bad.add(c["n"])
            report.problems.append(
                f"[{c['n']}] cites {c['artifact_id']}, which no tool returned in this answer"
            )
        elif not any(llm.quote_in(c["quote"], t) for t in texts.get(c["artifact_id"], [])):
            report.bad.add(c["n"])
            report.problems.append(
                f"[{c['n']}] quote is not word for word in {c['artifact_id']}: {c['quote'][:120]!r}"
            )
    for line in answer["answer_markdown"].splitlines():
        for unit in units(line):
            markers = {int(m) for m in MARKER.findall(unit)}
            if markers - numbers:
                missing = ", ".join(f"[{n}]" for n in sorted(markers - numbers))
                report.problems.append(f"{missing} has no entry in citations: {unit[:120]!r}")
            if needs_citation(unit) and not (markers & numbers) - report.bad:
                report.uncited.append(unit)
                report.problems.append(f"no valid citation for: {unit[:160]!r}")
    return report


def clean(answer: dict[str, Any], report: Report) -> tuple[dict[str, Any], list[str]]:
    """Drop failing citations and every unit without a valid one; abstain if nothing stays."""
    if answer["abstain"]:
        return answer, []
    valid = {c["n"] for c in answer["citations"]} - report.bad
    kept_lines, dropped = [], []
    for line in answer["answer_markdown"].splitlines():
        kept = []
        for unit in units(line):
            markers = {int(m) for m in MARKER.findall(unit)}
            if needs_citation(unit) and not markers & valid:
                dropped.append(unit)
                continue
            kept.append(MARKER.sub(lambda m: m.group(0) if int(m.group(1)) in valid else "", unit))
        if kept:
            body = " ".join(kept)
            bullet = BULLET.match(line)
            if bullet and not BULLET.match(body):  # the bullet's own sentence was dropped
                body = f"{bullet.group(0).strip()} {body}"
            kept_lines.append(body)
        elif not line.strip():
            kept_lines.append("")
    markdown = "\n".join(kept_lines).strip()
    used = {int(m) for m in MARKER.findall(markdown)}
    citations = [c for c in answer["citations"] if c["n"] in used]
    stated = any(needs_citation(u) for line in markdown.splitlines() for u in units(line))
    return {"answer_markdown": markdown, "citations": citations, "abstain": not stated}, dropped
