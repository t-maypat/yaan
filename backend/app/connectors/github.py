"""GitHub connector (17.4, D073-D074): pull requests in, comments out. Polling the REST API,
so nothing needs a webhook URL.

In: the sandbox repo's pull requests changed since the cursor, each with its reviews, become
live PR artifacts of the mapped product, in the same shape HERB's PRs have (state, merged,
reviews with reviewer ids), so the structural layer builds the PR's status chain and REVIEWED
edges, and the fold links FIXES when a ticket names the PR.
Out: a ticket comment on an issue a sandbox PR fixes, or any draft aimed at a sandbox PR,
becomes a comment on that PR.
"""

from datetime import datetime
from typing import Any

import httpx

from app import db
from app.connectors import base
from app.connectors.base import ConnectorError, Item, Maps
from app.ingest.normalize import Artifact

API = "https://api.github.com"


def ms(iso: str | None) -> int | None:
    return (
        int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000) if iso else None
    )


class GitHubConnector:
    name = "github"

    def __init__(
        self,
        token: str,
        repo: str,
        graph: str,
        maps: Maps | None = None,
        client: httpx.Client | None = None,
    ):
        self.repo, self.graph = repo, graph
        self.maps = maps or base.maps()
        self.http = client or httpx.Client(
            base_url=API,
            timeout=20,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    def _get(self, path: str, **params: Any) -> Any:
        r = self.http.get(path, params=params)
        if r.status_code >= 400:  # loud, with GitHub's message
            raise ConnectorError(
                f"GitHub GET {path}: {r.status_code} {r.json().get('message', '')}"
            )
        return r.json()

    def pr_id(self, number: int) -> str:
        return base.synthetic_id("pr", "github", f"{self.repo}-{number}")

    def fetch(self, since_ms: int) -> list[Item]:
        items = []
        page = 1
        while True:
            prs = self._get(
                f"/repos/{self.repo}/pulls",
                state="all",
                sort="updated",
                direction="desc",
                per_page=50,
                page=page,
            )
            fresh = [p for p in prs if (ms(p["updated_at"]) or 0) > since_ms]
            for p in fresh:
                reviews = self._get(f"/repos/{self.repo}/pulls/{p['number']}/reviews", per_page=100)
                items.append(self.normalize(p, reviews))
            if len(fresh) < len(prs) or len(prs) < 50:  # sorted by update: the rest is older
                return items
            page += 1

    def normalize(self, pr: dict[str, Any], reviews: list[dict[str, Any]]) -> Item:
        body = pr.get("body") or ""
        login = (pr.get("user") or {}).get("login")
        author = self.maps.person("github", login, body)
        created = ms(pr["created_at"]) or 0
        a = Artifact(
            id=self.pr_id(pr["number"]),
            kind="pr",
            product_hint=self.maps.product,
            ts_ms=created,
            ts_iso=pr["created_at"].replace("+00:00", "Z"),
            author_eid=author,
            title=pr["title"],
            text=body,
            url=pr["html_url"],
            raw_ref=f"github:{self.repo}#{pr['number']}",
            meta={
                "repo": self.repo,
                "number": pr["number"],
                "state": pr["state"],
                "merged": bool(pr.get("merged_at")),
                "login": author,
                "synthetic": True,
                "source": "github",
                "reviews": [
                    {
                        "state": r["state"],
                        "login": (r.get("user") or {}).get("login"),
                        "reviewer_eid": self.maps.person(
                            "github", (r.get("user") or {}).get("login"), r.get("body") or ""
                        ),
                        "comment": r.get("body") or "",
                        "ts_ms": ms(r.get("submitted_at")),
                        "ts_iso": r.get("submitted_at"),
                    }
                    for r in reviews
                    if r.get("state") in ("APPROVED", "CHANGES_REQUESTED", "COMMENTED")
                ],
            },
        )
        return Item(a, updated_ms=ms(pr["updated_at"]) or created)

    def post(self, action: dict[str, Any]) -> str | None:
        """A comment on the sandbox PR the action aims at, or on the sandbox PR that fixes the
        issue a ticket comment is about."""
        target = action.get("target_id") or ""
        prefix = self.pr_id(0)[: -len("0")]
        numbers: list[int] = []
        if target.startswith(prefix):
            numbers = [int(target.removeprefix(prefix))]
        elif action["kind"] == "ticket_comment" and target.startswith("iss:"):
            rows = db.run("issue_fix_prs", {"issue_id": target, "limit": 5}, graph=self.graph).rows
            numbers = [
                int(r["id"].removeprefix(prefix)) for r in rows if r["id"].startswith(prefix)
            ]
        if not numbers:
            return None
        refs = []
        for n in numbers:
            r = self.http.post(
                f"/repos/{self.repo}/issues/{n}/comments", json={"body": action["draft"]}
            )
            if r.status_code >= 400:
                raise ConnectorError(
                    f"GitHub comment on #{n}: {r.status_code} {r.json().get('message', '')}"
                )
            refs.append(r.json()["html_url"])
        return " ".join(refs)
