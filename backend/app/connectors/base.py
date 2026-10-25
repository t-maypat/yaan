"""The connector contract and its maps (Phase 17.2, D073, D074).

A connector reads one company tool and writes Yaan's approved actions back into it:
- `fetch(since_ms)` returns the items created or changed since then, each already normalized
  into Yaan's artifact schema (the same rows as data/normalized/), optionally with
  deterministic events (a Jira status change needs no LLM);
- `post(action)` sends one approved Sentinel draft to the tool when the action targets
  something that lives there, and returns a reference (a URL), else None.
`runner.sync` feeds the items to the live path (sentinel/live.ingest_artifact) and the
approved actions to `post`.

`connectors.yaml` (repo root) says which product each source container belongs to and which
person each sandbox account plays. The demo sandboxes hold only labeled synthetic items, and a
seeded item can name the person it is written as: "[synthetic demo · as Ian Davis
(eid_7017b913)]", so one human account can play the story's several people.

In org mode (YAAN_DATA_MODE=org, app/org.py, D097) the same connectors read a team's own tools:
no label is required or written, ids drop `synthetic-` (`msg:slack-…`), and an account maps to
its people.yaml entry, or to a person registered on first sight, never to a stand-in.
"""

import re
import time
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Protocol

import yaml

from app import org
from app.config import REPO_ROOT
from app.ingest.normalize import Artifact

CONFIG = REPO_ROOT / "connectors.yaml"
LABEL = "[synthetic demo"  # every sandbox item starts with this (rule 5)
PERSONA = re.compile(r"\[synthetic demo[^\]]*\((eid_[0-9a-f]{8})\)\]")


class ConnectorError(RuntimeError):
    """A connector can't run: missing token or map, or the source refused a call."""


class RateLimited(ConnectorError):
    """The source asked Yaan to slow down: no call to it for `retry_after_s` (D132)."""

    def __init__(self, message: str, retry_after_s: float):
        super().__init__(f"{message} (rate limited: back in {retry_after_s:.0f} s)")
        self.retry_after_s = retry_after_s


def http_error(tool: str, method: str, path: str, r: Any, detail: str | None = None):
    """A failed HTTP call as a ConnectorError, or RateLimited when the tool says when to come
    back: Retry-After (Jira, Notion, GitHub's secondary limit) or GitHub's exhausted primary
    limit (`x-ratelimit-remaining: 0` until `x-ratelimit-reset`). A 429 without a time waits
    a minute."""
    message = f"{tool} {method} {path}: {r.status_code} {detail or r.text[:300]}"
    wait: float | None = None
    if r.status_code in (403, 429, 503):
        if retry := r.headers.get("retry-after"):
            try:
                wait = max(1.0, float(retry))
            except ValueError:  # an HTTP date: rare; a minute is near enough
                wait = 60.0
        elif r.headers.get("x-ratelimit-remaining") == "0" and r.headers.get("x-ratelimit-reset"):
            wait = max(1.0, float(r.headers["x-ratelimit-reset"]) - time.time())
        elif r.status_code == 429:
            wait = 60.0
    return RateLimited(message, wait) if wait is not None else ConnectorError(message)


@dataclass
class Item:
    artifact: Artifact
    updated_ms: int  # the source's last change: a newer value re-ingests the artifact
    events: list[dict[str, Any]] | None = None  # None: the live path extracts with the LLM
    deleted: bool = False  # the source deleted it: Yaan forgets it (only its id matters, D126)


class Connector(Protocol):
    name: str

    def fetch(self, since_ms: int) -> list[Item]: ...

    def post(self, action: dict[str, Any], resolve: Any = None) -> str | None: ...


@dataclass
class Maps:
    """connectors.yaml: the product the sandboxes play, and per source its containers and
    people (source account id -> eid)."""

    product: str
    default_person: str | None  # demo only: who an unmapped sandbox account plays
    sources: dict[str, dict[str, Any]] = field(default_factory=dict)
    # org mode: how long Yaan keeps what every tool brought, unless a tool says (22.5)
    retention_days: Any = None

    def source(self, name: str) -> dict[str, Any]:
        return self.sources.get(name) or {}

    def containers(self, source: str, key: str, default: str | None) -> list[str]:
        """The repos, projects or pages a tool reads (D118): connectors.yaml's list under
        `key`, else the single one .env names."""
        listed = self.source(source).get(key)
        if listed:
            return [str(x) for x in (listed if isinstance(listed, list) else [listed])]
        return [default] if default else []

    def product_of(self, source: str, channel: str | None = None) -> str:
        """The product an item belongs to (D116): its channel's, else its source's, else the
        top-level `product`."""
        if channel and (p := org.channel_products(self.sources).get(channel)):
            return p
        return str(self.source(source).get("product") or self.product)

    def person(
        self,
        source: str,
        account: str | None,
        text: str = "",
        own: str | None = None,
        name: str | None = None,
        email: str | None = None,
    ) -> str:
        """Who an item is by: the persona its label names, else the account's mapping (the
        key "self" means the connector's own account, `own`), else the default person. In org
        mode there is no persona and no stand-in: an unmapped account is its people.yaml entry
        or a person registered under it (`name` is the tool's display name, if it gave one;
        `email` one the tool vouches for, D130)."""
        people = self.source(source).get("people") or {}
        if org.active():
            if own and account == own and "self" in people:
                return people["self"]
            if account and account in people:
                return people[account]
            return org.person_id(source, account, name, email)
        if m := PERSONA.search(text or ""):
            return m.group(1)
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
            default_person=raw.get("default_person") if org.active() else raw["default_person"],
            sources={k: v for k, v in raw.items() if isinstance(v, dict)},
            retention_days=raw.get("retention_days"),
        )
    except KeyError as e:
        raise ConnectorError(f"{CONFIG.name} needs {e.args[0]!r}") from e


def labeled(text: str) -> bool:
    return LABEL in (text or "")


def is_demo() -> bool:
    return not org.active()


def tag() -> str:
    """What a message a connector writes itself (a Jira status line, a Notion share) opens
    with: the demo label, or nothing in org mode."""
    return f"{LABEL}] " if is_demo() else ""


def id_prefix(kind_prefix: str, source: str) -> str:
    """`msg:synthetic-slack-` in the demo (the live path's rule-5 shape), `msg:slack-` in org
    mode: what every id a connector makes starts with."""
    return f"{kind_prefix}:synthetic-{source}-" if is_demo() else f"{kind_prefix}:{source}-"


def synthetic_id(kind_prefix: str, source: str, key: str) -> str:
    """`msg:synthetic-slack-…`, `pr:synthetic-github-…`; `msg:slack-…` in org mode."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", key).strip("-")
    return id_prefix(kind_prefix, source) + safe


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


# --- what may leave Yaan (H1, D109) ------------------------------------------------------------
# A drafted comment goes to a Jira ticket the whole project reads, an alert card to a Slack
# channel anyone may join: Yaan can't know each destination's readers, so text that leaves it
# quotes only sources the whole company may read. The rest keep their id, not their words.
WITHHELD = "quote withheld: its source isn't open to everyone"


def public_quotes(text: str | None, graph: str | None) -> str | None:
    """`text` with every `"quote" (id)` whose source isn't open to everyone replaced."""
    from app.agent import access
    from app.connectors.render import CITE

    if not text:
        return text
    ids = {m["id"] for m in CITE.finditer(text)}
    if not ids:
        return text
    open_ = access.Access.audience(None, graph).allowed(ids)
    return CITE.sub(lambda m: m[0] if m["id"] in open_ else f'"{WITHHELD}" ({m["id"]})', text)


def public_alert(alert: dict[str, Any], graph: str | None) -> dict[str, Any]:
    """An alert as a card outside Yaan may show it: its evidence quote only if open to all."""
    from app.agent import access

    aid = alert.get("artifact_id")
    if not alert.get("quote") or not aid:
        return alert
    if aid in access.Access.audience(None, graph).allowed({aid}):
        return alert
    return {**alert, "quote": WITHHELD}
