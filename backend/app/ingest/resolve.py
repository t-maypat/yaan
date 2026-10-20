"""S7 entity resolution for extracted events (Phase 8.2, R1-5 / D041).

Issues are keyed by their normalized title within a product: `iss:<product>:<hash>`.
`issue_reported` events create issues. Every later issue event (resolved, dismissed,
reopened) is matched to one in this order, and each step is logged:
1. the same normalized title;
2. rapidfuzz token-set ratio >= FUZZY (near-miss titles, e.g. a dropped word);
3. partial ratio >= PARTIAL against issues reported earlier in the same channel (a prose
   reference such as "the reported issue about the UI freezing"), then a reference whose
   words all appear in exactly one earlier report's description in that channel, then the
   best of the issues there assigned to the speaker (score >= ASSIGNEE_FLOOR);
4. else a resolved event becomes a new issue first seen at its fix (no customer); any
   other event stays unresolved (logged, not guessed).
Fuzzy steps compare `core` titles: generic words ("incorrect", "bug", "the") removed.

PR references ("PR#12", "PR# 10", a pull url) resolve to the product's own PR ids.
Spec: docs/CONTEXT.md §9.3 (S7); PLAN.md R1-5.
"""

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from rapidfuzz import fuzz

from app.ingest.normalize import Artifact

FUZZY = 90
PARTIAL = 80
ASSIGNEE_FLOOR = 50  # only among the speaker's own assigned issues in the same channel
_PR_URL = re.compile(r"github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)", re.IGNORECASE)
_PR_NUM = re.compile(r"(?:PR|pull request)\s*#?\s*(\d+)", re.IGNORECASE)
_FILLER = re.compile(r"^(the|a|an)\s+|\s+(bug|issue|problem)$")


# Both are pure, and the fold compares every event's title with every known issue's: cached,
# each distinct title is normalized once per process instead of once per comparison (D112).
@lru_cache(maxsize=1 << 16)
def norm_title(title: str) -> str:
    t = re.sub(r"[\"'‘’“”`]", "", title).casefold()
    t = re.sub(r"[^\w&/ -]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return _FILLER.sub("", _FILLER.sub("", t)).strip()


# Words that say "something is wrong" or glue a phrase together. They carry no identity, and
# left in they let "incorrect data encryption" match "Incorrect Data Mapping".
GENERIC = frozenset(
    "incorrect improper faulty error errors issue issues problem problems bug bugs the a an "
    "our in of on to due with for from by".split()
)


@lru_cache(maxsize=1 << 16)
def core(title: str) -> str:
    """The identity-bearing words of a title, singular, for fuzzy comparison only."""
    words = [w for w in norm_title(title).split() if w not in GENERIC]
    return " ".join(
        w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w for w in words
    )


def issue_id(product: str, title: str) -> str:
    digest = hashlib.sha1(norm_title(title).encode("utf-8")).hexdigest()[:10]
    return f"iss:{product}:{digest}"


@dataclass
class Issue:
    id: str
    product: str
    title: str
    channel: str
    first_ms: int
    description: str = ""  # the report's quote: later messages may reuse its wording
    assignee: str | None = None  # who the report asked to take it
    aliases: set[str] = field(default_factory=set)


@dataclass
class Resolution:
    issues: dict[str, Issue]
    events: list[dict[str, Any]]  # every input event, with issue_id / pr_id / how set
    unresolved: list[dict[str, Any]]


def resolve(events: list[dict[str, Any]], prs: dict[str, list[Artifact]]) -> Resolution:
    """Resolve one or more products' verified events. `prs` maps product -> its own PRs."""
    events = sorted(events, key=lambda e: (e["ts_ms"] or 0, e["artifact_id"], e["type"]))
    issues: dict[str, Issue] = {}
    by_product: dict[str, list[Issue]] = defaultdict(list)
    out, unresolved = [], []

    def new_issue(e: dict[str, Any]) -> Issue:
        iid = issue_id(e["product"], e["issue"])
        if iid not in issues:
            issues[iid] = Issue(
                iid,
                e["product"],
                e["issue"].strip(" '\""),
                e["channel"],
                e["ts_ms"] or 0,
                e["quote"] if e["type"] == "issue_reported" else "",
                (e.get("person") or None) if e["type"] == "issue_reported" else None,
            )
            by_product[e["product"]].append(issues[iid])
        issues[iid].aliases.add(e["issue"])
        return issues[iid]

    for e in events:
        e = dict(e)
        if e["type"].startswith("pr_") or (e["type"] == "issue_resolved" and e.get("pr")):
            ref = e.get("pr") or e["quote"]  # "hold off on merging PR# 15?" with pr left empty
            e["pr_id"], e["pr_how"] = resolve_pr(ref, prs.get(e["product"], []), e)
            if e["type"].startswith("pr_") and not e["pr_id"]:
                unresolved.append(e | {"why": "pr reference not found"})
        if e["type"].startswith("issue_"):
            title = (e.get("issue") or "").strip()
            if not title:
                unresolved.append(e | {"why": "no issue named"})
            elif e["type"] == "issue_reported":
                e["issue_id"], e["issue_how"] = new_issue(e).id, "reported"
            else:
                match, how = match_issue(title, e, by_product[e["product"]])
                if match is None and e["type"] == "issue_resolved":
                    # "the bug about X has been resolved" with no report: HERB states fixes
                    # of bugs no customer reported. Keep them, without a customer.
                    match, how = new_issue(e), "first_seen_resolved"
                if match is None:
                    unresolved.append(e | {"why": "issue not matched"})
                else:
                    match.aliases.add(title)
                    e["issue_id"], e["issue_how"] = match.id, how
        out.append(e)
    return Resolution(issues, out, unresolved)


def match_issue(title: str, e: dict[str, Any], known: list[Issue]) -> tuple[Issue | None, str]:
    n = norm_title(title)
    for issue in known:
        if norm_title(issue.title) == n:
            return issue, "exact"
    c = core(title)

    def best(score, pool: list[Issue]) -> tuple[float, Issue] | None:
        # ties ("cache invalidation" is a subset of two titles) go to the same channel,
        # then to the closer whole string
        ranked = sorted(
            pool,
            key=lambda i: (
                -score(c, core(i.title)),
                i.channel != e["channel"],
                -fuzz.ratio(c, core(i.title)),
                i.id,
            ),
        )
        return (score(c, core(ranked[0].title)), ranked[0]) if ranked else None

    if (hit := best(fuzz.token_set_ratio, known)) and hit[0] >= FUZZY:
        return hit[1], f"fuzzy:{hit[0]:.0f}"
    earlier = [i for i in known if i.channel == e["channel"] and i.first_ms <= (e["ts_ms"] or 0)]
    if (hit := best(fuzz.partial_ratio, earlier)) and hit[0] >= PARTIAL:
        return hit[1], f"partial:{hit[0]:.0f}"
    # "the bug about incorrect OAuth scope assignments" names no title but repeats the
    # report's description ("... due to incorrect OAuth scope assignments"): every core word
    # of the reference must appear in one earlier report from the same channel.
    words = set(c.split()) - {e["product"].casefold()}  # "... in VizForce"
    covering = [i for i in earlier if words and words <= set(core(i.description).split())]
    if len(covering) == 1:
        return covering[0], "description"
    # "data category classification" vs 'Misclassification of Data Categories': the fixer
    # is the engineer the report was assigned to. Take their best-scoring issue there.
    mine = [i for i in earlier if i.assignee and i.assignee in (e.get("speaker"), e.get("person"))]
    if (hit := best(fuzz.token_set_ratio, mine)) and hit[0] >= ASSIGNEE_FLOOR:
        return hit[1], f"assignee:{hit[0]:.0f}"
    return None, ""


def resolve_pr(ref: str | None, prs: list[Artifact], e: dict[str, Any]) -> tuple[str | None, str]:
    """A PR reference as written -> the product's own PR id, or (None, reason)."""
    if not ref:
        return None, "no reference"
    own = [p for p in prs if not p.external]
    if m := _PR_URL.search(ref):
        repo, num = m.group(1).lower(), int(m.group(2))
        for p in own:
            if p.meta["repo"].lower() == repo and p.meta["number"] == num:
                return p.id, "url"
        return None, "url not a product PR"
    if m := _PR_NUM.search(ref) or re.fullmatch(r"\s*#?(\d+)\s*", ref):
        num = int(m.group(1))
        same = [p for p in own if p.meta["number"] == num]
        if len(same) == 1:
            return same[0].id, "number"
        before = [p for p in same if (p.ts_ms or 0) <= (e["ts_ms"] or 0)]
        if before:  # two repos share numbers: take the newest PR opened before the event
            return max(before, key=lambda p: p.ts_ms or 0).id, "number:newest"
        return None, f"number {num}: {len(same)} candidates"
    return None, "unparsed reference"
