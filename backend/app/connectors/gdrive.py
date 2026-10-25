"""Google Drive connector (D090, EXPERIMENTAL: not run against a live API): Google Docs in,
comments out. Drive API v3, polling, built like the Notion connector.

In: each Google Doc in one folder becomes a Doc artifact (exported as plain text), a message
sharing it in the planning channel `connectors.yaml` maps (`gdrive.channel`), and each comment
as a review message there. Every round reads the whole folder (a comment doesn't reliably move
a file's modifiedTime) and re-ingests a document only when it or one of its comments is newer.
A comment's author has no email address in the API ("will not be populated"), so map comment
authors by display name and document owners by email under `gdrive.people`.
Out: a correction aimed at a document, or one of its comments, becomes a comment on the
document (plain text: Drive comments take no rich formatting through `content`).

AUTH, NOT BUILT: Drive needs OAuth 2.0. This scaffold takes an access token you already hold in
`GOOGLE_DRIVE_ACCESS_TOKEN` (scopes `drive.readonly` to read, `drive.file` or `drive` to
comment). The consent flow and the refresh of the token (they expire in about an hour) are NOT
implemented: when the token expires every call fails loudly with Google's 401.
`GOOGLE_DRIVE_FOLDER_ID` is the sandbox folder to read.

Docs (retrieved 2026-10-18), from Google's Discovery document
https://www.googleapis.com/discovery/v1/apis/drive/v3/rest and the guides:
- https://developers.google.com/workspace/drive/api/reference/rest/v3/files/list (q, fields,
  pageSize, pageToken; query syntax https://developers.google.com/workspace/drive/api/guides/search-files)
- https://developers.google.com/workspace/drive/api/reference/rest/v3/files/export (mimeType)
- https://developers.google.com/workspace/drive/api/reference/rest/v3/comments (list, create:
  `fields` is required)
"""

from typing import Any

import httpx

from app.connectors import render
from app.connectors.base import Item, Maps
from app.connectors.experimental import HttpConnector, to_ms

API = "https://www.googleapis.com"
DOC_MIME = "application/vnd.google-apps.document"
FILE_FIELDS = (
    "nextPageToken,files(id,name,createdTime,modifiedTime,webViewLink,owners(emailAddress))"
)
COMMENT_FIELDS = "nextPageToken,comments(id,content,createdTime,modifiedTime,author(displayName))"


class GoogleDriveConnector(HttpConnector):
    name = "gdrive"
    label = "Google Drive"

    def __init__(
        self,
        access_token: str,
        folder_id: str,
        graph: str,
        maps: Maps | None = None,
        client: httpx.Client | None = None,
    ):
        self.folder_id = folder_id
        super().__init__(
            graph,
            maps,
            client
            or httpx.Client(
                base_url=API, timeout=20, headers={"Authorization": f"Bearer {access_token}"}
            ),
        )

    def _pages(self, path: str, key: str, **params: Any) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        token = None
        while True:
            page = self._req(
                "GET", path, params={**params, **({"pageToken": token} if token else {})}
            )
            out.extend(page.get(key) or [])
            token = page.get("nextPageToken")
            if not token:
                return out

    def doc_url(self, file_id: str) -> str:
        return f"https://docs.google.com/document/d/{file_id}/edit"

    # --- reading ---------------------------------------------------------------------------
    def fetch(self, since_ms: int) -> list[Item]:
        channel_name = self.channel_name()
        files = self._pages(
            "/drive/v3/files",
            "files",
            q=(f"'{self.folder_id}' in parents and mimeType = '{DOC_MIME}' and trashed = false"),
            fields=FILE_FIELDS,
            pageSize=100,
            orderBy="modifiedTime desc",
        )
        items: list[Item] = []
        for f in files:
            comments = self._pages(
                f"/drive/v3/files/{f['id']}/comments",
                "comments",
                fields=COMMENT_FIELDS,
                pageSize=100,
            )
            changed = max([to_ms(f["modifiedTime"])] + [to_ms(c["modifiedTime"]) for c in comments])
            if changed <= since_ms:
                continue
            text = self._req(
                "GET",
                f"/drive/v3/files/{f['id']}/export",
                params={"mimeType": "text/plain"},
                text=True,
            ).strip()
            items.extend(self.normalize(f, text, comments, channel_name))
        return items

    def normalize(
        self,
        file: dict[str, Any],
        text: str,
        comments: list[dict[str, Any]],
        channel_name: str,
    ) -> list[Item]:
        owner = ((file.get("owners") or [{}])[0]).get("emailAddress")
        return self.doc_items(
            ref=file["id"],
            title=file["name"],
            text=text,
            url=file.get("webViewLink") or self.doc_url(file["id"]),
            created=to_ms(file["createdTime"]),
            edited=to_ms(file["modifiedTime"]),
            author=self.maps.person("gdrive", owner, text),
            comments=[
                (
                    c["id"],
                    to_ms(c["createdTime"]),
                    self.maps.person(
                        "gdrive", (c.get("author") or {}).get("displayName"), c["content"]
                    ),
                    c["content"],
                )
                for c in comments
                if not c.get("deleted")
            ],
            channel_name=channel_name,
        )

    # --- writing ---------------------------------------------------------------------------
    def link(self, artifact_id: str) -> render.Link | None:
        ref = self.doc_ref(artifact_id)
        return ("Google Doc", self.doc_url(ref)) if ref else None

    def post(self, action: dict[str, Any], resolve: render.Resolve | None = None) -> str | None:
        ref = self.doc_ref(action.get("target_id") or "")
        if ref is None:
            return None
        # a plain-text comment: the Markdown rendering without its heading and footer markup
        self._req(
            "POST",
            f"/drive/v3/files/{ref}/comments",
            params={"fields": "id"},
            json={"content": plain(action, resolve or self.link)},
        )
        return self.doc_url(ref)


def plain(action: dict[str, Any], resolve: render.Resolve | None) -> str:
    """A draft as plain text with each cited id replaced by its source's label and address."""
    head = "Correction" if action["kind"] == "doc_fix" else "Follow-up"
    lines = [f"Yaan Sentinel · {head}"]

    def label(artifact_id: str) -> str:
        hit = resolve(artifact_id) if resolve else None
        if hit and hit[1]:
            return f"{hit[0]} ({hit[1]})"
        return hit[0] if hit else artifact_id

    for line in filter(str.strip, action["draft"].split("\n")):
        lead, quote, aid, tail = render.split(line)
        if quote is None:
            lines.append(f"- {lead}")
            continue
        lines.append(f"- {lead} · {label(aid or '')}")
        lines.append(f'  "{quote}"')
        if tail:
            lines.append(f"  {tail}")
    lines.append(render.FOOTER)
    return "\n".join(lines)
