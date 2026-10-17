"""The Jira connector (17.5) against httpx's mock transport: no network, no token. Payload
shapes follow Jira Cloud REST v3 (search/jql, issue changelog, issue comments, ADF bodies)."""

import json

import httpx
import pytest

from app.connectors import jira
from app.connectors.base import Maps
from app.llm import quote_in

MAPS = Maps(
    product="VizForce",
    default_person="eid_bd791efc",
    sources={"jira": {"channel": "ch-force-d79648", "people": {"acc-hannah": "eid_9e9883de"}}},
)


def doc(*paragraphs):
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": p}]} for p in paragraphs
        ],
    }


ISSUE = {
    "key": "VIZ-1",
    "fields": {
        "summary": "CSV export times out on large dashboards",
        "description": doc(
            "[synthetic demo · as Ian Davis (eid_7017b913)] CUST-0003 reports the CSV "
            "export times out on dashboards over 50k rows."
        ),
        "status": {"name": "Done"},
        "assignee": {"accountId": "acc-hannah"},
        "reporter": {"accountId": "acc-me"},
        "components": [{"name": "Data Export Module"}],
        "created": "2027-03-12T00:53:20.000+0000",
        "updated": "2027-03-13T00:55:20.000+0000",
    },
}
CHANGELOG = {
    "total": 2,
    "values": [
        {
            "id": "100",
            "author": {"accountId": "acc-me"},
            "created": "2027-03-12T09:00:00.000+0000",
            "items": [{"field": "status", "fromString": "To Do", "toString": "In Progress"}],
        },
        {
            "id": "101",
            "author": {"accountId": "acc-hannah"},
            "created": "2027-03-13T00:55:20.000+0000",
            "items": [{"field": "status", "fromString": "In Progress", "toString": "Done"}],
        },
    ],
}
COMMENTS = {
    "total": 1,
    "comments": [
        {
            "id": "9",
            "author": {"accountId": "acc-me"},
            "created": "2027-03-16T08:00:00.000+0000",
            "updated": "2027-03-16T08:00:00.000+0000",
            "body": doc(
                "[synthetic demo · as Ian Davis (eid_7017b913)] CUST-0003 says it times out again."
            ),
        },
    ],
}


@pytest.fixture
def conn(monkeypatch):
    rows = [{"id": "ch-force-d79648", "name": "bug-davidgarcia-VizForce"}]
    monkeypatch.setattr(jira.db, "run", lambda name, params, **k: type("R", (), {"rows": rows}))
    seen = {"jql": [], "posted": []}

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/rest/api/3/search/jql":
            seen["jql"].append(request.url.params["jql"])
            return httpx.Response(200, json={"issues": [ISSUE], "isLast": True})
        if path.endswith("/changelog"):
            return httpx.Response(200, json=CHANGELOG)
        if path.endswith("/comment") and request.method == "GET":
            return httpx.Response(200, json=COMMENTS)
        if path.endswith("/comment") and request.method == "POST":
            seen["posted"].append((path, json.loads(request.content)["body"]))
            return httpx.Response(201, json={"id": "77"})
        return httpx.Response(404, text="nope")

    client = httpx.Client(
        base_url="https://lore-demo.atlassian.net", transport=httpx.MockTransport(handle)
    )
    c = jira.JiraConnector(
        "https://lore-demo.atlassian.net", "e", "t", "VIZ", "lore_demo", MAPS, client
    )
    c.seen = seen
    return c


def test_status_changes_map_to_issue_events():
    assert jira.change_kind("In Progress", "Done") == "issue_resolved"
    assert jira.change_kind("To Do", "Won't Do") == "issue_dismissed"
    assert jira.change_kind("Done", "To Do") == "issue_reopened"
    assert jira.change_kind("To Do", "In Progress") is None


def test_a_ticket_becomes_a_report_its_changes_and_its_comments(conn):
    items = conn.fetch(0)
    ids = [i.artifact.id for i in items]
    assert ids == [
        "msg:synthetic-jira-VIZ-1-created",
        "msg:synthetic-jira-VIZ-1-change-101",
        "msg:synthetic-jira-VIZ-1-comment-9",
    ]  # To Do -> In Progress states nothing
    report, done, comment = items
    a = report.artifact
    assert (a.channel_id, a.meta["channel_name"]) == ("ch-force-d79648", "bug-davidgarcia-VizForce")
    assert a.author_eid == "eid_7017b913"  # the persona in the description's label
    [ev] = report.events
    assert (ev["type"], ev["issue"], ev["component"], ev["customer"], ev["person"]) == (
        "issue_reported",
        "CSV export times out on large dashboards",
        "Data Export Module",
        "CUST-0003",
        "eid_9e9883de",
    )
    assert quote_in(ev["quote"], a.text) and ev["speaker"] == "eid_7017b913"
    [ev2] = done.events
    assert (ev2["type"], ev2["speaker"], done.artifact.author_eid) == (
        "issue_resolved",
        "eid_9e9883de",
        "eid_9e9883de",
    )
    assert done.artifact.text.startswith("[synthetic demo] Jira VIZ-1")  # labeled (rule 5)
    assert comment.events is None  # the extractor reads comments like any message
    assert (
        "times out again" in comment.artifact.text and comment.artifact.author_eid == "eid_7017b913"
    )


def test_polling_uses_a_relative_window(conn):
    conn.fetch(0)
    assert conn.seen["jql"][-1] == "project = VIZ ORDER BY created ASC"
    import time

    conn.fetch(int(time.time() * 1000) - 10 * 60000)
    assert '"-70m"' in conn.seen["jql"][-1]  # 10 minutes + an hour of margin


def test_comments_go_on_the_ticket(conn, monkeypatch):
    ref = conn.post(
        {
            "id": "a1",
            "kind": "doc_fix",
            "target_id": "msg:synthetic-jira-VIZ-1-change-101",
            "draft": "Correction: it regressed.\nRecheck PR 1.",
        }
    )
    assert ref == "https://lore-demo.atlassian.net/browse/VIZ-1?focusedCommentId=77"
    path, body = conn.seen["posted"][-1]
    assert (
        path.endswith("/issue/VIZ-1/comment") and len(body["content"]) == 2
    )  # one ADF paragraph a line
    monkeypatch.setattr(
        jira.db,
        "run",
        lambda name, params, **k: type(
            "R",
            (),
            {"rows": [{"id": "msg:20270101-1-abc"}, {"id": "msg:synthetic-jira-VIZ-1-created"}]},
        ),
    )
    assert conn.post(
        {"id": "a2", "kind": "ticket_comment", "target_id": "iss:VizForce:1", "draft": "x"}
    )
    assert (
        conn.post({"id": "a3", "kind": "notify_owner", "target_id": "eid_1", "draft": "x"}) is None
    )


def test_adf_round_trip():
    assert jira.adf_text(doc("one", "two")) == "one\ntwo\n"
    assert jira.adf_text(jira.adf("a\n\nb")) == "a\nb\n"
