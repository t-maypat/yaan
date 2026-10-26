"""Confluence Cloud connector (D090; checked against the live API, D145): pages in,
footer comments out. REST v2, polling, built like the Notion connector.

In: each page of one space becomes a Doc artifact (its storage-format body as plain text), a
message sharing it in the planning channel `connectors.yaml` maps (`confluence.channel`), and
each footer comment as a review message in that channel, so `doc_discussion` finds the review.
Out: a correction aimed at a page, or at one of its comments, becomes a footer comment on the
page (Confluence storage format: one paragraph per draft line, quotes in a blockquote).

Auth: HTTP basic with `CONFLUENCE_EMAIL` + `CONFLUENCE_API_TOKEN` (an Atlassian API token),
against `CONFLUENCE_BASE_URL` (https://<site>.atlassian.net). `CONFLUENCE_SPACE_ID` is the
numeric id of the space to read.

Docs (retrieved 2026-10-18; the reference pages are rendered by script, so the fields below
are taken from the published OpenAPI file https://dac-static.atlassian.com/cloud/confluence/openapi-v2.v3.json):
- https://developer.atlassian.com/cloud/confluence/rest/v2/api-group-page/ (GET /pages:
  space-id, sort=-modified-date, body-format=storage, limit, cursor; PageBulk: id, title,
  authorId, createdAt, version { number, createdAt, authorId }, body.storage.value,
  _links.next)
- https://developer.atlassian.com/cloud/confluence/rest/v2/api-group-comment/ (GET
  /pages/{id}/footer-comments, POST /footer-comments with pageId and body
  { representation, value })
- https://developer.atlassian.com/cloud/confluence/rest/v2/intro/ (basic auth, base path
  /wiki/api/v2)
"""

import html
import re
from typing import Any

import httpx

from app.connectors import render
from app.connectors.base import Item, Maps
from app.connectors.experimental import HttpConnector, to_ms

V2 = "/wiki/api/v2"
TAG = re.compile(r"<[^>]+>")
BREAK = re.compile(r"</(?:p|h[1-6]|li|tr|blockquote)>|<br\s*/?>")


def storage_text(value: str) -> str:
    """Confluence storage format (XHTML) to plain text: block ends become newlines."""
    return html.unescape(TAG.sub("", BREAK.sub("\n", value or ""))).strip()


def storage_comment(action: dict[str, Any], resolve: render.Resolve | None) -> str:
    """A draft as storage format: a heading, then a paragraph per line, quotes in blockquotes."""
    head = "Correction" if action["kind"] == "doc_fix" else "Follow-up"
    out = [f"<p><strong>Yaan Sentinel · {head}</strong></p>"]

    def label(artifact_id: str) -> str:
        hit = resolve(artifact_id) if resolve else None
        if hit and hit[1]:
            return f'<a href="{html.escape(hit[1])}">{html.escape(hit[0])}</a>'
        return html.escape(hit[0] if hit else artifact_id)

    for line in filter(str.strip, action["draft"].split("\n")):
        lead, quote, aid, tail = render.split(line)
        if quote is None:
            out.append(f"<p>{html.escape(lead)}</p>")
            continue
        out.append(f"<p><strong>{html.escape(lead)}</strong> · {label(aid or '')}</p>")
        out.append(f"<blockquote><p>{html.escape(quote)}</p></blockquote>")
        if tail:
            out.append(f"<p>{html.escape(tail)}</p>")
    out.append(f"<p><em>{html.escape(render.FOOTER)}</em></p>")
    return "".join(out)


class ConfluenceConnector(HttpConnector):
    name = "confluence"
    label = "Confluence"

    def __init__(
        self,
        base_url: str,
        email: str,
        token: str,
        space_id: str,
        graph: str,
        maps: Maps | None = None,
        client: httpx.Client | None = None,
    ):
        self.root = base_url.rstrip("/")
        self.space_id = space_id
        super().__init__(
            graph,
            maps,
            client
            or httpx.Client(
                base_url=self.root,
                auth=(email, token),
                timeout=20,
                headers={"Accept": "application/json"},
            ),
        )

    def page_url(self, page_id: str) -> str:
        return f"{self.root}/wiki/pages/viewpage.action?pageId={page_id}"

    def _results(self, path: str, **params: Any) -> list[dict[str, Any]]:
        """All pages of a v2 list: each response names the next one in `_links.next`."""
        out: list[dict[str, Any]] = []
        page = self._req("GET", path, params={**params, "limit": 50})
        while True:
            out.extend(page.get("results") or [])
            nxt = (page.get("_links") or {}).get("next")
            if not nxt:
                return out
            page = self._req("GET", nxt)

    # --- reading ---------------------------------------------------------------------------
    def fetch(self, since_ms: int) -> list[Item]:
        channel_name = self.channel_name()
        items: list[Item] = []
        pages = self._results(
            f"{V2}/pages",
            **{"space-id": self.space_id, "sort": "-modified-date", "body-format": "storage"},
        )
        for page in pages:
            changed = to_ms(page["version"]["createdAt"])
            comments = self._results(
                f"{V2}/pages/{page['id']}/footer-comments", **{"body-format": "storage"}
            )
            changed = max([changed] + [to_ms(c["version"]["createdAt"]) for c in comments])
            if changed <= since_ms:
                continue
            items.extend(self.normalize(page, comments, channel_name))
        return items

    def normalize(
        self, page: dict[str, Any], comments: list[dict[str, Any]], channel_name: str
    ) -> list[Item]:
        text = storage_text(page["body"]["storage"]["value"])
        author = self.maps.person("confluence", page.get("authorId"), text)
        return self.doc_items(
            ref=str(page["id"]),
            title=page["title"],
            text=text,
            url=self.page_url(str(page["id"])),
            created=to_ms(page["createdAt"]),
            edited=to_ms(page["version"]["createdAt"]),
            author=author,
            comments=[
                (
                    str(c["id"]),
                    to_ms(c["version"]["createdAt"]),
                    self.maps.person(
                        "confluence",
                        c["version"].get("authorId"),
                        storage_text(c["body"]["storage"]["value"]),
                    ),
                    storage_text(c["body"]["storage"]["value"]),
                )
                for c in comments
            ],
            channel_name=channel_name,
        )

    # --- writing ---------------------------------------------------------------------------
    def link(self, artifact_id: str) -> render.Link | None:
        ref = self.doc_ref(artifact_id)
        return ("Confluence page", self.page_url(ref)) if ref else None

    def post(self, action: dict[str, Any], resolve: render.Resolve | None = None) -> str | None:
        ref = self.doc_ref(action.get("target_id") or "")
        if ref is None:
            return None
        out = self._req(
            "POST",
            f"{V2}/footer-comments",
            json={
                "pageId": ref,
                "body": {
                    "representation": "storage",
                    "value": storage_comment(action, resolve or self.link),
                },
            },
        )
        return f"{self.page_url(ref)}&focusedCommentId={out['id']}"
