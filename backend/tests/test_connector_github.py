"""The GitHub connector (17.4) against httpx's mock transport: no network, no token. Payload
shapes follow GitHub's REST API (pulls, pull reviews, issue comments)."""

import json
from pathlib import Path

import httpx
import pytest

from app import db
from app.connectors import github, runner
from app.connectors.base import Maps

MAPS = Maps(
    product="VizForce",
    default_person="eid_bd791efc",
    sources={"github": {"people": {"hannah-gh": "eid_9e9883de"}}},
)
REPO = "acme/lore-demo-sandbox"
PULLS = [
    {
        "number": 2,
        "title": "Revert PR# 1: Fix CSV export timeout",
        "body": "[synthetic demo] Reverting while we investigate.",
        "state": "open",
        "merged_at": None,
        "created_at": "2027-03-15T10:00:00Z",
        "updated_at": "2027-03-15T10:00:00Z",
        "html_url": f"https://github.com/{REPO}/pull/2",
        "user": {"login": "someone"},
    },
    {
        "number": 1,
        "title": "Fix CSV export timeout",
        "body": "[synthetic demo] Streams the export in chunks.",
        "state": "closed",
        "merged_at": "2027-03-13T02:00:00Z",
        "created_at": "2027-03-13T00:53:20Z",
        "updated_at": "2027-03-13T02:00:00Z",
        "html_url": f"https://github.com/{REPO}/pull/1",
        "user": {"login": "hannah-gh"},
    },
]
REVIEWS = {
    1: [
        {
            "state": "APPROVED",
            "body": "[synthetic demo · as Bob Johnson (eid_b7702c98)] LGTM",
            "submitted_at": "2027-03-13T01:00:00Z",
            "user": {"login": "hannah-gh"},
        },
        {"state": "PENDING", "body": "", "submitted_at": None, "user": {"login": "x"}},
    ],
    2: [],
}


def handler(posted):
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path == f"/repos/{REPO}/pulls":
            return httpx.Response(200, json=PULLS)
        if request.method == "GET" and path.endswith("/reviews"):
            return httpx.Response(200, json=REVIEWS[int(path.split("/")[-2])])
        if request.method == "POST" and path.endswith("/comments"):
            n = int(path.split("/")[-2])
            posted.append((n, json.loads(request.content)["body"]))
            return httpx.Response(201, json={"html_url": f"https://github.com/{REPO}/pull/{n}#c1"})
        return httpx.Response(404, json={"message": "Not Found"})

    return handle


@pytest.fixture
def conn():
    posted: list = []
    client = httpx.Client(base_url=github.API, transport=httpx.MockTransport(handler(posted)))
    c = github.GitHubConnector("t", REPO, "lore_demo", MAPS, client=client)
    c.posted = posted
    return c


def test_prs_come_in_with_herbs_shape_and_reviews(conn):
    items = {i.artifact.meta["number"]: i for i in conn.fetch(0)}
    pr = items[1].artifact
    assert pr.id == "pr:synthetic-github-acme-lore-demo-sandbox-1" and pr.kind == "pr"
    assert pr.author_eid == "eid_9e9883de" and pr.url.endswith("/pull/1")
    assert (pr.meta["state"], pr.meta["merged"]) == ("closed", True)
    assert pr.meta["reviews"] == [
        {
            "state": "APPROVED",
            "login": "hannah-gh",
            "reviewer_eid": "eid_b7702c98",  # the persona in the review's label
            "comment": "[synthetic demo · as Bob Johnson (eid_b7702c98)] LGTM",
            "ts_ms": github.ms("2027-03-13T01:00:00Z"),
            "ts_iso": "2027-03-13T01:00:00Z",
        }
    ]
    assert items[1].updated_ms == github.ms("2027-03-13T02:00:00Z")
    assert items[2].artifact.title.startswith("Revert PR# 1")  # the linker's REVERTS pattern
    assert [i.artifact.meta["number"] for i in conn.fetch(github.ms("2027-03-14T00:00:00Z"))] == [
        2
    ]  # since cursor


def test_comments_go_on_the_targeted_pr_or_the_issues_fix_pr(conn, monkeypatch):
    rows = [{"id": conn.pr_id(1), "url": "u"}, {"id": "pr:github_com_salesforce_x_pull_3"}]
    monkeypatch.setattr(github.db, "run", lambda name, params, **k: type("R", (), {"rows": rows}))
    ref = conn.post(
        {"id": "a1", "kind": "ticket_comment", "target_id": "iss:VizForce:1", "draft": "Regressed."}
    )
    assert ref == f"https://github.com/{REPO}/pull/1#c1" and conn.posted[0][0] == 1
    assert (
        conn.posted[0][1].startswith("### Lore Sentinel · Follow-up")
        and "- Regressed." in (conn.posted[0][1])
    )
    conn.post({"id": "a2", "kind": "doc_fix", "target_id": conn.pr_id(2), "draft": "Note."})
    assert conn.posted[-1][0] == 2 and "Correction" in conn.posted[-1][1]
    assert conn.link(conn.pr_id(7)) == ("PR #7", f"https://github.com/{REPO}/pull/7")
    assert conn.link("pr:github_com_salesforce_x_pull_3") is None  # HERB's: no sandbox url
    assert (
        conn.post({"id": "a3", "kind": "notify_owner", "target_id": "eid_1", "draft": "x"}) is None
    )


def test_a_ticket_comment_goes_to_every_tool_that_holds_it(monkeypatch):
    class Tool:
        def __init__(self, name):
            self.name = name

        def post(self, action, resolve=None):
            return f"https://{self.name}/{action['id']}"

    marked = []

    def run(name, params=None, **k):
        if name == "actions_to_send":
            return type(
                "R",
                (),
                {
                    "rows": [
                        {"id": "a1", "kind": "ticket_comment", "target_id": "iss:1"},
                        {"id": "a2", "kind": "notify_owner", "target_id": "eid_1"},
                    ]
                },
            )
        marked.extend(params["rows"])
        return type("R", (), {"rows": []})

    monkeypatch.setattr(db, "run", run)
    sent, _ = runner.deliver([Tool("jira"), Tool("github")], "lore_demo")
    assert [(s["id"], s["via"]) for s in sent] == [("a1", "jira,github"), ("a2", "jira")]
    assert sent[0]["ref"] == "https://jira/a1 https://github/a1"


@pytest.mark.db
def test_issue_fix_prs_finds_the_fixing_pr(empty_graph):
    text = (Path(__file__).parent / "fixtures" / "agent_company.cypher").read_text(encoding="utf-8")
    db.run_text(text, label="agent_company", graph=empty_graph, readonly=False)
    rows = db.run(
        "issue_fix_prs", {"issue_id": "iss:Widget:abc", "limit": 5}, graph=empty_graph
    ).rows
    assert [r["id"] for r in rows] == ["pr:w1"]


@pytest.mark.db
def test_issue_report_ids_lists_the_reporting_messages(empty_graph):
    text = (Path(__file__).parent / "fixtures" / "agent_company.cypher").read_text(encoding="utf-8")
    db.run_text(text, label="agent_company", graph=empty_graph, readonly=False)
    rows = db.run(
        "issue_report_ids", {"issue_id": "iss:Widget:abc", "limit": 5}, graph=empty_graph
    ).rows
    assert [r["id"] for r in rows] == ["msg:r1"]
