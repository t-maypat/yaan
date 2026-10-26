"""GitLab connector (D090; checked against the live API, D145): merge requests in,
notes out. REST v4, polling, like the GitHub connector.

In: the project's merge requests updated since the cursor become PR artifacts in the shape
HERB's PRs have (state, merged, reviews). A merge request's human notes (system notes skipped)
are its reviews, all `COMMENTED`: GitLab's approvals are a separate endpoint this scaffold
doesn't read, so no REVIEWED edge carries an approval.
Out: a ticket comment on an issue a sandbox merge request fixes, or any draft aimed at a
merge request, becomes a note on it (Markdown).

Auth: `GITLAB_TOKEN` (a personal / project access token, sent as `PRIVATE-TOKEN`),
`GITLAB_PROJECT` (numeric id or `group/project` path), optional `GITLAB_BASE_URL`
(default https://gitlab.com, for self-managed).

Docs (retrieved 2026-10-18):
- https://docs.gitlab.com/api/merge_requests/ (list project merge requests: updated_after,
  state, order_by; fields iid, title, description, state, created_at, updated_at, merged_at,
  web_url, author)
- https://docs.gitlab.com/api/notes/ (list and create merge request notes; the note object)
- https://docs.gitlab.com/api/rest/ (PRIVATE-TOKEN header, URL-encoded project path, paging)
"""

from typing import Any
from urllib.parse import quote

import httpx

from app import db
from app.connectors import base, render
from app.connectors.base import Item, Maps
from app.connectors.experimental import HttpConnector, to_iso, to_ms
from app.ingest.normalize import Artifact

DEFAULT_URL = "https://gitlab.com"


class GitLabConnector(HttpConnector):
    name = "gitlab"
    label = "GitLab"

    def __init__(
        self,
        token: str,
        project: str,
        graph: str,
        maps: Maps | None = None,
        client: httpx.Client | None = None,
        base_url: str | None = None,
    ):
        self.project = project
        self.root = (base_url or DEFAULT_URL).rstrip("/")
        self._own: str | None = None
        super().__init__(
            graph,
            maps,
            client
            or httpx.Client(
                base_url=f"{self.root}/api/v4",
                timeout=20,
                headers={"PRIVATE-TOKEN": token},
            ),
        )
        self.api = f"/projects/{quote(project, safe='')}"

    def who(self, username: str | None, text: str = "") -> str:
        """The person a GitLab username plays; "self" in the map is the token's own user."""
        own = None
        if "self" in (self.maps.source("gitlab").get("people") or {}):
            if self._own is None:
                self._own = self._req("GET", "/user")["username"]
            own = self._own
        return self.maps.person("gitlab", username, text, own)

    def pr_id(self, iid: int) -> str:
        return base.synthetic_id("pr", "gitlab", f"{self.project}-{iid}")

    def web(self, iid: int) -> str | None:
        """The merge request's address when the project is given as a path."""
        if self.project.isdigit():
            return None
        return f"{self.root}/{self.project}/-/merge_requests/{iid}"

    # --- reading ---------------------------------------------------------------------------
    def fetch(self, since_ms: int) -> list[Item]:
        items: list[Item] = []
        page = 1
        while True:
            params: dict[str, Any] = {
                "state": "all",
                "order_by": "updated_at",
                "sort": "desc",
                "per_page": 50,
                "page": page,
            }
            if since_ms:
                params["updated_after"] = to_iso(since_ms)
            mrs = self._req("GET", f"{self.api}/merge_requests", params=params)
            for mr in mrs:
                notes = self._req(
                    "GET",
                    f"{self.api}/merge_requests/{mr['iid']}/notes",
                    params={"sort": "asc", "order_by": "created_at", "per_page": 100},
                )
                items.append(self.normalize(mr, notes))
            if len(mrs) < 50:
                return items
            page += 1

    def normalize(self, mr: dict[str, Any], notes: list[dict[str, Any]]) -> Item:
        body = mr.get("description") or ""
        author = self.who((mr.get("author") or {}).get("username"), body)
        created = to_ms(mr["created_at"])
        merged = mr["state"] == "merged"
        a = Artifact(
            id=self.pr_id(mr["iid"]),
            kind="pr",
            product_hint=self.maps.product_of("gitlab"),
            ts_ms=created,
            ts_iso=mr["created_at"].replace("+00:00", "Z"),
            author_eid=author,
            title=mr["title"],
            text=body,
            url=mr.get("web_url"),
            raw_ref=f"gitlab:{self.project}!{mr['iid']}",
            meta={
                "repo": self.project,
                "number": mr["iid"],
                "state": "open" if mr["state"] == "opened" else "closed",
                "merged": merged,
                "login": author,
                "synthetic": base.is_demo(),
                "source": "gitlab",
                "reviews": [
                    {
                        "state": "COMMENTED",
                        "login": (n.get("author") or {}).get("username"),
                        "reviewer_eid": self.who(
                            (n.get("author") or {}).get("username"), n["body"]
                        ),
                        "comment": n["body"],
                        "ts_ms": to_ms(n["created_at"]),
                        "ts_iso": n["created_at"],
                    }
                    for n in notes
                    if not n.get("system")
                ],
            },
        )
        return Item(a, updated_ms=to_ms(mr["updated_at"]))

    # --- writing ---------------------------------------------------------------------------
    def link(self, artifact_id: str) -> render.Link | None:
        prefix = self.pr_id(0)[: -len("0")]
        n = artifact_id.removeprefix(prefix)
        if not artifact_id.startswith(prefix) or not n.isdigit():
            return None
        return f"MR !{n}", self.web(int(n))

    def post(self, action: dict[str, Any], resolve: render.Resolve | None = None) -> str | None:
        """A note on the merge request the action aims at, or on the merge requests that fix
        the issue a ticket comment is about."""
        target = action.get("target_id") or ""
        prefix = self.pr_id(0)[: -len("0")]
        numbers: list[int] = []
        if target.startswith(prefix) and target.removeprefix(prefix).isdigit():
            numbers = [int(target.removeprefix(prefix))]
        elif action["kind"] == "ticket_comment" and target.startswith("iss:"):
            rows = db.run("issue_fix_prs", {"issue_id": target, "limit": 5}, graph=self.graph).rows
            numbers = [
                int(r["id"].removeprefix(prefix))
                for r in rows
                if r["id"].startswith(prefix) and r["id"].removeprefix(prefix).isdigit()
            ]
        if not numbers:
            return None
        body = render.markdown(action, resolve or self.link)
        refs = []
        for n in numbers:
            note = self._req("POST", f"{self.api}/merge_requests/{n}/notes", json={"body": body})
            url = self.web(n)
            refs.append(
                f"{url}#note_{note['id']}"
                if url
                else f"{self.root}/api/v4{self.api}/merge_requests/{n}/notes/{note['id']}"
            )
        return " ".join(refs)
