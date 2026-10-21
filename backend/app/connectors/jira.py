"""Jira Cloud connector (17.5, D073-D074): tickets in, comments out. REST v3, polling.

HERB has no tracker, so Lore extracts issue states from chat with an LLM. A tracker states
them outright: each ticket becomes messages in the bug channel the project maps to, with
deterministic events, so no LLM reads them (the live path still checks every quote):
- the ticket's creation -> `issue_reported` (title, component, customer, assignee);
- a status change -> `issue_resolved` (to Done, Resolved, Closed…), `issue_dismissed` (to
  Won't Do, Declined…), `issue_reopened` (out of either), decided by whoever changed it;
- a comment -> a message the extractor reads like any other ("it broke again").
Out: a ticket comment on an issue a Jira ticket reported, or any draft aimed at a ticket's
message, becomes a comment on the ticket.
"""

import re
import time
from datetime import datetime
from typing import Any

import httpx

from app import db
from app.connectors import base, render
from app.connectors.base import ConnectorError, Item, Maps
from app.ingest.normalize import Artifact

DONE = {"done", "resolved", "closed", "fixed", "complete", "completed"}
DISMISSED = {
    "won't do",
    "won't fix",
    "wontfix",
    "declined",
    "rejected",
    "cancelled",
    "canceled",
    "not a bug",
    "invalid",
    "duplicate",
}
CUSTOMER = re.compile(r"\bCUST-\d{4}\b")
PR_URL = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+")


def ms(iso: str) -> int:
    # Jira: 2027-03-13T00:53:20.000+0000
    return int(datetime.strptime(iso[:19] + iso[23:], "%Y-%m-%dT%H:%M:%S%z").timestamp() * 1000)


def iso(stamp: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stamp / 1000))


def adf_text(node: Any) -> str:
    """Atlassian Document Format to plain text: paragraphs on their own lines."""
    if isinstance(node, str) or node is None:
        return node or ""
    if node.get("type") == "text":
        return node.get("text", "")
    inner = "".join(adf_text(c) for c in node.get("content") or [])
    return inner + ("\n" if node.get("type") in ("paragraph", "heading", "listItem") else "")


def adf(text: str) -> dict[str, Any]:
    paragraphs = [p for p in text.split("\n") if p.strip()] or [text]
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": p}]} for p in paragraphs
        ],
    }


def change_kind(old: str | None, new: str | None) -> str | None:
    """The issue event a status change states, if any."""
    o, n = (old or "").strip().lower(), (new or "").strip().lower()
    if n in DONE:
        return "issue_resolved"
    if n in DISMISSED:
        return "issue_dismissed"
    if o in DONE | DISMISSED:
        return "issue_reopened"
    return None


class JiraConnector:
    name = "jira"

    def __init__(
        self,
        base_url: str,
        email: str,
        token: str,
        project: str,
        graph: str,
        maps: Maps | None = None,
        client: httpx.Client | None = None,
    ):
        self.project, self.graph = project, graph
        self.maps = maps or base.maps()
        self.channel = self.maps.source("jira").get("channel")
        self._own: str | None = None
        self.http = client or httpx.Client(
            base_url=base_url.rstrip("/"),
            auth=(email, token),
            timeout=20,
            headers={"Accept": "application/json"},
        )

    def _req(self, method: str, path: str, **kw: Any) -> Any:
        r = self.http.request(method, path, **kw)
        if r.status_code >= 400:
            detail = r.text[:300]
            raise ConnectorError(f"Jira {method} {path}: {r.status_code} {detail}")
        return r.json()

    def who(self, account: str | None, text: str = "", name: str | None = None) -> str:
        """The person a Jira account plays; "self" in the map is the token's own account."""
        own = None
        if "self" in (self.maps.source("jira").get("people") or {}):
            if self._own is None:
                self._own = self._req("GET", "/rest/api/3/myself")["accountId"]
            own = self._own
        return self.maps.person("jira", account, text, own, name=name)

    def msg_id(self, key: str, part: str) -> str:
        return base.synthetic_id("msg", "jira", f"{key}-{part}")

    def key_of(self, artifact_id: str) -> str | None:
        prefix = base.synthetic_id("msg", "jira", "x")[:-1]
        if not artifact_id.startswith(prefix):
            return None
        project, number = artifact_id.removeprefix(prefix).split("-")[:2]
        if project != self.project:  # another project's ticket: its own connector posts (D118)
            return None
        return f"{project}-{number}"

    # --- reading ---------------------------------------------------------------------------
    def fetch(self, since_ms: int) -> list[Item]:
        if not self.channel:
            raise ConnectorError("connectors.yaml: jira.channel (the bug channel tickets land in)")
        rows = db.run("get_channel", {"id": self.channel}, graph=self.graph).rows
        if not rows:
            raise ConnectorError(f"jira.channel {self.channel!r} isn't in the graph")
        channel_name = rows[0]["name"]
        jql = f"project = {self.project}"
        if since_ms:  # relative minutes: no dependence on the account's time zone
            minutes = int((time.time() * 1000 - since_ms) / 60000) + 60
            jql += f' AND updated >= "-{minutes}m"'
        items: list[Item] = []
        token = None
        while True:
            params = {
                "jql": jql + " ORDER BY created ASC",
                "fields": "summary,description,status,assignee,reporter,components,created,updated",
                "maxResults": 50,
            }
            if token:
                params["nextPageToken"] = token
            page = self._req("GET", "/rest/api/3/search/jql", params=params)
            for issue in page.get("issues", []):
                items.extend(self.normalize(issue, channel_name))
            token = page.get("nextPageToken")
            if page.get("isLast", True) or not token:
                return items

    def _paged(self, path: str) -> list[dict[str, Any]]:
        out, start = [], 0
        while True:
            page = self._req("GET", path, params={"startAt": start, "maxResults": 100})
            values = page.get("values") or page.get("comments") or []
            out.extend(values)
            start += len(values)
            if not values or start >= page.get("total", start):
                return out

    def _artifact(
        self, key: str, part: str, stamp: int, author: str, text: str, channel_name: str
    ) -> Artifact:
        return Artifact(
            id=self.msg_id(key, part),
            kind="message",
            product_hint=self.maps.product_of("jira", self.channel),
            channel_id=self.channel,
            ts_ms=stamp,
            ts_iso=iso(stamp),
            author_eid=author,
            text=text,
            raw_ref=f"jira:{key}:{part}",
            meta={
                "channel_name": channel_name,
                "sender": author,
                "synthetic": base.is_demo(),
                "source": "jira",
                "jira_key": key,
            },
        )

    def normalize(self, issue: dict[str, Any], channel_name: str) -> list[Item]:
        key, f = issue["key"], issue["fields"]
        title = f["summary"]
        description = adf_text(f.get("description")).strip()
        rep = f.get("reporter") or {}
        reporter = self.who(rep.get("accountId"), description, rep.get("displayName"))
        assignee = (
            self.who(f["assignee"].get("accountId"), name=f["assignee"].get("displayName"))
            if f.get("assignee")
            else None
        )
        component = (f.get("components") or [{}])[0].get("name") if f.get("components") else None
        customers = CUSTOMER.findall(description)
        created = ms(f["created"])
        head = f"Jira {key} '{title}'" + (f" (component: {component})" if component else "")
        text = (
            f"{description}\n{head} reported"
            + (f" for {', '.join(customers)}" if customers else "")
            + (f", assigned to @{assignee}." if assignee else ".")
        )
        a = self._artifact(key, "created", created, reporter, text, channel_name)
        quote = text[text.index(head) :].split("\n")[0]
        events = [
            base.structured_event(
                "issue_reported",
                a,
                quote,
                reporter,
                issue=title,
                component=component,
                customer=", ".join(customers) or None,
                person=assignee,
            )
        ]
        items = [Item(a, updated_ms=created, events=events)]
        comments = self._paged(f"/rest/api/3/issue/{key}/comment")
        # PR links the ticket itself gives, in time order: a resolution names the latest one
        mentions = [(created, u) for u in PR_URL.findall(description)]
        for c in comments:
            mentions += [(ms(c["created"]), u) for u in PR_URL.findall(adf_text(c.get("body")))]
        mentions.sort()
        for h in self._paged(f"/rest/api/3/issue/{key}/changelog"):
            for change in h.get("items") or []:
                kind = change_kind(change.get("fromString"), change.get("toString"))
                if change.get("field") != "status" or not kind:
                    continue
                by = h.get("author") or {}
                who = self.who(by.get("accountId"), name=by.get("displayName"))
                stamp = ms(h["created"])
                named = [u for t, u in mentions if t <= stamp]
                fix = named[-1:] if kind == "issue_resolved" else []
                line = (
                    f"Jira {key} '{title}' moved from {change.get('fromString')} to "
                    f"{change.get('toString')}"
                )
                body = f"{base.tag()}{line} by @{who}" + (f", fixed by {fix[0]}." if fix else ".")
                b = self._artifact(key, f"change-{h['id']}", stamp, who, body, channel_name)
                event = base.structured_event(
                    kind, b, line, who, issue=title, pr=fix[0] if fix else None
                )
                items.append(Item(b, updated_ms=stamp, events=[event]))
        for c in comments:
            body = adf_text(c.get("body")).strip()
            by = c.get("author") or {}
            who = self.who(by.get("accountId"), body, by.get("displayName"))
            stamp = ms(c["created"])
            text = f"{body}\n(on Jira {key} '{title}')"
            items.append(
                Item(
                    self._artifact(key, f"comment-{c['id']}", stamp, who, text, channel_name),
                    updated_ms=ms(c["updated"]),
                )
            )
        return items

    # --- writing ---------------------------------------------------------------------------
    def link(self, artifact_id: str) -> render.Link | None:
        """A Jira-held artifact's label and the ticket's url."""
        key = self.key_of(artifact_id)
        return (f"Jira {key}", f"{self.http.base_url}/browse/{key}") if key else None

    def post(self, action: dict[str, Any], resolve: render.Resolve | None = None) -> str | None:
        target = action.get("target_id") or ""
        key = self.key_of(target)
        if key is None and action["kind"] == "ticket_comment" and target.startswith("iss:"):
            rows = db.run(
                "issue_report_ids", {"issue_id": target, "limit": 5}, graph=self.graph
            ).rows
            key = next((k for r in rows if (k := self.key_of(r["id"]))), None)
        if key is None:
            return None
        mark = render.marker(action)
        for c in self._paged(f"/rest/api/3/issue/{key}/comment"):  # sent before? (D110)
            if mark in adf_text(c.get("body")):
                return f"{self.http.base_url}/browse/{key}?focusedCommentId={c['id']}"
        out = self._req(
            "POST",
            f"/rest/api/3/issue/{key}/comment",
            json={"body": render.jira_adf(action, resolve or self.link)},
        )
        return f"{self.http.base_url}/browse/{key}?focusedCommentId={out['id']}"
