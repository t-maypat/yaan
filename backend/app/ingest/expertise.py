"""S9 expertise (Phase 10.0, 10.2, 10.3): areas, degree-of-authorship, truck factor. D059.

There are no Feature nodes (R1-6), so expertise is measured over **areas** of a product:
- `component` areas: the components bug reports name (`Issue.component`, case-folded). A
  compound like "Backend Server & Visualization Module" counts for both parts only when both
  are components of the product on their own; "Database & User Interface" stays one area.
- `work_item` areas: development PRs (internal, not fixing an issue) grouped by normalized
  title, since HERB re-attempts the same titled work; a revert joins the work item of the PR
  it reverts (REVERTS). A bug-fix PR belongs to its issue's component instead.

A **delivery** is one unit of work in an area: an issue fixed there (authored the FIXES PR, or
stated the resolution), or a PR authored in a work item. Degree-of-authorship (Fritz et al.,
adapted from files to areas, CONTEXT §3.2 / §9.7), per person p and area a:
    DOA_abs = 3.293 + 1.098·FA + 0.164·DL − 0.321·ln(1 + AC)
FA = p made the area's first delivery, DL = p's deliveries, AC = everyone else's. p is an
**author** of a when DOA_abs / max_a(DOA_abs) > 0.75 and DOA_abs ≥ 3.293. People who only
reviewed or discussed get no DOA (the formula is for people who changed the thing), just
the discussion signal. The ranking score adds recency on HERB's clock (D057):
    score = DOA_norm · exp(−days since p's last delivery / 90) + 0.2 · discussion_norm

**Truck factor** (Avelino et al.): remove the person who authors the most areas, repeat until
more than half of the product's areas have no author left; TF = how many were removed.

Pure functions here; `derive.expertise` reads the graph and writes the result.
"""

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

DAY_MS = 86_400_000
RECENCY_DAYS = 90
DISCUSSION_WEIGHT = 0.2
AUTHOR_NORM = 0.75
AUTHOR_ABS = 3.293
_REVERT = re.compile(r"^\s*revert(?:ing)?\s+pr\s*#?\s*\d+\s*:?\s*", re.I)
_PUNCT = re.compile(r"[^a-z0-9 ]+")


def doa(first: bool, deliveries: int, others: int) -> float:
    return 3.293 + 1.098 * int(first) + 0.164 * deliveries - 0.321 * math.log(1 + others)


def norm_text(text: str, product: str = "") -> str:
    t = _PUNCT.sub(" ", text.casefold().replace("&", " and "))
    words = [w for w in t.split() if w != product.casefold()]
    return " ".join(words)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")[:60]


def component_areas(raw: str | None, known: dict[str, str]) -> list[str]:
    """The component keys an issue's component names: itself, or both halves of 'A & B' /
    'A and B' when each half is a component on its own. `known` = key -> display name."""
    if not raw:
        return []
    key = norm_text(raw)
    parts = [norm_text(x) for x in re.split(r"\s+(?:&|and)\s+", raw, flags=re.I)]
    if len(parts) > 1 and all(p in known and p != key for p in parts):
        return parts
    return [key]


def work_item_key(title: str, product: str) -> str:
    return norm_text(_REVERT.sub("", title or ""), product)


@dataclass
class Area:
    id: str
    name: str
    kind: str  # component | work_item
    product_id: str
    aliases: set[str] = field(default_factory=set)
    deliveries: list[tuple[str, int, str]] = field(default_factory=list)  # person, ts, ref
    talk: list[tuple[str, int]] = field(default_factory=list)  # person, ts
    issues: set[str] = field(default_factory=set)
    prs: set[str] = field(default_factory=set)


def build_areas(
    issues: list[dict], issue_people: list[dict], prs: list[dict], reviews: list[dict]
) -> dict[str, Area]:
    """Areas with their deliveries and discussion, from the area_* query rows."""
    areas: dict[str, Area] = {}
    names: dict[str, dict[str, str]] = defaultdict(dict)  # product -> component key -> name
    for i in issues:
        if i["component"] and i["product_id"]:
            key = norm_text(i["component"])
            names[i["product_id"]].setdefault(key, i["component"])
    issue_areas: dict[str, list[str]] = {}
    for i in issues:
        product = i["product_id"]
        keys = component_areas(i["component"], names.get(product, {}))
        ids = []
        for key in keys:
            aid = f"area:{product}:c:{slug(key)}"
            area = areas.setdefault(
                aid, Area(aid, names[product].get(key, key), "component", product)
            )
            area.aliases.add(i["component"])
            area.issues.add(i["id"])
            ids.append(aid)
        issue_areas[i["id"]] = ids

    # issue work: a fix (PR author) and a resolution statement by the same person on the same
    # issue are one delivery; other claims are discussion
    seen: set[tuple[str, str, str]] = set()
    for r in sorted(issue_people, key=lambda r: (r["ts_ms"] or 0, r["how"])):
        for aid in issue_areas.get(r["issue_id"], []):
            area = areas[aid]
            if r["how"] in ("fix", "resolved"):
                key = (aid, r["person_id"], r["issue_id"])
                if key not in seen:
                    seen.add(key)
                    area.deliveries.append((r["person_id"], r["ts_ms"] or 0, r["issue_id"]))
                if r["how"] == "fix":
                    area.prs.add(r["ref_id"])
            else:
                area.talk.append((r["person_id"], r["ts_ms"] or 0))

    by_id = {p["id"]: p for p in prs}
    fixing = {p["id"] for p in prs if p["fixes"]}
    pr_area: dict[str, str] = {}

    def item_of(p: dict) -> str | None:
        root, hops = p, 0
        while root.get("reverts") and root["reverts"] in by_id and hops < 5:
            root, hops = by_id[root["reverts"]], hops + 1
        if root["id"] in fixing:
            return None  # a revert of a bug fix: the fix's component, below
        key = work_item_key(root["title"], root["product_id"])
        return f"area:{root['product_id']}:w:{slug(key)}" if key else None

    for p in sorted({p["id"]: p for p in prs}.values(), key=lambda p: (p["ts_ms"] or 0, p["id"])):
        if p["id"] in fixing:
            continue
        aid = item_of(p)
        if not aid:
            continue
        root_title = _REVERT.sub("", p["title"] or "") if not p.get("reverts") else None
        area = areas.setdefault(
            aid, Area(aid, root_title or p["title"], "work_item", p["product_id"])
        )
        if root_title and area.name.lower().startswith("revert"):
            area.name = root_title
        area.aliases.add(p["title"])
        area.prs.add(p["id"])
        pr_area[p["id"]] = aid
        if p["author"]:
            area.deliveries.append((p["author"], p["ts_ms"] or 0, p["id"]))
    for p in prs:
        if p["id"] in fixing:
            for aid in issue_areas.get(p["fixes"], []):
                pr_area.setdefault(p["id"], aid)
    for r in reviews:
        aid = pr_area.get(r["pr_id"])
        if aid:
            areas[aid].talk.append((r["person_id"], r["ts_ms"] or 0))
    return areas


def knows(area: Area, now_ms: int) -> list[dict[str, Any]]:
    """KNOWS rows for one area: DOA for the people who delivered, discussion for all."""
    if not area.deliveries and not area.talk:
        return []
    first_person = min(area.deliveries, key=lambda d: (d[1], d[2]))[0] if area.deliveries else None
    dl = Counter(p for p, _, _ in area.deliveries)
    total = sum(dl.values())
    talk = Counter(p for p, _ in area.talk)
    top_talk = max(talk.values(), default=0)
    people = set(dl) | set(talk)
    raw = {p: doa(p == first_person, dl[p], total - dl[p]) for p in dl}
    top = max(raw.values(), default=0.0)
    rows = []
    for p in sorted(people):
        times = [t for q, t, _ in area.deliveries if q == p] + [t for q, t in area.talk if q == p]
        last = max(times) if times else 0
        delivered = [t for q, t, _ in area.deliveries if q == p]
        abs_ = raw.get(p)
        norm = abs_ / top if abs_ is not None and top > 0 else 0.0
        disc = talk[p] / top_talk if top_talk else 0.0
        days = max(0.0, (now_ms - max(delivered)) / DAY_MS) if delivered else None
        recency = math.exp(-days / RECENCY_DAYS) if days is not None else 0.0
        rows.append(
            {
                "person_id": p,
                "area_id": area.id,
                "doa_abs": round(abs_, 4) if abs_ is not None else None,
                "doa_norm": round(norm, 4),
                "author": abs_ is not None and norm > AUTHOR_NORM and abs_ >= AUTHOR_ABS,
                "deliveries": dl[p],
                "first": p == first_person,
                "discussion": round(disc, 4),
                "score": round(norm * recency + DISCUSSION_WEIGHT * disc, 4),
                "first_ms": min(times) if times else None,
                "last_ms": last or None,
                "refs": sorted({ref for q, _, ref in area.deliveries if q == p})[:20],
            }
        )
    return rows


def truck_factor(authors_by_area: dict[str, set[str]]) -> tuple[int, list[str]]:
    """Avelino's greedy truck factor over areas that have at least one author: remove the top
    author until more than half of those areas have none. Returns (TF, people removed)."""
    areas = {a: set(ps) for a, ps in authors_by_area.items() if ps}
    if not areas:
        return 0, []
    removed: list[str] = []
    while sum(1 for ps in areas.values() if not ps) <= len(areas) / 2:
        counts = Counter(p for ps in areas.values() for p in ps)
        if not counts:
            break
        top = min(counts, key=lambda p: (-counts[p], p))
        removed.append(top)
        for ps in areas.values():
            ps.discard(top)
    return len(removed), removed
