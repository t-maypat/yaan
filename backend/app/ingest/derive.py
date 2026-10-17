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
