"""What the eight later connectors share (D090): an httpx client with loud errors, the channel
lookup, and the two artifact shapes they emit.

Every module built on this was written from the vendor's API reference, is unit-tested against
payloads copied from it, and was checked against the live service (D145). They are OFF unless
all of a connector's env vars are set (`runner.configured`). The connectors README lists them.
"""

from datetime import UTC, datetime
from typing import Any

import httpx

from app import db
from app.connectors import base, render
from app.connectors.base import ConnectorError, Item, Maps
from app.ingest.normalize import Artifact


def to_ms(stamp: str) -> int:
    """An RFC 3339 / ISO 8601 time to epoch ms; a time without a zone is UTC."""
    t = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    return int((t if t.tzinfo else t.replace(tzinfo=UTC)).timestamp() * 1000)


def to_iso(stamp_ms: int) -> str:
    return datetime.fromtimestamp(stamp_ms / 1000, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class HttpConnector:
    """Base of the experimental connectors: `name` keys the cursor, the ids and connectors.yaml;
    `label` prefixes every error. Subclasses build `self.http` (or take a test client)."""

    name = ""
    label = ""

    def __init__(self, graph: str, maps: Maps | None, client: httpx.Client):
        self.graph = graph
        self.maps = maps or base.maps()
        self.http = client
        self.channel: str | None = self.maps.source(self.name).get("channel")

    def _req(self, method: str, path: str, *, text: bool = False, **kw: Any) -> Any:
        r = self.http.request(method, path, **kw)
        if r.status_code >= 400:  # loud, with the vendor's message
            raise ConnectorError(f"{self.label} {method} {path}: {r.status_code} {r.text[:300]}")
        return r.text if text else r.json()

    def channel_name(self, channel: str | None = None) -> str:
        """The name of the graph channel items land in (connectors.yaml: <name>.channel)."""
        channel = channel or self.channel
        if not channel:
            raise ConnectorError(
                f"connectors.yaml: {self.name}.channel (the channel its items land in)"
            )
        rows = db.run("get_channel", {"id": channel}, graph=self.graph).rows
        if not rows:
            raise ConnectorError(f"{self.name}.channel {channel!r} isn't in the graph")
        return rows[0]["name"]

    def message(
        self,
        key: str,
        stamp: int,
        author: str,
        text: str,
        channel_name: str,
        channel: str | None = None,
        **meta: Any,
    ) -> Artifact:
        return Artifact(
            id=base.synthetic_id("msg", self.name, key),
            kind="message",
            product_hint=self.maps.product_of(self.name, channel or self.channel),
            channel_id=channel or self.channel,
            ts_ms=stamp,
            ts_iso=to_iso(stamp),
            author_eid=author,
            text=text,
            raw_ref=f"{self.name}:{key}",
            meta={
                "channel_name": channel_name,
                "sender": author,
                "synthetic": base.is_demo(),
                "source": self.name,
                **meta,
            },
        )

    def doc_items(
        self,
        ref: str,
        title: str,
        text: str,
        url: str | None,
        created: int,
        edited: int,
        author: str,
        comments: list[tuple[str, int, str, str]],
        channel_name: str,
    ) -> list[Item]:
        """A document the way Notion's connector emits it: the Doc artifact, the message that
        shares it in the mapped channel, and each comment as a review message there.
        `comments` are (comment id, ms, author eid, text)."""
        doc_id = base.synthetic_id("doc", self.name, ref)
        doc = Artifact(
            id=doc_id,
            kind="doc",
            product_hint=self.maps.product_of(self.name, self.channel),
            ts_ms=created,
            ts_iso=to_iso(created),
            author_eid=author,
            title=title,
            text=text,
            url=url,
            raw_ref=f"{self.name}:{ref}",
            meta={
                "doc_type": title,
                "version": "base",
                "base_id": doc_id,
                "feedback": None,
                "synthetic": base.is_demo(),
                "source": self.name,
            },
        )
        items = [Item(doc, updated_ms=edited)]
        share = f"{base.tag()}@{author} shared the {title} for review: {url or title}"
        items.append(
            Item(
                self.message(f"{ref}-shared", created + 1, author, share, channel_name),
                updated_ms=created + 1,
            )
        )
        for cid, stamp, who, words in comments:
            items.append(
                Item(
                    self.message(
                        f"{ref}-comment-{cid}",
                        stamp,
                        who,
                        f"{words}\n(comment on the {title} in {self.label})",
                        channel_name,
                    ),
                    updated_ms=stamp,
                )
            )
        return items

    def doc_ref(self, artifact_id: str) -> str | None:
        """The document an id of this connector belongs to (the doc itself, its share message
        or one of its comments), else None."""
        for kind in ("doc", "msg"):
            prefix = base.synthetic_id(kind, self.name, "x")[:-1]
            if artifact_id.startswith(prefix):
                rest = artifact_id.removeprefix(prefix)
                if kind == "msg":
                    rest = rest.split("-comment-")[0].removesuffix("-shared")
                return rest or None
        return None

    def link(self, artifact_id: str) -> render.Link | None:
        return None
