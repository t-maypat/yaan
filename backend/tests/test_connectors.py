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


def test_a_sync_round_ingests_new_and_newer_items_once(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "live_dir", lambda graph: tmp_path / graph)
    monkeypatch.setattr(runner, "deliver", lambda connectors, graph: ([], []))
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
        msg(3).id: "unknown author",
    }
    assert calls == [(msg(1).id, False, events), (msg(3).id, False, None)]
    # the same versions again: nothing; a newer version of item 1: re-ingested as a replacement
    assert runner.sync([fake], "lore_demo")["ingested"] == []
    fake.items.append(Item(msg(1), updated_ms=20))
    assert [r["id"] for r in runner.sync([fake], "lore_demo")["ingested"]] == [msg(1).id]
    assert calls[-1] == (msg(1).id, True, None)
    assert runner.load_state("lore_demo")["slack"]["since_ms"] == 20


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
    assert [(s["id"], s["via"]) for s in sent] == [("act:1", "github"), ("act:2", "slack")]
    assert [m["id"] for m in marked] == ["act:1", "act:2"]
    assert unsent == [{"id": "act:3", "kind": "doc_fix", "why": "notion: token expired"}]


def test_the_repo_maps_parse():
    base.maps.cache_clear()
    m = base.maps()
    assert m.product == "VizForce" and m.default_person.startswith("eid_")
    assert m.source("slack")["channels"]["vizforce-bugs"].startswith("ch-")


# --- on the real server ----------------------------------------------------------------------
@pytest.mark.db
def test_actions_to_send_lists_approved_unsent_and_marking_removes_them(empty_graph):
    db.run_text(
        "CREATE (:Action {id: 'act:1', kind: 'ticket_comment', status: 'approved', decided_ms: 2,"
        " target_kind: 'issue', target_id: 'iss:W:1', target_name: 'Slow export', draft: 'Hi'}),"
        " (:Action {id: 'act:2', kind: 'notify_owner', status: 'proposed', target_id: 'eid_1'}),"
        " (:Action {id: 'act:3', kind: 'doc_fix', status: 'approved', decided_ms: 1,"
        " target_id: 'msg:synthetic-slack-1'})",
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
