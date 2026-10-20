"""S9 Derived layers. WORKS_ON built in Phase 8.0 (R1-2); the rest in Phase 10.

WORKS_ON is computed inside FalkorDB in one statement: for every product, team and stage,
the first and last time a member of the team authored one of the product's artifacts. The
stage is the channel's (planning / dev / support), else the artifact kind (pr, doc,
meeting). The authority policy and the Sentinel's blast radius read it.
Still to come (Phase 10): KNOWS (degree-of-authorship + recency), COLLAB,
Company-USES-Product, PRECEDENT_FOR, truck factor.
Spec: docs/CONTEXT.md §9.3 (S9), §9.7.
"""

import time
from typing import Any

from app import db

MIN_ARTIFACTS = 3  # a team with one stray message isn't working on the product


def works_on(graph: str | None = None, min_artifacts: int = MIN_ARTIFACTS) -> dict:
    result = db.run(
        "derive_works_on", {"min_artifacts": min_artifacts}, graph=graph, readonly=False
    )
    return {"rows": 0, **result.stats}


EXPERTISE_ORDER = ("upsert_areas", "link_in_area_issue", "link_in_area_pr", "upsert_knows")
READ_LIMIT = 50_000


def run_stamp() -> int:
    """A rebuild's mark on what it writes, so it can then remove only what an earlier rebuild
    left: the layer is replaced in place, never emptied first (D112)."""
    return time.time_ns() // 1000


def expertise(graph: str | None = None) -> dict:
    """Phase 10.0-10.3 (D059): rebuild Areas, IN_AREA and KNOWS, and set each product's truck
    factor. Idempotent: the expertise layer is rewritten from the graph, then whatever the
    rebuild didn't write is removed (D112: readers never see it empty)."""
    from app.ingest import expertise as ex

    run = run_stamp()

    def read(name: str) -> list[dict]:
        return db.run_all(name, {}, cap=READ_LIMIT, graph=graph, timeout_ms=60_000)

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
    report = {}
    for name in EXPERTISE_ORDER:
        if rows[name]:
            stamped = [r | {"run": run} for r in rows[name]]
            result = db.run(name, {"rows": stamped}, graph=graph, readonly=False)
            report[name] = {"rows": len(rows[name]), **result.stats}
    for name in ("clear_areas", "clear_stale_expertise_edges"):  # what the rebuild didn't write
        report[name] = {"rows": 0, **db.run(name, {"run": run}, graph=graph, readonly=False).stats}
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
        return db.run_all(name, {}, cap=READ_LIMIT, graph=graph, timeout_ms=60_000)

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
    ranked = db.run_all("person_pagerank", {}, cap=READ_LIMIT, graph=graph, timeout_ms=60_000)
    scores = [
        {"id": r["id"], "score": round(r["score"], 6), "rank": n} for n, r in enumerate(ranked, 1)
    ]
    result = db.run("set_person_pagerank", {"rows": scores}, graph=graph, readonly=False)
    report["set_person_pagerank"] = {"rows": len(scores), **result.stats}
    return {"report": report, "pairs": len(rows) // 2, "top": scores[:5]}


MIN_COMMUNITY = 3  # CDLP leaves people with no strong tie in a group of their own


def communities(graph: str | None = None) -> dict:
    """10.4 (D064): FalkorDB's label propagation over COLLAB gives working groups; each group
    of >= MIN_COMMUNITY people becomes a Community node with its org-chart team mix, so the
    graph shows where real collaboration crosses team lines. Betweenness scores are stored on
    Person (the bridges between groups). Run after `collab`."""
    from collections import Counter, defaultdict

    def read(name: str) -> list[dict]:
        return db.run_all(name, {}, cap=READ_LIMIT, graph=graph, timeout_ms=60_000)

    groups: dict[int, list[str]] = defaultdict(list)
    for r in read("person_communities"):
        groups[r["community"]].append(r["id"])
    team = {r["person_id"]: r["team"] for r in read("person_teams")}
    rows, links = [], []
    for members in groups.values():
        if len(members) < MIN_COMMUNITY:
            continue
        members.sort()
        teams = Counter(team.get(p) or "no team" for p in members)
        top, n = min(teams.items(), key=lambda kv: (-kv[1], kv[0]))
        cid = f"comm:{members[0]}"
        rows.append(
            {
                "id": cid,
                "size": len(members),
                "teams": [f"{t} ({k})" for t, k in teams.most_common()],
                "team_count": len(teams),
                "top_team": top,
                "top_team_share": round(n / len(members), 3),
                "label": f"Working group of {len(members)}, mostly {top}",
            }
        )
        links += [{"person_id": p, "community_id": cid} for p in members]
    report = {
        "clear_communities": {
            "rows": 0,
            **db.run("clear_communities", {}, graph=graph, readonly=False).stats,
        }
    }
    for name, data in (("upsert_communities", rows), ("link_in_community", links)):
        if data:
            result = db.run(name, {"rows": data}, graph=graph, readonly=False)
            report[name] = {"rows": len(data), **result.stats}
    bc = read("person_betweenness")
    scores = [
        {"id": r["id"], "score": round(r["score"], 4), "rank": n} for n, r in enumerate(bc, 1)
    ]
    result = db.run("set_person_betweenness", {"rows": scores}, graph=graph, readonly=False)
    report["set_person_betweenness"] = {"rows": len(scores), **result.stats}
    sizes = sorted((r["size"] for r in rows), reverse=True)
    return {"report": report, "communities": sizes, "bridges": scores[:3]}


PRECEDENT_MIN_SIM = 0.80  # bge-small cosine; ~the top tenth of same-kind pairs on dev (D066)
PRECEDENTS_PER_DECISION = 3


def precedents(graph: str | None = None, min_sim: float = PRECEDENT_MIN_SIM) -> dict:
    """10.6 (D066): for each extracted decision (reports excluded), up to 3 earlier decisions of
    the same kind and value in the same product, about a different issue or PR whose title and
    description are similar (local embeddings, cosine >= min_sim), become
    (earlier)-[:PRECEDENT_FOR {basis: 'similar_subject', similarity}]->(later).

    Compared within each (kind, value, product) group as matrix products, in blocks (D112):
    the pairwise loop it replaces grew with the square of all decisions, in Python."""
    from app.ingest import chunk_embed

    run = run_stamp()
    rows = db.run_all(
        "precedent_candidates", {}, cap=READ_LIMIT, graph=graph, timeout_ms=60_000
    )
    vecs = chunk_embed.embed_passages([r["text"][:800] for r in rows]) if rows else []
    links = similar_earlier(rows, vecs, min_sim)
    report = {}
    if links:
        stamped = [x | {"run": run} for x in links]
        result = db.run("link_similar_precedent", {"rows": stamped}, graph=graph, readonly=False)
        report["link_similar_precedent"] = {"rows": len(links), **result.stats}
    cleared = db.run("clear_similar_precedents", {"run": run}, graph=graph, readonly=False)
    report["clear_similar_precedents"] = {"rows": 0, **cleared.stats}
    return {"report": report, "decisions": len(rows), "links": len(links)}


PRECEDENT_BLOCK = 512  # decisions compared at once: a block x group similarity matrix


def similar_earlier(
    rows: list[dict], vecs: list[list[float]], min_sim: float
) -> list[dict[str, Any]]:
    """For each decision, its PRECEDENTS_PER_DECISION most similar earlier decisions of the same
    kind, value and product about another subject, at cosine >= min_sim. Ties go to the larger
    id, as the pairwise loop's `sorted(..., reverse=True)` did."""
    import numpy as np

    groups: dict[tuple, list[int]] = {}
    for i, r in enumerate(rows):
        groups.setdefault((r["kind"], r["value"], r["product_id"]), []).append(i)
    links = []
    for members in groups.values():
        if len(members) < 2:
            continue
        v = np.asarray([vecs[i] for i in members], dtype=np.float64)
        ts = np.asarray([rows[i]["ts_ms"] or 0 for i in members])
        code: dict[str, int] = {}  # exact subject ids as integers, for an elementwise compare
        subj = np.asarray([code.setdefault(rows[i]["subject_id"], len(code)) for i in members])
        for lo in range(0, len(members), PRECEDENT_BLOCK):
            block = slice(lo, lo + PRECEDENT_BLOCK)
            sims = v[block] @ v.T
            ok = (ts[None, :] < ts[block, None]) & (subj[None, :] != subj[block, None])
            ok &= sims >= min_sim
            for bi, row_ok in enumerate(ok):
                hits = np.flatnonzero(row_ok)
                if not hits.size:
                    continue
                d = rows[members[lo + bi]]
                scored = sorted(
                    ((float(sims[bi, j]), rows[members[j]]["id"]) for j in hits), reverse=True
                )
                for sim, pid in scored[:PRECEDENTS_PER_DECISION]:
                    links.append(
                        {"precedent_id": pid, "decision_id": d["id"], "similarity": round(sim, 4)}
                    )
    return links


def uses(graph: str | None = None) -> dict:
    """10.6 (D066): Company-USES-Product from the issues their customers reported, rebuilt in
    place: written first, then the edges this run didn't write go (D112)."""
    run = run_stamp()
    report = {
        "derive_uses": {
            "rows": 0,
            **db.run("derive_uses", {"run": run}, graph=graph, readonly=False).stats,
        }
    }
    cleared = db.run("clear_uses", {"run": run}, graph=graph, readonly=False)
    report["clear_uses"] = {"rows": 0, **cleared.stats}
    return report
