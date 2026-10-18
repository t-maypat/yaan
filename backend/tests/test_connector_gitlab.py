"""The GitLab connector (D090, experimental) against httpx's mock transport: no network, no
token. The note object is the docs' own example (docs.gitlab.com/api/notes); the merge request
uses the documented fields (iid, title, description, state, created_at, updated_at, web_url,
author)."""

import json

import httpx
import pytest

from app import db
from app.connectors import gitlab
from app.connectors.base import ConnectorError, Maps
from app.connectors.experimental import to_ms

MAPS = Maps(
    product="VizForce",
    default_person="eid_bd791efc",
    sources={"gitlab": {"people": {"hannah": "eid_9e9883de"}}},
)
PROJECT = "acme/lore-demo"
MR = {
    "id": 1,
    "iid": 11,
    "project_id": 5,
    "title": "Fix CSV export timeout",
    "description": "[synthetic demo] Streams the export in chunks.",
    "state": "merged",
    "created_at": "2027-03-13T00:53:20.000Z",
    "updated_at": "2027-03-13T02:00:00.000Z",
    "merged_at": "2027-03-13T02:00:00.000Z",
    "web_url": f"https://gitlab.com/{PROJECT}/-/merge_requests/11",
    "author": {"id": 1, "name": "Hannah", "username": "hannah"},
}
NOTES = [
    {  # the docs' example note, with a [synthetic demo] body
        "id": 301,
        "body": "[synthetic demo · as Bob Johnson (eid_b7702c98)] Comment for MR",
        "author": {"id": 1, "username": "pipin", "name": "Pip", "state": "active"},
        "created_at": "2013-10-02T08:57:14Z",
        "updated_at": "2013-10-02T08:57:14Z",
        "system": False,
        "noteable_id": 2,
        "noteable_type": "MergeRequest",
        "project_id": 5,
        "noteable_iid": 2,
        "resolvable": False,
        "confidential": False,
        "internal": False,
    },
    {
        "id": 302,
        "body": "approved this merge request",
        "system": True,
        "author": {},
        "created_at": "2013-10-02T09:00:00Z",
    },
]


@pytest.fixture
def conn():
    seen = {"get": [], "posted": []}

    def handle(request: httpx.Request) -> httpx.Response:
        raw = request.url.raw_path.decode()
        assert raw.startswith("/api/v4/projects/acme%2Flore-demo/")  # the path is URL-encoded
        if request.method == "GET" and request.url.path.endswith("/merge_requests"):
            seen["get"].append(dict(request.url.params))
            return httpx.Response(200, json=[MR])
        if request.method == "GET" and request.url.path.endswith("/notes"):
            return httpx.Response(200, json=NOTES)
        if request.method == "POST" and request.url.path.endswith("/notes"):
            seen["posted"].append((request.url.path, json.loads(request.content)["body"]))
            return httpx.Response(201, json={"id": 777, "body": "x"})
        return httpx.Response(404, json={"message": "404 Not Found"})

    client = httpx.Client(
        base_url="https://gitlab.com/api/v4", transport=httpx.MockTransport(handle)
    )
    c = gitlab.GitLabConnector("t", PROJECT, "lore_demo", MAPS, client)
    c.seen = seen
    return c


def test_merge_requests_come_in_as_prs_with_notes_as_reviews(conn):
    (item,) = conn.fetch(0)
    pr = item.artifact
    assert pr.id == "pr:synthetic-gitlab-acme-lore-demo-11" and pr.kind == "pr"
    assert pr.author_eid == "eid_9e9883de" and pr.url.endswith("/merge_requests/11")
    assert (pr.meta["state"], pr.meta["merged"]) == ("closed", True)
    (review,) = pr.meta["reviews"]  # the system note is skipped
    assert review["state"] == "COMMENTED" and review["reviewer_eid"] == "eid_b7702c98"
    assert item.updated_ms == to_ms("2027-03-13T02:00:00Z")
    assert "updated_after" not in conn.seen["get"][0]
    conn.fetch(to_ms("2027-03-13T00:00:00Z"))
    assert conn.seen["get"][1]["updated_after"] == "2027-03-13T00:00:00Z"


def test_notes_go_on_the_targeted_mr_or_the_issues_fix_mr(conn, monkeypatch):
    rows = [{"id": conn.pr_id(11)}, {"id": "pr:github_com_salesforce_x_pull_3"}]
    monkeypatch.setattr(db, "run", lambda name, params, **k: type("R", (), {"rows": rows}))
    ref = conn.post(
        {"id": "a1", "kind": "ticket_comment", "target_id": "iss:VizForce:1", "draft": "Regressed."}
    )
    assert ref == f"https://gitlab.com/{PROJECT}/-/merge_requests/11#note_777"
    path, body = conn.seen["posted"][0]
    assert path.endswith("/merge_requests/11/notes") and "- Regressed." in body
    conn.post({"id": "a2", "kind": "doc_fix", "target_id": conn.pr_id(4), "draft": "Note."})
    assert conn.seen["posted"][-1][0].endswith("/merge_requests/4/notes")
    assert conn.link(conn.pr_id(7)) == ("MR !7", f"https://gitlab.com/{PROJECT}/-/merge_requests/7")
    assert (
        conn.post({"id": "a3", "kind": "notify_owner", "target_id": "eid_1", "draft": "x"}) is None
    )


def test_api_errors_fail_loudly(conn):
    conn.http = httpx.Client(
        base_url="https://gitlab.com/api/v4",
        transport=httpx.MockTransport(
            lambda r: httpx.Response(401, json={"message": "401 Unauthorized"})
        ),
    )
    with pytest.raises(ConnectorError, match="401"):
        conn.fetch(0)


def test_the_default_client_sends_the_private_token_to_a_self_managed_host():
    c = gitlab.GitLabConnector("tok", "42", "lore_demo", MAPS, base_url="https://git.example.com/")
    assert c.http.headers["private-token"] == "tok"
    assert str(c.http.base_url) == "https://git.example.com/api/v4/"
    assert c.web(3) is None  # a numeric project id gives no browsable address
