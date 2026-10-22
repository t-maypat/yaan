"""Slack threads, edits and deletions (F1, F7, D126), with a fake Slack client and no database:
a reply names its thread's root, the socket's events become items (an unchanged edit costs no
model call), the sweep finds what the socket missed and infers a deletion only from a complete
read, and the runner forgets deletions first and keeps them until that works."""

import pytest
from slack_sdk.errors import SlackApiError
from test_connector_slack import MAPS, FakeClient
from test_connectors import Fake, msg, offline  # noqa: F401  (the fixture)

from app import db, forget
from app.connectors import runner, slack
from app.connectors.base import Item

CHANNEL = {"id": "ch-force-d79648", "name": "bug-davidgarcia-VizForce"}
ROOT, REPLY, LATE = "1805000000.000100", "1805000500.000200", "1805000900.000300"


def said(ts: str, text: str, **kw) -> dict:
    return {"type": "message", "user": "U1", "ts": ts, "text": f"[synthetic demo] {text}", **kw}


class Threads(FakeClient):
    """History plus thread replies; `broken` threads can't be read (a rate limit, say)."""

    def __init__(self, history, threads, broken=()):
        super().__init__(history)
        self.threads, self.broken = threads, set(broken)

    def conversations_history(self, channel, oldest=None, **kw):
        return {"messages": [m for m in self.history if float(m["ts"]) >= float(oldest or 0)]}

    def conversations_replies(self, channel, ts, **kw):
        if ts in self.broken:
            raise SlackApiError("rate limited", {"ok": False, "error": "ratelimited"})
        return {"messages": self.threads.get(ts, [])}


@pytest.fixture
def conn(monkeypatch):
    rows = [CHANNEL]
    monkeypatch.setattr(slack.db, "run", lambda name, params, **k: type("R", (), {"rows": rows}))
    slack._heard.clear()
    yield slack.SlackConnector(Threads([], {}), "lore_demo", MAPS)
    slack._heard.clear()


def sid(ts: str) -> str:
    return f"msg:synthetic-slack-C1-{ts}"


def test_a_reply_names_its_threads_root_and_a_root_names_none(conn):
    root = conn.normalize(said(ROOT, "Export broken?", thread_ts=ROOT, reply_count=1), "C1",
                          CHANNEL["id"], CHANNEL["name"])  # fmt: skip
    reply = conn.normalize(said(REPLY, "Fixed it.", thread_ts=ROOT), "C1", CHANNEL["id"],
                           CHANNEL["name"])  # fmt: skip
    assert "thread_id" not in root.artifact.meta
    assert reply.artifact.meta["thread_id"] == sid(ROOT)
    # Slack's placeholder for a deleted root with replies is not knowledge
    gone = said(ROOT, "This message was deleted.", subtype="tombstone")
    assert conn.normalize(gone, "C1", CHANNEL["id"], CHANNEL["name"]) is None


def test_the_socket_hears_replies_edits_and_deletions(conn):
    edited = said(REPLY, "Fixed it in 4.2.", thread_ts=ROOT, edited={"ts": LATE})
    events = [
        {**said(REPLY, "Fixed it.", thread_ts=ROOT), "channel": "C1"},  # a reply
        {"subtype": "message_changed", "channel": "C1", "message": edited,
         "previous_message": said(REPLY, "Fixed it.", thread_ts=ROOT)},  # new words
        {"subtype": "message_changed", "channel": "C1",  # a link preview: same words
         "message": said(ROOT, "Export broken?", attachments=[{}]),
         "previous_message": said(ROOT, "Export broken?")},
        {"subtype": "message_deleted", "channel": "C1", "deleted_ts": LATE,
         "previous_message": said(LATE, "oops")},
        {"subtype": "message_changed", "channel": "C1",  # a deleted root that keeps replies
         "message": {"ts": ROOT, "subtype": "tombstone", "text": "This message was deleted."},
         "previous_message": said(ROOT, "Export broken?")},
        {**said("1805000950.0", "elsewhere"), "channel": "C7"},  # a channel Yaan doesn't read
    ]  # fmt: skip
    assert [slack.hear(e) for e in events] == [True] * 6
    assert not slack.hear({"subtype": "channel_join", "channel": "C1", "ts": "1.0"})
    ours = {"bot_id": "B1", "channel": "C1", "ts": "1.0", "text": "Lore answered"}
    assert not slack.hear(ours)
    card = {"ts": "1.0", "bot_id": "B1", "text": "Approved"}  # its own alert card, updated
    assert not slack.hear({"subtype": "message_changed", "channel": "C1", "message": card})

    items = conn.heard()
    assert [(i.artifact.id, i.deleted) for i in items] == [
        (sid(REPLY), False),
        (sid(REPLY), False),
        (sid(LATE), True),
        (sid(ROOT), True),
    ]
    reply, edit = items[0], items[1]
    assert reply.artifact.meta["thread_id"] == sid(ROOT)
    assert edit.updated_ms == 1805000900000 and "4.2" in edit.artifact.text  # a newer version
    assert conn.heard() == []  # taken once


def test_a_failed_round_puts_what_it_heard_back(conn, monkeypatch):
    slack.hear({**said(REPLY, "Fixed it.", thread_ts=ROOT), "channel": "C1"})

    def down():
        raise slack.ConnectorError("Slack conversations_list: ratelimited")

    monkeypatch.setattr(conn, "channel_ids", down)
    with pytest.raises(slack.ConnectorError):
        conn.heard()
    monkeypatch.undo()
    rows = type("R", (), {"rows": [CHANNEL]})
    monkeypatch.setattr(slack.db, "run", lambda name, params, **k: rows)
    assert [i.artifact.id for i in conn.heard()] == [sid(REPLY)]


def test_the_sweep_finds_what_the_socket_missed_and_infers_deletions_only_from_full_reads(
    conn, monkeypatch
):
    top, old_root = "1805000300.000400", "1700000000.000100"  # old_root: before the window
    history = [said(ROOT, "Export broken?", thread_ts=ROOT, reply_count=2), said(top, "Ship it.")]
    threads = {
        ROOT: [
            said(ROOT, "Export broken?", thread_ts=ROOT),
            said(REPLY, "Fixed it.", thread_ts=ROOT),  # missed while down
            said(LATE, "Fixed it in 4.2.", thread_ts=ROOT, edited={"ts": LATE}),  # edited
        ]
    }
    conn.client = Threads(history, threads, broken={old_root})
    norm = {m["ts"]: conn.normalize(m, "C1", CHANNEL["id"], CHANNEL["name"]).artifact.text
            for m in [*history, *threads[ROOT]]}  # fmt: skip
    held = [
        {"id": sid(ROOT), "text": norm[ROOT], "thread_id": None},
        {"id": sid(top), "text": norm[top], "thread_id": None},
        {"id": sid(LATE), "text": "[synthetic demo] Fixed it.", "thread_id": sid(ROOT)},
        {"id": sid("1805000600.000500"), "text": "gone", "thread_id": sid(ROOT)},  # deleted reply
        {"id": sid("1805000700.000600"), "text": "gone too", "thread_id": None},  # deleted
        {"id": sid("1805000800.000700"), "text": "?", "thread_id": sid(old_root)},  # unreadable
    ]
    monkeypatch.setattr(slack.db, "run_all", lambda name, params, **k: held)
    monkeypatch.setattr(slack, "now_ms", lambda: 1805001000000)
    out = {(i.artifact.id, i.deleted) for i in conn.sweep(days=1)}
    assert out == {
        (sid(REPLY), False),  # missed: in now
        (sid(LATE), False),  # edited while down: its new words in
        (sid("1805000600.000500"), True),  # gone from a thread read in full
        (sid("1805000700.000600"), True),  # gone from the channel
    }  # the unchanged ones and the reply under an unreadable thread are left alone


def test_the_runner_forgets_deletions_first_and_keeps_them_until_that_works(
    offline, monkeypatch  # noqa: F811
):
    from app.sentinel import live

    tries: list[list[str]] = []

    def forgetting(ids, graph, reason):
        tries.append(ids)
        if len(tries) == 1:
            raise db.LockBusy("the writer lock is busy")
        return {}

    ingested = []
    monkeypatch.setattr(forget, "forget", forgetting)
    monkeypatch.setattr(
        live,
        "ingest_artifact",
        lambda a, graph, events=None, replace=False: ingested.append(a.id)
        or {"alerts": [], "freshness_lag_ms": 1},
    )
    fake = Fake("slack", [Item(msg(1), 10), Item(msg(2), 20, deleted=True)])
    first = runner.sync([fake], "lore_demo")
    assert ingested == [msg(1).id] and "forgetting 1" in first["waiting"][0]["why"]
    fake.items = []
    again = runner.sync([fake], "lore_demo")
    assert tries == [[msg(2).id], [msg(2).id]] and again["forgotten"] == [
        {"source": "slack", "items": 1}
    ]
    assert runner.load_state("lore_demo")["slack"]["deletes"] == []


def test_the_sweep_runs_in_org_mode_at_start_and_daily_and_never_on_its_own_in_the_demo(
    monkeypatch,
):
    class Sweeps(Fake):
        def sweep(self, days=None):
            return []

    c, mine = Sweeps("slack"), {"swept_ms": 0}
    runner._swept.discard("slack")
    assert not runner.sweep_due(c, mine, None)  # the demo: a reset would bring old ones back
    assert runner.sweep_due(c, mine, 7)  # asked for (yaan connectors reconcile)
    assert not runner.sweep_due(Fake("jira"), mine, 7)  # nothing to sweep
    monkeypatch.setattr(runner.base, "is_demo", lambda: False)
    assert runner.sweep_due(c, mine, None)  # org mode, this process's first round
    runner._swept.add("slack")
    import time

    mine["swept_ms"] = int(time.time() * 1000)
    assert not runner.sweep_due(c, mine, None)
    mine["swept_ms"] -= runner.SWEEP_EVERY_MS
    assert runner.sweep_due(c, mine, None)  # a day later
    runner._swept.discard("slack")


def test_with_the_socket_the_history_read_is_a_safety_net(conn, monkeypatch):
    """D132: messages arrive as socket events, so history is read every HISTORY_EVERY_MS,
    from the last read with a minute's overlap (the socket's items move the cursor)."""
    reads = []

    class Counting(Threads):
        def conversations_history(self, channel, oldest=None, **kw):
            reads.append(float(oldest) * 1000)
            return super().conversations_history(channel, oldest, **kw)

    conn.client = Counting([said(ROOT, "Export broken?")], {})
    clock = [1805001000000]
    monkeypatch.setattr(slack, "now_ms", lambda: clock[0])
    monkeypatch.setitem(slack._live, "socket", True)
    conn.fetch(0)
    clock[0] += 60_000
    conn.fetch(clock[0])  # the socket brought this minute's messages: no read
    assert len(reads) == 1
    clock[0] += slack.HISTORY_EVERY_MS
    conn.fetch(clock[0])  # the cursor moved on; the read starts where the last one did
    assert reads[1] == pytest.approx(1805001000000 - 60_000)
