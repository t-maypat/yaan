"""Connector core (17.2, D074): maps and personas, one sync round, write-back of approved
actions. Fake connectors and a faked live path: no network, no LLM. The two write-back
queries run on the real server (marked db)."""

import pytest

from app import db
from app.connectors import base, runner
from app.connectors.base import Item, Maps
from app.ingest.normalize import Artifact
from app.sentinel import live

MAPS = Maps(
    product="Widget",
    default_person="eid_a1",
    sources={"slack": {"people": {"U1": "eid_b2"}, "channels": {"widget-bugs": "ch-bug"}}},
)


def msg(n: int, text: str = "[synthetic demo] Export is slow again.", ts: int = 1000) -> Artifact:
    return Artifact(
        id=base.synthetic_id("msg", "slack", f"C1-{n}"),
        kind="message",
        product_hint="Widget",
        channel_id="ch-bug",
        ts_ms=ts + n,
        ts_iso="2027-03-09T10:00:00Z",
        author_eid="eid_a1",
        text=text,
        raw_ref=f"test:{n}",
        meta={"channel_name": "bug-Widget"},
    )


class Fake:
    def __init__(self, name, items=(), takes=None, fails=False):
        self.name, self.items, self.takes, self.fails = name, list(items), takes, fails
        self.posted = []

    def fetch(self, since_ms):
        return [i for i in self.items if i.updated_ms > since_ms]

    def post(self, action, resolve=None):
        if self.fails:
            raise base.ConnectorError("token expired")
        if self.takes and action["target_id"].startswith(self.takes):
            self.posted.append(action["id"])
            return f"https://{self.name}.example/{action['id']}"
        return None


# --- no database -----------------------------------------------------------------------------
def test_who_an_item_is_by_label_then_map_then_default():
    labeled = "[synthetic demo · as Ann (eid_0123abcd)] hi"
    assert MAPS.person("slack", "U9", labeled) == "eid_0123abcd"
    assert MAPS.person("slack", "U1", "[synthetic demo] hi") == "eid_b2"
    assert MAPS.person("slack", "U9", "[synthetic demo] hi") == "eid_a1"
    assert MAPS.person("jira", None) == "eid_a1"


def test_synthetic_ids_have_the_live_paths_rule5_shape():
    assert base.synthetic_id("pr", "github", "acme/demo#12") == "pr:synthetic-github-acme-demo-12"
    live.check_synthetic(base.synthetic_id("msg", "slack", "C1/170.5"))
    with pytest.raises(live.LiveError, match="rule 5"):
        live.check_synthetic("msg:20270309-0-abc")


@pytest.fixture
def offline(tmp_path, monkeypatch):
    """A sync round with no database: state in tmp_path, nothing present in the graph,
    nothing to deliver, dead letters collected in a list."""
    monkeypatch.setattr(live, "live_dir", lambda graph: tmp_path / graph)
    monkeypatch.setattr(live, "present_ids", lambda graph, ids: set())
    monkeypatch.setattr(runner, "deliver", lambda connectors, graph: ([], []))
    dead = []

    def record_dead(graph, source, item, error, attempts):
        dead.append((item.artifact.id, type(error).__name__, attempts))

    monkeypatch.setattr(runner, "record_dead", record_dead)
    return dead


def answers(a, graph, events=None, replace=False):
    return {"alerts": [], "freshness_lag_ms": 1}


def test_a_sync_round_ingests_new_and_newer_items_once(offline, monkeypatch):
    calls = []

    def ingest(a, graph, events=None, replace=False):
        calls.append((a.id, replace, events))
        if "refuse" in a.text:
            raise live.LiveError("unknown author")
        return {"alerts": [{"id": "alert:1"}] if a.ts_ms == 1001 else [], "freshness_lag_ms": 5}

    monkeypatch.setattr(live, "ingest_artifact", ingest)
    events = [{"type": "issue_resolved", "quote": "Done"}]
    fake = Fake(
        "slack",
        [
            Item(msg(1), updated_ms=10, events=events),
            Item(msg(2, "no label here"), updated_ms=11),
            Item(msg(3, "[synthetic demo] refuse me"), updated_ms=12),
        ],
    )
    first = runner.sync([fake], "lore_demo")
    assert [r["id"] for r in first["ingested"]] == [msg(1).id]
    assert first["ingested"][0]["alerts"] == ["alert:1"]
    assert {r["id"]: r["why"] for r in first["refused"]} == {
        msg(2).id: "not labeled [synthetic demo]",
    }
    # refused by the live path: kept as a dead letter, with the item, not dropped (D107)
    assert [r["why"] for r in first["dead"]] == ["LiveError: unknown author"]
    assert offline == [(msg(3).id, "LiveError", 1)]
    assert calls == [(msg(1).id, False, events), (msg(3).id, False, None)]
    # the same versions again: nothing; a newer version of item 1: re-ingested as a replacement
    assert runner.sync([fake], "lore_demo")["ingested"] == []
    fake.items.append(Item(msg(1), updated_ms=20))
    assert [r["id"] for r in runner.sync([fake], "lore_demo")["ingested"]] == [msg(1).id]
    assert calls[-1] == (msg(1).id, True, None)
    assert runner.load_state("lore_demo")["slack"]["since_ms"] == 20


def test_an_item_that_may_still_go_in_waits_and_holds_the_cursor(offline, monkeypatch):
    """The spend cap, FalkorDB down, the lock busy: not the item's fault. It isn't counted,
    isn't marked seen, and the cursor stays below it, so the next round fetches it again."""
    from app import llm

    outcome = {msg(2).id: llm.SpendCapReached("cap")}

    def ingest(a, graph, events=None, replace=False):
        if a.id in outcome:
            raise outcome[a.id]
        return answers(a, graph)

    monkeypatch.setattr(live, "ingest_artifact", ingest)
    # item 3 went in at version 20, after item 2 (version 15) had to wait
    fake = Fake("slack", [Item(msg(1), updated_ms=10), Item(msg(2), 15), Item(msg(3), 20)])
    report = runner.sync([fake], "lore_demo")
    assert [r["id"] for r in report["ingested"]] == [msg(1).id, msg(3).id]
    assert [(r["id"], r["failures"]) for r in report["waiting"]] == [(msg(2).id, 0)]
    state = runner.load_state("lore_demo")["slack"]
    assert state["since_ms"] == 14  # below the waiting version, though item 3 (20) went in
    assert msg(2).id not in state["seen"]
    outcome.clear()  # the cap resets: the item goes in, the done ones aren't redone
    again = runner.sync([fake], "lore_demo")
    assert [r["id"] for r in again["ingested"]] == [msg(2).id]
    assert runner.load_state("lore_demo")["slack"]["since_ms"] == 20
    assert offline == []


def test_an_item_that_keeps_failing_becomes_a_dead_letter(offline, monkeypatch):
    def ingest(a, graph, events=None, replace=False):
        raise KeyError("a bug in a stage")

    monkeypatch.setattr(live, "ingest_artifact", ingest)
    fake = Fake("slack", [Item(msg(1), updated_ms=10)])
    for n in range(1, runner.POISON_AFTER):
        report = runner.sync([fake], "lore_demo")
        assert [r["failures"] for r in report["waiting"]] == [n]
    report = runner.sync([fake], "lore_demo")
    assert report["waiting"] == [] and [r["id"] for r in report["dead"]] == [msg(1).id]
    assert offline == [(msg(1).id, "KeyError", runner.POISON_AFTER)]
    state = runner.load_state("lore_demo")["slack"]
    assert state["since_ms"] == 10 and state["failures"] == {}  # the connector moves on


def test_a_tool_that_fails_to_answer_waits_and_the_others_go_on(offline, monkeypatch):
    monkeypatch.setattr(live, "ingest_artifact", answers)

    class Down(Fake):
        def fetch(self, since_ms):
            raise base.ConnectorError("Jira GET /search: 503")

    report = runner.sync([Down("jira"), Fake("slack", [Item(msg(1), 10)])], "lore_demo")
    assert report["waiting"] == [
        {"id": None, "source": "jira", "why": "ConnectorError: Jira GET /search: 503"}
    ]
    assert [r["id"] for r in report["ingested"]] == [msg(1).id]


def test_seen_versions_are_kept_a_day_behind_the_cursor_and_state_writes_are_atomic(
    offline, monkeypatch, tmp_path
):
    monkeypatch.setattr(live, "ingest_artifact", answers)
    day = runner.KEEP_SEEN_MS
    fake = Fake("slack", [Item(msg(1), updated_ms=10), Item(msg(2), updated_ms=10 + 2 * day)])
    runner.sync([fake], "lore_demo")
    assert set(runner.load_state("lore_demo")["slack"]["seen"]) == {msg(2).id}
    assert [p.name for p in (tmp_path / "lore_demo").iterdir()] == [runner.STATE]


def test_the_heartbeat_says_whether_the_loop_is_alive(offline):
    assert runner.worker_health("lore_demo") == {"status": "absent"}
    runner.beat("lore_demo", healthy=True, pause_s=30)
    assert runner.worker_health("lore_demo")["status"] == "ok"
    runner.beat("lore_demo", healthy=False, pause_s=60)
    beat = runner.worker_health("lore_demo")
    assert beat["status"] == "waiting"
    late = beat["next_by_ms"] + runner.HEARTBEAT_GRACE_MS + 1
    assert runner.worker_health("lore_demo", now_ms=late)["status"] == "stale"


def test_deliver_hands_each_action_to_the_tool_that_holds_its_target(monkeypatch):
    actions = [
        {"id": "act:1", "kind": "ticket_comment", "target_id": "pr:synthetic-github-7"},
        {"id": "act:2", "kind": "notify_owner", "target_id": "eid_b2"},
        {"id": "act:3", "kind": "doc_fix", "target_id": "doc:synthetic-notion-x"},
    ]
    marked = []

    def run(name, params=None, **k):
        if name == "actions_to_send":
            return type("R", (), {"rows": actions})
        marked.extend(params["rows"])
        return type("R", (), {"rows": []})

    monkeypatch.setattr(db, "run", run)
    github, slack = Fake("github", takes="pr:"), Fake("slack", takes="eid_")
    notion = Fake("notion", fails=True)
    sent, unsent = runner.deliver([notion, github, slack], "lore_demo")
    assert [(s["id"], s["via"]) for s in sent] == [("act:2", "slack")]
    assert [m["id"] for m in marked] == ["act:2"]
    # the ticket comment reached GitHub but Notion failed: not marked sent, so the next round
    # sends it again (GitHub's copy is found by its marker, D110)
    why = "notion: ConnectorError: token expired"
    assert unsent == [
        {"id": "act:1", "kind": "ticket_comment", "why": why},
        {"id": "act:3", "kind": "doc_fix", "why": why},
    ]


def test_the_repo_maps_parse():
    base.maps.cache_clear()
    m = base.maps()
    assert m.product == "VizForce" and m.default_person.startswith("eid_")
    assert m.source("slack")["channels"]["vizforce-bugs"].startswith("ch-")


# --- on the real server ----------------------------------------------------------------------
@pytest.mark.db
def test_actions_to_send_lists_approved_unsent_and_marking_removes_them(empty_graph):
    db.run_text(
        "CREATE (done:Alert {id: 'alert:1', status: 'approved'}),"
        " (busy:Alert {id: 'alert:2', status: 'responding'}),"
        " (:Action {id: 'act:1', kind: 'ticket_comment', status: 'approved', decided_ms: 2,"
        " target_kind: 'issue', target_id: 'iss:W:1', target_name: 'Slow export', draft: 'Hi'})"
        "-[:FOR]->(done),"
        " (:Action {id: 'act:2', kind: 'notify_owner', status: 'proposed', target_id: 'eid_1'})"
        "-[:FOR]->(done),"
        " (:Action {id: 'act:3', kind: 'doc_fix', status: 'approved', decided_ms: 1,"
        " target_id: 'msg:synthetic-slack-1'})-[:FOR]->(done),"
        # approved, but its response is still being written (D110): not sent yet
        " (:Action {id: 'act:4', kind: 'doc_fix', status: 'approved', decided_ms: 0,"
        " target_id: 'msg:synthetic-slack-2'})-[:FOR]->(busy)",
        label="actions",
        graph=empty_graph,
        readonly=False,
    )
    rows = db.run("actions_to_send", {"limit": 10}, graph=empty_graph).rows
    assert [r["id"] for r in rows] == ["act:3", "act:1"]  # oldest decision first
    db.run(
        "mark_action_sent",
        {"rows": [{"id": "act:3", "ref": "https://x/1", "ms": 5, "via": "slack"}]},
        graph=empty_graph,
        readonly=False,
    )
    assert [r["id"] for r in db.run("actions_to_send", {"limit": 10}, graph=empty_graph).rows] == [
        "act:1"
    ]


def test_a_dead_letter_keeps_its_item_and_retry_replays_it(empty_graph, monkeypatch):
    """The DeadLetter round trip on the real server: stored with the item and the reason,
    listed (without the item), counted in Graph health, replayed by retry and then gone."""
    from app.ingest import health

    events = [{"type": "issue_resolved", "quote": "Export is slow"}]
    item = Item(msg(1), updated_ms=10, events=events)
    runner.record_dead(empty_graph, "slack", item, live.LiveError("unknown author"), 1)
    runner.record_dead(empty_graph, "jira", Item(msg(2), 11), KeyError("bug"), 5)
    rows = runner.dead_letters(empty_graph)
    assert sorted((r["source"], r["reason"], r["attempts"]) for r in rows) == [
        ("jira", "kept failing", 5),
        ("slack", "refused", 1),
    ]
    assert "payload" not in rows[0]
    found = health.report(empty_graph)["dead_letters"]
    assert {(r["source"], r["reason"]): r["n"] for r in found} == {
        ("jira", "kept failing"): 1,
        ("slack", "refused"): 1,
    }

    replayed = []

    def ingest(a, graph, events=None, replace=False):
        if a.id == msg(2).id:
            raise live.LiveError("still unknown")
        replayed.append((a, events, replace))
        return answers(a, graph)

    monkeypatch.setattr(live, "ingest_artifact", ingest)
    out = runner.retry(empty_graph)
    assert [r["artifact_id"] for r in out["ingested"]] == [msg(1).id]
    assert out["failed"] == [{"id": f"dead:jira:{msg(2).id}", "why": "LiveError: still unknown"}]
    assert replayed == [(msg(1), events, False)]
    left = runner.dead_letters(empty_graph)
    assert [(r["source"], r["attempts"], r["error"]) for r in left] == [
        ("jira", 6, "LiveError: still unknown")
    ]
