"""Notion connector (17.6, D073-D074): documents and their review in, corrections out.

In: each child page of the demo page shared with the integration is a document. Lore reads it
the way it reads HERB's: a Doc artifact (its blocks as text), the message that shared it in the
planning channel `connectors.yaml` maps (so `doc_discussion` finds its review), and each page
comment as a review message in that channel. "Who reviewed the spec and what did they suggest?"
then works on a real Notion page.
Out: a correction aimed at a Notion document, or at one of its comments, becomes a page comment.
"""

import time
from datetime import datetime
from typing import Any

import httpx

from app import db
from app.connectors import base, render
from app.connectors.base import ConnectorError, Item, Maps
from app.ingest.normalize import Artifact

API = "https://api.notion.com"
VERSION = "2022-06-28"
COMMENTS_EVERY_MS = 600_000
TEXT_BLOCKS = (
    "paragraph",
    "heading_1",
    "heading_2",
    "heading_3",
    "bulleted_list_item",
    "numbered_list_item",
    "to_do",
    "quote",
    "callout",
)


def ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def iso(stamp: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stamp / 1000))


def rich(parts: list[dict[str, Any]] | None) -> str:
    return "".join(p.get("plain_text", "") for p in parts or [])


class NotionConnector:
    name = "notion"
    reads = ("child pages of the pages you name", "page comments")  # README (D128)
    writes = ("comments on the page",)

    def __init__(
        self,
        token: str,
        page_id: str,
        graph: str,
        maps: Maps | None = None,
        client: httpx.Client | None = None,
    ):
        self.page_id, self.graph = page_id.replace("-", ""), graph
        self.maps = maps or base.maps()
        self.channel = self.maps.source("notion").get("channel")
        self._comments_ms: int | None = None  # when every page's comments were last read
        self.http = client or httpx.Client(
            base_url=API,
            timeout=20,
            headers={"Authorization": f"Bearer {token}", "Notion-Version": VERSION},
        )

    def _req(self, method: str, path: str, **kw: Any) -> Any:
        r = self.http.request(method, path, **kw)
        if r.status_code >= 400:
            raise base.http_error("Notion", method, path, r)
        return r.json()

    def _all(self, path: str, **params: Any) -> list[dict[str, Any]]:
        out, cursor = [], None
        while True:
            page = self._req(
                "GET",
                path,
                params={**params, "page_size": 100, **({"start_cursor": cursor} if cursor else {})},
            )
            out.extend(page.get("results") or [])
            if not page.get("has_more"):
                return out
            cursor = page.get("next_cursor")

    def doc_id(self, page_id: str) -> str:
        return base.synthetic_id("doc", "notion", page_id.replace("-", ""))

    # --- reading ---------------------------------------------------------------------------
    def fetch(self, since_ms: int) -> list[Item]:
        if not self.channel:
            raise ConnectorError("connectors.yaml: notion.channel (where documents are shared)")
        rows = db.run("get_channel", {"id": self.channel}, graph=self.graph).rows
        if not rows:
            raise ConnectorError(f"notion.channel {self.channel!r} isn't in the graph")
        items: list[Item] = []
        now = int(time.time() * 1000)
        # a new comment may not change the page's edit time: every page's comments are read
        # every COMMENTS_EVERY_MS, an edited page's at once (D132; before, every round)
        comments_due = self._comments_ms is None or now - self._comments_ms >= COMMENTS_EVERY_MS
        for block in self._all(f"/v1/blocks/{self.page_id}/children"):
            if block.get("type") != "child_page":
                continue
            page = self._req("GET", f"/v1/pages/{block['id']}")
            edited = ms(page["last_edited_time"])
            if edited <= since_ms and not comments_due:
                continue
            comments = self._all("/v1/comments", block_id=block["id"])
            changed = max([edited] + [ms(c["created_time"]) for c in comments])
            if changed <= since_ms:
                continue
            body = self._all(f"/v1/blocks/{block['id']}/children")
            items.extend(self.normalize(block, page, body, comments, rows[0]["name"]))
        if comments_due:
            self._comments_ms = now
        return items

    def normalize(
        self,
        block: dict[str, Any],
        page: dict[str, Any],
        body: list[dict[str, Any]],
        comments: list[dict[str, Any]],
        channel_name: str,
    ) -> list[Item]:
        title = block["child_page"]["title"]
        text = "\n".join(
            rich(b[b["type"]].get("rich_text")) for b in body if b.get("type") in TEXT_BLOCKS
        ).strip()
        author = self.maps.person("notion", (page.get("created_by") or {}).get("id"), text)
        created = ms(page["created_time"])
        doc = Artifact(
            id=self.doc_id(block["id"]),
            kind="doc",
            product_hint=self.maps.product_of("notion", self.channel),
            ts_ms=created,
            ts_iso=iso(created),
            author_eid=author,
            title=title,
            text=text,
            url=page["url"],
            raw_ref=f"notion:{block['id']}",
            meta={
                "doc_type": title,
                "version": "base",
                "base_id": self.doc_id(block["id"]),
                "feedback": None,
                "synthetic": base.is_demo(),
                "source": "notion",
            },
        )
        items = [Item(doc, updated_ms=ms(page["last_edited_time"]))]

        def message(part: str, stamp: int, who: str, words: str) -> Artifact:
            return Artifact(
                id=base.synthetic_id("msg", "notion", f"{block['id'].replace('-', '')}-{part}"),
                kind="message",
                product_hint=self.maps.product_of("notion", self.channel),
                channel_id=self.channel,
                ts_ms=stamp,
                ts_iso=iso(stamp),
                author_eid=who,
                text=words,
                raw_ref=f"notion:{block['id']}:{part}",
                meta={
                    "channel_name": channel_name,
                    "sender": who,
                    "synthetic": base.is_demo(),
                    "source": "notion",
                },
            )

        share = f"{base.tag()}@{author} shared the {title} for review: {page['url']}"
        items.append(Item(message("shared", created + 1, author, share), updated_ms=created + 1))
        for c in comments:
            words = rich(c.get("rich_text")).strip()
            who = self.maps.person("notion", (c.get("created_by") or {}).get("id"), words)
            stamp = ms(c["created_time"])
            items.append(
                Item(
                    message(
                        f"comment-{c['id'].replace('-', '')}",
                        stamp,
                        who,
                        f"{words}\n(comment on the {title} in Notion)",
                    ),
                    updated_ms=stamp,
                )
            )
        return items

    # --- writing ---------------------------------------------------------------------------
    def post(self, action: dict[str, Any], resolve: Any = None) -> str | None:
        target = action.get("target_id") or ""
        doc_prefix = base.synthetic_id("doc", "notion", "x")[:-1]
        msg_prefix = base.synthetic_id("msg", "notion", "x")[:-1]
        if target.startswith(doc_prefix):
            page = target.removeprefix(doc_prefix)
        elif target.startswith(msg_prefix):
            page = target.removeprefix(msg_prefix).split("-")[0]
        else:
            return None
        mark = render.marker(action)
        url = f"https://www.notion.so/{page}"
        for c in self._all("/v1/comments", block_id=page):  # sent before? (D110)
            if mark in "".join(t.get("plain_text", "") for t in c.get("rich_text") or []):
                return url
        body = action["draft"][: 2000 - len(mark) - 2] + f"\n{mark}"
        self._req(
            "POST",
            "/v1/comments",
            json={
                "parent": {"page_id": page},
                "rich_text": [{"type": "text", "text": {"content": body}}],
            },
        )
        return url
