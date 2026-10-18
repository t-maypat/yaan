"""Gmail connector (D090, EXPERIMENTAL: not run against a live API): mail in, replies out.
Gmail API v1, polling.

In: the messages that carry one label (a sandbox label you create, `GMAIL_LABEL`, a label id)
become messages in the channel `connectors.yaml` maps (`gmail.channel`): the subject line, then
the text/plain body. The sender's address (From) is the account in `gmail.people`.
Out: a draft aimed at a message that came from Gmail is sent as a plain-text reply in its
thread (Markdown, readable as text). Nothing else is ever emailed: a ticket comment on an
issue is NOT sent as mail.

AUTH, NOT BUILT: Gmail needs OAuth 2.0. This scaffold takes an access token you already hold in
`GMAIL_ACCESS_TOKEN` (scopes `gmail.readonly` to read, `gmail.send` to reply). The consent
flow and the token's refresh (about an hour) are NOT implemented: an expired token fails
loudly with Google's 401. Sending real mail is why this one stays off until you set both vars.

Docs (retrieved 2026-10-18), from Google's Discovery document
https://gmail.googleapis.com/$discovery/rest?version=v1 and the guides:
- https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list
  (q, labelIds, pageToken; search operators https://support.google.com/mail/answer/7190 for
  `after:<epoch seconds>`)
- https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/get
  (format=full, payload.headers, payload.parts, internalDate)
- https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/send
  and https://developers.google.com/workspace/gmail/api/guides/sending (raw = base64url RFC 2822,
  threadId to reply in a thread)
"""

import base64
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Any

import httpx

from app.connectors import base, render
from app.connectors.base import ConnectorError, Item, Maps
from app.connectors.experimental import HttpConnector

API = "https://gmail.googleapis.com"


def b64(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")


def plain_body(part: dict[str, Any]) -> str:
    """The first text/plain body in a message payload, depth first."""
    if part.get("mimeType") == "text/plain" and (part.get("body") or {}).get("data"):
        return b64(part["body"]["data"])
    for sub in part.get("parts") or []:
        if text := plain_body(sub):
            return text
    return ""


def header(payload: dict[str, Any], name: str) -> str:
    for h in payload.get("headers") or []:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


class GmailConnector(HttpConnector):
    name = "gmail"
    label = "Gmail"

    def __init__(
        self,
        access_token: str,
        label_id: str,
        graph: str,
        maps: Maps | None = None,
        client: httpx.Client | None = None,
    ):
        self.label_id = label_id
        super().__init__(
            graph,
            maps,
            client
            or httpx.Client(
                base_url=API, timeout=20, headers={"Authorization": f"Bearer {access_token}"}
            ),
        )

    def msg_key(self, artifact_id: str) -> str | None:
        prefix = base.synthetic_id("msg", "gmail", "x")[:-1]
        return artifact_id.removeprefix(prefix) if artifact_id.startswith(prefix) else None

    # --- reading ---------------------------------------------------------------------------
    def fetch(self, since_ms: int) -> list[Item]:
        channel_name = self.channel_name()
        params: dict[str, Any] = {"labelIds": self.label_id, "maxResults": 100}
        if since_ms:  # Gmail's `after:` takes epoch seconds
            params["q"] = f"after:{since_ms // 1000}"
        items: list[Item] = []
        token = None
        while True:
            page = self._req(
                "GET",
                "/gmail/v1/users/me/messages",
                params={**params, **({"pageToken": token} if token else {})},
            )
            for ref in page.get("messages") or []:
                msg = self._req(
                    "GET", f"/gmail/v1/users/me/messages/{ref['id']}", params={"format": "full"}
                )
                items.append(self.normalize(msg, channel_name))
            token = page.get("nextPageToken")
            if not token:
                return items

    def normalize(self, msg: dict[str, Any], channel_name: str) -> Item:
        payload = msg["payload"]
        subject, body = header(payload, "Subject"), plain_body(payload).strip()
        text = f"{subject}\n{body}" if subject else body
        sender = parseaddr(header(payload, "From"))[1].lower()
        stamp = int(msg["internalDate"])
        a = self.message(
            msg["id"],
            stamp,
            self.maps.person("gmail", sender, text),
            text,
            channel_name,
            thread_id=msg.get("threadId"),
            subject=subject,
        )
        return Item(a, updated_ms=stamp)

    # --- writing ---------------------------------------------------------------------------
    def link(self, artifact_id: str) -> render.Link | None:
        key = self.msg_key(artifact_id)
        return ("Email", f"https://mail.google.com/mail/u/0/#all/{key}") if key else None

    def post(self, action: dict[str, Any], resolve: render.Resolve | None = None) -> str | None:
        key = self.msg_key(action.get("target_id") or "")
        if key is None:
            return None
        orig = self._req(
            "GET",
            f"/gmail/v1/users/me/messages/{key}",
            params={"format": "metadata", "metadataHeaders": ["From", "Subject", "Message-ID"]},
        )
        payload = orig["payload"]
        to = parseaddr(header(payload, "From"))[1]
        if not to:
            raise ConnectorError(f"Gmail: message {key} has no From address to reply to")
        mail = EmailMessage()
        mail["To"] = to
        subject = header(payload, "Subject")
        mail["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
        if mid := header(payload, "Message-ID"):
            mail["In-Reply-To"] = mid
            mail["References"] = mid
        mail.set_content(render.markdown(action, resolve or self.link))
        raw = base64.urlsafe_b64encode(mail.as_bytes()).decode("ascii")
        sent = self._req(
            "POST",
            "/gmail/v1/users/me/messages/send",
            json={"raw": raw, "threadId": orig.get("threadId")},
        )
        return f"https://mail.google.com/mail/u/0/#all/{sent['id']}"
