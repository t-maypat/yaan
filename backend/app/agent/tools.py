"""Agent tools 1-14. search_artifacts in Phase 4, the rest in Phases 9-11.

Read-only; accept names or ids; return compact JSON with evidence_ids and the .cypher
file used; explain the why (path/edges), not just values.
Spec: docs/CONTEXT.md §9.6 (tool table and design rules).
"""

import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from app import db
from app.ingest.chunk_embed import embed_query

RRF_K = 60  # reciprocal rank fusion constant (Cormack et al.); damps the top ranks
# Chunks per list fed to fusion, per result wanted: enough for per-artifact grouping to
# leave k. Deeper lists let weak matches found by both lists crowd out strong hits found by
# one; dev hybrid hit@20 is 0.958 at 3 vs 0.906 at 5 (D042).
DEPTH_FACTOR = 3
INDEX_FACTOR = 5  # the graph-wide vector index is asked for more: its filters apply after it
MAX_FETCH = 200
MAX_KEYWORDS = 12
SNIPPET_CHARS = 300
STOPWORDS = frozenset(
    """a about above after again against all am an and any are as at be because been before
    being below between both but by can could did do does doing down during each few for from
    further had has have having he her here hers him his how i if in into is it its itself just
    me more most my no nor not now of off on once only or other our ours out over own same she
    should so some such than that the their theirs them then there these they this those
    through to too under until up very was we were what when where which while who whom why
    will with would you your yours tell give list name show find please any was were""".split()
)
Mode = Literal["hybrid", "fulltext", "vector"]
_EMBED_LOCK = threading.Lock()  # agent tools run in the eval's thread pool; one embedder


def keywords(text: str) -> str:
    """A full-text query from free text: content words joined with OR (A11).

    FalkorDB full-text ANDs every word and has no stopword list, so a raw question would
    require "what", "the" and every other word to appear. Words are \\w+ tokens, so no
    query syntax (`|`, `-`, `%`, quotes) can come from the user's text. The product's own
    name stays in: within one product's files it separates that team's discussion from
    HERB's distractors (dropping it cost 0.17 full-text hit@10 on dev, D042).
    """
    words = [w for w in re.findall(r"\w+", text.lower()) if len(w) > 1 and w not in STOPWORDS]
    return " | ".join(list(dict.fromkeys(words))[:MAX_KEYWORDS])


def search_artifacts(
    query: str,
    kinds: list[str] | None = None,
    product_id: str | None = None,
    since_ms: int | None = None,
    until_ms: int | None = None,
    k: int = 10,
    *,
    mode: Mode = "hybrid",
    full_text: bool = False,
    people: bool = False,
    graph: str | None = None,
    reach: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Tool 1: hybrid search over chunks, fused with RRF, one result per artifact.

    `mode` is for evaluation and ablations: full-text only, vector only, or both fused.
    `full_text` adds each best chunk's whole text (the baseline RAG reads it; agents quote
    from get_artifact instead). `people` adds each artifact's author and participant ids,
    the metadata HERB's own RAG documents carry (D036). `reach`: a viewer's (Access.reach):
    only what they may read is ranked (D131).
    """
    from app.agent.access import UNSCOPED

    fetch = min(MAX_FETCH, k * DEPTH_FACTOR)
    filters = {"kinds": kinds, "product_id": product_id, "since_ms": since_ms, "until_ms": until_ms}
    reach = reach or UNSCOPED
    lists: dict[str, list[dict[str, Any]]] = {}
    timings: dict[str, float] = {}
    used: list[str] = []
    q = keywords(query)
    if mode in ("hybrid", "fulltext") and q:
        started = time.perf_counter()
        lists["fulltext"] = db.run(
            "search_fulltext", {"q": q, "k": fetch, **filters, **reach}, graph=graph
        ).rows
        timings["fulltext_ms"] = ms_since(started)
        used.append("search_fulltext.cypher")
    if mode in ("hybrid", "vector"):
        started = time.perf_counter()
        with _EMBED_LOCK:
            vector = embed_query(query)
        timings["embed_ms"] = ms_since(started)
        started = time.perf_counter()
        # With a product, score that product's own chunks exactly: the index returns the
        # nearest chunks graph-wide, and filtering those by product loses most of them once
        # several products share the graph (D042). Unscoped searches keep the index.
        name = "search_vector" if product_id is None else "search_vector_scoped"
        # a viewer's search filters the index's nearest: ask the index for as many as allowed
        index_k = MAX_FETCH if reach["scoped"] else min(MAX_FETCH, k * INDEX_FACTOR)
        params = {"qvec": vector, "fetch": index_k, "k": fetch, **filters, **reach}
        lists["vector"] = db.run(name, params, graph=graph).rows
        timings["vector_ms"] = ms_since(started)
        used.append(f"{name}.cypher")

    results = fuse(lists, k, full_text)
    if people and results:
        started = time.perf_counter()
        ids = [r["artifact_id"] for r in results]
        found = {
            row["artifact_id"]: row
            for row in db.run("artifact_people", {"ids": ids}, graph=graph).rows
        }
        for r in results:
            r["authors"] = sorted(found.get(r["artifact_id"], {}).get("authors", []))
            r["participants"] = sorted(found.get(r["artifact_id"], {}).get("participants", []))
        timings["people_ms"] = ms_since(started)
        used.append("artifact_people.cypher")
    return {
        "query": query,
        "keywords": q,
        "mode": mode,
        "filters": {key: value for key, value in filters.items() if value is not None},
        "results": results,
        "evidence_ids": [r["artifact_id"] for r in results],
        "cypher": used,
        "timings": timings,
    }


def fuse(
    lists: dict[str, list[dict[str, Any]]], k: int, full_text: bool = False
) -> list[dict[str, Any]]:
    """Reciprocal rank fusion over chunk lists, then the best chunk per artifact."""
    scores: dict[str, float] = {}
    best: dict[str, dict[str, Any]] = {}
    ranks: dict[str, dict[str, int]] = {}
    for name, rows in lists.items():
        for rank, row in enumerate(rows, start=1):
            chunk = row["chunk_id"]
            scores[chunk] = scores.get(chunk, 0.0) + 1.0 / (RRF_K + rank)
            best.setdefault(chunk, row)
            ranks.setdefault(chunk, {})[f"{name}_rank"] = rank
    per_artifact: dict[str, dict[str, Any]] = {}
    for chunk in sorted(scores, key=lambda c: -scores[c]):
        row = best[chunk]
        if row["artifact_id"] in per_artifact:
            continue
        per_artifact[row["artifact_id"]] = {
            "artifact_id": row["artifact_id"],
            "kind": row["kind"],
            "title": row["title"],
            "url": row.get("url"),
            "ts_iso": row["ts_iso"],
            "product_id": row["product_id"],
            "chunk_id": chunk,
            "field": row["field"],
            "snippet": row["text"][:SNIPPET_CHARS],
            "rrf": round(scores[chunk], 5),
            **ranks[chunk],
        }
        if full_text:
            per_artifact[row["artifact_id"]]["text"] = row["text"]
        if len(per_artifact) == k:
            break
    return list(per_artifact.values())


def ms_since(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


# --- agent tools that need no extraction: slice A (9.0, D038) ------------------------------
# Each returns compact JSON-ready data with the .cypher files it ran, so an answer can show
# its work. Product-level tools list everything the product has (tens of rows): the model
# reads the full set instead of trusting a top-k search not to miss one.
LIST_TEXT_CHARS = 500  # per message or report in a list
ARTIFACT_CHARS = 8000  # get_artifact; meetings run to ~10k characters
DOC_WINDOW_MS = 7 * 86_400_000  # a doc discussion with no newer version shared ends here
MAX_DOCS = 3


def cut_note(cut: bool, shown: int) -> dict[str, str]:
    """`truncated` for a list cut at its cap, telling the model what that means; nothing for
    a complete list, so complete results (and the answer cache keyed on them) don't change."""
    if not cut:
        return {}
    return {
        "truncated": f"only the first {shown} are listed and more exist: say 'at least "
        f"{shown}' and never present this list or its counts as complete"
    }


def clip(text: str | None, limit: int) -> str | None:
    if text is None or len(text) <= limit:
        return text
    return text[:limit] + f" … [{len(text) - limit} more characters: call get_artifact]"


def resolve_entity(text: str, product_id: str | None = None, graph: str | None = None) -> dict:
    """Tool 2: a name, codename or role -> ids. Questions say "CollaborateForce" and
    "Engineering Lead"; the text says "abAIX" and people's names (D038 evidence)."""
    words = " ".join(re.findall(r"\w+", text))  # full-text ANDs the words; no syntax passes
    out: dict[str, Any] = {"text": text, "cypher": ["resolve_product.cypher"]}
    out["products"] = db.run("resolve_product", {"text": text.strip()}, graph=graph).rows
    names = []
    if words:
        params = {"q": words, "product_id": product_id, "k": 10}
        names = db.run("resolve_names", params, graph=graph).rows
        out["cypher"].append("resolve_names.cypher")
    for kind, label in (("people", "Person"), ("customers", "Customer"), ("companies", "Company")):
        out[kind] = [
            {k: v for k, v in r.items() if k not in ("labels", "score") and v is not None}
            for r in names
            if label in r["labels"]
        ]
    if product_id:
        params = {"product_id": product_id, "role": text.strip(), "k": 25}
        out["people_with_role"] = db.run("people_by_role", params, graph=graph).rows
        out["cypher"].append("people_by_role.cypher")
    return out


def get_artifact(artifact_id: str, graph: str | None = None) -> dict:
    """Tool 3: one artifact's full text for quoting, plus the people around it."""
    rows = db.run("get_artifact", {"id": artifact_id}, graph=graph).rows
    if not rows:
        return {"error": f"no artifact with id {artifact_id!r}"}
    a = rows[0]
    people = db.run("artifact_people_detail", {"id": artifact_id}, graph=graph).rows
    return {
        **{k: v for k, v in a.items() if k != "fields" and v is not None},
        "text": clip(a["text"], ARTIFACT_CHARS),
        "fields": [f for f in a["fields"] if f["field"]],
        "people": people,
        "cypher": ["get_artifact.cypher", "artifact_people_detail.cypher"],
    }


def doc_discussion(doc: str, product_id: str, graph: str | None = None) -> dict:
    """Tool 15: a document's authors and its review: the Slack discussion after it was shared
    (until a newer version is shared there) and the meetings that discussed its type.

    Checked on the 5 dev "authors and key reviewers" questions: author + discussion +
    meeting speakers held every person in HERB's answer (D038).
    """
    found = db.run("find_docs", {"product_id": product_id, "doc": doc}, graph=graph).rows
    by_base: dict[str, list[dict[str, Any]]] = {}
    for d in found:
        by_base.setdefault(d["base_id"] or d["id"], []).append(d)
    documents = []
    for base, versions in list(by_base.items())[:MAX_DOCS]:
        params = {"doc_id": base, "window_ms": DOC_WINDOW_MS}
        messages, cut = db.run_capped("doc_discussion", params, cap=60, graph=graph)
        meetings = db.run("doc_meetings", {"doc_id": base}, graph=graph).rows
        authors = {p for v in versions for p in v["authors"]}
        documents.append(
            {
                "base_id": base,
                "doc_type": versions[0]["doc_type"],
                "versions": [
                    {k: v[k] for k in ("id", "version", "title", "ts_iso", "url")} for v in versions
                ],
                "authors": sorted(authors),
                # Two lists, not a flag: the model read a flagged author summary as the
                # author's own suggestions (D047). The review is what others said.
                "feedback": [
                    {**m, "text": clip(m["text"], LIST_TEXT_CHARS)}
                    for m in messages
                    if m["person_id"] not in authors
                ],
                "author_messages": [
                    {**m, "text": clip(m["text"], LIST_TEXT_CHARS)}
                    for m in messages
                    if m["person_id"] in authors
                ],
                "meetings": [
                    {
                        **mt,
                        "speakers": [
                            {**s, "is_author": s["id"] in authors}
                            for s in mt["speakers"]
                            if s["id"]
                        ],
                    }
                    for mt in meetings
                ],
                **cut_note(cut, len(messages)),
            }
        )
    return {
        "doc": doc,
        "product_id": product_id,
        "documents": documents,
        "note": None if documents else "no document of this product matches; try its type",
        "cypher": ["find_docs.cypher", "doc_discussion.cypher", "doc_meetings.cypher"],
    }


def issue_reports(product_id: str, graph: str | None = None) -> dict:
    """Tool 16: every customer issue report in the product's support channels, with the
    customers' companies (MENTIONS Customer -> WORKS_AT -> Company) and the people mentioned
    (usually the assignee). Slice B replaces it with Issue nodes and their status chains."""
    rows, cut = db.run_capped("issue_reports", {"product_id": product_id}, cap=150, graph=graph)
    return {
        "product_id": product_id,
        "reports": [{**r, "text": clip(r["text"], LIST_TEXT_CHARS)} for r in rows],
        **cut_note(cut, len(rows)),
        "cypher": ["issue_reports.cypher"],
    }


def list_prs(
    product_id: str,
    status: str | None = None,
    graph: str | None = None,
    action: str | None = None,
    as_of_ms: int | None = None,
) -> dict:
    """Tool 5, PR case: the product's own pull requests with their status chain from the PR
    record and its reviews (D028), plus what the team said to do with each in Slack: the
    `pr:<id>/action` chain from extracted claims (D049). External PRs are left out.

    With `as_of_ms` (R2) every PR shows the state in force at that moment: PRs opened later
    are left out, the chain stops there, and only what was said by then is kept."""
    as_of = as_of_ms is not None
    params = {
        "product_id": product_id,
        "status": None if as_of else status,
        "action": None if as_of else action,
    }
    rows, cut = db.run_capped("product_prs", params, cap=200, graph=graph)
    cypher = ["product_prs.cypher"]
    states = states_at(product_id, as_of_ms, graph) if as_of else {}
    if as_of:
        cypher.append("states_at.cypher")
    kept = []
    for r in rows:
        r["reviews"] = [rv for rv in r["reviews"] if rv["id"]]
        reverts_ms = [u.pop("ts_ms") for u in r["reverted_by"]]  # for as_of only; not shown
        r["reverted_by"] = [
            {**u, "reason": clip(u["reason"], 300)}
            for u, ms in zip(r["reverted_by"], reverts_ms, strict=True)
            if u["number"] and (not as_of or (ms or 0) <= as_of_ms)
        ]
        r["said"] = [c for c in r["said"] if c["action"]]
        said_ms = [c.pop("ts_ms") for c in r["said"]]  # for as_of only; not shown
        chain_ms = r.pop("chain_ms")
        if as_of:
            state = states.get(r["id"], {})
            if "pr_status" not in state:
                continue  # not opened yet at that moment
            r["chain"] = [v for v, ms in zip(r["chain"], chain_ms, strict=True) if ms <= as_of_ms]
            r["status"] = state["pr_status"]
            r["team_action"] = state.get("pr_action")
            if r["team_action"] is None:
                r["action_reason"] = None
            r["said"] = [
                c for c, ms in zip(r["said"], said_ms, strict=True) if (ms or 0) <= as_of_ms
            ]
            if (status and r["status"] != status) or (action and r["team_action"] != action):
                continue
        kept.append(r)
    out = {"product_id": product_id, "prs": kept, "cypher": cypher, **cut_note(cut, len(rows))}
    if as_of:
        out["as_of"] = iso(as_of_ms)
    return out


UNRESOLVED = ("reported", "reopened")


def list_issues(
    product_id: str,
    status: str | None = None,
    company: str | None = None,
    graph: str | None = None,
    as_of_ms: int | None = None,
) -> dict:
    """Tool 5, issue case (slice B): canonical customer issues (S7, D049) with their current
    status from the fold, the status history as claims with quotes, the assignee, reporting
    customers and companies, and the PRs that fixed them. `status='unresolved'` means
    reported or reopened; `company` keeps issues reported by that company's customers.

    With `as_of_ms` (R2) each issue shows the status in force at that moment (its decision's
    validity window holds it), issues reported later are left out, and the history stops."""
    as_of = as_of_ms is not None
    query_status = None if status == "unresolved" or as_of else status
    params = {"product_id": product_id, "status": query_status}
    states = states_at(product_id, as_of_ms, graph) if as_of else {}
    issues = []
    rows, cut = db.run_capped("product_issues", params, cap=200, graph=graph)
    for r in rows:
        r["customers"] = [c for c in r["customers"] if c["id"]]
        r["assignees"] = [a for a in r["assignees"] if a["id"]]
        r["fixed_by"] = [p for p in r["fixed_by"] if p["id"]]
        r["history"] = [h for h in r["history"] if h["status"]]
        history_ms = [h.pop("ts_ms") for h in r["history"]]  # for as_of only; not shown
        for h in r["history"]:  # only refused claims carry the key, so other output is unchanged
            if reason := h.pop("refused"):
                h["not_applied"] = f"the authority policy refused this change: {reason}"
        if as_of:
            if "issue_status" not in states.get(r["id"], {}):
                continue  # not reported yet at that moment
            r["status"] = states[r["id"]]["issue_status"]
            r["history"] = [
                h for h, ms in zip(r["history"], history_ms, strict=True) if (ms or 0) <= as_of_ms
            ]
            r["reopen_count"] = sum(h["status"] == "reopened" for h in r["history"])
            if status not in (None, "unresolved") and r["status"] != status:
                continue
        if status == "unresolved" and r["status"] not in UNRESOLVED:
            continue
        companies = " ".join((c["company"] or "") for c in r["customers"]).casefold()
        if company and company.casefold().replace(" ", "") not in companies.replace(" ", ""):
            continue
        issues.append(r)
    counts: dict[str, int] = {}
    for r in issues:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    out = {
        "product_id": product_id,
        "filters": {"status": status, "company": company},
        "counts_by_status": counts,
        "issues": issues,
        **cut_note(cut, len(rows)),
        "cypher": ["product_issues.cypher"],
    }
    if as_of:
        out["as_of"] = iso(as_of_ms)
        out["cypher"].append("states_at.cypher")
    return out


_PRODUCT_IDS: dict[str | None, frozenset[str]] = {}


def product_ids(graph: str | None = None, refresh: bool = False) -> frozenset[str]:
    """Every Product id in the graph, cached per graph; `loop.call_tool` refuses ids outside
    it, after one re-read so a product added since is still found (D070)."""
    if refresh or graph not in _PRODUCT_IDS:
        rows = db.run("list_products", {"limit": 100}, graph=graph).rows
        _PRODUCT_IDS[graph] = frozenset(r["id"] for r in rows)
    return _PRODUCT_IDS[graph]


def links_shared(product_id: str, graph: str | None = None) -> dict:
    """Tool 9: links shared in the product's messages, who shared them and where."""
    rows, cut = db.run_capped("links_shared", {"product_id": product_id}, cap=150, graph=graph)
    return {
        "product_id": product_id,
        "links": rows,
        **cut_note(cut, len(rows)),
        "cypher": ["links_shared.cypher"],
    }


AREA_MATCH_MIN = 70  # rapidfuzz token-set score for a topic to name an area
AREA_MATCH_SPREAD = 8  # keep areas within this many points of the best match


def match_areas(topic: str, areas: list[dict]) -> list[dict]:
    """The areas a topic names: best rapidfuzz token-set score over name and aliases."""
    from rapidfuzz import fuzz

    t = topic.casefold()
    scored = []
    for a in areas:
        names = [a["name"], *(a.get("aliases") or [])]
        best = max(fuzz.token_set_ratio(t, n.casefold()) for n in names if n)
        # a subset scores 100 under token-set ("API" in "API & Data Integration Layer"), so
        # ties go to the closer full string
        exact = max(fuzz.ratio(t, n.casefold()) for n in names if n)
        scored.append((best, exact, a))
    scored.sort(key=lambda x: (-x[0], -x[1], x[2]["id"]))
    if not scored or scored[0][0] < AREA_MATCH_MIN:
        return []
    top = scored[0][0]
    return [
        {**a, "match": s} for s, _, a in scored if s >= max(AREA_MATCH_MIN, top - AREA_MATCH_SPREAD)
    ][:3]


def collab_paths(pairs: list[tuple[str, str]], graph: str | None = None) -> dict[tuple, list]:
    """Shortest COLLAB paths (<= 4 hops, FalkorDB shortestPath) for (from, to) person pairs:
    {(a, b): [names along the path]}; an empty list when they aren't connected (D065)."""
    if not pairs:
        return {}
    rows = db.run_all(
        "collab_paths", {"pairs": [{"a": a, "b": b} for a, b in pairs]}, cap=200, graph=graph
    )
    return {(r["a"], r["b"]): r["names"] for r in rows}


def who_knows(topic: str, product_id: str, graph: str | None = None) -> dict:
    """Tool 10 (10.7, D059): who knows an area of the product best. The topic is matched to
    the product's areas (bug components and development work items). For each area: the
    experts by degree-of-authorship with recency (`author` = holds the area), the PRs they
    wrote there as quotable evidence, the listed owner (who its issues are assigned to), and
    whether an expert is a key person in the product's truck factor. When no author is left,
    `connectors` are the best-connected collaborators (PageRank over COLLAB)."""
    areas = match_areas(
        topic, db.run_all("product_areas", {"product_id": product_id}, cap=2000, graph=graph)
    )
    cypher = ["product_areas.cypher"]
    if not areas:
        return {
            "topic": topic,
            "product_id": product_id,
            "areas": [],
            "cypher": cypher,
            "note": "no component or work item of this product matches the topic; "
            "try search_artifacts",
        }
    ids = [a["id"] for a in areas]
    params = {"ids": ids}
    experts = db.run_all("area_experts", params, cap=1000, graph=graph)
    evidence = db.run_all("area_evidence", params, cap=2000, graph=graph)
    owners = db.run_all("area_owners", params, cap=1000, graph=graph)
    truck = (db.run("product_truck", {"product_id": product_id}, graph=graph).rows or [{}])[0]
    key_people = set(truck.get("truck_people") or [])
    cypher += [
        "area_experts.cypher",
        "area_evidence.cypher",
        "area_owners.cypher",
        "product_truck.cypher",
    ]
    out_areas = []
    any_author = False
    for a in areas:
        # authors first: an area's author still knows it after the recency score has decayed
        mine = sorted(
            (e for e in experts if e["area_id"] == a["id"]),
            key=lambda e: (not e["author"], -e["score"]),
        )
        authors = [e for e in mine if e["author"]]
        any_author = any_author or bool(authors)
        ranked = []
        for e in mine[:6]:
            prs = [
                {
                    "artifact_id": v["artifact_id"],
                    "number": v["number"],
                    "title": v["title"],
                    "ts_iso": v["ts_iso"],
                }
                for v in evidence
                if v["area_id"] == a["id"] and v["person_id"] == e["id"]
            ][:3]
            ranked.append(
                {
                    "id": e["id"],
                    "name": e["name"],
                    "role": e["role"],
                    "author": e["author"],
                    "first": e["first"],
                    "deliveries": e["deliveries"],
                    "discussion": e["discussion"],
                    "score": e["score"],
                    "key_person": e["id"] in key_people,
                    "evidence": prs,
                }
            )
        assigned = [o for o in owners if o["area_id"] == a["id"]]
        open_ = [o for o in assigned if o["status"] in UNRESOLVED]
        listed = open_ or assigned
        out_areas.append(
            {
                "id": a["id"],
                "name": a["name"],
                "kind": a["kind"],
                "match": a["match"],
                "experts": ranked,
                "single_expert": len(authors) == 1,
                # each owner with the issue's report message, which asks them to take it:
                # the listed owner is a graph fact, and this is the quote that backs it (D074)
                "listed_owners": [
                    {
                        "id": o["id"],
                        "name": o["name"],
                        "role": o["role"],
                        "issue": o["title"],
                        "status": o["status"],
                        "artifact_id": o["report_id"],
                        "text": clip(o["report_text"], 300),
                    }
                    for o in listed[:5]
                ],
            }
        )
    # how each area's first listed owner reaches its top expert, when they differ (D065)
    pairs = []
    for a in out_areas:
        if (
            a["listed_owners"]
            and a["experts"]
            and a["listed_owners"][0]["id"] != a["experts"][0]["id"]
        ):
            pairs.append((a["listed_owners"][0]["id"], a["experts"][0]["id"]))
    paths = collab_paths(pairs, graph)
    for a in out_areas:
        if a["listed_owners"] and a["experts"]:
            owner, expert = a["listed_owners"][0], a["experts"][0]
            if owner["id"] == expert["id"]:
                a["owner_is_expert"] = True
            else:
                names = paths.get((owner["id"], expert["id"])) or []
                a["owner_is_expert"] = False
                a["owner_to_expert"] = {
                    "owner": owner["name"],
                    "expert": expert["name"],
                    "path": names,
                    "hops": len(names) - 1 if names else None,
                }
    if pairs:
        cypher.append("collab_paths.cypher")
    out = {
        "topic": topic,
        "product_id": product_id,
        "areas": out_areas,
        "truck_factor": {
            "value": truck.get("truck_factor"),
            "key_people": truck.get("truck_people") or [],
            "meaning": "removing these people in order leaves more than half of the "
            "product's areas with no author",
        },
        "cypher": cypher,
    }
    if not any_author:
        out["connectors"] = db.run("area_connectors", {"ids": ids, "limit": 5}, graph=graph).rows
        out["cypher"].append("area_connectors.cypher")
    return out


# --- R2: the rest of the toolset (decisions, as-of, rankings, absences, impact, people) -----
ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"  # the artifacts' ts_iso form, so ISO strings compare in order
DAY_MS = 86_400_000
PR_NUMBER = re.compile(r"(?:\bPR\s*#?\s*|#)(\d+)\b", re.IGNORECASE)
SUBJECT_MATCH_MIN = 70  # rapidfuzz token-set score for a phrase to name an issue or PR title
QUOTE_CHARS = 300


def iso(ms: int | None) -> str | None:
    from datetime import UTC, datetime

    return None if ms is None else datetime.fromtimestamp(ms / 1000, UTC).strftime(ISO_FORMAT)


def parse_as_of(value: str | None) -> int | None:
    """An ISO date or datetime -> epoch ms (UTC). A bare date means the end of that day, so
    "as of 2027-01-15" includes everything said on the 15th."""
    from datetime import UTC, datetime

    if value is None or not str(value).strip():
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        if len(text) == 10:
            day = datetime.fromisoformat(text).replace(tzinfo=UTC)
            return int(day.timestamp() * 1000) + DAY_MS - 1
        moment = datetime.fromisoformat(text)
    except ValueError as e:
        raise ValueError(f"as_of {value!r} is not an ISO date such as 2027-01-15") from e
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return int(moment.timestamp() * 1000)


def states_at(product_id: str, as_of_ms: int, graph: str | None = None) -> dict[str, dict]:
    """{subject id: {decision kind: value}} for the decisions in force at `as_of_ms`."""
    rows = db.run_all(
        "states_at", {"product_id": product_id, "as_of_ms": as_of_ms}, cap=10_000, graph=graph
    )
    out: dict[str, dict] = {}
    for r in rows:
        out.setdefault(r["subject_id"], {})[r["kind"]] = r["value"]
    return out


def match_subjects(subject: str, product_id: str, graph: str | None = None) -> list[dict]:
    """The product's issues or own PRs a phrase names: an id (iss:… / pr:… / dec:…), a PR
    number ("PR #12"), or a title, matched like who_knows matches areas. At most 3."""
    from rapidfuzz import fuzz

    text = subject.strip()
    rows = db.run_all("lineage_subjects", {"product_id": product_id}, cap=10_000, graph=graph)
    if text.startswith("dec:"):  # dec:<topic>:<valid_from_ms>, topic = <subject id>/<facet>
        text = text[4:].rsplit(":", 1)[0].rsplit("/", 1)[0]
    if text.startswith(("iss:", "pr:")):
        return [r for r in rows if r["id"] == text.split("/")[0]]
    if m := PR_NUMBER.search(text):
        return [r for r in rows if r["kind"] == "pr" and r["number"] == int(m[1])][:3]
    t = text.casefold()
    scored = []
    for r in rows:
        if not r["title"]:
            continue
        title = r["title"].casefold()
        scored.append((fuzz.token_set_ratio(t, title), fuzz.ratio(t, title), r))
    scored.sort(key=lambda x: (-x[0], -x[1], x[2]["id"]))
    if not scored or scored[0][0] < SUBJECT_MATCH_MIN:
        return []
    top = scored[0][0]
    return [r for s, _, r in scored if s >= max(SUBJECT_MATCH_MIN, top - AREA_MATCH_SPREAD)][:3]


def topics_of(subject: dict) -> list[str]:
    facets = ("status", "action") if subject["kind"] == "pr" else ("status",)
    return [f"{subject['id']}/{f}" for f in facets]


def _state(row: dict, as_of_ms: int | None) -> dict:
    """One decision of a chain, compact: nulls left out of the nested lists."""
    start, end = row["valid_from_ms"], row["valid_to_ms"]
    in_force = (
        start <= as_of_ms and (end is None or end > as_of_ms)
        if as_of_ms is not None
        else row["status"] == "active"
    )
    out = {
        "decision_id": row["id"],
        "value": row["value"],
        "statement": row["statement"],
        "from": iso(start),
        "to": iso(end),
        "in_force": in_force,
        "origin": row["origin"],
        "decided_by": [p for p in row["deciders"] if p["id"]],
        "evidence": [
            {**e, "quote": clip(e["quote"], QUOTE_CHARS)}
            for e in row["evidence"]
            if e["artifact_id"]
        ],
        "said": [
            {**c, "quote": clip(c["quote"], QUOTE_CHARS)} for c in row["claims"] if c["artifact_id"]
        ],
    }
    if row["ts_inferred"]:
        out["time_inferred"] = True  # HERB has no merged_at / closed_at (D028)
    for key in ("refused", "alerts", "responses", "precedents"):
        items = [x for x in row[key] if x.get("id") or x.get("artifact_id")]
        if items:
            out[key] = items
    return out


def decision_lineage(
    subject: str, product_id: str, as_of_ms: int | None = None, graph: str | None = None
) -> dict:
    """Tool 6 (R2): how an issue's or PR's state changed over time and why. Each state comes
    with its validity window, who decided it, the verbatim evidence, the claims behind it
    with the authority verdict, refused claims, the alerts it raised, the responses it caused
    and similar earlier cases. `as_of_ms` marks the state in force at that moment."""
    subjects = match_subjects(subject, product_id, graph)
    cypher = ["lineage_subjects.cypher"]
    if not subjects:
        return {
            "subject": subject,
            "product_id": product_id,
            "subjects": [],
            "cypher": cypher,
            "note": "no issue or pull request of this product matches; try list_issues or "
            "list_prs to find its exact title or number",
        }
    topics = [t for s in subjects for t in topics_of(s)]
    rows = db.run_all("decision_chain", {"topics": topics}, cap=2000, graph=graph)
    cypher.append("decision_chain.cypher")
    out_subjects = []
    for s in subjects:
        item = {k: s[k] for k in ("id", "kind", "title", "number", "url", "status") if s[k]}
        for facet in ("status", "action"):
            chain = [_state(r, as_of_ms) for r in rows if r["topic"] == f"{s['id']}/{facet}"]
            if chain:
                item[f"{facet}_chain"] = chain
                now = [c["value"] for c in chain if c["in_force"]]
                item[f"{facet}_{'as_of' if as_of_ms is not None else 'now'}"] = (
                    now[-1] if now else None
                )
        out_subjects.append(item)
    out = {"subject": subject, "product_id": product_id, "subjects": out_subjects, "cypher": cypher}
    if as_of_ms is not None:
        out["as_of"] = iso(as_of_ms)
    return out


def impact_of(subject: str, product_id: str, graph: str | None = None) -> dict:
    """Tool 11 (R2): the blast radius of an issue's or PR's current state (11.2, D053): the
    fixing PRs with authors and reviewers, reporting customers and their companies,
    assignees, other issues on the same component, the evidence of the state it replaced,
    WORKS_ON teams and CAUSED decisions."""
    from app.sentinel import blast_radius

    decision_id = subject.strip() if subject.strip().startswith("dec:") else None
    cypher = ["impact_of.cypher"]
    found = None
    if decision_id is None:
        subjects = match_subjects(subject, product_id, graph)
        cypher.insert(0, "lineage_subjects.cypher")
        if not subjects:
            return {
                "subject": subject,
                "product_id": product_id,
                "cypher": cypher,
                "note": "no issue or pull request of this product matches",
            }
        found = subjects[0]
        rows = db.run_all(
            "decision_chain", {"topics": [f"{found['id']}/status"]}, cap=2000, graph=graph
        )
        cypher.insert(1, "decision_chain.cypher")
        active = [r for r in rows if r["status"] == "active"] or rows[-1:]
        if not active:
            return {
                "subject": subject,
                "product_id": product_id,
                "matched": {k: found[k] for k in ("id", "kind", "title")},
                "cypher": cypher,
                "note": "no recorded decision about it yet",
            }
        decision_id = active[-1]["id"]
    radius = blast_radius.impact_of(decision_id, graph=graph)
    radius.pop("rows")  # the Inbox draws from them; the agent reads the grouped lists
    out = {"subject": subject, "product_id": product_id, **radius, "cypher": cypher}
    if found:
        out["matched"] = {k: found[k] for k in ("id", "kind", "title", "status") if found[k]}
    return out


ALERT_KEYS = (
    "id",
    "kind",
    "severity",
    "status",
    "summary",
    "subject_title",
    "from_value",
    "to_value",
    "ts_iso",
    "artifact_id",
    "quote",
    "actor_name",
    "actor_role",
    "reason",
    "decision_id",
)


def open_alerts(
    product_id: str | None, status: str | None = "open", kind: str | None = None, graph=None
) -> dict:
    """Tool 12 (R2): the Change Sentinel's alerts (11.1, 11.5): regressions, reopenings,
    reversals, unauthorized changes, work outside the working group, key people; each with
    the change, its evidence quote and who it notifies."""
    params = {"status": status, "product_id": product_id, "alert_id": None, "artifact_id": None}
    rows, cut = db.run_capped("list_alerts", params, cap=200, graph=graph)
    alerts = []
    for r in rows:
        if kind and r["kind"] != kind:
            continue
        a = {k: r[k] for k in ALERT_KEYS if r.get(k) is not None}
        if a.get("quote"):
            a["quote"] = clip(a["quote"], QUOTE_CHARS)
        a["notifies"] = [n["name"] for n in r["notifies"] if n["id"]]
        alerts.append(a)
    counts: dict[str, int] = {}
    for a in alerts:
        counts[a["kind"]] = counts.get(a["kind"], 0) + 1
    return {
        "product_id": product_id,
        "filters": {"status": status, "kind": kind},
        "counts_by_kind": counts,
        "alerts": alerts,
        **cut_note(cut, len(rows)),
        "cypher": ["list_alerts.cypher"],
    }


PEOPLE_METRICS = (
    "prs_authored",
    "prs_merged",
    "prs_reviewed",
    "prs_approved",
    "changes_requested",
    "bugs_fixed",
    "open_bugs_assigned",
)
ISSUE_METRICS = {  # metric -> which of a company's issues count
    "issues_reported": lambda i: True,
    "issues_unresolved": lambda i: i["status"] in UNRESOLVED,
    "issues_resolved": lambda i: i["status"] == "resolved",
    "issues_dismissed": lambda i: i["status"] == "dismissed",
    "issues_reopened": lambda i: i["status"] == "reopened" or (i["reopen_count"] or 0) > 0,
}


def rank(
    metric: str, product_id: str, by: str | None = None, k: int | None = None, graph=None
) -> dict:
    """Tool 7 (R2): a ranking counted by the graph, not by the model. People by PR and bug
    work, or companies (by='customer': customers) by the issues they reported. Ties share a
    rank; each row names its evidence."""
    k = min(k or 10, 50)
    if metric in PEOPLE_METRICS:
        params = {"product_id": product_id, "metric": metric}
        rows, cut = db.run_capped("rank_people", params, cap=200, graph=graph)
        ranking = [{**r, "n": int(r["n"])} for r in rows]
        cypher = ["rank_people.cypher"]
    elif metric in ISSUE_METRICS:
        entity = "customer" if by == "customer" else "company"
        params = {"product_id": product_id, "entity": entity}
        rows, cut = db.run_capped("rank_companies", params, cap=500, graph=graph)
        ranking = []
        for r in rows:
            counted = [i for i in r["issues"] if ISSUE_METRICS[metric](i)]
            if counted:
                ranking.append(
                    {"id": r["id"], "name": r["name"], "n": len(counted), "issues": counted}
                )
        ranking.sort(key=lambda x: (-x["n"], x["name"] or ""))
        cypher = ["rank_companies.cypher"]
    else:
        raise ValueError(f"unknown metric {metric!r}")
    previous, place = None, 0
    for i, r in enumerate(ranking, 1):  # standard competition ranking: 1, 1, 3
        if r["n"] != previous:
            place, previous = i, r["n"]
        r["rank"] = place
    return {
        "metric": metric,
        "product_id": product_id,
        "by": by,
        "total_ranked": len(ranking),
        **cut_note(cut, len(rows)),
        "ranking": ranking[:k],
        "cypher": cypher,
    }


MISSING_KINDS = (
    "resolved_without_fix",
    "open_without_fix",
    "issue_without_assignee",
    "pr_without_review",
    "area_single_expert",
)


def find_missing(kind: str, product_id: str, graph: str | None = None) -> dict:
    """Tool 8 (R2): what has no path where one is expected (absence-of-path queries):
    resolved issues no PR fixes, open issues with no fix in progress, issues nobody was
    assigned, own PRs nobody reviewed, areas only one person holds (no backup)."""
    if kind not in MISSING_KINDS:
        raise ValueError(f"unknown kind {kind!r}")
    params = {"product_id": product_id, "kind": kind}
    rows, cut = db.run_capped("find_missing", params, cap=200, graph=graph)
    return {
        "kind": kind,
        "product_id": product_id,
        "count": len(rows),
        **cut_note(cut, len(rows)),
        "items": rows,
        "cypher": ["find_missing.cypher"],
    }


def resolve_person(
    text: str, product_id: str | None, graph: str | None = None
) -> tuple[dict | None, list[dict]]:
    """A person's id from an id or a name: (best match, namesakes). Namesakes are common in
    HERB (98 names for 530 people), so the one most active in the product wins (D037)."""
    t = text.strip()
    if t.startswith("eid_"):
        return {"id": t}, []
    words = " ".join(re.findall(r"\w+", t))
    if not words:
        return None, []
    rows = db.run(
        "resolve_names", {"q": words, "product_id": product_id, "k": 10}, graph=graph
    ).rows
    people = [r for r in rows if "Person" in r["labels"]]
    exact = [r for r in people if (r["name"] or "").casefold() == t.casefold()] or people
    if not exact:
        return None, []
    keep = ("id", "name", "role", "activity")
    return {k: exact[0][k] for k in keep}, [{k: r[k] for k in keep} for r in exact[1:5]]


def connection_path(a: str, b: str, product_id: str | None, graph: str | None = None) -> dict:
    """Tool 13 (R2, D065): the shortest chain of people who work together (COLLAB: shared
    reviews, meetings and chats) between two people, at most 6 hops, with each tie."""
    pa, alt_a = resolve_person(a, product_id, graph)
    pb, alt_b = resolve_person(b, product_id, graph)
    cypher = ["resolve_names.cypher", "connection_path.cypher"]
    if not pa or not pb:
        missing = [x for x, p in ((a, pa), (b, pb)) if not p]
        return {"a": a, "b": b, "cypher": cypher, "note": f"no person found for {missing}"}
    rows = db.run("connection_path", {"a": pa["id"], "b": pb["id"]}, graph=graph).rows
    people = rows[0]["people"] if rows else []
    ties = rows[0]["ties"] if rows else []
    out: dict[str, Any] = {
        "a": pa,
        "b": pb,
        "connected": bool(people),
        "hops": len(people) - 1 if people else None,
        "path": people,
        "ties": ties,
        "cypher": cypher,
    }
    if alt_a or alt_b:
        out["namesakes"] = {"a": alt_a, "b": alt_b}
    if not people:
        out["note"] = "no chain of collaborators within 6 hops"
    return out


def person_profile(person: str, product_id: str | None, graph: str | None = None) -> dict:
    """Tool 4 (R2): one person: role, team, manager, working group, PageRank / betweenness
    rank, what they did per product, the areas they know, closest collaborators, open
    issues assigned to them, and their latest artifacts in the product (to cite)."""
    found, namesakes = resolve_person(person, product_id, graph)
    cypher = ["resolve_names.cypher"]
    if not found:
        return {"person": person, "cypher": cypher, "note": "no person with that name or id"}
    pid = found["id"]
    head = db.run("person_profile", {"id": pid}, graph=graph).rows
    if not head:
        return {"person": person, "cypher": cypher, "note": f"no person {pid}"}
    activity = db.run_all("person_activity", {"id": pid}, cap=1000, graph=graph)
    links = db.run("person_links", {"id": pid, "product_id": product_id}, graph=graph).rows
    cypher += ["person_profile.cypher", "person_activity.cypher", "person_links.cypher"]
    profile = {k: v for k, v in head[0].items() if v not in (None, [])}
    profile["managers"] = [m for m in head[0]["managers"] if m["id"]]
    by_product: dict[str, dict[str, int]] = {}
    for r in activity:
        key = f"{r['relation'].lower()}_{r['kind']}"
        by_product.setdefault(r["product_id"], {})[key] = int(r["n"])
    sections: dict[str, list[dict]] = {}
    for r in links:
        item = {k: v for k, v in r.items() if k != "section" and v is not None}
        if r["section"] == "recent":
            item.pop("n", None)
            item["artifact_id"] = item.pop("id")
            item["text"] = clip(item.get("text"), 200)
        sections.setdefault(r["section"], []).append(item)
    truck = db.run("person_truck", {"id": pid}, graph=graph).rows
    cypher.append("person_truck.cypher")
    out = {
        **profile,
        "activity_by_product": by_product,
        "knows": sections.get("knows", []),
        "collaborators": sections.get("collaborator", []),
        "open_issues_assigned": sections.get("assigned_open", []),
        "recent_artifacts": sections.get("recent", []),
        "key_person_in": [t["product_id"] for t in truck],
        "cypher": cypher,
    }
    if namesakes:
        out["namesakes"] = namesakes
    return out


def agent_search(
    query: str,
    kinds: list[str] | None = None,
    product_id: str | None = None,
    k: int | None = None,
    graph: str | None = None,
    reach: dict[str, Any] | None = None,
) -> dict:
    """Tool 1 as the agent sees it: hybrid search with authors and participants."""
    out = search_artifacts(
        query,
        kinds=kinds,
        product_id=product_id,
        k=min(k or 10, 20),
        people=True,
        graph=graph,
        reach=reach,
    )
    return {key: out[key] for key in ("query", "keywords", "filters", "results", "cypher")}


# Strict schemas share one budget per request (structured-outputs docs, read 17 Oct): at
# most 20 strict tools, 24 optional parameters and 16 union-typed parameters (anyOf or
# nullable) across all of them. 17 tools with nullable parameters came to 28 unions (a 400),
# so optional parameters are left out of `required` instead of being nullable (D068).
OPTIONAL = "x-lore-optional"


def _optional(schema: dict[str, Any]) -> dict[str, Any]:
    """A parameter the model may leave out (the handler reads it with .get())."""
    return {**schema, OPTIONAL: True}


def _tool(name: str, description: str, properties: dict[str, Any]) -> dict[str, Any]:
    """A strict tool spec: the API guarantees inputs match the schema exactly."""
    props = {k: {x: y for x, y in v.items() if x != OPTIONAL} for k, v in properties.items()}
    return {
        "name": name,
        "description": description,
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": props,
            "required": [k for k, v in properties.items() if not v.get(OPTIONAL)],
            "additionalProperties": False,
        },
    }


KINDS = ["message", "meeting", "meeting_chat", "doc", "pr", "link"]
PRODUCT = {
    "type": "string",
    "description": "a product id; an empty string means the question's product",
}
AS_OF = _optional(
    {
        "type": "string",
        "description": "an ISO date (2027-01-15) for the state at the end of that day; leave it "
        "out for the question's as-of date, or now",
    }
)
_SPECS = [
    _tool(
        "search_artifacts",
        "Keyword + semantic search over the text of Slack messages, meeting transcripts, "
        "documents, pull requests and shared links of the product. Returns the best-matching "
        "artifacts with a snippet, their authors and participants. Use it for questions about "
        "content, and to find artifacts the other tools don't list.",
        {
            "query": {"type": "string", "description": "what to look for, in plain words"},
            "kinds": _optional({"type": "array", "items": {"type": "string", "enum": KINDS}}),
            "k": _optional({"type": "integer", "description": "results wanted, at most 20"}),
            "product_id": PRODUCT,
        },
    ),
    _tool(
        "resolve_entity",
        "Map a name to ids: a product name or codename (the text often uses codenames), a "
        "person's name (several people can share one; the most active in the product come "
        "first), a customer or company name, or a role such as 'Engineering Lead' (returns "
        "the people with that role who worked on the product).",
        {"text": {"type": "string"}, "product_id": PRODUCT},
    ),
    _tool(
        "get_artifact",
        "The full text of one artifact (message, meeting transcript, document, PR, link) and "
        "the people around it: authors, attendees, speakers, reviewers with their review "
        "state, people mentioned. Use it to read and quote an artifact.",
        {"artifact_id": {"type": "string"}},
    ),
    _tool(
        "doc_discussion",
        "A document's authors and how it was reviewed: 'feedback' holds every Slack message "
        "by other people in the channel where it was shared, from the share until a newer "
        "version was shared (with each person's id and role); 'author_messages' holds the "
        "author's own share, replies and summaries, which are not suggestions by the author; "
        "and the meetings that discussed it (speakers with roles, is_author marked). Use it "
        "for who wrote or reviewed a document and what changes people suggested. Changes "
        "suggested by a role are what people with that role said in feedback or meetings; "
        "if the only person with that role is the author, nobody with it suggested changes.",
        {
            "doc": {
                "type": "string",
                "description": "a document type such as 'Market Research Report', or its id",
            },
            "product_id": PRODUCT,
        },
    ),
    _tool(
        "issue_reports",
        "Every customer-reported issue in the product's support channels: the report text "
        "(issue title, affected component, impact), the customers with their companies, and "
        "the people mentioned (usually who it was assigned to).",
        {"product_id": PRODUCT},
    ),
    _tool(
        "list_prs",
        "The product's own pull requests with number, title, url, current status and the "
        "status history (open, approved, changes_requested, merged, closed), author and "
        "reviewers. PRs that 'need to be merged' are approved but not merged: filter with "
        "status 'approved' (not action 'to_merge'). A PR undone by a later 'Revert PR# N' "
        "pull request lists it in reverted_by, with the stated reason: the PRs that 'were "
        "reverted' are these originals, not the revert PRs. team_action is what the team "
        "said in Slack: to_close (the team agreed not to merge it: an approved PR that needs "
        "to be closed), on_hold, reverted, with action_reason (product_direction = a change "
        "in product direction, introduced_bugs = it introduced bugs), or to_merge (someone "
        "said they'd merge it; such PRs were usually merged since). 'said' lists those Slack "
        "messages with quotes to cite. as_of gives each PR's state on that date.",
        {
            "status": _optional(
                {
                    "type": "string",
                    "enum": ["open", "approved", "changes_requested", "merged", "closed"],
                }
            ),
            "action": _optional(
                {"type": "string", "enum": ["to_merge", "to_close", "on_hold", "reverted"]}
            ),
            "as_of": AS_OF,
            "product_id": PRODUCT,
        },
    ),
    _tool(
        "list_issues",
        "The product's customer issues (bugs), one canonical issue per bug, built from the "
        "support channels: title, component, current status, reopen_count, assignee, the "
        "reporting customers with their companies, the PRs that fixed it (with author), and "
        "the status history: each change with who said it, their role, the message id and "
        "a quote to cite. Statuses: reported (open, not yet fixed), resolved (fixed), "
        "dismissed (found to need no fix), reopened (open again). 'unresolved' = reported "
        "or reopened. counts_by_status counts what was returned. Use it for bug counts, "
        "unresolved / resolved / reopened / dismissed issues, who resolved or dismissed "
        "them, and the companies affected. as_of gives the status on that date.",
        {
            "status": _optional(
                {
                    "type": "string",
                    "enum": ["reported", "resolved", "dismissed", "reopened", "unresolved"],
                }
            ),
            "company": _optional(
                {"type": "string", "description": "only issues reported by this company"}
            ),
            "as_of": AS_OF,
            "product_id": PRODUCT,
        },
    ),
    _tool(
        "links_shared",
        "Links shared in the product's Slack messages (demos, competitor sites, documents), "
        "with their description, who shared each and in which message.",
        {"product_id": PRODUCT},
    ),
    _tool(
        "who_knows",
        "Who knows an area of the product best: a bug component (e.g. 'API Security Module') "
        "or a development work item (e.g. 'GraphQL schema versioning'). For each matching "
        "area: experts ranked by degree of authorship (author = holds the area; deliveries = "
        "bugs fixed or PRs written there) with the PRs they wrote as evidence to cite, the "
        "listed owners (who its issues are assigned to), owner_to_expert (when they differ: "
        "the shortest chain of people who work together, from the owner to the expert), "
        "single_expert, and key_person when "
        "the person is in the product's truck factor (the people whose loss leaves most "
        "areas with no expert). connectors (collaborators who bridge groups, then the best "
        "connected) appear when no expert is left. Use it for who to ask, who is the "
        "expert, and who resolved issues in a component.",
        {
            "topic": {"type": "string", "description": "the component or work, in plain words"},
            "product_id": PRODUCT,
        },
    ),
    _tool(
        "decision_lineage",
        "How an issue's or pull request's state changed over time, and why: every state in "
        "order with its validity window (from / to; in_force marks the one that holds now, or "
        "on the as_of date), who decided it, the verbatim evidence, what people said with "
        "the authority policy's verdict, claims the policy refused, alerts the change raised, "
        "the responses it caused and similar earlier cases. A PR has a status_chain (the "
        "record) and an action_chain (what the team said to do). Use it for what happened to "
        "X, when it changed, who reversed or reopened it, and the state on a past date.",
        {
            "subject": {
                "type": "string",
                "description": "an issue title, a PR number such as 'PR #12', or an id",
            },
            "as_of": AS_OF,
            "product_id": PRODUCT,
        },
    ),
    _tool(
        "rank",
        "A ranking counted by the graph, so you never count by hand: people by "
        "prs_authored, prs_merged, prs_reviewed, prs_approved, changes_requested, bugs_fixed "
        "(authored a PR that fixed an issue) or open_bugs_assigned; or companies (by "
        "'customer': customers) by issues_reported, issues_unresolved, issues_resolved, "
        "issues_dismissed or issues_reopened. Each row has n, its rank (ties share one) and "
        "the PRs or issues counted, to cite. Use it for who did the most or which company "
        "has the most.",
        {
            "metric": {
                "type": "string",
                "enum": [*PEOPLE_METRICS, *ISSUE_METRICS],
            },
            "by": _optional({"type": "string", "enum": ["company", "customer"]}),
            "k": _optional({"type": "integer", "description": "rows wanted, at most 50"}),
            "product_id": PRODUCT,
        },
    ),
    _tool(
        "find_missing",
        "What is missing where something is expected: resolved_without_fix (issues marked "
        "resolved that no PR fixes), open_without_fix (open issues with no fix yet), "
        "issue_without_assignee, pr_without_review (the product's PRs nobody reviewed), "
        "area_single_expert (components or work items only one person holds: no backup; "
        "detail is their id). Each item has an "
        "artifact to cite when there is one.",
        {
            "kind": {"type": "string", "enum": list(MISSING_KINDS)},
            "product_id": PRODUCT,
        },
    ),
    _tool(
        "impact_of",
        "What a change to an issue or pull request touches (its blast radius): the PRs that "
        "fix it with their authors and reviewers, the customers who reported it and their "
        "companies, assignees, other open issues on the same component, the evidence of the "
        "state it replaced, the teams working on the product and decisions it caused. Use "
        "it for what breaks or who is affected if X changes.",
        {
            "subject": {
                "type": "string",
                "description": "an issue title, a PR number such as 'PR #12', or a decision id",
            },
            "product_id": PRODUCT,
        },
    ),
    _tool(
        "open_alerts",
        "The change sentinel's alerts: regression (a fixed bug reported again), reopened, "
        "reversal, unauthorized (a change by someone without authority), outside_group (a "
        "decision made outside the working group), key_person (one person holds an area), "
        "bus_factor. Each with the change, the evidence quote and who it notifies.",
        {
            "status": _optional({"type": "string", "enum": ["open", "approved", "dismissed"]}),
            "kind": _optional({"type": "string"}),
            "product_id": PRODUCT,
        },
    ),
    _tool(
        "person_profile",
        "One person: role, team, manager, working group, how central they are (PageRank and "
        "betweenness rank over who works with whom), what they authored, reviewed and "
        "attended per product, the areas they know, closest collaborators, open issues "
        "assigned to them, products where they are a key person, and their latest artifacts "
        "in the product to cite. Accepts a name (namesakes: the most active in the product "
        "first) or an eid_ id.",
        {"person": {"type": "string"}, "product_id": PRODUCT},
    ),
    _tool(
        "connection_path",
        "The shortest chain of people who work together (shared reviews, meetings, chats) "
        "between two people, up to 6 hops, with each tie's strength. Use it for how two "
        "people are connected or who can introduce one to the other.",
        {"a": {"type": "string"}, "b": {"type": "string"}, "product_id": PRODUCT},
    ),
]


def _graph_query_spec() -> dict[str, Any]:
    from app.agent.graph_query import MAX_LIMIT, schema_card

    return _tool(
        "graph_query",
        "Last resort, only when no other tool fits: run one read-only Cypher query on the "
        f"FalkorDB graph. Writes are refused; LIMIT at most {MAX_LIMIT}; 2 s timeout; a "
        "failed query returns its error so you can fix it (3 tries per answer). The rows "
        "have no text to quote unless you return artifact ids and text; cite with "
        f"get_artifact. Schema:\n{schema_card()}",
        {"cypher": {"type": "string"}},
    )


# --- the registry (R2): one entry per tool, used by the loop and the MCP server ------------
class ToolInputError(ValueError):
    """Arguments a tool can't use as given; the model sees the message and can retry."""


def input_problem(spec: dict[str, Any], args: Any) -> str | None:
    """Why `args` don't fit the tool's input schema (the first problem), or None. A null
    value counts as left out, and product_id may be left out: the question's product (or
    "every product" for the tools that allow it) fills in, as `loop.call_tool` does."""
    from jsonschema import Draft202012Validator

    if not isinstance(args, dict):
        return "arguments must be an object"
    schema = dict(spec["input_schema"])
    schema["required"] = [k for k in schema.get("required", []) if k != "product_id"]
    given = {k: v for k, v in args.items() if v is not None}
    error = next(iter(Draft202012Validator(schema).iter_errors(given)), None)
    if error is None:
        return None
    where = ".".join(str(p) for p in error.absolute_path)
    return f"{where + ': ' if where else ''}{error.message}"


@dataclass(frozen=True)
class Scope:
    """What a question fixes for every tool call: its product, as-of moment and graph."""

    product_id: str | None = None
    as_of_ms: int | None = None
    graph: str | None = None
    reach: dict[str, Any] | None = None  # a viewer's: search reads only what they may (D131)


@dataclass(frozen=True)
class Tool:
    spec: dict[str, Any]
    run: Callable[[dict[str, Any], Scope], dict]
    needs_product: bool = True


def _as_of(args: dict[str, Any], scope: Scope) -> int | None:
    try:
        return parse_as_of(args.get("as_of")) if args.get("as_of") else scope.as_of_ms
    except ValueError as e:
        raise ToolInputError(str(e)) from e


def _graph_query(args: dict[str, Any], scope: Scope) -> dict:
    from app.agent.graph_query import Refused, graph_query

    try:
        return graph_query(args["cypher"], scope.graph)
    except Refused as e:
        raise ToolInputError(f"query refused: {e}") from e


_HANDLERS: dict[str, tuple[Callable[[dict[str, Any], Scope], dict], bool]] = {
    "search_artifacts": (
        lambda a, s: agent_search(
            a["query"], a.get("kinds"), s.product_id, a.get("k"), s.graph, s.reach
        ),
        False,
    ),
    "resolve_entity": (lambda a, s: resolve_entity(a["text"], s.product_id, s.graph), False),
    "get_artifact": (lambda a, s: get_artifact(a["artifact_id"], s.graph), False),
    "doc_discussion": (lambda a, s: doc_discussion(a["doc"], s.product_id, s.graph), True),
    "issue_reports": (lambda a, s: issue_reports(s.product_id, s.graph), True),
    "list_prs": (
        lambda a, s: list_prs(
            s.product_id, a.get("status"), s.graph, a.get("action"), _as_of(a, s)
        ),
        True,
    ),
    "list_issues": (
        lambda a, s: list_issues(
            s.product_id, a.get("status"), a.get("company"), s.graph, _as_of(a, s)
        ),
        True,
    ),
    "links_shared": (lambda a, s: links_shared(s.product_id, s.graph), True),
    "who_knows": (lambda a, s: who_knows(a["topic"], s.product_id, s.graph), True),
    "decision_lineage": (
        lambda a, s: decision_lineage(a["subject"], s.product_id, _as_of(a, s), s.graph),
        True,
    ),
    "rank": (
        lambda a, s: rank(a["metric"], s.product_id, a.get("by"), a.get("k"), s.graph),
        True,
    ),
    "find_missing": (lambda a, s: find_missing(a["kind"], s.product_id, s.graph), True),
    "impact_of": (lambda a, s: impact_of(a["subject"], s.product_id, s.graph), True),
    "open_alerts": (
        lambda a, s: open_alerts(s.product_id, a.get("status"), a.get("kind"), s.graph),
        False,
    ),
    "person_profile": (lambda a, s: person_profile(a["person"], s.product_id, s.graph), False),
    "connection_path": (
        lambda a, s: connection_path(a["a"], a["b"], s.product_id, s.graph),
        False,
    ),
    "graph_query": (_graph_query, False),
}

REGISTRY: dict[str, Tool] = {
    spec["name"]: Tool(spec, *_HANDLERS[spec["name"]]) for spec in [*_SPECS, _graph_query_spec()]
}
# The answer agent's tools, in a fixed order (part of every cached turn's key).
TOOL_SPECS = [t.spec for t in REGISTRY.values()]
# Lore − graph tools (9.8, R1-7): the same loop with search and reading only.
SEARCH_ONLY = ("search_artifacts", "get_artifact")
