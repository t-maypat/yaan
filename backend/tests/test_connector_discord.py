"""The Discord connector (D090, experimental) against httpx's mock transport: no network, no token.
Message fields follow the docs' message object (id, channel_id, author, content, timestamp,
edited_timestamp); Create Message takes content and message_reference as documented."""

import json

import httpx
import pytest

from app import db
from app.connectors import discord
from app.connectors.base import ConnectorError, Maps
from app.connectors.experimental import to_ms

MAPS = Maps(
    product="VizForce",
    default_person="eid_bd791efc",
    sources={"discord": {"channels": {"555": "ch-1"}, "people": {"42": "eid_9e9883de"}}},
)
CH = "555"


def snowflake(ms: int) -> str:
    return str((ms - discord.EPOCH) << 22 | 7)


T1, T2 = to_ms("2027-03-14T15:00:00Z"), to_ms("2027-03-14T15:05:00Z")
MESSAGES = [  # newest first, as Discord returns them
    {
        "id": snowflake(T2),
        "channel_id": CH,
        "author": {"id": "99", "username": "ian"},
        "content": "[synthetic demo · as Ian Davis (eid_7017b913)] CUST-0003 times out again.",
        "timestamp": "2027-03-14T15:05:00.000000+00:00",
        "edited_timestamp": "2027-03-14T15:06:00.000000+00:00",
    },
    {
        "id": snowflake(T1),
        "channel_id": CH,
        "author": {"id": "42", "username": "hannah"},
        "content": "[synthetic demo] Fix is in review.",
        "timestamp": "2027-03-14T15:00:00.000000+00:00",
        "edited_timestamp": None,
    },
]


def test_snowflakes_convert_both_ways():
    assert discord.snowflake_ms(snowflake(T1)) == T1
    assert discord.snowflake_after(T1) == str((T1 - discord.EPOCH) << 22)


@pytest.fixture
def conn(monkeypatch):
    monkeypatch.setattr(
        db,
        "run",
        lambda name, params, **k: type("R", (), {"rows": [{"id": "ch-1", "name": "bugs"}]}),
    )
    seen = {"gets": [], "posted": []}

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == f"/api/v10/channels/{CH}/messages":
            seen["gets"].append(dict(request.url.params))
            return httpx.Response(200, json=MESSAGES)
        if request.method == "POST" and request.url.path.endswith("/messages"):
            seen["posted"].append((request.url.path, json.loads(request.content)))
            return httpx.Response(200, json={"id": "9001", "channel_id": CH})
        return httpx.Response(404, json={"message": "404: Not Found", "code": 0})

    client = httpx.Client(base_url=discord.API, transport=httpx.MockTransport(handle))
    c = discord.DiscordConnector("bot-token", CH, "lore_demo", MAPS, client)
    c.seen = seen
    return c


def test_channel_messages_come_in_with_the_edit_time_as_their_version(conn):
    items = conn.fetch(0)
    by_id = {i.artifact.id: i for i in items}
    first = by_id[f"msg:synthetic-discord-{CH}-{snowflake(T1)}"]
    second = by_id[f"msg:synthetic-discord-{CH}-{snowflake(T2)}"]
    assert first.artifact.author_eid == "eid_9e9883de" and first.artifact.channel_id == "ch-1"
    assert second.artifact.author_eid == "eid_7017b913"  # the persona in the label
    assert (first.artifact.ts_ms, first.updated_ms) == (T1, T1)
    assert second.updated_ms == to_ms("2027-03-14T15:06:00Z")
    assert conn.seen["gets"][0] == {"after": "0", "limit": "100"}
    conn.fetch(T1)
    assert conn.seen["gets"][1]["after"] == discord.snowflake_after(T1)


def test_an_unmapped_channel_fails_loudly():
    c = discord.DiscordConnector("t", "777", "lore_demo", MAPS)
    with pytest.raises(ConnectorError, match="discord.channels"):
        c.fetch(0)


def test_a_draft_is_a_reply_to_the_message_it_is_aimed_at(conn):
    target = f"msg:synthetic-discord-{CH}-{snowflake(T2)}"
    ref = conn.post(
        {"id": "a1", "kind": "notify_owner", "target_id": target, "draft": "Regressed."}
    )
    assert ref == f"discord:{CH}/9001"
    path, sent = conn.seen["posted"][0]
    assert path == f"/api/v10/channels/{CH}/messages"
    assert sent["message_reference"] == {
        "message_id": snowflake(T2),
        "fail_if_not_exists": False,
    }
    assert sent["allowed_mentions"] == {"parse": []}
    assert "- Regressed." in sent["content"] and "<sub>" not in sent["content"]
    assert (
        conn.post({"id": "a2", "kind": "doc_fix", "target_id": "msg:herb_x", "draft": "x"}) is None
    )


def test_a_reply_over_the_limit_is_refused_not_cut(conn):
    target = f"msg:synthetic-discord-{CH}-{snowflake(T2)}"
    with pytest.raises(ConnectorError, match="2000"):
        conn.post({"id": "a1", "kind": "notify_owner", "target_id": target, "draft": "x" * 2100})
    assert conn.seen["posted"] == []


def test_the_default_client_authenticates_as_a_bot():
    c = discord.DiscordConnector("bot-token", CH, "lore_demo", MAPS)
    assert c.http.headers["authorization"] == "Bot bot-token"
