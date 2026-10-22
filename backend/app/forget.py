"""Forgetting (F2, D125): what a deletion in a source tool, `yaan forget` and `yaan erase person`
do.

Yaan copies an item's words into many places: the artifact, its search chunks and their
vectors, the events extracted from it (and through the fold its claims, decisions and issue
titles), alerts and drafted actions that quote it, dead letters that keep it for a retry, and
the LLM and embedding disk caches. `forget(ids)` removes every copy and leaves a content-free
`:Tombstone` (id, kind, when, why):
- the event log stays consistent: the item's events are closed (`retracted_ms`), not deleted,
  with their words blanked, so "what did we believe on 3 May" still replays, without them;
- nothing can cite it any more: no text, so no quote to check, and answers fail closed;
- a later sync, sweep or backfill sees the tombstone and doesn't bring the item back.

`erase_person(eid)` forgets everything a person wrote, deletes their Person node, tokens and
directory entry, replaces their @-mentions in what others wrote with a placeholder of the same
length, takes their name off alerts and drafts, and records a hash of their id so their items
stay out while a source still holds them. Plain-text mentions of their name in others' words
are listed for a human, never rewritten: a name can be a namesake's.

COVERS says what happens to each property that can hold someone's words; test_forget fails
when a write query stores such a property on a label it doesn't list, so a new copy of text
can't quietly escape erasure.
"""

import hashlib
import time
from collections.abc import Iterable
from typing import Any

from app import db, filestate, org
from app.config import get_settings

BATCH = 500  # ids per write statement: each must finish inside FalkorDB's 10 s write budget
READ_CAP = 200_000
# as long as "@eid_1a2b3c4d", so quote offsets still hold; not hex, so it names nobody
PLACEHOLDER = "@eid_erased00"
FORMER = "a former member"
WITHDRAWN = "Withdrawn: the item behind this alert was deleted"

COVERS: dict[str, dict[str, str]] = {
    "Artifact": {"*": "deleted; a Tombstone keeps its id, kind, when and why"},
    "Chunk": {"*": "deleted, with its vector's embedding-cache entry"},
    "DeadLetter": {"*": "deleted (an erased person's: any that names them)"},
    "Event": {
        p: "blanked, and the event closed; an erased person's @-mention replaced"
        for p in ("quote", "issue", "component", "reason")
    },
    "Claim": {
        p: "rebuilt by the fold from the remaining events"
        for p in ("quote", "reason", "authority_reason")
    },
    "Decision": {"statement": "rebuilt by the fold"},
    "Issue": {p: "rebuilt by the fold" for p in ("title", "name", "description", "aliases")}
    | {"component": "rebuilt by the fold"},
    "Area": {p: "rebuilt by the next derive (marked due)" for p in ("name", "aliases")}
    | {"deliveries": "rebuilt by the next derive"},
    "Alert": {
        p: "scrubbed, and an open alert withdrawn"
        for p in ("quote", "summary", "reason", "subject_title")
    }
    | {"actor_name": "replaced when its person is erased"},
    "Action": {
        p: "scrubbed, and one not yet sent withdrawn" for p in ("draft", "quote", "target_name")
    },
    "Person": {"name": "deleted with the node by erase_person"},
}
# names an admin configured or Yaan computed from them, never anyone's words
EXEMPT = {
    "Channel": "connectors.yaml",
    "Company": "the org's customers list",
    "Customer": "the org's customers list",
    "Org": "connectors.yaml",
    "Product": "connectors.yaml",
    "Team": "people.yaml",
    "Community": "a label made of team names",
    "Tombstone": "why it was forgotten (the source's or the admin's note), never its words",
}


def batches(rows: list[Any], size: int = BATCH) -> Iterable[list[Any]]:
    for lo in range(0, len(rows), size):
        yield rows[lo : lo + size]


def forget(
    ids: Iterable[str],
    graph: str | None = None,
    reason: str = "deleted",
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Remove every copy of these items' words (the module docstring says where they are).
    Idempotent: an id already forgotten, or never ingested, gets a tombstone and nothing else.
    Returns counts, never content."""
    from app.sentinel import live

    graph = live.demo_graph(graph)
    now_ms = now_ms or int(time.time() * 1000)
    ids = sorted(set(ids))
    if not ids:
        return {"graph": graph, "asked": 0}
    targets = _read_all("forget_targets", ids, graph)
    texts = [r["text"] for r in _read_all("forget_chunk_texts", ids, graph) if r["text"]]
    kinds = {r["id"]: r["kind"] for r in targets}
    products = {p for r in targets for p in r["products"] if p}
    events = actions = 0
    alerts: list[str] = []
    with db.writer_lock(graph):
        for chunk in batches(ids):
            row = _write("forget_events", {"ids": chunk, "now_ms": now_ms}, graph).rows[0]
            events += row["events"]
            products |= {p for p in row["products"] if p}
        for chunk in batches(ids):
            params = {"ids": chunk, "now_ms": now_ms, "summary": WITHDRAWN}
            alerts += [r["id"] for r in _write("forget_alerts", params, graph).rows]
        for chunk in batches(ids):
            params = {"ids": chunk, "alert_ids": alerts, "now_ms": now_ms}
            actions += len(_write("forget_actions", params, graph).rows)
        for chunk in batches(ids):
            for name in ("forget_dead_letters", "forget_chunks", "forget_artifacts"):
                _write(name, {"ids": chunk}, graph)
        rows = [
            {"id": i, "kind": kinds.get(i, "unknown"), "now_ms": now_ms, "reason": reason}
            for i in ids
        ]
        for chunk in batches(rows):
            _write("upsert_tombstones", {"rows": chunk}, graph)
        refolded = refold(products, graph, now_ms)
    return {
        "graph": graph,
        "asked": len(ids),
        "artifacts": len(targets),
        "events_closed": events,
        "alerts_scrubbed": len(alerts),
        "actions_scrubbed": actions,
        "products_refolded": sorted(products),
        "decisions_changed": refolded,
        "vectors_dropped": forget_vectors(texts),
        "llm_cache": clear_llm_cache(),
    }


def erase_person(person: str, graph: str | None = None, now_ms: int | None = None) -> dict:
    """GDPR erasure of one person (an eid_ id) from Yaan: see the module docstring."""
    from app import auth
    from app.sentinel import live

    graph = live.demo_graph(graph)
    now_ms = now_ms or int(time.time() * 1000)
    listed = org.roster()
    if person in listed:
        raise org.OrgError(
            f"{listed[person]['name']} is listed in {org.PEOPLE_FILE.name}: remove the entry "
            "first, or Yaan registers them again from it"
        )
    name = _name(person, graph)
    authored = [r["id"] for r in _read_all("person_authored", [person], graph, key="id")]
    mentioned = [
        r["id"]
        for r in _read_all("person_mentions", [person], graph, key="id")
        if r["id"] not in set(authored)
    ]
    receipt = forget(authored, graph, reason="erasure", now_ms=now_ms)
    mention = f"@{person}"
    swap = {"mention": mention, "placeholder": PLACEHOLDER, "id": person}
    products: set[str] = set()
    with db.writer_lock(graph):
        for chunk in batches(mentioned):
            _write("erase_mentions", {"ids": chunk, **swap}, graph)
            row = _write("erase_event_mentions", {"ids": chunk, **swap}, graph).rows[0]
            products |= {p for p in row["products"] if p}
        row = _write("erase_event_people", {"id": person}, graph).rows[0]
        products |= {p for p in row["products"] if p}
        named = {"id": person, "name": name or person, "former": FORMER, **swap}
        alerts = len(_write("erase_person_alerts", named, graph).rows)
        actions = len(_write("erase_person_actions", named, graph).rows)
        _write("erase_dead_letters", {"id": person}, graph)
        _write("delete_person", {"id": person}, graph)
        _write("upsert_suppressed", {"id": suppression_hash(person), "now_ms": now_ms}, graph)
        changed = refold(products, graph, now_ms)
    with filestate.update(org.directory_file(graph), sort_keys=True) as met:
        met.pop(person, None)
    org._hints.pop(person, None)
    by_name = []
    if name and not name.startswith(tuple(f"{s} user" for s in org.SOURCE_NAMES.values())):
        rows = db.run_all("name_mentions", {"name": name}, cap=1000, graph=graph, timeout_ms=30_000)
        by_name = sorted(r["id"] for r in rows)
    return receipt | {
        "person": person,
        "mentions_replaced": len(mentioned),
        "alerts_renamed": alerts,
        "actions_renamed": actions,
        "decisions_changed": receipt["decisions_changed"] + changed,
        "tokens_revoked": auth.revoke(person),
        "named_in": by_name,  # plain-text mentions of the name: for a human to review
        "llm_cache": clear_llm_cache(),
    }


def refold(products: Iterable[str], graph: str, now_ms: int) -> int:
    """Fold each product again from its remaining events (the caller holds the writer lock),
    judge the chains that changed, and mark the derived layer due. Returns how many topics
    changed."""
    from app.ingest import events as event_store
    from app.ingest import fold
    from app.sentinel import engine

    have = set(event_store.products(graph))
    topics: set[str] = set()
    for product in sorted(set(products) & have):
        topics |= set(fold.fold_product(product, graph)["write"]["topics"])
    if topics:
        engine.run(graph, topics=sorted(topics))
    due = {"id": "derive", "dirty_ms": now_ms, "done_ms": None}
    db.run("mark_job", due, graph=graph, readonly=False)
    return len(topics)


def suppression_hash(person: str) -> str:
    return hashlib.sha256(f"yaan-erased:{person}".encode()).hexdigest()


def suppressed(people: Iterable[str | None], graph: str) -> set[str]:
    """Which of these people were erased: their items stay out."""
    hashes = {suppression_hash(p): p for p in set(people) if p}
    if not hashes:
        return set()
    rows = db.run("suppressed_ids", {"ids": sorted(hashes), "limit": len(hashes)}, graph=graph)
    return {hashes[r["id"]] for r in rows.rows}


def forgotten(ids: Iterable[str], graph: str) -> set[str]:
    """Which of these item ids were forgotten: a connector must not bring them back."""
    ids = sorted(set(ids))
    out: set[str] = set()
    for chunk in batches(ids):
        rows = db.run("forgotten_ids", {"ids": chunk, "limit": len(chunk)}, graph=graph).rows
        out |= {r["id"] for r in rows}
    return out


def blocked(artifacts: Iterable[Any], graph: str) -> dict[str, str]:
    """{artifact id: why} for items that must not go in: forgotten, or by an erased person.
    The reason names no content."""
    artifacts = list(artifacts)
    gone = forgotten([a.id for a in artifacts], graph)
    erased = suppressed([a.author_eid for a in artifacts], graph)
    why = "forgotten: deleted from Yaan, never ingested again"
    out = {a.id: why for a in artifacts if a.id in gone}
    out |= {
        a.id: "its author was erased from Yaan"
        for a in artifacts
        if a.author_eid in erased and a.id not in out
    }
    return out


def forget_vectors(texts: list[str]) -> int:
    """Drop the embedding-cache entries of these chunk texts (keyed by the text itself)."""
    from app.ingest import chunk_embed

    cache = chunk_embed._cache()
    return sum(bool(cache.delete(chunk_embed.embed_key("passage", t))) for t in texts)


def clear_llm_cache() -> str:
    """Org mode: empty the LLM disk cache, whose keys hash whole prompts, so the entries that
    held a forgotten item can't be told apart; there it only saves money. The demo keeps it:
    it is the eval's free replay, and the demo's data is synthetic."""
    if get_settings().lore_data_mode != "org":
        return "kept (demo: synthetic data, and the eval replays from it)"
    from app import llm

    return f"cleared ({llm._cache().clear()} entries)"


def _name(person: str, graph: str) -> str | None:
    rows = db.run("person_name", {"id": person}, graph=graph).rows
    return rows[0]["name"] if rows else (org.directory(graph).get(person) or {}).get("name")


def _read_all(name: str, ids: list[str], graph: str, key: str = "ids") -> list[dict]:
    if key == "id":
        return db.run_all(name, {"id": ids[0]}, cap=READ_CAP, graph=graph, timeout_ms=30_000)
    out: list[dict] = []
    for chunk in batches(ids):
        out += db.run_all(name, {"ids": chunk}, cap=READ_CAP, graph=graph, timeout_ms=30_000)
    return out


def _write(name: str, params: dict[str, Any], graph: str):
    return db.run(name, params, graph=graph, readonly=False)
