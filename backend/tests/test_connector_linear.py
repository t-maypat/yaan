"""The Linear connector (D090, experimental) against httpx's mock transport: no network, no key.
Field names follow Linear's published GraphQL schema; the vendor docs give no full response
example, so the payload is built from the documented fields."""

import json

import httpx
import pytest

from app import db
from app.connectors import linear
from app.connectors.base import Maps
from app.connectors.experimental import to_ms
from app.llm import quote_in

MAPS = Maps(
    product="VizForce",
    default_person="eid_bd791efc",
    sources={"linear": {"channel": "ch-1", "people": {"u-hannah": "eid_9e9883de"}}},
)
ISSUE = {
    "id": "uuid-1",
    "identifier": "ENG-12",
    "title": "CSV export times out",
    "description": "[synthetic demo · as Ian Davis (eid_7017b913)] CUST-0003 reports a timeout.",
    "url": "https://linear.app/acme/issue/ENG-12/csv-export-times-out",
    "createdAt": "2027-03-12T00:53:20.000Z",
    "updatedAt": "2027-03-13T00:55:20.000Z",
    "completedAt": "2027-03-13T00:55:20.000Z",
    "canceledAt": None,
    "creator": {"id": "u-me"},
    "assignee": {"id": "u-hannah"},
    "state": {"name": "Done", "type": "completed"},
    "comments": {
        "nodes": [
            {
                "id": "c-1",
                "body": "[synthetic demo] Fix is in review.",
                "createdAt": "2027-03-12T20:00:00.000Z",
                "updatedAt": "2027-03-12T21:00:00.000Z",
                "user": {"id": "u-hannah"},
            }
        ]
    },
}


@pytest.fixture
def conn(monkeypatch):
    monkeypatch.setattr(
        db,
        "run",
        lambda name, params, **k: type("R", (), {"rows": [{"id": "ch-1", "name": "bugs"}]}),
    )
    seen = {"queries": []}

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen["queries"].append(body)
        if "commentCreate" in body["query"]:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "commentCreate": {
                            "success": True,
                            "comment": {
                                "id": "c-9",
                                "url": "https://linear.app/acme/issue/ENG-12#c-9",
                            },
                        }
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "data": {
                    "issues": {
                        "nodes": [ISSUE],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            },
        )

    client = httpx.Client(base_url=linear.API, transport=httpx.MockTransport(handle))
    c = linear.LinearConnector("lin_api_x", "ENG", "lore_demo", MAPS, client)
    c.seen = seen
    return c


def test_an_issue_becomes_messages_with_deterministic_events(conn):
    items = conn.fetch(0)
    created, state, comment = items
    a = created.artifact
    assert a.id == "msg:synthetic-linear-ENG-12-created" and a.channel_id == "ch-1"
    assert a.author_eid == "eid_7017b913"  # the persona in the label
    ev = created.events[0]
    assert (ev["type"], ev["issue"], ev["customer"], ev["person"]) == (
        "issue_reported",
        "CSV export times out",
        "CUST-0003",
        "eid_9e9883de",
    )
    assert quote_in(ev["quote"], a.text)  # the quote is a verbatim substring (rule 2)
    assert state.events[0]["type"] == "issue_resolved" and "moved to Done" in state.artifact.text
    assert comment.artifact.id.endswith("ENG-12-comment-c-1")
    assert comment.updated_ms == to_ms("2027-03-12T21:00:00Z")
    sent = conn.seen["queries"][0]["variables"]["filter"]
    assert sent == {"team": {"key": {"eq": "ENG"}}}  # first round: no updatedAt bound


def test_the_cursor_filters_by_updated_at(conn):
    conn.fetch(to_ms("2027-03-13T00:00:00Z"))
    flt = conn.seen["queries"][0]["variables"]["filter"]
    assert flt["updatedAt"] == {"gt": "2027-03-13T00:00:00Z"}


def test_a_ticket_comment_goes_on_the_issue_that_a_linear_message_reported(conn, monkeypatch):
    rows = [{"id": "msg:synthetic-jira-X-1-created"}, {"id": "msg:synthetic-linear-ENG-12-created"}]
    monkeypatch.setattr(db, "run", lambda name, params, **k: type("R", (), {"rows": rows}))
    ref = conn.post(
        {"id": "a1", "kind": "ticket_comment", "target_id": "iss:VizForce:1", "draft": "Regressed."}
    )
    assert ref == "https://linear.app/acme/issue/ENG-12#c-9"
    sent = conn.seen["queries"][-1]["variables"]["input"]
    assert sent["issueId"] == "ENG-12" and "### Yaan Sentinel · Follow-up" in sent["body"]
    assert conn.link("msg:synthetic-linear-ENG-12-comment-c-1") == ("Linear ENG-12", None)
    assert (
        conn.post({"id": "a2", "kind": "notify_owner", "target_id": "eid_1", "draft": "x"}) is None
    )


def test_graphql_errors_fail_loudly(conn):
    conn.http = httpx.Client(
        base_url=linear.API,
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"errors": [{"message": "Authentication required"}]})
        ),
    )
    with pytest.raises(linear.ConnectorError, match="Authentication required"):
        conn.fetch(0)


def test_the_default_client_sends_a_personal_key_without_bearer(monkeypatch):
    c = linear.LinearConnector("lin_api_x", "ENG", "lore_demo", MAPS)
    assert c.http.headers["authorization"] == "lin_api_x"
    assert str(c.http.base_url).startswith("https://api.linear.app")
