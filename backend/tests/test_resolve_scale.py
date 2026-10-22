"""The fold's resolution at scale (Phase 21, F3, D129): the indexed matcher gives exactly the
answers of the scan it replaced (frozen below as `reference`), and a pass that reuses the last
pass's stored results gives exactly what a full pass gives, after random additions, late
events, retractions and forgotten events. Random histories use a small vocabulary, so titles
collide, tie, share descriptions and fall through every step of the matcher."""

import random
from collections import defaultdict

from rapidfuzz import fuzz

from app.ingest import resolve
from app.ingest.resolve import ASSIGNEE_FLOOR, FUZZY, PARTIAL, Issue, core, issue_id, norm_title

WORDS = "cache login export chart sync token oauth upload report the bug incorrect data".split()
PEOPLE = ["eid_a", "eid_b", "eid_c", None]


# --- the scan the index replaced, frozen (resolve.py before D129) ------------------------------
def reference(events):
    events = sorted(events, key=resolve.order)  # its own sort was stable: the same tie order
    issues, by_product, out, unresolved = {}, defaultdict(list), [], []

    def new_issue(e):
        iid = issue_id(e["product"], e["issue"])
        if iid not in issues:
            issues[iid] = resolve.made(e)
            by_product[e["product"]].append(issues[iid])
        issues[iid].aliases.add(e["issue"])
        return issues[iid]

    for e in events:
        e = dict(e)
        title = (e.get("issue") or "").strip()
        if not title:
            unresolved.append(e | {"why": "no issue named"})
        elif e["type"] == "issue_reported":
            e["issue_id"], e["issue_how"] = new_issue(e).id, "reported"
        else:
            match, how = reference_match(title, e, by_product[e["product"]])
            if match is None and e["type"] == "issue_resolved":
                match, how = new_issue(e), "first_seen_resolved"
            if match is None:
                unresolved.append(e | {"why": "issue not matched"})
            else:
                match.aliases.add(title)
                e["issue_id"], e["issue_how"] = match.id, how
        out.append(e)
    return resolve.Resolution(issues, out, unresolved)


def reference_match(title, e, known: list[Issue]):
    n = norm_title(title)
    for issue in known:
        if norm_title(issue.title) == n:
            return issue, "exact"
    c = core(title)

    def best(score, pool):
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
    words = set(c.split()) - {e["product"].casefold()}
    covering = [i for i in earlier if words and words <= set(core(i.description).split())]
    if len(covering) == 1:
        return covering[0], "description"
    mine = [i for i in earlier if i.assignee and i.assignee in (e.get("speaker"), e.get("person"))]
    if (hit := best(fuzz.token_set_ratio, mine)) and hit[0] >= ASSIGNEE_FLOOR:
        return hit[1], f"assignee:{hit[0]:.0f}"
    return None, ""


# --- random histories --------------------------------------------------------------------------
def history(rnd: random.Random, n: int, start: int = 0) -> list[dict]:
    out = []
    for k in range(start, start + n):
        kind = rnd.choice(["issue_reported"] * 3 + ["issue_resolved"] * 3 + ["issue_reopened",
                          "issue_dismissed"])  # fmt: skip
        title = " ".join(rnd.sample(WORDS, rnd.randint(1, 4)))
        out.append(
            {
                "id": f"evt:{k}",
                "type": kind,
                "ts_ms": rnd.randint(0, 60),  # many share a moment
                "artifact_id": f"msg:{rnd.randint(0, 40)}",  # and a message
                "product": "W",
                "channel": rnd.choice(["bug-a", "bug-b"]),
                "issue": rnd.choice([title, title.upper(), "", f"the {title} bug"]),
                "quote": " ".join(rnd.sample(WORDS, rnd.randint(2, 6))),
                "person": rnd.choice(PEOPLE),
                "speaker": rnd.choice(PEOPLE),
            }
        )
    return out


def outcome(res: resolve.Resolution):
    events = sorted(
        (e["id"], e.get("issue_id"), e.get("issue_how")) for e in res.events if e.get("id")
    )
    issues = sorted(
        (i.id, i.title, i.channel, i.first_ms, i.description, i.assignee, tuple(sorted(i.aliases)))
        for i in res.issues.values()
    )
    unresolved = sorted((u["id"], u["why"]) for u in res.unresolved)
    return events, issues, unresolved


def test_the_index_matches_the_scan_it_replaced():
    for seed in range(40):
        found = history(random.Random(seed), 160)
        assert outcome(resolve.resolve(found, {})) == outcome(reference(found)), seed


def kept(state: dict, res: resolve.Resolution) -> dict:
    """The results on the events after a pass: stored rows in, past events' results out."""
    state = {**state, **{r["id"]: (r["issue"], r["how"]) for r in res.store}}
    for gone in res.gone:
        state.pop(gone, None)
    return state


def test_a_pass_that_reuses_stored_results_equals_a_full_pass():
    for seed in range(60):
        rnd = random.Random(1000 + seed)
        now = history(rnd, 150)
        state = kept({}, resolve.resolve(now, {}))
        for step in range(4):
            retracted = rnd.sample(now, rnd.randint(0, 6))  # their results stay on the node
            forgotten = rnd.sample([e for e in now if e not in retracted], rnd.randint(0, 2))
            late = history(rnd, rnd.randint(0, 8), start=10_000 * (step + 1) + seed)
            out = {e["id"] for e in retracted + forgotten}
            now = [e for e in now if e["id"] not in out] + late
            # forget retracts an event and blanks its words (D125): it can't say what it made
            blanked = [e | {"issue": None, "quote": ""} for e in forgotten]
            prior = resolve.Prior(state, gone=[e for e in retracted + blanked if e["id"] in state])
            again = resolve.resolve(now, {}, prior)
            assert outcome(again) == outcome(resolve.resolve(now, {})), (seed, step)
            state = kept(prior.results, again)


def test_an_unchanged_history_is_not_matched_again():
    found = history(random.Random(5), 300)
    first = resolve.resolve(found, {})
    second = resolve.resolve(found, {}, resolve.Prior(kept({}, first)))
    assert first.matched > 100 and second.matched == 0 and second.store == []
    late = history(random.Random(6), 3, start=900)
    for e in late:
        e["ts_ms"] = 999  # after everything: only these are matched
    third = resolve.resolve(found + late, {}, resolve.Prior(kept({}, first)))
    assert third.matched <= 3
