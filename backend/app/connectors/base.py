"""The connector contract and its maps (Phase 17.2, D073, D074).

A connector reads one company tool and writes Lore's approved actions back into it:
- `fetch(since_ms)` returns the items created or changed since then, each already normalized
  into Lore's artifact schema (the same rows as data/normalized/), optionally with
  deterministic events (a Jira status change needs no LLM);
- `post(action)` sends one approved Sentinel draft to the tool when the action targets
  something that lives there, and returns a reference (a URL), else None.
`runner.sync` feeds the items to the live path (sentinel/live.ingest_artifact) and the
approved actions to `post`.

`connectors.yaml` (repo root) says which product each source container belongs to and which
person each sandbox account plays. The demo sandboxes hold only labeled synthetic items, and a
seeded item can name the person it is written as: "[synthetic demo · as Ian Davis
(eid_7017b913)]", so one human account can play the story's several people.
"""

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Protocol

import yaml

from app.config import REPO_ROOT
from app.ingest.normalize import Artifact

CONFIG = REPO_ROOT / "connectors.yaml"
LABEL = "[synthetic demo"  # every sandbox item starts with this (rule 5)
PERSONA = re.compile(r"\[synthetic demo[^\]]*\((eid_[0-9a-f]{8})\)\]")


class ConnectorError(RuntimeError):
    """A connector can't run: missing token or map, or the source refused a call."""


@dataclass
class Item:
    artifact: Artifact
    updated_ms: int  # the source's last change: a newer value re-ingests the artifact
    events: list[dict[str, Any]] | None = None  # None: the live path extracts with the LLM


class Connector(Protocol):
    name: str

    def fetch(self, since_ms: int) -> list[Item]: ...

    def post(self, action: dict[str, Any], resolve: Any = None) -> str | None: ...


@dataclass
class Maps:
    """connectors.yaml: the product the sandboxes play, and per source its containers and
    people (source account id -> eid)."""

    product: str
    default_person: str
    sources: dict[str, dict[str, Any]] = field(default_factory=dict)

    def source(self, name: str) -> dict[str, Any]:
        return self.sources.get(name) or {}

    def person(
        self, source: str, account: str | None, text: str = "", own: str | None = None
    ) -> str:
        """Who an item is by: the persona its label names, else the account's mapping (the
        key "self" means the connector's own account, `own`), else the default person."""
        if m := PERSONA.search(text or ""):
            return m.group(1)
        people = self.source(source).get("people") or {}
        if own and account == own and "self" in people:
            return people["self"]
        return people.get(account or "", self.default_person)


@lru_cache
def maps() -> Maps:
    if not CONFIG.is_file():
        raise ConnectorError(f"{CONFIG.name} is missing: copy the example in docs/connectors.md")
    raw = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    try:
        return Maps(
            product=raw["product"],
            default_person=raw["default_person"],
            sources={k: v for k, v in raw.items() if isinstance(v, dict)},
        )
    except KeyError as e:
        raise ConnectorError(f"{CONFIG.name} needs {e.args[0]!r}") from e


def labeled(text: str) -> bool:
    return LABEL in (text or "")


def synthetic_id(kind_prefix: str, source: str, key: str) -> str:
    """`msg:synthetic-slack-…`, `pr:synthetic-github-…`: the live path's rule-5 id shape."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", key).strip("-")
    return f"{kind_prefix}:synthetic-{source}-{safe}"


def structured_event(
    kind: str, a: Artifact, quote: str, speaker: str, **fields: Any
) -> dict[str, Any]:
    """An event in the extractor's shape (S5) for a fact the source states outright, e.g. a
    Jira status change: no LLM, and the live path still checks the quote against the text."""
    start = a.text.find(quote)
    return {
        "type": kind,
        "msg": 0,
        "quote": quote,
        "issue": fields.get("issue"),
        "component": fields.get("component"),
        "customer": fields.get("customer"),
        "person": fields.get("person"),
        "pr": fields.get("pr"),
        "reason": fields.get("reason"),
        "decisive": True,
        "confidence": 1.0,
        "unit_id": f"unit:{a.product_hint}:{a.id}",
        "product": a.product_hint,
        "channel": (a.meta or {}).get("channel_name"),
        "moved": False,
        "artifact_id": a.id,
        "ts_ms": a.ts_ms,
        "ts_iso": a.ts_iso,
        "speaker": speaker,
        "start": max(start, 0),
        "end": max(start, 0) + len(quote),
    }
