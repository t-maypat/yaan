"""The Slack connector (17.3) against a fake Slack client: no network, no tokens. Payload
shapes follow Slack's Web API (conversations.list / history, chat.postMessage)."""

import pytest

from app.connectors import slack
from app.connectors.base import ConnectorError, Maps

MAPS = Maps(
    product="VizForce",
    default_person="eid_bd791efc",
    sources={
        "slack": {
            "channels": {"vizforce-bugs": "ch-force-d79648"},
            "alerts_channel": "lore-alerts",
            "people": {"U1": "eid_9e9883de"},
        }
    },
)


class FakeClient:
    def __init__(self, history):
        self.history, self.posted = history, []

    def conversations_list(self, **kw):
        return {
            "channels": [{"name": "vizforce-bugs", "id": "C1"}, {"name": "lore-alerts", "id": "C9"}]
        }

    def conversations_history(self, channel, oldest=None, limit=None, **kw):
        if channel != "C1":  # the alerts channel holds only what the bot posted
            return {"messages": self.mine(channel)}
        return {"messages": [m for m in self.history if float(m["ts"]) > float(oldest or 0)]}

    def conversations_replies(self, channel, ts, **kw):
        return {"messages": [m for m in self.mine(channel) if m.get("thread_ts") == ts]}

    def mine(self, channel):
        return [
            {"ts": p["ts"], "thread_ts": p.get("thread_ts"), "metadata": p.get("metadata")}
            for p in self.posted
            if p["channel"] == channel
        ]

    def chat_postMessage(self, **kw):
        kw["ts"] = f"1800000000.00000{len(self.posted) + 1}"
        self.posted.append(kw)
        return {"ts": kw["ts"]}

    def chat_getPermalink(self, channel, message_ts):
        stamp = message_ts.replace(".", "")
        return {"permalink": f"https://lore-demo.slack.com/archives/{channel}/p{stamp}"}


HISTORY = [
    {
        "type": "message",
        "user": "U2",
        "ts": "1805000000.000100",
        "text": "[synthetic demo · as Ian Davis (eid_7017b913)] <@U1> CSV export times out "
        "again for CUST-0003, see <https://github.com/x/y/pull/1|PR 1>",
    },
    {
        "type": "message",
        "user": "U1",
        "ts": "1805000100.000200",
        "text": "[synthetic demo] On it.",
        "edited": {"ts": "1805000200.000000"},
    },
    {"type": "message", "bot_id": "B1", "ts": "1805000300.000300", "text": "Lore answered"},
    {
        "type": "message",
        "subtype": "channel_join",
        "user": "U3",
        "ts": "1805000400.0004",
        "text": "<@U3> has joined",
    },
]


@pytest.fixture
def conn(monkeypatch):
    rows = [{"id": "ch-force-d79648", "name": "bug-davidgarcia-VizForce"}]
    monkeypatch.setattr(slack.db, "run", lambda name, params, **k: type("R", (), {"rows": rows}))
    return slack.SlackConnector(FakeClient(HISTORY), "lore_demo", MAPS)


def test_messages_become_live_messages_in_the_channel_they_play(conn):
    items = conn.fetch(0)
    assert len(items) == 2  # the bot's own post and the join event are not knowledge
    first, second = (i.artifact for i in items)
    assert first.id == "msg:synthetic-slack-C1-1805000000.000100"
    assert (first.channel_id, first.meta["channel_name"]) == (
        "ch-force-d79648",
        "bug-davidgarcia-VizForce",
    )
    assert first.author_eid == "eid_7017b913"  # the persona its label names
    assert (
        "@eid_9e9883de CSV export" in first.text
        and "PR 1 (https://github.com/x/y/pull/1)" in first.text
    )
    assert first.ts_iso == "2027-03-14T04:53:20Z"
    assert second.author_eid == "eid_9e9883de"  # mapped account
    assert items[1].updated_ms == 1805000200000  # an edit makes a newer version
    assert [i.artifact.id for i in conn.fetch(1805000000500)] == [second.id]  # since the cursor


def test_a_channel_the_bot_cant_see_is_a_loud_error(monkeypatch, conn):
    conn.conf = {**conn.conf, "channels": {"vizforce-dev": "ch-x"}}
    with pytest.raises(ConnectorError, match="/invite @lore"):
        conn.fetch(0)


def test_post_routes_notifications_and_thread_corrections(conn):
    note = {
        "id": "a1",
        "kind": "notify_owner",
        "target_id": "eid_9e9883de",
        "target_name": "Hannah Garcia",
        "draft": "Please recheck.",
    }
    ref = conn.post(note)
    assert ref.startswith("https://lore-demo.slack.com/archives/C9/")
    assert (
        conn.client.posted[-1]["channel"] == "C9"
        and "For Hannah Garcia" in conn.client.posted[-1]["text"]
        and conn.client.posted[-1]["blocks"]
    )
    fix = {
        "id": "a2",
        "kind": "doc_fix",
        "target_id": "msg:synthetic-slack-C1-1805000000.000100",
        "draft": "Correction: it regressed.",
    }
    conn.post(fix)
    assert (
        conn.client.posted[-1]["thread_ts"] == "1805000000.000100"
        and conn.client.posted[-1]["channel"] == "C1"
    )
    other = {"id": "a3", "kind": "ticket_comment", "target_id": "iss:VizForce:1", "draft": "x"}
    assert conn.post(other) is None  # Jira's or GitHub's
    # sending them again (a retry after a timeout or a crash) finds them: nothing posted twice
    count = len(conn.client.posted)
    assert conn.post(note) == ref and conn.post(fix).endswith("p1800000000000002")
    assert len(conn.client.posted) == count
    assert conn.client.posted[0]["metadata"]["event_payload"] == {"action_id": "a1"}


def test_answer_text_keeps_the_checked_quotes_and_links_slack_sources():
    out = {
        "answer_markdown": "**Hannah Garcia** fixed it [1].",
        "abstain": False,
        "citations": [{"n": 1, "artifact_id": "pr:x", "quote": "Fix CSV export"}],
    }
    text = slack.answer_text(out, lambda aid: None)
    assert (
        text.startswith("*Hannah Garcia* fixed it [1].") and "[1] `pr:x`: “Fix CSV export”" in text
    )
    linked = slack.answer_text(out, lambda aid: "https://s/p1")
    assert "[1] <https://s/p1|pr:x>" in linked
    assert (
        slack.answer_text({**out, "abstain": True, "citations": []}, lambda a: None)
        == "*Hannah Garcia* fixed it [1]."
    )


def test_alert_cards_offer_approve_and_dismiss_until_decided():
    alert = {
        "id": "alert:1",
        "summary": "Regression: CSV export",
        "kind": "regression",
        "severity": "high",
        "product_id": "VizForce",
        "radius_size": 6,
        "quote": "times out again",
    }
    blocks = slack.alert_blocks(alert)
    actions = next(b for b in blocks if b["type"] == "actions")
    assert [e["action_id"] for e in actions["elements"]] == ["lore_approve", "lore_dismiss"]
    assert actions["elements"][0]["value"] == "alert:1" and actions["elements"][0]["confirm"]
    assert blocks[0]["type"] == "header" and "Regression" in blocks[0]["text"]["text"]
    done = slack.alert_blocks(alert, decided="Approved by <@U1>")
    assert done[-1]["type"] == "context" and all(b["type"] != "actions" for b in done)


def test_a_bot_post_counts_only_when_it_opens_with_a_persona_label(conn):
    seeded = {
        "bot_id": "B1",
        "ts": "1805000500.0005",
        "text": "[synthetic demo · as Ian Davis (eid_7017b913)] CUST-0003 has reopened it.",
    }
    item = conn.normalize(seeded, "C1", "ch-force-d79648", "bug-davidgarcia-VizForce")
    assert item and item.artifact.author_eid == "eid_7017b913"
    quoted = {
        "bot_id": "B1",
        "ts": "1805000600.0006",
        "text": '*To Hannah Garcia*: "[synthetic '
        'demo · as Ian Davis (eid_7017b913)] CUST-0003 has reopened it."',
    }
    assert conn.normalize(quoted, "C1", "ch-force-d79648", "bug-davidgarcia-VizForce") is None
