"""S4 Deterministic linking (no LLM). Built in Phase 6.

Everything that can be resolved from ids, urls and dictionaries, before any extraction:
- urls in text -> (Artifact)-[:SHARES {url}]->(Artifact) for the PR, doc or link with that
  url; Slack writes links as <url|label>, so a url ends at "|" or ">";
- @eid_... in text -> MENTIONS Person (system messages such as channel joins are skipped);
- product names and codenames (Product.aliases, from renamed channels) -> MENTIONS Product,
  with the names it went by in `surfaces`. A mention never creates IMPLEMENTS / FIXES;
- CUST-nnnn ids, unambiguous customer names, company names -> MENTIONS Customer / Company;
- meeting speakers -> (Person)-[:SPOKE_IN {turns, method}]->(Meeting / MeetingChat). A name is
  looked up among that meeting's participants first (98 names for 530 people, so the
  directory alone is ambiguous), then the directory; a tie is broken only when exactly one
  candidate is active in the product (authored something in it).
Every surface form found is counted on its artifact (`mention_count`), and the ones that
couldn't be resolved are kept in `unresolved` ("speaker:Fiona Miller", "url:..."), so the
health report can compute the unresolved-mention rate from the graph itself.
Spec: docs/CONTEXT.md §9.3 (S4), §6.4, §3.1 #5. Decisions: D037.
"""

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app import db
from app.config import get_settings
from app.ingest.normalize import Artifact
from app.ingest.structural import Metadata, channel_rows, slug

URL = re.compile(r"https?://[^\s<>|\"]+")
URL_TAIL = ".,;:!?)]}'"
EID = re.compile(r"\beid_[0-9a-f]{8}\b")
CUSTOMER_ID = re.compile(r"\bCUST-\d+\b")
SPEAKER = re.compile(r"^([^:\n]{2,60}): ", re.MULTILINE)
# "Revert PR# 8: Implement GraphQL Server": the PR that undoes PR 8 of the same repository.
REVERT = re.compile(r"^\s*Revert\s+(?:PR\s*)?#\s*(\d+)\b", re.IGNORECASE)
PERSON_NAME = re.compile(r"^[A-Z][\w'.-]+(?: [A-Z][\w'.-]+){1,3}$")  # "Fiona Miller"

LOAD_ORDER = (
    "link_shares",
    "link_mentions_person",
    "link_mentions_product",
    "link_mentions_customer",
    "link_mentions_company",
    "link_spoke_in",
    "link_reverts",
    "set_link_notes",
)


@dataclass
class Notes:
    """What linking found in one artifact: mention count and the unresolved surfaces."""

    mentions: int = 0
    unresolved: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Dictionaries:
    by_url: dict[str, str]  # url (no trailing "/") -> artifact id
    names: dict[str, list[str]]  # employee name -> eids
    employee_names: dict[str, str]  # eid -> name
    products: dict[str, str]  # product name or codename -> product id
    customer_ids: set[str]
    customer_names: dict[str, str]  # unambiguous customer name -> customer id
    companies: dict[str, str]  # company name -> company id
    active: dict[str, set[str]]  # product -> eids that authored something in it


def dictionaries(
    meta: Metadata,
    products: dict[str, list[Artifact]],
    aliases: dict[str, list[str]] | None = None,
) -> Dictionaries:
    """`aliases`: codenames known already (the graph's Product.aliases), beside the ones the
    artifacts' renamed channels show (D113: the live linker has no channel history)."""
    by_url: dict[str, str] = {}
    active: dict[str, set[str]] = defaultdict(set)
    surfaces: dict[str, str] = {}
    for product, artifacts in products.items():
        surfaces[product] = product
        for codename in [*channel_rows(product, artifacts)[1], *(aliases or {}).get(product, [])]:
            surfaces[codename] = product
        for a in artifacts:
            if a.url and a.kind in ("pr", "doc", "link"):
                by_url.setdefault(a.url.rstrip("/"), a.id)
            if a.author_eid in meta.employees:
                active[product].add(a.author_eid)
    names: dict[str, list[str]] = defaultdict(list)
    for eid, e in sorted(meta.employees.items()):
        names[e["name"]].append(eid)
    customer_names = Counter(c["name"] for c in meta.customers)
    return Dictionaries(
        by_url=by_url,
        names=dict(names),
        employee_names={eid: e["name"] for eid, e in meta.employees.items()},
        products=surfaces,
        customer_ids={c["id"] for c in meta.customers},
        customer_names={  # a customer's name links only if no one else has it
            c["name"]: c["id"]
            for c in meta.customers
            if customer_names[c["name"]] == 1 and c["name"] not in names
        },
        companies={c["company"]: f"co:{slug(c['company'])}" for c in meta.customers},
        active=dict(active),
    )


# --- text helpers ----------------------------------------------------------------------------
def urls(text: str) -> list[str]:
    """Urls in text, with Slack's <url|label> markup and trailing punctuation cut off."""
    return [u.rstrip(URL_TAIL).rstrip("/") for u in URL.findall(text)]


def find_words(surfaces: dict[str, str], text: str) -> list[tuple[str, str]]:
    """(surface, id) for each dictionary entry found as a whole word, case-sensitive."""
    return [
        (s, target)
        for s, target in surfaces.items()
        if re.search(rf"(?<![A-Za-z0-9_]){re.escape(s)}(?![A-Za-z0-9_])", text)
    ]


def speaker_turns(text: str) -> Counter:
    """Speaker name -> turns, from "Name: words" lines that look like a person's name."""
    return Counter(
        m.group(1).strip() for m in SPEAKER.finditer(text) if PERSON_NAME.match(m.group(1).strip())
    )


def resolve_speaker(
    name: str, participants: list[str], d: Dictionaries, product: str
) -> tuple[str | None, str, list[str]]:
    """(eid or None, method, candidates). Participants first, then the directory; a tie is
    broken only if exactly one candidate authored something in this product."""
    in_meeting = sorted({e for e in participants if d.employee_names.get(e) == name})
    pool = in_meeting or d.names.get(name, [])
    base = "participants" if in_meeting else "directory"
    if len(pool) == 1:
        return pool[0], base, pool
    active = [e for e in pool if e in d.active.get(product, set())]
    if len(active) == 1:
        return active[0], f"{base}+activity", pool
    return None, "unresolved", pool


# --- rows ------------------------------------------------------------------------------------
def build_rows(
    meta: Metadata,
    products: dict[str, list[Artifact]],
    context: dict[str, list[Artifact]] | None = None,
    aliases: dict[str, list[str]] | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    """All link rows for the given products, plus a log of every unresolved mention.
    `context` artifacts only resolve urls, names and revert targets; they aren't linked again
    (the live path links one new artifact against what is already loaded, 17.2, looked up in
    the graph for that artifact, D113). `aliases`: product codenames already known."""
    pool = {p: [*arts, *(context or {}).get(p, [])] for p, arts in products.items()}
    d = dictionaries(meta, pool, aliases)
    rows: dict[str, list[dict[str, Any]]] = {name: [] for name in LOAD_ORDER}
    unresolved_log: list[dict[str, Any]] = []
    for product, artifacts in products.items():
        meetings = {a.id: a for a in artifacts if a.kind == "meeting"}
        for a in artifacts:
            notes = Notes()
            link_artifact(a, product, d, meetings, rows, notes)
            if notes.mentions:
                rows["set_link_notes"].append(
                    {
                        "artifact_id": a.id,
                        "mentions": notes.mentions,
                        "unresolved": notes.unresolved,
                    }
                )
            unresolved_log += [
                {"artifact_id": a.id, "product": product, "surface": u} for u in notes.unresolved
            ]
        unresolved_log += link_reverts(artifacts, product, rows, pool[product])
    for name in LOAD_ORDER:  # identical rows (a link shared by two products) load once
        rows[name] = list({json.dumps(r, sort_keys=True): r for r in rows[name]}.values())
    return rows, unresolved_log


def link_reverts(
    artifacts: list[Artifact],
    product: str,
    rows: dict[str, list[dict[str, Any]]],
    pool: list[Artifact] | None = None,
) -> list[dict[str, Any]]:
    """(PR)-[:REVERTS]->(PR): a "Revert PR# N" pull request undoes PR N of its own repository.

    The question "which PRs were reverted?" asks for the originals, and only the revert's
    title says which one it undoes (D047). Returns the reverts whose target isn't loaded.
    """
    by_number = {
        (a.meta.get("repo"), str(a.meta.get("number"))): a.id
        for a in (pool or artifacts)
        if a.kind == "pr" and a.meta
    }
    missing = []
    for a in artifacts:
        if a.kind != "pr" or not (m := REVERT.match(a.title or "")):
            continue
        target = by_number.get((a.meta.get("repo"), m.group(1)))
        if target and target != a.id:
            rows["link_reverts"].append({"revert_id": a.id, "original_id": target})
        elif not target:
            missing.append(
                {"artifact_id": a.id, "product": product, "surface": f"revert:#{m.group(1)}"}
            )
    return missing


def link_artifact(
    a: Artifact,
    product: str,
    d: Dictionaries,
    meetings: dict[str, Artifact],
    rows: dict[str, list[dict[str, Any]]],
    notes: Notes,
) -> None:
    text = "\n".join(t for t in (a.title, a.text) if t)

    for u in dict.fromkeys(urls(text)):
        notes.mentions += 1
        target = d.by_url.get(u)
        if target is None:
            notes.unresolved.append(f"url:{u}")
        elif target != a.id:
            rows["link_shares"].append({"artifact_id": a.id, "target_id": target, "url": u})
    words = URL.sub(" ", text)  # names inside urls (github.com/.../VizForce) aren't mentions

    if not a.system:  # channel joins and creations are membership events, not mentions
        for eid in dict.fromkeys(EID.findall(words)):
            notes.mentions += 1
            if eid in d.employee_names and eid != a.author_eid:
                rows["link_mentions_person"].append({"artifact_id": a.id, "entity_id": eid})
            elif eid not in d.employee_names:
                notes.unresolved.append(f"eid:{eid}")

    surfaces: dict[str, list[str]] = defaultdict(list)  # product -> names it went by here
    for surface, product_id in find_words(d.products, words):
        notes.mentions += 1
        surfaces[product_id].append(surface)
    for product_id, found in surfaces.items():
        rows["link_mentions_product"].append(
            {"artifact_id": a.id, "entity_id": product_id, "surfaces": sorted(found)}
        )
    for cid in dict.fromkeys(CUSTOMER_ID.findall(words)):
        notes.mentions += 1
        if cid in d.customer_ids:
            rows["link_mentions_customer"].append({"artifact_id": a.id, "entity_id": cid})
        else:
            notes.unresolved.append(f"customer:{cid}")
    for _, cid in find_words(d.customer_names, words):
        notes.mentions += 1
        rows["link_mentions_customer"].append({"artifact_id": a.id, "entity_id": cid})
    for _, company_id in find_words(d.companies, words):
        notes.mentions += 1
        rows["link_mentions_company"].append({"artifact_id": a.id, "entity_id": company_id})

    if a.kind in ("meeting", "meeting_chat"):
        meeting = a if a.kind == "meeting" else meetings.get(a.meta.get("meeting_id") or "")
        participants = meeting.participants if meeting else []
        for name, turns in speaker_turns(a.text).items():
            notes.mentions += 1
            eid, method, candidates = resolve_speaker(name, participants, d, product)
            if eid is None:
                notes.unresolved.append(f"speaker:{name}")
            else:
                rows["link_spoke_in"].append(
                    {"person_id": eid, "artifact_id": a.id, "turns": turns, "method": method}
                )


def write_unresolved(entries: list[dict[str, Any]], path: Path | None = None) -> Path:
    """data/logs/link_unresolved.jsonl: every mention linking couldn't resolve (6.3)."""
    path = path or get_settings().data_dir / "logs" / "link_unresolved.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return path


def load(rows: dict[str, list[dict[str, Any]]], graph: str | None = None) -> dict[str, dict]:
    """Run every link query over its rows; return the change counters per query."""
    report = {}
    for name in LOAD_ORDER:
        if rows.get(name):
            result = db.run(name, {"rows": rows[name]}, graph=graph, readonly=False)
            report[name] = {"rows": len(rows[name]), **result.stats}
    return report
