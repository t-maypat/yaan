"""Citation validator, deterministic (Phase 9.0; spec docs/CONTEXT.md §9.6).

Three rules, checked in code (no LLM):
1. a cited artifact must have come back from a tool in this turn;
2. its quote must appear word for word (ignoring case and whitespace) in that artifact;
3. every sentence or list item that states something needs at least one [n] marker that
   points at a valid citation.
The loop gives the model one repair round with the problems listed; `clean` then drops
whatever still fails, and an answer with nothing left becomes an abstention.

`autocite` runs first: a line with no marker that names the exact url of an artifact a tool
returned gets a citation to that artifact, quoting the url (D051). The quote is still
checked word for word like any other; the model just forgot to put the marker on that line.

`reattribute` runs next (D070): a quote that isn't in the artifact it cites, but is word for
word in exactly one artifact nested in the same tool row (a PR and the PR that reverted it),
is pointed at that artifact. Every bad quote of the 17 Oct dev run was a revert PR's words
cited on the PR it reverted. Two or more candidates leave the citation failing.
"""

import re
from dataclasses import dataclass, field
from typing import Any

from app import llm

MARKER = re.compile(r"\[(\d+)\]")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])(?:\s+|$)")
URL = re.compile(r"https?://[^\s)\]>\"'`]+")

# Grounding, not just provenance (D099). A quote that is verbatim but says nothing ("a", an id)
# proved only that the artifact exists; these rules ask that it back the sentence:
# 4. a quote has at least MIN_QUOTE_WORDS words and MIN_QUOTE_CHARS characters, unless the
#    sentence citing it repeats it (a title, an id or a link the sentence names);
# 5. a sentence shares at least one content word or identifier with the quotes it cites;
# 6. every identifier in a sentence (an eid_, a CUST- id, a PR number, a link) is in the text
#    or the tool record (author, number, url) of an artifact the sentence cites.
MIN_QUOTE_WORDS = 3
MIN_QUOTE_CHARS = 10
AUTOCITE_EXTRA_WORDS = 2  # words a url line may add beyond the linked artifact's own text
IDENT = re.compile(
    r"https?://[^\s)\]>\"'`|]+|\beid_[0-9a-f]{8}\b|\bCUST-\d{3,}\b|\bPR\s*#?\s*\d+\b|(?<![\w/&])#\d+\b",
    re.I,
)
STOP = frozenset(
    """the and for with that this from have has had was were are been being into onto over
    under about than then they them their there what when where which while who whom whose
    will would could should also only just very more most some such each other these those
    its our your his her she him not but nor yet can may might must said says one two all any
    who's it's""".split()
)


def words(text: str) -> set[str]:
    """Content words, crudely stemmed (plural s off, first 5 letters): "suggested" ~
    "suggestion", "demos" ~ "demo"."""
    out = set()
    for w in re.findall(r"[a-z0-9]+", text.casefold()):
        if len(w) < 3 or w in STOP:
            continue
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.add(w[:5])
    return out


def idents(text: str) -> set[str]:
    """Identifiers, canonical: urls without trailing punctuation, ids lowercased, PRs as #N."""
    out = set()
    for raw in IDENT.findall(text):
        r = raw.rstrip(".,;:").casefold()
        if r.startswith("http"):
            out.add(r)
        elif r.startswith(("eid_", "cust-")):
            out.add(r)
        else:
            out.add("#" + re.sub(r"\D", "", r))
    return out


def in_record(ident: str, texts: list[str], facts: set[str]) -> bool:
    """Is the identifier in an artifact's texts or its tool record?"""
    if ident.startswith("#"):
        n = ident[1:]
        pattern = re.compile(rf"(?:#|pull/|PR\s*#?\s*){n}\b", re.I)
        return n in facts or any(pattern.search(t) for t in texts)
    hay = [t.casefold() for t in texts] + [f.casefold() for f in facts]
    return any(ident in h for h in hay)


def too_short(quote: str) -> bool:
    q = llm.normalize(quote)
    return len(q) < MIN_QUOTE_CHARS or len(re.findall(r"\w+", q)) < MIN_QUOTE_WORDS


@dataclass
class Report:
    problems: list[str] = field(default_factory=list)
    bad: set[int] = field(default_factory=set)  # citation numbers that failed rule 1, 2 or 4
    uncited: list[str] = field(default_factory=list)  # units that break rule 3
    unsupported: set[str] = field(default_factory=set)  # cited units that break rule 5 or 6

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


def check(
    answer: dict[str, Any],
    seen: set[str],
    texts: dict[str, list[str]],
    facts: dict[str, set[str]] | None = None,
    related: dict[str, set[str]] | None = None,
) -> Report:
    """Validate an answer against what tools returned (`seen`), the artifacts' texts, their
    tool records (`facts`) and the artifacts nested with them in one tool row (`related`)."""
    report = Report()
    if answer["abstain"]:
        return report
    facts, related = facts or {}, related or {}
    lines = answer["answer_markdown"].splitlines()
    citing: dict[int, list[str]] = {}  # citation number -> the units that cite it
    for line in lines:
        for unit in units(line):
            for m in MARKER.findall(unit):
                citing.setdefault(int(m), []).append(unit)
    numbers = set()
    by_n = {}
    for c in answer["citations"]:
        numbers.add(c["n"])
        by_n[c["n"]] = c
        if c["artifact_id"] not in seen:
            report.bad.add(c["n"])
            report.problems.append(
                f"[{c['n']}] cites {c['artifact_id']}, which no tool returned in this answer"
            )
        elif not quoted(c["quote"], texts.get(c["artifact_id"], [])):
            report.bad.add(c["n"])
            report.problems.append(
                f"[{c['n']}] quote is not word for word in {c['artifact_id']}: {c['quote'][:120]!r}"
            )
        elif too_short(c["quote"]) and not record_citation(c, citing.get(c["n"], []), texts, facts):
            report.bad.add(c["n"])
            report.problems.append(
                f"[{c['n']}] quote {c['quote'][:60]!r} is too short to show anything: copy at "
                f"least {MIN_QUOTE_WORDS} words of {c['artifact_id']} that state the fact"
            )
    valid = numbers - report.bad
    for i, line in enumerate(lines):
        for unit in units(line):
            markers = {int(m) for m in MARKER.findall(unit)}
            if markers - numbers:
                missing = ", ".join(f"[{n}]" for n in sorted(markers - numbers))
                report.problems.append(f"{missing} has no entry in citations: {unit[:120]!r}")
            if not needs_citation(unit):
                continue
            if not markers & valid:
                if not cited_children(lines, i, valid):
                    report.uncited.append(unit)
                    report.problems.append(f"no valid citation for: {unit[:160]!r}")
                continue
            cites = [by_n[n] for n in sorted(markers & valid)]
            if why := unsupported(unit, cites, texts, facts, related):
                report.unsupported.add(unit)
                report.problems.append(f"{why}: {unit[:160]!r}")
    return report


def record_citation(
    c: dict[str, Any], citing: list[str], texts: dict[str, list[str]], facts: dict[str, set[str]]
) -> bool:
    """May this short quote stand? Yes when a sentence citing it repeats it (a title or link it
    names), or names an identifier held in the cited artifact's own record: "Bob Davis
    (eid_e0273792) spoke in the meeting [6]" cites the meeting whose speakers include him,
    and the meeting's id is the honest quote for that."""
    q = re.compile(rf"(?<!\w){re.escape(llm.normalize(c['quote']))}(?!\w)")
    if any(q.search(llm.normalize(u)) for u in citing):  # whole words: "a" isn't in "alice"
        return True
    aid = c["artifact_id"]
    return any(
        in_record(i, texts.get(aid, []), facts.get(aid, set()))
        for u in citing
        for i in idents(MARKER.sub("", u))
    )


def unsupported(
    unit: str,
    cites: list[dict[str, Any]],
    texts: dict[str, list[str]],
    facts: dict[str, set[str]],
    related: dict[str, set[str]],
) -> str | None:
    """Why the unit's valid citations don't back it (rules 5 and 6), or None.

    Rule 5 is lexical: the sentence must share a content word or identifier with its quotes
    or with the cited artifacts' records (author, title, people), so a correct paraphrase
    ("dismissed" for "does not require a fix", said by the cited author) passes and a quote
    about something else ("Stay updated with this news article") fails. It checks that the
    sentence is about what it cites, not that the quote entails it."""
    claim = MARKER.sub("", unit)
    quotes = " ".join(c["quote"] for c in cites)
    cited = {c["artifact_id"] for c in cites}
    near = cited | {r for a in cited for r in related.get(a, ())}
    titles = [texts[a][0] for a in near - cited if texts.get(a)]  # the related row's title
    record = " ".join([*(f for a in near for f in facts.get(a, ())), *titles])
    if not (words(claim) & (words(quotes) | words(record)) or idents(claim) & idents(quotes)):
        return "the quotes cited share no word with the sentence: quote the words that state it"
    for ident in sorted(idents(claim)):
        if not any(in_record(ident, texts.get(a, []), facts.get(a, set())) for a in near):
            return f"{ident} is in none of the artifacts this sentence cites"
    return None


def autocite(
    answer: dict[str, Any], urls: dict[str, str], texts: dict[str, list[str]] | None = None
) -> tuple[dict[str, Any], list[int]]:
    """Cite marker-less sentences that name a returned artifact's own url (`urls`: url -> id).
    With `texts`, only a line that says little beyond the artifact's own words (a title and
    its link) is cited: "Revenue fell 40% after <url> shipped" claims more than the link
    shows, so it keeps no marker and must be cited by the model or dropped (D099)."""
    if answer["abstain"]:
        return answer, []
    citations = list(answer["citations"])
    by_url = {c["quote"]: c["n"] for c in citations if c["quote"] in urls}
    next_n = max((c["n"] for c in citations), default=0) + 1
    added: list[int] = []
    lines = []
    for line in answer["answer_markdown"].splitlines():
        if not URL.search(line):
            lines.append(line)
            continue
        lead = line[: len(line) - len(line.lstrip())]
        parts = []
        for unit in units(line):
            if not MARKER.search(unit):
                for url in dict.fromkeys(u.rstrip(".,;:") for u in URL.findall(unit)):
                    if url not in urls:
                        continue
                    if texts is not None:
                        own = words(" ".join([url, *texts.get(urls[url], [])]))
                        if len(words(unit.replace(url, " ")) - own) > AUTOCITE_EXTRA_WORDS:
                            continue
                    if url not in by_url:
                        by_url[url] = next_n
                        citations.append({"n": next_n, "artifact_id": urls[url], "quote": url})
                        added.append(next_n)
                        next_n += 1
                    # right after the url, so the marker stays inside this sentence
                    unit = unit.replace(url, f"{url} [{by_url[url]}]", 1)
            parts.append(unit)
        lines.append(lead + " ".join(parts))
    return {**answer, "answer_markdown": "\n".join(lines), "citations": citations}, added


def quoted(quote: str, texts: list[str]) -> bool:
    return any(llm.quote_in(quote, t) for t in texts)


def reattribute(
    answer: dict[str, Any], related: dict[str, set[str]], texts: dict[str, list[str]]
) -> tuple[dict[str, Any], list[int]]:
    """Point a citation whose quote isn't in its artifact at the one related artifact that
    holds the quote word for word (`related`: artifacts nested in one tool row, loop.collect).
    Returns the answer and the citation numbers moved."""
    if answer["abstain"]:
        return answer, []
    citations, moved = [], []
    for c in answer["citations"]:
        aid = c["artifact_id"]
        if aid in texts and not quoted(c["quote"], texts[aid]):
            holders = [
                r for r in sorted(related.get(aid, ())) if quoted(c["quote"], texts.get(r, []))
            ]
            if len(holders) == 1:
                c = {**c, "artifact_id": holders[0]}
                moved.append(c["n"])
        citations.append(c)
    return {**answer, "citations": citations}, moved


def indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def cited_children(lines: list[str], i: int, valid: set[int]) -> bool:
    """Do the lines indented under lines[i] carry a valid citation? A list item such as
    "- PR #13: <url>" is backed by the cited sub-items under it (D047)."""
    depth = indent(lines[i])
    for line in lines[i + 1 :]:
        if not line.strip():
            continue
        if indent(line) <= depth:
            return False
        if {int(m) for m in MARKER.findall(line)} & valid:
            return True
    return False


def clean(answer: dict[str, Any], report: Report) -> tuple[dict[str, Any], list[str]]:
    """Drop failing citations and every unit without a valid one; abstain if nothing stays."""
    if answer["abstain"]:
        return answer, []
    valid = {c["n"] for c in answer["citations"]} - report.bad
    kept_lines, dropped = [], []
    lines = answer["answer_markdown"].splitlines()
    for i, line in enumerate(lines):
        kept = []
        backed = cited_children(lines, i, valid)
        for unit in units(line):
            markers = {int(m) for m in MARKER.findall(unit)}
            if needs_citation(unit) and (
                (not markers & valid and not backed) or unit in report.unsupported
            ):
                dropped.append(unit)
                continue
            kept.append(MARKER.sub(lambda m: m.group(0) if int(m.group(1)) in valid else "", unit))
        if kept:
            body = " ".join(kept)
            bullet = BULLET.match(line)
            if bullet and not BULLET.match(body):  # the bullet's own sentence was dropped
                body = f"{bullet.group(0).strip()} {body}"
            kept_lines.append(line[: indent(line)] + body)
        elif not line.strip():
            kept_lines.append("")
    markdown = "\n".join(kept_lines).strip()
    used = {int(m) for m in MARKER.findall(markdown)}
    citations = [c for c in answer["citations"] if c["n"] in used]
    stated = any(needs_citation(u) for line in markdown.splitlines() for u in units(line))
    return {"answer_markdown": markdown, "citations": citations, "abstain": not stated}, dropped
