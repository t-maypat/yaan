"""Agent tools 1-14. search_artifacts in Phase 4, the rest in Phases 9-11.

Read-only; accept names or ids; return compact JSON with evidence_ids and the .cypher
file used; explain the why (path/edges), not just values.
Spec: docs/CONTEXT.md §9.6 (tool table and design rules).
"""

import re
import threading
import time
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
) -> dict[str, Any]:
    """Tool 1: hybrid search over chunks, fused with RRF, one result per artifact.

    `mode` is for evaluation and ablations: full-text only, vector only, or both fused.
    `full_text` adds each best chunk's whole text (the baseline RAG reads it; agents quote
    from get_artifact instead). `people` adds each artifact's author and participant ids,
    the metadata HERB's own RAG documents carry (D036).
    """
    fetch = min(MAX_FETCH, k * DEPTH_FACTOR)
    filters = {"kinds": kinds, "product_id": product_id, "since_ms": since_ms, "until_ms": until_ms}
    lists: dict[str, list[dict[str, Any]]] = {}
    timings: dict[str, float] = {}
    used: list[str] = []
    q = keywords(query)
    if mode in ("hybrid", "fulltext") and q:
        started = time.perf_counter()
        lists["fulltext"] = db.run(
            "search_fulltext", {"q": q, "k": fetch, **filters}, graph=graph
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
        params = {"qvec": vector, "fetch": min(MAX_FETCH, k * INDEX_FACTOR), "k": fetch, **filters}
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
        params = {"doc_id": base, "window_ms": DOC_WINDOW_MS, "limit": 60}
        messages = db.run("doc_discussion", params, graph=graph).rows
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
    rows = db.run("issue_reports", {"product_id": product_id, "limit": 150}, graph=graph).rows
    return {
        "product_id": product_id,
        "reports": [{**r, "text": clip(r["text"], LIST_TEXT_CHARS)} for r in rows],
        "cypher": ["issue_reports.cypher"],
    }


def list_prs(
    product_id: str,
    status: str | None = None,
    graph: str | None = None,
    action: str | None = None,
) -> dict:
    """Tool 5, PR case: the product's own pull requests with their status chain from the PR
    record and its reviews (D028), plus what the team said to do with each in Slack: the
    `pr:<id>/action` chain from extracted claims (D049). External PRs are left out."""
    params = {"product_id": product_id, "status": status, "action": action, "limit": 200}
    rows = db.run("product_prs", params, graph=graph).rows
    for r in rows:
        r["reviews"] = [rv for rv in r["reviews"] if rv["id"]]
        r["reverted_by"] = [
            {**u, "reason": clip(u["reason"], 300)} for u in r["reverted_by"] if u["number"]
        ]
        r["said"] = [c for c in r["said"] if c["action"]]
    return {"product_id": product_id, "prs": rows, "cypher": ["product_prs.cypher"]}


UNRESOLVED = ("reported", "reopened")


def list_issues(
    product_id: str,
    status: str | None = None,
    company: str | None = None,
    graph: str | None = None,
) -> dict:
    """Tool 5, issue case (slice B): canonical customer issues (S7, D049) with their current
    status from the fold, the status history as claims with quotes, the assignee, reporting
    customers and companies, and the PRs that fixed them. `status='unresolved'` means
    reported or reopened; `company` keeps issues reported by that company's customers."""
    query_status = None if status == "unresolved" else status
    params = {"product_id": product_id, "status": query_status, "limit": 200}
    issues = []
    for r in db.run("product_issues", params, graph=graph).rows:
        r["customers"] = [c for c in r["customers"] if c["id"]]
        r["assignees"] = [a for a in r["assignees"] if a["id"]]
        r["fixed_by"] = [p for p in r["fixed_by"] if p["id"]]
        r["history"] = [h for h in r["history"] if h["status"]]
        if status == "unresolved" and r["status"] not in UNRESOLVED:
            continue
        companies = " ".join((c["company"] or "") for c in r["customers"]).casefold()
        if company and company.casefold().replace(" ", "") not in companies.replace(" ", ""):
            continue
        issues.append(r)
    counts: dict[str, int] = {}
    for r in issues:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {
        "product_id": product_id,
        "filters": {"status": status, "company": company},
        "counts_by_status": counts,
        "issues": issues,
        "cypher": ["product_issues.cypher"],
    }


def links_shared(product_id: str, graph: str | None = None) -> dict:
    """Tool 9: links shared in the product's messages, who shared them and where."""
    rows = db.run("links_shared", {"product_id": product_id, "limit": 150}, graph=graph).rows
    return {"product_id": product_id, "links": rows, "cypher": ["links_shared.cypher"]}


def agent_search(
    query: str,
    kinds: list[str] | None = None,
    product_id: str | None = None,
    k: int | None = None,
    graph: str | None = None,
) -> dict:
    """Tool 1 as the agent sees it: hybrid search with authors and participants."""
    out = search_artifacts(
        query, kinds=kinds, product_id=product_id, k=min(k or 10, 20), people=True, graph=graph
    )
    return {key: out[key] for key in ("query", "keywords", "filters", "results", "cypher")}


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


def _tool(name: str, description: str, properties: dict[str, Any]) -> dict[str, Any]:
    """A strict tool spec: the API guarantees inputs match the schema exactly."""
    return {
        "name": name,
        "description": description,
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }


KINDS = ["message", "meeting", "meeting_chat", "doc", "pr", "link"]
PRODUCT = _nullable(
    {"type": "string", "description": "a product id; null uses the question's product"}
)
TOOL_SPECS = [
    _tool(
        "search_artifacts",
        "Keyword + semantic search over the text of Slack messages, meeting transcripts, "
        "documents, pull requests and shared links of the product. Returns the best-matching "
        "artifacts with a snippet, their authors and participants. Use it for questions about "
        "content, and to find artifacts the other tools don't list.",
        {
            "query": {"type": "string", "description": "what to look for, in plain words"},
            "kinds": _nullable({"type": "array", "items": {"type": "string", "enum": KINDS}}),
            "k": _nullable({"type": "integer", "description": "results wanted, at most 20"}),
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
        "reviewers. 'Approved but not merged' PRs show status 'approved'. A PR undone by a "
        "later 'Revert PR# N' pull request lists it in reverted_by, with the stated reason: "
        "the PRs that 'were reverted' are these originals, not the revert PRs. team_action is "
        "what the team decided in Slack: to_merge, to_close (the team agreed not to merge "
        "it: an approved PR that needs to be closed), on_hold, or reverted, with "
        "action_reason (product_direction = a change in product direction, introduced_bugs "
        "= it introduced bugs). 'said' lists those Slack messages with quotes to cite.",
        {
            "status": _nullable(
                {
                    "type": "string",
                    "enum": ["open", "approved", "changes_requested", "merged", "closed"],
                }
            ),
            "action": _nullable(
                {"type": "string", "enum": ["to_merge", "to_close", "on_hold", "reverted"]}
            ),
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
        "them, and the companies affected.",
        {
            "status": _nullable(
                {
                    "type": "string",
                    "enum": ["reported", "resolved", "dismissed", "reopened", "unresolved"],
                }
            ),
            "company": _nullable(
                {"type": "string", "description": "only issues reported by this company"}
            ),
            "product_id": PRODUCT,
        },
    ),
    _tool(
        "links_shared",
        "Links shared in the product's Slack messages (demos, competitor sites, documents), "
        "with their description, who shared each and in which message.",
        {"product_id": PRODUCT},
    ),
]
