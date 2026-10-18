"""S2 Structural load (no LLM). Built in Phase 3.

People, teams, orgs, customers, companies, channels and artifacts; AUTHORED, ATTENDED,
IN_CHANNEL, REPLY_TO, IN_PRODUCT and REVIEWED edges; structural PR status decisions
(origin='structural'). Batched UNWIND/MERGE, idempotent.
Spec: docs/CONTEXT.md §9.2, §9.3 (S2).

Rows are built in plain Python from metadata/*.json and the normalized artifacts
(Phase 2), then each list goes to one query file: nodes first, then edges, so every edge
query finds both ends. Every query MERGEs on a deterministic id, so loading twice gives
the same graph. HERB has no threads, so there are no REPLY_TO edges (herb_format.md).
Decisions: D026 (teams), D027 (no unique constraints), D028 (PR chains), D029 (products).
"""

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app import db
from app.ingest.herb_fetch import herb_dir
from app.ingest.normalize import Artifact

STAGES = {"planning": "planning", "develop": "dev", "bug": "support"}  # channel name prefix
REVIEW_STATUS = {"APPROVED": "approved", "CHANGES_REQUESTED": "changes_requested"}
CHANNEL_START = "This is the very beginning of the "  # slack_admin_bot's first message

# Node queries, then edge queries: the order rows are loaded in.
LOAD_ORDER = (
    "upsert_orgs",
    "upsert_teams",
    "upsert_people",
    "upsert_customers",
    "upsert_companies",
    "upsert_products",
    "upsert_channels",
    "upsert_messages",
    "upsert_meetings",
    "upsert_meeting_chats",
    "upsert_docs",
    "upsert_prs",
    "upsert_links",
    "upsert_decisions",
    "link_part_of",
    "link_member_of",
    "link_reports_to",
    "link_works_at",
    "link_authored",
    "link_attended",
    "link_reviewed",
    "link_in_channel",
    "link_in_product",
    "link_affects",
    "link_decided_by",
    "link_evidenced_by",
    "link_supersedes",
)
ARTIFACT_QUERY = {
    "message": "upsert_messages",
    "meeting": "upsert_meetings",
    "meeting_chat": "upsert_meeting_chats",
    "doc": "upsert_docs",
    "pr": "upsert_prs",
    "link": "upsert_links",
}


@dataclass(frozen=True)
class Metadata:
    employees: dict[str, dict[str, Any]]
    hierarchy: list[dict[str, Any]]
    customers: list[dict[str, Any]]


def load_metadata(folder: Path | None = None) -> Metadata:
    folder = folder or herb_dir() / "metadata"

    def read(name: str) -> Any:
        return json.loads((folder / name).read_text(encoding="utf-8"))

    return Metadata(
        read("employee.json"), read("salesforce_team.json"), read("customers_data.json")
    )


def slug(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


# --- org structure (D026) ------------------------------------------------------------------
def org_rows(meta: Metadata) -> dict[str, list[dict[str, Any]]]:
    """Orgs, teams (one per VP group), people, MEMBER_OF, PART_OF and REPORTS_TO rows."""
    rows: dict[str, list[dict[str, Any]]] = {
        k: [] for k in ("upsert_orgs", "upsert_teams", "link_part_of", "link_member_of")
    }
    rows["link_reports_to"] = []
    for vp in meta.hierarchy:
        team_id = f"team:{vp['employee_id']}"
        rows["upsert_teams"].append(
            {
                "id": team_id,
                "name": f"{vp['name']}'s team ({vp['org']})",
                "org": vp["org"],
                "lead_id": vp["employee_id"],
            }
        )
        rows["link_part_of"].append({"team_id": team_id, "org_id": f"org:{vp['org']}"})
        for person, manager in walk(vp, None):
            rows["link_member_of"].append({"person_id": person, "team_id": team_id})
            if manager:
                rows["link_reports_to"].append({"person_id": person, "manager_id": manager})
    orgs = sorted({e["org"] for e in meta.employees.values()})
    rows["upsert_orgs"] = [{"id": f"org:{o}", "name": o} for o in orgs]
    rows["upsert_people"] = [
        {
            "id": eid,
            "name": e["name"],
            "role": e["role"],
            "location": e["location"],
            "org": e["org"],
        }
        for eid, e in sorted(meta.employees.items())
    ]
    return rows


def walk(node: dict[str, Any], manager: str | None) -> list[tuple[str, str | None]]:
    """(person, manager) pairs down the hierarchy: lists under a person report to them."""
    pairs = [(node["employee_id"], manager)]
    for value in node.values():
        if isinstance(value, list):
            for child in value:
                if isinstance(child, dict):
                    pairs += walk(child, node["employee_id"])
    return pairs


def customer_rows(meta: Metadata) -> dict[str, list[dict[str, Any]]]:
    companies = sorted({c["company"] for c in meta.customers})
    return {
        "upsert_customers": [
            {"id": c["id"], "name": c["name"], "role": c["role"]} for c in meta.customers
        ],
        "upsert_companies": [{"id": f"co:{slug(c)}", "name": c} for c in companies],
        "link_works_at": [
            {"customer_id": c["id"], "company_id": f"co:{slug(c['company'])}"}
            for c in meta.customers
        ],
    }


# --- products and channels (D029) ----------------------------------------------------------
def channel_rows(product: str, artifacts: list[Artifact]) -> tuple[list[dict], list[str]]:
    """Channels of one product, and the product's codenames found in renamed channels."""
    channels: dict[str, dict[str, Any]] = {}
    for a in artifacts:
        if a.kind != "message" or a.channel_id in channels:
            continue
        name = a.meta["channel_name"]
        original = name
        if a.system and CHANNEL_START in a.text:  # the channel's first message names it
            original = a.text.split(CHANNEL_START, 1)[1].removesuffix(" channel.")
        channels[a.channel_id] = {
            "id": a.channel_id,
            "name": name,
            "original_name": original,
            "stage": STAGES.get(name.split("-", 1)[0]),
            "product_id": product,
        }
    codenames = set()
    for c in channels.values():  # planning-abAIX-PM → planning-CollaborateForce-PM: abAIX
        old, new = c["original_name"].split("-"), c["name"].split("-")
        if len(old) == len(new):
            codenames |= {o for o, n in zip(old, new, strict=True) if n == product and o != n}
    return list(channels.values()), sorted(codenames)


# --- PR status chains (D028) ---------------------------------------------------------------
def pr_status_chain(pr: Artifact) -> list[dict[str, Any]]:
    """The `pr:<id>/status` decision chain from a PR's fields and review times.

    open at created_at → approved / changes_requested at each review that changes the status
    → merged or closed (time inferred: no merged_at / closed_at in HERB). Reviews dated before
    the PR are clamped to its creation; every step is at least 1 ms after the previous one.
    """
    herb_id = pr.id.removeprefix("pr:")
    topic = f"pr:{herb_id}/status"
    label = f"PR #{pr.meta['number']} in {pr.meta['repo']}"
    created = pr.ts_ms or 0
    events = [(created, "open", pr.author_eid, "summary", pr.text, False)]
    reviews = sorted(enumerate(pr.meta["reviews"]), key=lambda ir: (ir[1]["ts_ms"], ir[0]))
    last_activity = created
    for i, r in reviews:
        ts = max(r["ts_ms"], created)
        last_activity = max(last_activity, ts)
        status = REVIEW_STATUS.get(r["state"])
        if status:
            field, quote = f"reviews[{i}].comment", r["comment"] or r["state"]
            events.append((ts, status, r["reviewer_eid"], field, quote, ts != r["ts_ms"]))
    if pr.meta["merged"]:
        events.append((last_activity, "merged", None, "merged", "True", True))
    elif pr.meta["state"] == "closed":
        events.append((last_activity, "closed", None, "state", "closed", True))

    chain: list[dict[str, Any]] = []
    for ts, value, who, field, quote, inferred in events:
        if chain and chain[-1]["value"] == value:
            continue  # the same status again changes nothing
        if chain and ts <= chain[-1]["valid_from_ms"]:
            ts, inferred = chain[-1]["valid_from_ms"] + 1, True
        chain.append(
            {
                "id": f"dec:{topic}:{ts}",
                "topic": topic,
                "kind": "pr_status",
                "value": value,
                "statement": f"{label} is {value.replace('_', ' ')}",
                "valid_from_ms": ts,
                "valid_to_ms": None,
                "status": "active",
                "confidence": 1.0,
                "origin": "structural",
                "ts_inferred": inferred,
                "_who": who,
                "_field": field,
                "_quote": quote,
            }
        )
    for older, newer in zip(chain, chain[1:], strict=False):
        older["valid_to_ms"] = newer["valid_from_ms"]
        older["status"] = "superseded"
    return chain


# --- artifacts -----------------------------------------------------------------------------
def artifact_row(a: Artifact, observed_ms: int) -> dict[str, Any]:
    row = {
        "id": a.id,
        "kind": a.kind,
        "title": a.title,
        "text": a.text,
        "url": a.url,
        "ts_ms": a.ts_ms,
        "ts_iso": a.ts_iso,
        "product_id": None if a.kind == "link" else a.product_hint,  # links are shared (D023)
        "external": a.external,
        "raw_ref": a.raw_ref,
        "observed_ms": observed_ms,
    }
    if a.kind == "message":
        row |= {"channel_id": a.channel_id, "system": a.system}
    elif a.kind == "meeting":
        row["document_type"] = a.meta["document_type"]
    elif a.kind == "meeting_chat":
        row["meeting_id"] = a.meta["meeting_id"]
    elif a.kind == "doc":
        keys = ("doc_type", "version", "base_id", "feedback")
        row |= {k: a.meta[k] for k in keys}
    elif a.kind == "pr":
        keys = ("repo", "number", "state", "merged")
        row |= {k: a.meta[k] for k in keys}
    return row


def product_rows(
    product: str, artifacts: list[Artifact], people: set[str], observed_ms: int
) -> dict[str, list[dict[str, Any]]]:
    """Every row one product contributes: its node, channels, artifacts, edges, decisions."""
    rows: dict[str, list[dict[str, Any]]] = {name: [] for name in LOAD_ORDER}
    channels, codenames = channel_rows(product, artifacts)
    rows["upsert_products"].append({"id": product, "name": product, "aliases": codenames})
    rows["upsert_channels"] = channels

    def person(eid: str | None) -> bool:
        return eid is not None and eid in people

    for a in artifacts:
        row = artifact_row(a, observed_ms)
        rows["link_in_product"].append({"artifact_id": a.id, "product_id": product})
        if person(a.author_eid):
            rows["link_authored"].append({"person_id": a.author_eid, "artifact_id": a.id})
        if a.kind == "message":
            rows["link_in_channel"].append({"artifact_id": a.id, "channel_id": a.channel_id})
        if a.kind == "meeting":
            for eid in dict.fromkeys(a.participants):  # a repeated eid attends once
                if person(eid):
                    rows["link_attended"].append({"person_id": eid, "artifact_id": a.id})
        if a.kind == "pr":
            for r in a.meta["reviews"]:
                if person(r["reviewer_eid"]):
                    rows["link_reviewed"].append(
                        {
                            "person_id": r["reviewer_eid"],
                            "artifact_id": a.id,
                            "ts_ms": r["ts_ms"],
                            "ts_iso": r["ts_iso"],
                            "state": r["state"],
                            "comment": r["comment"],
                        }
                    )
            chain = pr_status_chain(a)
            row["current_status"] = chain[-1]["value"]
            add_decisions(rows, chain, a.id, person, observed_ms)
        rows[ARTIFACT_QUERY[a.kind]].append(row)
    return rows


def add_decisions(rows, chain, artifact_id, person, observed_ms) -> None:
    for d in chain:
        rows["upsert_decisions"].append(
            {k: v for k, v in d.items() if not k.startswith("_")} | {"observed_ms": observed_ms}
        )
        rows["link_affects"].append({"decision_id": d["id"], "artifact_id": artifact_id})
        if person(d["_who"]):
            rows["link_decided_by"].append({"decision_id": d["id"], "person_id": d["_who"]})
        quote = d["_quote"]
        rows["link_evidenced_by"].append(
            {
                "decision_id": d["id"],
                "artifact_id": artifact_id,
                "field": d["_field"],
                "quote": quote,
                "quote_hash": hashlib.sha1(quote.encode("utf-8")).hexdigest()[:12],
            }
        )
    for older, newer in zip(chain, chain[1:], strict=False):
        rows["link_supersedes"].append({"new_id": newer["id"], "old_id": older["id"]})


def build_rows(
    meta: Metadata, products: dict[str, list[Artifact]], observed_ms: int | None = None
) -> dict[str, list[dict[str, Any]]]:
    """All rows for the metadata plus the given products, keyed by query name."""
    observed_ms = observed_ms if observed_ms is not None else int(time.time() * 1000)
    rows: dict[str, list[dict[str, Any]]] = {name: [] for name in LOAD_ORDER}
    for part in (org_rows(meta), customer_rows(meta)):
        for name, items in part.items():
            rows[name] += items
    people = set(meta.employees)
    for product, artifacts in products.items():
        for name, items in product_rows(product, artifacts, people, observed_ms).items():
            rows[name] += items
    dedupe_links(rows)
    return rows


def dedupe_links(rows: dict[str, list[dict[str, Any]]]) -> None:
    """A link shared by several products is one node: keep its first row (D023)."""
    seen: set[str] = set()
    unique = []
    for row in rows["upsert_links"]:
        if row["id"] not in seen:
            seen.add(row["id"])
            unique.append(row)
    rows["upsert_links"] = unique


def load(rows: dict[str, list[dict[str, Any]]], graph: str | None = None) -> dict[str, dict]:
    """Run every query in LOAD_ORDER over its rows; return the change counters per query."""
    report = {}
    for name in LOAD_ORDER:
        if rows.get(name):
            result = db.run(name, {"rows": rows[name]}, graph=graph, readonly=False)
            report[name] = {"rows": len(rows[name]), **result.stats}
    return report
