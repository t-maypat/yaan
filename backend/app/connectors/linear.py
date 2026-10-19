"""Linear connector (D090, EXPERIMENTAL: not run against a live API): issues in, comments out.

Linear states a ticket's facts outright, so like Jira each issue becomes messages in the bug
channel `connectors.yaml` maps (`linear.channel`), with deterministic events and no LLM:
- the issue's creation -> `issue_reported` (title, customer ids in the description, assignee);
- a state of type completed -> `issue_resolved`; canceled or duplicate -> `issue_dismissed`
  (stamped with the issue's completedAt / canceledAt; Linear's issue history, which would give
  who moved it and every reopening, is not read);
- a comment -> a message the extractor reads like any other.
Out: a ticket comment on an issue a Linear issue reported, or any draft aimed at one of its
messages, becomes a comment on the issue (rendered as Markdown, which Linear comments take).

Auth: `LINEAR_API_KEY`, a personal API key, sent as `Authorization: <key>` with no "Bearer".
`LINEAR_TEAM` is the team key (the "ENG" of ENG-12) that scopes what is read.

Docs (retrieved 2026-10-18):
- https://linear.app/developers/graphql (endpoint, Authorization header)
- https://linear.app/developers/pagination (first / after, pageInfo { hasNextPage endCursor })
- https://linear.app/developers/filtering (`gt` on date fields)
- Linear's published schema, https://github.com/linear/linear/blob/master/packages/sdk/src/schema.graphql
  (Issue and Comment fields, IssueFilter.updatedAt, CommentCreateInput.issueId accepting an
  issue identifier like "LIN-123", CommentPayload, WorkflowState.type)
"""

import re
from typing import Any

import httpx

from app import db
from app.connectors import base, render
from app.connectors.base import ConnectorError, Item, Maps
from app.connectors.experimental import HttpConnector, to_iso, to_ms

API = "https://api.linear.app"
CUSTOMER = re.compile(r"\bCUST-\d{4}\b")
DISMISSED = {"canceled", "duplicate"}  # WorkflowState.type values besides "completed"

ISSUES = """
query Issues($filter: IssueFilter, $after: String) {
  issues(first: 50, after: $after, orderBy: updatedAt, filter: $filter) {
    nodes {
      id identifier title description url createdAt updatedAt completedAt canceledAt
      creator { id }
      assignee { id }
      state { name type }
      comments(first: 100) { nodes { id body createdAt updatedAt user { id } } }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""
VIEWER = "query { viewer { id } }"
COMMENT = """
mutation CommentCreate($input: CommentCreateInput!) {
  commentCreate(input: $input) { success comment { id url } }
}
"""
ID = re.compile(r"([A-Z][A-Z0-9]*-\d+)-")  # the issue key after the id prefix


class LinearConnector(HttpConnector):
    name = "linear"
    label = "Linear"

    def __init__(
        self,
        api_key: str,
        team: str,
        graph: str,
        maps: Maps | None = None,
        client: httpx.Client | None = None,
    ):
        self.team = team
        self._own: str | None = None
        super().__init__(
            graph,
            maps,
            client
            or httpx.Client(
                base_url=API,
                timeout=20,
                headers={"Authorization": api_key, "Content-Type": "application/json"},
            ),
        )

    def _gql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        out = self._req("POST", "/graphql", json={"query": query, "variables": variables or {}})
        if out.get("errors"):  # GraphQL reports failures with status 200
            raise ConnectorError(
                f"Linear GraphQL: {out['errors'][0].get('message', out['errors'])}"
            )
        return out["data"]

    def who(self, account: str | None, text: str = "") -> str:
        """The person a Linear user plays; "self" in the map is the key's own user."""
        own = None
        if "self" in (self.maps.source("linear").get("people") or {}):
            if self._own is None:
                self._own = self._gql(VIEWER)["viewer"]["id"]
            own = self._own
        return self.maps.person("linear", account, text, own)

    def ident_of(self, artifact_id: str) -> str | None:
        prefix = base.id_prefix("msg", "linear")
        m = ID.match(artifact_id.removeprefix(prefix)) if artifact_id.startswith(prefix) else None
        return m.group(1) if m else None

    # --- reading ---------------------------------------------------------------------------
    def fetch(self, since_ms: int) -> list[Item]:
        channel_name = self.channel_name()
        flt: dict[str, Any] = {"team": {"key": {"eq": self.team}}}
        if since_ms:
            flt["updatedAt"] = {"gt": to_iso(since_ms)}
        items: list[Item] = []
        after = None
        while True:
            page = self._gql(ISSUES, {"filter": flt, "after": after})["issues"]
            for issue in page["nodes"]:
                items.extend(self.normalize(issue, channel_name))
            if not page["pageInfo"]["hasNextPage"]:
                return items
            after = page["pageInfo"]["endCursor"]

    def normalize(self, issue: dict[str, Any], channel_name: str) -> list[Item]:
        key, title = issue["identifier"], issue["title"]
        description = (issue.get("description") or "").strip()
        reporter = self.who((issue.get("creator") or {}).get("id"), description)
        assignee = self.who(issue["assignee"]["id"]) if issue.get("assignee") else None
        customers = CUSTOMER.findall(description)
        created = to_ms(issue["createdAt"])
        head = f"Linear {key} '{title}'"
        text = (
            f"{description}\n{head} reported"
            + (f" for {', '.join(customers)}" if customers else "")
            + (f", assigned to @{assignee}." if assignee else ".")
        )
        a = self.message(f"{key}-created", created, reporter, text, channel_name, linear_key=key)
        quote = text[text.index(head) :].split("\n")[0]
        events = [
            base.structured_event(
                "issue_reported",
                a,
                quote,
                reporter,
                issue=title,
                customer=", ".join(customers) or None,
                person=assignee,
            )
        ]
        items = [Item(a, updated_ms=created, events=events)]

        state = issue.get("state") or {}
        kind, stamp = {
            "completed": ("issue_resolved", issue.get("completedAt")),
            "canceled": ("issue_dismissed", issue.get("canceledAt")),
            "duplicate": ("issue_dismissed", issue.get("canceledAt")),
        }.get(state.get("type"), (None, None))
        if kind and stamp:
            who = assignee or reporter
            line = f"Linear {key} '{title}' moved to {state['name']}"
            b = self.message(
                f"{key}-state",
                to_ms(stamp),
                who,
                f"{base.tag()}{line} by @{who}.",
                channel_name,
                linear_key=key,
            )
            items.append(
                Item(
                    b,
                    updated_ms=to_ms(stamp),
                    events=[base.structured_event(kind, b, line, who, issue=title)],
                )
            )
        for c in issue["comments"]["nodes"]:
            body = (c.get("body") or "").strip()
            who = self.who((c.get("user") or {}).get("id"), body)
            items.append(
                Item(
                    self.message(
                        f"{key}-comment-{c['id']}",
                        to_ms(c["createdAt"]),
                        who,
                        f"{body}\n(on Linear {key} '{title}')",
                        channel_name,
                        linear_key=key,
                    ),
                    updated_ms=to_ms(c["updatedAt"]),
                )
            )
        return items

    # --- writing ---------------------------------------------------------------------------
    def link(self, artifact_id: str) -> render.Link | None:
        """A Linear-held artifact's label (the issue's url isn't derivable from its key)."""
        key = self.ident_of(artifact_id)
        return (f"Linear {key}", None) if key else None

    def post(self, action: dict[str, Any], resolve: render.Resolve | None = None) -> str | None:
        target = action.get("target_id") or ""
        key = self.ident_of(target)
        if key is None and action["kind"] == "ticket_comment" and target.startswith("iss:"):
            rows = db.run(
                "issue_report_ids", {"issue_id": target, "limit": 5}, graph=self.graph
            ).rows
            key = next((k for r in rows if (k := self.ident_of(r["id"]))), None)
        if key is None:
            return None
        body = render.markdown(action, resolve or self.link)
        # CommentCreateInput.issueId takes a UUID or an identifier like "ENG-12"
        out = self._gql(COMMENT, {"input": {"issueId": key, "body": body}})["commentCreate"]
        if not out["success"]:
            raise ConnectorError(f"Linear: commentCreate on {key} reported success=false")
        return out["comment"]["url"]
