"""S9 Derived layers. WORKS_ON built in Phase 8.0 (R1-2); the rest in Phase 10.

WORKS_ON is computed inside FalkorDB in one statement: for every product, team and stage,
the first and last time a member of the team authored one of the product's artifacts. The
stage is the channel's (planning / dev / support), else the artifact kind (pr, doc,
meeting). The authority policy and the Sentinel's blast radius read it.
Still to come (Phase 10): KNOWS (degree-of-authorship + recency), COLLAB,
Company-USES-Product, PRECEDENT_FOR, truck factor.
Spec: docs/CONTEXT.md §9.3 (S9), §9.7.
"""

from app import db

MIN_ARTIFACTS = 3  # a team with one stray message isn't working on the product


def works_on(graph: str | None = None, min_artifacts: int = MIN_ARTIFACTS) -> dict:
    result = db.run(
        "derive_works_on", {"min_artifacts": min_artifacts}, graph=graph, readonly=False
    )
    return {"rows": 0, **result.stats}


EXPERTISE_ORDER = ("upsert_areas", "link_in_area_issue", "link_in_area_pr", "upsert_knows")
READ_LIMIT = 50_000


def expertise(graph: str | None = None) -> dict:
    """Phase 10.0-10.3 (D059): rebuild Areas, IN_AREA and KNOWS, and set each product's truck
    factor. Idempotent: the expertise layer is cleared and rewritten from the graph."""
    from app.ingest import expertise as ex

    def read(name: str) -> list[dict]:
        return db.run(name, {"limit": READ_LIMIT}, graph=graph, timeout_ms=60_000).rows

    areas = ex.build_areas(
        read("area_issues"), read("area_issue_people"), read("area_prs"), read("area_reviews")
    )
    clock = {r["product_id"]: r["last_ms"] for r in read("product_clock")}
    rows: dict[str, list[dict]] = {name: [] for name in EXPERTISE_ORDER}
    authors: dict[str, dict[str, set[str]]] = {}
    for a in areas.values():
        k = ex.knows(a, clock.get(a.product_id) or 0)
        rows["upsert_knows"] += k
        area_authors = {r["person_id"] for r in k if r["author"]}
        authors.setdefault(a.product_id, {})[a.id] = area_authors
        times = [t for _, t, _ in a.deliveries]
        rows["upsert_areas"].append(
            {
                "id": a.id,
                "name": a.name,
                "kind": a.kind,
                "product_id": a.product_id,
                "aliases": sorted(a.aliases),
                "deliveries": len(a.deliveries),
                "authors": sorted(area_authors),
                "first_ms": min(times) if times else None,
                "last_ms": max(times) if times else None,
            }
        )
        rows["link_in_area_issue"] += [{"issue_id": i, "area_id": a.id} for i in sorted(a.issues)]
        rows["link_in_area_pr"] += [{"pr_id": p, "area_id": a.id} for p in sorted(a.prs)]
    truck = []
    for product, by_area in sorted(authors.items()):
        tf, people = ex.truck_factor(by_area)
        left = {aid: ps - set(people) for aid, ps in by_area.items() if ps}
        truck.append(
            {
                "product_id": product,
                "truck_factor": tf,
                "truck_people": people,
                "areas_with_author": len(left),
                "areas_orphaned": sum(1 for ps in left.values() if not ps),
            }
        )
    report = {
        "clear_areas": {"rows": 0, **db.run("clear_areas", {}, graph=graph, readonly=False).stats}
    }
    for name in EXPERTISE_ORDER:
        if rows[name]:
            result = db.run(name, {"rows": rows[name]}, graph=graph, readonly=False)
            report[name] = {"rows": len(rows[name]), **result.stats}
    result = db.run("set_product_truck", {"rows": truck}, graph=graph, readonly=False)
    report["set_product_truck"] = {"rows": len(truck), **result.stats}
    return {"report": report, "truck": truck, "areas": len(areas)}


COLLAB_MIN_W = 0.25  # one 55-person meeting adds 1/54 per pair; keep repeated, real collaboration


def collab_weights(
    meetings: list[dict], messages: list[dict], prs: list[dict], reviews: list[dict]
) -> dict[tuple[str, str], dict]:
    """Pair weights (a < b) from three signals (10.1, D060): each review links reviewer and PR
    author (1 per review); each meeting with n attendees adds 1/(n-1) per pair; so does each
    Slack conversation (channel + HERB's conversation key) with n writers."""
    from collections import defaultdict

    pairs: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"w": 0.0, "reviews": 0, "meetings": 0, "chats": 0}
    )

    def groups(rows, key, member):
        out = defaultdict(set)
        for r in rows:
            out[key(r)].add(r[member])
        return out

    for kind, by in (
        ("meetings", groups(meetings, lambda r: r["meeting_id"], "person_id")),
        (
            "chats",
            groups(
                messages,
                lambda r: (r["channel_id"], r["message_id"].removeprefix("msg:").split("-", 1)[0]),
                "person_id",
            ),
        ),
    ):
        for people in by.values():
            ps = sorted(people)
            for i, a in enumerate(ps):
                for b in ps[i + 1 :]:
                    pairs[(a, b)]["w"] += 1 / (len(ps) - 1)
                    pairs[(a, b)][kind] += 1
    author = {p["id"]: p["author"] for p in prs}
    for r in reviews:
        a, b = r["person_id"], author.get(r["pr_id"])
        if a and b and a != b:
            key = (min(a, b), max(a, b))
            pairs[key]["w"] += 1
            pairs[key]["reviews"] += 1
    return dict(pairs)


def collab(graph: str | None = None, min_w: float = COLLAB_MIN_W) -> dict:
    """10.1 + 10.4: rebuild COLLAB (both directions, pairs with weight >= min_w), then run
    FalkorDB's PageRank on it and store each person's score and rank."""

    def read(name: str) -> list[dict]:
        return db.run(name, {"limit": READ_LIMIT}, graph=graph, timeout_ms=60_000).rows

    pairs = collab_weights(
        read("collab_meetings"), read("collab_messages"), read("area_prs"), read("area_reviews")
    )
    rows = []
    for (a, b), v in sorted(pairs.items()):
        if v["w"] >= min_w:
            v = {**v, "w": round(v["w"], 4)}
            rows += [{"a": a, "b": b, **v}, {"a": b, "b": a, **v}]
    report = {
        "clear_collab": {"rows": 0, **db.run("clear_collab", {}, graph=graph, readonly=False).stats}
    }
    if rows:
        result = db.run("upsert_collab", {"rows": rows}, graph=graph, readonly=False)
        report["upsert_collab"] = {"rows": len(rows), **result.stats}
    ranked = db.run("person_pagerank", {"limit": READ_LIMIT}, graph=graph, timeout_ms=60_000).rows
    scores = [
        {"id": r["id"], "score": round(r["score"], 6), "rank": n} for n, r in enumerate(ranked, 1)
    ]
    result = db.run("set_person_pagerank", {"rows": scores}, graph=graph, readonly=False)
    report["set_person_pagerank"] = {"rows": len(scores), **result.stats}
    return {"report": report, "pairs": len(rows) // 2, "top": scores[:5]}
