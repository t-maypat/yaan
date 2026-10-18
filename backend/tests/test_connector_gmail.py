"""The Gmail connector (D090, experimental) against httpx's mock transport: no network, no
token. Field names follow Google's Discovery document for Gmail v1 (Message, MessagePart,
MessagePartHeader, ListMessagesResponse); the vendor pages give no full response example."""

import base64
import email
import json

import httpx
import pytest

from app import db
from app.connectors import gmail
from app.connectors.base import Maps

MAPS = Maps(
    product="VizForce",
    default_person="eid_bd791efc",
    sources={"gmail": {"channel": "ch-1", "people": {"ian@customer.example": "eid_7017b913"}}},
)


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")  # Gmail omits padding


MESSAGE = {
    "id": "17f3a9c",
    "threadId": "17f3a00",
    "labelIds": ["Label_1"],
    "internalDate": "1815000000000",
    "payload": {
        "mimeType": "multipart/alternative",
        "headers": [
            {"name": "From", "value": "Ian Davis <Ian@Customer.example>"},
            {"name": "Subject", "value": "CSV export times out again"},
            {"name": "Message-ID", "value": "<abc@mail.example>"},
        ],
        "parts": [
            {
                "mimeType": "text/plain",
                "body": {"data": b64("[synthetic demo] CUST-0003: it times out again.")},
            },
            {"mimeType": "text/html", "body": {"data": b64("<p>html</p>")}},
        ],
    },
}


@pytest.fixture
def conn(monkeypatch):
    monkeypatch.setattr(
        db,
        "run",
        lambda name, params, **k: type("R", (), {"rows": [{"id": "ch-1", "name": "inbox"}]}),
    )
    seen = {"lists": [], "sent": []}

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/gmail/v1/users/me/messages" and request.method == "GET":
            seen["lists"].append(dict(request.url.params))
            return httpx.Response(
                200,
                json={
                    "messages": [{"id": "17f3a9c", "threadId": "17f3a00"}],
                    "resultSizeEstimate": 1,
                },
            )
        if path == "/gmail/v1/users/me/messages/17f3a9c" and request.method == "GET":
            return httpx.Response(200, json=MESSAGE)
        if path == "/gmail/v1/users/me/messages/send":
            seen["sent"].append(json.loads(request.content))
            return httpx.Response(200, json={"id": "sent-1", "threadId": "17f3a00"})
        return httpx.Response(404, json={"error": {"code": 404}})

    client = httpx.Client(base_url=gmail.API, transport=httpx.MockTransport(handle))
    c = gmail.GmailConnector("ya29.x", "Label_1", "lore_demo", MAPS, client)
    c.seen = seen
    return c


def test_a_labeled_mail_becomes_a_message_by_its_sender(conn):
    (item,) = conn.fetch(0)
    a = item.artifact
    assert a.id == "msg:synthetic-gmail-17f3a9c" and a.channel_id == "ch-1"
    assert a.text == "CSV export times out again\n[synthetic demo] CUST-0003: it times out again."
    assert a.author_eid == "eid_7017b913"  # From, lower-cased, mapped
    assert (a.ts_ms, item.updated_ms) == (1815000000000, 1815000000000)
    assert conn.seen["lists"][0]["labelIds"] == "Label_1" and "q" not in conn.seen["lists"][0]
    conn.fetch(1815000000000)
    assert conn.seen["lists"][1]["q"] == "after:1815000000"  # epoch seconds


def test_a_reply_is_sent_in_the_thread_and_nothing_else_is_ever_mailed(conn):
    ref = conn.post(
        {
            "id": "a1",
            "kind": "notify_owner",
            "target_id": "msg:synthetic-gmail-17f3a9c",
            "draft": "It regressed.",
        }
    )
    assert ref == "https://mail.google.com/mail/u/0/#all/sent-1"
    sent = conn.seen["sent"][0]
    assert sent["threadId"] == "17f3a00"
    raw = email.message_from_bytes(base64.urlsafe_b64decode(sent["raw"]))
    assert raw["To"] == "Ian@Customer.example"
    assert raw["Subject"] == "Re: CSV export times out again"
    assert raw["In-Reply-To"] == "<abc@mail.example>"
    assert "It regressed." in raw.get_payload(decode=True).decode()
    # an issue comment is not mail: no fallback through the issue's reports
    assert (
        conn.post(
            {"id": "a2", "kind": "ticket_comment", "target_id": "iss:VizForce:1", "draft": "x"}
        )
        is None
    )
    assert len(conn.seen["sent"]) == 1


def test_it_sends_the_access_token_it_was_given():
    c = gmail.GmailConnector("ya29.x", "Label_1", "lore_demo", MAPS)
    assert c.http.headers["authorization"] == "Bearer ya29.x"
