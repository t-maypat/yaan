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
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from rapidfuzz import fuzz, process

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
    # what to store on the events for the next pass (D129): {id, issue, how} for each issue
    # event whose result is new or changed, and the ids of past events no longer in the pass
    store: list[dict[str, Any]] = field(default_factory=list)
    gone: list[str] = field(default_factory=list)
    matched: int = 0  # issue events matched afresh; the others kept their stored result


@dataclass
class Prior:
    """The last pass's results, read back from the events (D129). `results` maps an event id
    to (issue id or None, how); `gone` holds the past events not in this pass (retracted or
    forgotten since), whose issues are gone unless another event makes them."""

    results: dict[str, tuple[str | None, str]]
    gone: list[dict[str, Any]] = field(default_factory=list)


VERSION = f"r2:{FUZZY}:{PARTIAL}:{ASSIGNEE_FLOOR}"  # results stored by another version are redone
CREATES = ("reported", "first_seen_resolved")  # the hows of an event that makes its issue
UNMATCHED = "unmatched:"  # an unresolved issue event is stored as unmatched:<why>
TOUCH_LIMIT = 256  # past this many changed issues, matching afresh is cheaper than checking


def order(e: dict[str, Any]) -> tuple:
    """The order events are resolved in. The id breaks the last ties, so the result never
    depends on the order the graph returned them."""
    return (e["ts_ms"] or 0, e["artifact_id"], e["type"], e.get("id") or "")


def made(e: dict[str, Any]) -> Issue:
    """The issue an event makes when it is the first to name it."""
    reported = e["type"] == "issue_reported"
    return Issue(
        issue_id(e["product"], e["issue"]),
        e["product"],
        e["issue"].strip(" '\""),
        e["channel"],
        e["ts_ms"] or 0,
        e["quote"] if reported else "",
        (e.get("person") or None) if reported else None,
    )


class Known:
    """One product's issues so far, indexed for the matcher (D129): `match_issue` gives the
    same answers as a scan of every issue, but each step looks only at issues that can win."""

    def __init__(self) -> None:
        self.issues: list[Issue] = []
        self.cores: list[str] = []
        self.by_norm: dict[str, Issue] = {}
        self.channel: dict[str, tuple[list[Issue], list[str]]] = defaultdict(lambda: ([], []))
        self.words: dict[tuple[str, str], list[Issue]] = defaultdict(list)  # description words
        self.assigned: dict[tuple[str, str], list[Issue]] = defaultdict(list)

    def add(self, issue: Issue) -> None:
        c = core(issue.title)
        self.issues.append(issue)
        self.cores.append(c)
        self.by_norm.setdefault(norm_title(issue.title), issue)
        mine, cores = self.channel[issue.channel]
        mine.append(issue)
        cores.append(c)
        for w in set(core(issue.description).split()):
            self.words[(issue.channel, w)].append(issue)
        if issue.assignee:
            self.assigned[(issue.channel, issue.assignee)].append(issue)


def resolve(
    events: list[dict[str, Any]], prs: dict[str, list[Artifact]], prior: Prior | None = None
) -> Resolution:
    """Resolve one or more products' verified events. `prs` maps product -> its own PRs.

    With `prior` (the last pass's stored results, D129) an issue event keeps its stored
    result unless it is new, its issue changed, or an issue that changed (made by another
    event now, or no longer made at all) could take part in its match (`touched`). The
    result is the one a full pass gives; only the matching work is skipped."""
    events = sorted(events, key=order)
    results = prior.results if prior else {}
    gone = sorted(prior.gone, key=order) if prior else []
    if any(not (e.get("issue") or "").strip() and results.get(e["id"], ("", ""))[1] in CREATES
           for e in gone):  # fmt: skip
        results, gone = {}, []  # a past event's words were blanked (forget): match everything
    issues: dict[str, Issue] = {}
    known: dict[str, Known] = defaultdict(Known)
    pr_index = {product: PrIndex(own) for product, own in prs.items()}
    out, unresolved, store = [], [], []
    matched = 0
    # the event that made each issue in the last pass: the first, in order, stored as making it
    was_made_by: dict[str, str] = {}
    for e in sorted([*gone, *(e for e in events if e.get("id") in results)], key=order):
        iid, how = results.get(e["id"], (None, ""))
        if iid and how in CREATES:
            was_made_by.setdefault(iid, e["id"])
    made_then = {eid: iid for iid, eid in was_made_by.items()}
    made_by: dict[str, str] = {}
    # issues not as the last pass had them at this point: made by another event, or not at all.
    # An issue some stored result names but no stored event made (forgotten) counts too.
    changed_ids = {i for i, _ in results.values() if i and i not in was_made_by}
    changed: dict[str, dict[tuple[str, str], Issue]] = defaultdict(dict)

    def flag(issue: Issue, which: str) -> None:
        if results:  # a full pass matches everything anyway
            changed_ids.add(issue.id)
            changed[issue.product][(issue.id, which)] = issue

    def new_issue(e: dict[str, Any]) -> Issue:
        iid = issue_id(e["product"], e["issue"])
        if iid not in issues:
            issues[iid] = made(e)
            known[e["product"]].add(issues[iid])
            made_by[iid] = e.get("id") or ""
            if was_made_by.get(iid) != made_by[iid]:
                flag(issues[iid], "now")
        issues[iid].aliases.add(e["issue"])
        return issues[iid]

    def lost(e: dict[str, Any]) -> None:
        """After e's turn: an issue e made in the last pass and didn't make now is changed."""
        iid = made_then.get(e.get("id") or "")
        if iid and made_by.get(iid) != e["id"]:
            flag(made(e), "then")

    def reusable(e: dict[str, Any], title: str) -> bool:
        was = results.get(e.get("id") or "")
        if was is None or was[0] in changed_ids:
            return False
        if was[0] and was[1] not in CREATES and was[0] not in issues:
            return False  # can't happen with a consistent prior; match rather than guess
        nearby = changed[e["product"]]
        return len(nearby) <= TOUCH_LIMIT and not touched(title, e, nearby.values())

    timeline = sorted([*((e, False) for e in gone), *((e, True) for e in events)],
                      key=lambda x: order(x[0]))  # fmt: skip
    for e, live in timeline:
        if not live:  # a past event no longer here: what it made is gone, or made elsewhere
            lost(e)
            continue
        e = dict(e)
        if e["type"].startswith("pr_") or (e["type"] == "issue_resolved" and e.get("pr")):
            ref = e.get("pr") or e["quote"]  # "hold off on merging PR# 15?" with pr left empty
            e["pr_id"], e["pr_how"] = (pr_index.get(e["product"]) or PrIndex([])).find(ref, e)
            if e["type"].startswith("pr_") and not e["pr_id"]:
                unresolved.append(e | {"why": "pr reference not found"})
        if e["type"].startswith("issue_"):
            title = (e.get("issue") or "").strip()
            if not title:
                unresolved.append(e | {"why": "no issue named"})
                out.append(e)
                continue
            iid: str | None
            if e["type"] == "issue_reported":
                iid, how = new_issue(e).id, "reported"
            else:
                if reusable(e, title):
                    iid, how = results[e["id"]]
                else:
                    matched += 1
                    hit, how = match_issue(title, e, known[e["product"]])
                    iid = hit.id if hit else None
                if how == "first_seen_resolved" or (iid is None and e["type"] == "issue_resolved"):
                    # "the bug about X has been resolved" with no report: HERB states fixes
                    # of bugs no customer reported. Keep them, without a customer.
                    iid, how = new_issue(e).id, "first_seen_resolved"
                elif iid is not None:
                    issues[iid].aliases.add(title)
            if iid is None:
                unresolved.append(e | {"why": "issue not matched"})
                how = f"{UNMATCHED}issue not matched"
            else:
                e["issue_id"], e["issue_how"] = iid, how
            if e.get("id") and results.get(e["id"]) != (iid, how):
                store.append({"id": e["id"], "issue": iid, "how": how})
            lost(e)
        out.append(e)
    return Resolution(issues, out, unresolved, store, [e["id"] for e in gone], matched)


def touched(title: str, e: dict[str, Any], changed: Iterable[Issue]) -> bool:
    """Could one of these issues change the event's match? Yes if it takes part in any step of
    `match_issue`: the same title, a fuzzy score at its floor, or, in the event's channel, a
    partial score at its floor, a description holding every word, or the speaker's own issue
    scoring at the assignee floor. An issue below every floor can't win or tie a step."""
    n, c = norm_title(title), core(title)
    words = set(c.split()) - {e["product"].casefold()}
    who = {e.get("speaker"), e.get("person")} - {None}
    for i in changed:
        ci = core(i.title)
        if norm_title(i.title) == n or fuzz.token_set_ratio(c, ci) >= FUZZY:
            return True
        if i.channel != e["channel"]:
            continue
        if fuzz.partial_ratio(c, ci) >= PARTIAL:
            return True
        if words and words <= set(core(i.description).split()):
            return True
        if i.assignee in who and fuzz.token_set_ratio(c, ci) >= ASSIGNEE_FLOOR:
            return True
    return False


def match_issue(title: str, e: dict[str, Any], known: Known) -> tuple[Issue | None, str]:
    n = norm_title(title)
    if (same := known.by_norm.get(n)) is not None:
        return same, "exact"
    c = core(title)

    def best(score, pool: list[Issue], cores: list[str], floor: float) -> tuple | None:
        # Only issues at the floor can win, so a scan in rapidfuzz finds them (from a hair
        # below it, so no rounding drops one) and only they are ranked (D129). Ties ("cache
        # invalidation" is a subset of two titles) go to the same channel, then to the closer
        # whole string, then to the id.
        found = process.extract(c, cores, scorer=score, score_cutoff=floor - 0.5, limit=None)
        passing = [(s, pool[k]) for _, _, k in found if (s := score(c, cores[k])) >= floor]
        if not passing:
            return None
        return min(
            passing,
            key=lambda si: (
                -si[0],
                si[1].channel != e["channel"],
                -fuzz.ratio(c, core(si[1].title)),
                si[1].id,
            ),
        )

    if hit := best(fuzz.token_set_ratio, known.issues, known.cores, FUZZY):
        return hit[1], f"fuzzy:{hit[0]:.0f}"
    # the channel's issues: every known issue was reported before this event (time order)
    earlier, cores = known.channel[e["channel"]]
    if hit := best(fuzz.partial_ratio, earlier, cores, PARTIAL):
        return hit[1], f"partial:{hit[0]:.0f}"
    # "the bug about incorrect OAuth scope assignments" names no title but repeats the
    # report's description ("... due to incorrect OAuth scope assignments"): every core word
    # of the reference must appear in one earlier report from the same channel.
    words = set(c.split()) - {e["product"].casefold()}  # "... in VizForce"
    if words:
        postings = sorted((known.words.get((e["channel"], w), []) for w in words), key=len)
        covering = [i for i in postings[0] if words <= set(core(i.description).split())]
        if len(covering) == 1:
            return covering[0], "description"
    # "data category classification" vs 'Misclassification of Data Categories': the fixer
    # is the engineer the report was assigned to. Take their best-scoring issue there.
    mine = list(
        {
            i.id: i
            for who in (e.get("speaker"), e.get("person"))
            if who
            for i in known.assigned.get((e["channel"], who), [])
        }.values()
    )
    if hit := best(fuzz.token_set_ratio, mine, [core(i.title) for i in mine], ASSIGNEE_FLOOR):
        return hit[1], f"assignee:{hit[0]:.0f}"
    return None, ""


class PrIndex:
    """A product's own PRs by number (D129: a lookup per reference, not a scan of every PR)."""

    def __init__(self, prs: list[Artifact]) -> None:
        self.number: dict[int, list[Artifact]] = defaultdict(list)
        for p in prs:
            if not p.external:
                self.number[p.meta["number"]].append(p)

    def find(self, ref: str | None, e: dict[str, Any]) -> tuple[str | None, str]:
        """A PR reference as written -> the product's own PR id, or (None, reason)."""
        if not ref:
            return None, "no reference"
        if m := _PR_URL.search(ref):
            repo, num = m.group(1).lower(), int(m.group(2))
            for p in self.number.get(num, []):
                if p.meta["repo"].lower() == repo:
                    return p.id, "url"
            return None, "url not a product PR"
        if m := _PR_NUM.search(ref) or re.fullmatch(r"\s*#?(\d+)\s*", ref):
            num = int(m.group(1))
            same = self.number.get(num, [])
            if len(same) == 1:
                return same[0].id, "number"
            before = [p for p in same if (p.ts_ms or 0) <= (e["ts_ms"] or 0)]
            if before:  # two repos share numbers: take the newest PR opened before the event
                return max(before, key=lambda p: p.ts_ms or 0).id, "number:newest"
            return None, f"number {num}: {len(same)} candidates"
        return None, "unparsed reference"


def resolve_pr(ref: str | None, prs: list[Artifact], e: dict[str, Any]) -> tuple[str | None, str]:
    """A PR reference as written -> the product's own PR id, or (None, reason)."""
    return PrIndex(prs).find(ref, e)
