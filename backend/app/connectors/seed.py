"""`lore connectors seed` (17.7): writes the labeled synthetic story into the sandboxes.

Act `setup` (before the demo):
1. Notion: the "CSV Export Spec" page, with Fiona Davis's review comment;
2. Jira: ticket "CSV export times out on large dashboards", reported as Ian Davis for CUST-0003,
   assigned to the token's own account (Hannah Garcia in connectors.yaml);
3. GitHub: a branch, a commit, the fix PR, a review comment as Bob Johnson, the merge;
4. Jira: a comment linking the PR, then the move to Done.
Act `regression` (the live moment): the customer re-report in #vizforce-bugs, written as Ian
Davis. Typing the same text in Slack yourself works too; `lore connectors run` picks it up.

Every item opens with "[synthetic demo" (rule 5), and personas are named in the label.
Each step prints what it created; a failing call stops the act loudly.
"""

import base64
import time
from collections.abc import Callable
from typing import Any

import httpx

from app.connectors.base import ConnectorError
from app.connectors.jira import DONE, adf

TITLE = "CSV export times out on large dashboards"
SPEC = "CSV Export Spec"
IAN = "[synthetic demo · as Ian Davis (eid_7017b913)]"
FIONA = "[synthetic demo · as Fiona Davis (eid_131494b8)]"
HANNAH = "[synthetic demo · as Hannah Garcia (eid_9e9883de)]"
BOB = "[synthetic demo · as Bob Johnson (eid_b7702c98)]"
REGRESSION = (
    f"{IAN} CUST-0003 has reopened '{TITLE}': the CSV export times out on large dashboards "
    "again since this morning's release."
)
FIX_CODE = '''"""[synthetic demo] Stream the CSV export in chunks: large dashboards no longer
time out."""


def stream_rows(rows, chunk=5000):
    for i in range(0, len(rows), chunk):
        yield rows[i : i + chunk]
'''


PR_BODY = (
    f"{HANNAH} Streams the CSV export in chunks of 5,000 rows so large dashboards no longer "
    "time out (CUST-0003)."
)


def check(r: httpx.Response, what: str) -> Any:
    if r.status_code >= 400:
        raise ConnectorError(f"seed: {what}: {r.status_code} {r.text[:300]}")
    return r.json() if r.content else {}


SPEC_TEXT = (
    "[synthetic demo] The CSV export streams dashboard rows in chunks of 5,000, so exports of "
    "large dashboards finish within the 30 s gateway limit."
)
SPEC_OWNERS = "Owner: Hannah Garcia. Reviewers: Fiona Davis, Bob Johnson."
FIONA_REVIEW = (
    f"{FIONA} Add a section on what happens when a chunk fails halfway, and a test with a "
    "100k-row dashboard."
)


def text(content: str) -> list[dict[str, Any]]:
    return [{"type": "text", "text": {"content": content}}]


def paragraph(content: str) -> dict[str, Any]:
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": text(content)}}


def notion_spec(http: httpx.Client, parent: str, say: Callable[[str], None]) -> str:
    body = {
        "parent": {"page_id": parent},
        "properties": {"title": {"title": text(SPEC)}},
        "children": [paragraph(SPEC_TEXT), paragraph(SPEC_OWNERS)],
    }
    page = check(http.post("/v1/pages", json=body), "Notion page")
    comment = {"parent": {"page_id": page["id"]}, "rich_text": text(FIONA_REVIEW)}
    check(http.post("/v1/comments", json=comment), "Notion comment")
    say(f"notion: page {page.get('url', page['id'])} + Fiona Davis's review comment")
    return page["id"]


def jira_ticket(http: httpx.Client, project: str, say: Callable[[str], None]) -> str:
    me = check(http.get("/rest/api/3/myself"), "Jira myself")["accountId"]
    fields: dict[str, Any] = {
        "project": {"key": project},
        "summary": TITLE,
        "issuetype": {"name": "Task"},
        "assignee": {"accountId": me},
        "description": adf(
            f"{IAN} CUST-0003 reports that the CSV export times out on dashboards over "
            "50k rows; their weekly report fails."
        ),
    }
    comp = http.post(
        "/rest/api/3/component", json={"name": "Data Export Module", "project": project}
    )
    if comp.status_code < 400:
        fields["components"] = [{"name": "Data Export Module"}]
    key = check(http.post("/rest/api/3/issue", json={"fields": fields}), "Jira issue")["key"]
    say(f"jira: {key} '{TITLE}' reported as Ian Davis, assigned to you (Hannah Garcia)")
    return key


def jira_done(http: httpx.Client, key: str, pr_url: str, say: Callable[[str], None]) -> None:
    check(
        http.post(
            f"/rest/api/3/issue/{key}/comment",
            json={"body": adf(f"{HANNAH} Fix: {pr_url} streams the export in chunks.")},
        ),
        "Jira comment",
    )
    transitions = check(http.get(f"/rest/api/3/issue/{key}/transitions"), "Jira transitions")
    done = next((t for t in transitions["transitions"] if t["to"]["name"].lower() in DONE), None)
    if done is None:
        names = [t["to"]["name"] for t in transitions["transitions"]]
        raise ConnectorError(f"seed: no transition to a done status in {names}")
    check(
        http.post(f"/rest/api/3/issue/{key}/transitions", json={"transition": {"id": done["id"]}}),
        "Jira transition",
    )
    say(f"jira: {key} comment with the PR link, then moved to {done['to']['name']}")


def github_fix(http: httpx.Client, repo: str, say: Callable[[str], None]) -> str:
    base = check(http.get(f"/repos/{repo}"), "GitHub repo")["default_branch"]
    sha = check(http.get(f"/repos/{repo}/git/ref/heads/{base}"), "GitHub ref")["object"]["sha"]
    # One branch per take, so the story can be seeded again after `lore demo reset`.
    branch = f"lore-demo/csv-export-chunks-{int(time.time())}"
    check(
        http.post(f"/repos/{repo}/git/refs", json={"ref": f"refs/heads/{branch}", "sha": sha}),
        "GitHub branch",
    )
    path = f"/repos/{repo}/contents/export/csv_stream.py"
    commit = {
        "message": "[synthetic demo] Stream the CSV export in chunks",
        "content": base64.b64encode(FIX_CODE.encode()).decode(),
        "branch": branch,
    }
    # An earlier take merged the file: GitHub updates a file only when told its current sha.
    old = http.get(path, params={"ref": branch})
    if old.status_code == 200 and old.json().get("sha"):
        commit["sha"] = old.json()["sha"]
    check(http.put(path, json=commit), "GitHub commit")
    pr = check(
        http.post(
            f"/repos/{repo}/pulls",
            json={
                "title": "Fix CSV export timeout on large dashboards",
                "head": branch,
                "base": base,
                "body": PR_BODY,
            },
        ),
        "GitHub PR",
    )
    check(
        http.post(
            f"/repos/{repo}/pulls/{pr['number']}/reviews",
            json={
                "event": "COMMENT",
                "body": f"{BOB} LGTM: chunking keeps memory flat and fixes the timeout.",
            },
        ),
        "GitHub review",
    )
    check(
        http.put(f"/repos/{repo}/pulls/{pr['number']}/merge", json={"merge_method": "squash"}),
        "GitHub merge",
    )
    say(f"github: PR #{pr['number']} {pr['html_url']} reviewed as Bob Johnson and merged")
    return pr["html_url"]


def setup(
    jira: httpx.Client,
    project: str,
    github: httpx.Client,
    repo: str,
    notion: httpx.Client | None,
    notion_parent: str | None,
    say: Callable[[str], None] = print,
) -> dict:
    out: dict[str, Any] = {}
    if notion is not None and notion_parent:
        out["notion_page"] = notion_spec(notion, notion_parent, say)
    out["jira_key"] = jira_ticket(jira, project, say)
    out["pr_url"] = github_fix(github, repo, say)
    jira_done(jira, out["jira_key"], out["pr_url"], say)
    return out


def regression(slack_client: Any, channel_id: str, say: Callable[[str], None] = print) -> str:
    """The re-report, shown under the persona's name when the app has chat:write.customize
    (the label stays in the text: it marks the item synthetic and names who it's written as)."""
    from slack_sdk.errors import SlackApiError

    try:
        r = slack_client.chat_postMessage(
            channel=channel_id,
            text=REGRESSION,
            username="Ian Davis · synthetic demo",
            icon_emoji=":bust_in_silhouette:",
        )
    except SlackApiError as e:
        if (e.response or {}).get("error") != "missing_scope":
            raise
        r = slack_client.chat_postMessage(channel=channel_id, text=REGRESSION)
    say(f"slack: posted the customer re-report in #{channel_id} (ts {r['ts']})")
    return r["ts"]
