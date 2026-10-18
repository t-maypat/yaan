"""S1 Normalize. Built in Phase 2.

HERB records -> data/normalized/<product>/artifacts.jsonl, ordered by ts_ms. Row:
{id, kind, product_hint, channel_id, ts_ms, author_eid?, participants?, title?, text,
url?, raw_ref}. IDs per CONTEXT §9.2; times as UTC epoch ms plus ISO strings.
Spec: docs/CONTEXT.md §9.3 (S1).

Beyond the §9.3 row: `ts_iso`, `external` and `system` flags (D024), and `meta` for the
fields only one kind has (PR state and reviews, doc type and version, …). Text is kept
verbatim, Slack markup included, so later quotes stay exact substrings of it.
Decisions: D022 (zone-less times are UTC), D023 (Link ids), D024 (distractors kept).
"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict

from app.config import get_settings
from app.ingest.herb_fetch import herb_dir
from app.ingest.herb_loader import Product, load_product

Kind = Literal["message", "meeting", "meeting_chat", "doc", "pr", "link"]
KINDS: tuple[str, ...] = Kind.__args__
HERB_TIME = "%Y-%m-%dT%H:%M:%S"
INTERNAL_PR_OWNER = "salesforce"
INTERNAL_HOST = "sf-internal.slack.com"
BOT_SENDER = "slack_admin_bot"
DOC_VERSIONS = ("final", "latest")  # doc ids: <base>, final_<base>, latest_<base>


class Artifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Kind
    product_hint: str
    channel_id: str | None = None
    ts_ms: int | None
    ts_iso: str | None
    author_eid: str | None = None
    participants: list[str] = []
    title: str | None = None
    text: str
    url: str | None = None
    external: bool = False
    system: bool = False
    raw_ref: str
    meta: dict[str, Any] = {}


def parse_ts(value: str) -> tuple[int, str]:
    """HERB time (no zone, D022) -> (UTC epoch ms, ISO string with Z). Strict format."""
    moment = datetime.strptime(value, HERB_TIME).replace(tzinfo=UTC)
    return int(moment.timestamp() * 1000), f"{value}Z"


def parse_bool(value: str) -> bool:
    if value not in ("True", "False"):
        raise ValueError(f"expected 'True' or 'False', got {value!r}")
    return value == "True"


def eid_or_none(login: str) -> str | None:
    """HERB eids look like eid_1a2b3c4d; external PR logins (EMP_…) and the bot aren't."""
    return login if login.startswith("eid_") else None


def link_id(link: str) -> str:
    """D023: a Link is keyed by its normalized address, shared across products."""
    parts = urlsplit(link.strip())
    key = f"{parts.scheme.lower()}://{parts.netloc.lower()}{parts.path.rstrip('/')}"
    if parts.query:
        key += f"?{parts.query}"
    return "url:" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def doc_version(doc_id: str) -> tuple[str, str]:
    """('final', 'ainaix_x') for 'final_ainaix_x'; ('base', id) without a version prefix."""
    head, _, rest = doc_id.partition("_")
    return (head, rest) if head in DOC_VERSIONS and rest else ("base", doc_id)


def normalize_product(product: Product) -> tuple[list[Artifact], dict[str, int]]:
    """All artifacts of one product as S1 rows, sorted, plus counts of dropped duplicates."""
    name = product.name
    rows: list[tuple[int, Artifact]] = []  # (file index, row): the index breaks ties

    for i, m in enumerate(product.slack):
        ms, iso = parse_ts(m.Message.User.timestamp)
        sender = m.Message.User.userId
        row = Artifact(
            id=f"msg:{m.id}",
            kind="message",
            product_hint=name,
            channel_id=m.Channel.channelID,
            ts_ms=ms,
            ts_iso=iso,
            author_eid=eid_or_none(sender),
            text=m.Message.User.text,
            system=sender == BOT_SENDER,
            raw_ref=f"{name}:slack[{i}]",
            meta={"channel_name": m.Channel.name, "sender": sender},
        )
        rows.append((i, row))

    for i, mt in enumerate(product.meeting_transcripts):
        ms, iso = parse_ts(mt.date)
        row = Artifact(
            id=f"mtg:{mt.id}",
            kind="meeting",
            product_hint=name,
            ts_ms=ms,
            ts_iso=iso,
            participants=list(mt.participants),
            title=mt.id,
            text=mt.transcript,
            raw_ref=f"{name}:meeting_transcripts[{i}]",
            meta={"document_type": mt.document_type},
        )
        rows.append((i, row))

    for i, chat in enumerate(product.meeting_chats):
        ms, iso = parse_ts(chat.text.split("\n", 1)[0].strip())  # first line is the time
        meeting = chat.id.removesuffix("_chat")
        row = Artifact(
            id=f"chat:{chat.id}",
            kind="meeting_chat",
            product_hint=name,
            ts_ms=ms,
            ts_iso=iso,
            text=chat.text,
            raw_ref=f"{name}:meeting_chats[{i}]",
            meta={"meeting_id": f"mtg:{meeting}" if meeting != chat.id else None},
        )
        rows.append((i, row))

    for i, d in enumerate(product.documents):
        ms, iso = parse_ts(d.date)
        version, base = doc_version(d.id)
        row = Artifact(
            id=f"doc:{d.id}",
            kind="doc",
            product_hint=name,
            ts_ms=ms,
            ts_iso=iso,
            author_eid=eid_or_none(d.author),
            title=d.id,
            text=d.content,
            url=d.document_link,
            raw_ref=f"{name}:documents[{i}]",
            meta={
                "doc_type": d.type,
                "version": version,
                "base_id": f"doc:{base}",
                "feedback": d.feedback,
            },
        )
        rows.append((i, row))

    for i, pr in enumerate(product.prs):
        ms, iso = parse_ts(pr.created_at)
        owner, repo = urlsplit(pr.link).path.strip("/").split("/")[:2]
        reviews = []
        for r in pr.reviews:
            r_ms, r_iso = parse_ts(r.submitted_at)
            reviews.append(
                {
                    "state": r.state,
                    "login": r.user.login,
                    "reviewer_eid": eid_or_none(r.user.login),
                    "comment": r.comment,
                    "ts_ms": r_ms,
                    "ts_iso": r_iso,
                }
            )
        row = Artifact(
            id=f"pr:{pr.id}",
            kind="pr",
            product_hint=name,
            ts_ms=ms,
            ts_iso=iso,
            author_eid=eid_or_none(pr.user.login),
            title=pr.title,
            text=pr.summary,
            url=pr.link,
            external=owner.lower() != INTERNAL_PR_OWNER,
            raw_ref=f"{name}:prs[{i}]",
            meta={
                "repo": f"{owner}/{repo}",
                "number": int(pr.number),
                "state": pr.state,
                "merged": parse_bool(pr.merged),
                "mergeable": parse_bool(pr.mergeable),
                "login": pr.user.login,
                "reviews": reviews,
            },
        )
        rows.append((i, row))

    links: dict[str, Artifact] = {}
    dropped = 0
    for i, u in enumerate(product.urls):
        lid = link_id(u.link)
        if lid in links:  # D023: first row per link wins
            dropped += 1
            continue
        links[lid] = Artifact(
            id=lid,
            kind="link",
            product_hint=name,
            ts_ms=None,  # HERB URLs carry no time; S4 dates them by the messages sharing them
            ts_iso=None,
            text=u.description,
            url=u.link,
            external=urlsplit(u.link).netloc.lower() != INTERNAL_HOST,
            raw_ref=f"{name}:urls[{i}]",
            meta={"herb_id": u.id},
        )
        rows.append((i, links[lid]))

    rows.sort(key=lambda r: sort_key(*r))
    artifacts = [row for _, row in rows]
    ids = [a.id for a in artifacts]
    if len(set(ids)) != len(ids):
        raise ValueError(f"{name}: duplicate artifact ids after normalize")
    return artifacts, {"duplicate_links_dropped": dropped}


def sort_key(index: int, row: Artifact) -> tuple[bool, int, int, int]:
    """D024: by time (timeless rows last), then kind, then HERB's file order."""
    return (row.ts_ms is None, row.ts_ms or 0, KINDS.index(row.kind), index)


def normalized_dir(product: str) -> Path:
    return get_settings().data_dir / "normalized" / product


def write_jsonl(artifacts: list[Artifact], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for a in artifacts:
            f.write(a.model_dump_json() + "\n")
    return path


def read_jsonl(path: Path) -> list[Artifact]:
    with path.open(encoding="utf-8") as f:
        return [Artifact.model_validate(json.loads(line)) for line in f if line.strip()]


def normalize(products: list[str], herb: Path | None = None) -> dict[str, dict[str, Any]]:
    """Normalize each product to data/normalized/<product>/artifacts.jsonl; return counts."""
    report = {}
    for name in products:
        path = (herb or herb_dir()) / "products" / f"{name}.json"
        if not path.is_file():
            raise FileNotFoundError(f"no HERB product {name!r} at {path}")
        artifacts, stats = normalize_product(load_product(path))
        out = write_jsonl(artifacts, normalized_dir(name) / "artifacts.jsonl")
        counts = {k: sum(a.kind == k for a in artifacts) for k in KINDS}
        report[name] = {"path": str(out), "counts": counts, **stats}
    return report
