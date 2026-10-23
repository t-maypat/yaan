"""Retention and the access audit log (22.5): what connectors.yaml says is kept, items past it
refused at the door and forgotten daily, and who was shown what, by id only. The database
test forgets real nodes in a throwaway graph; every cache it could touch is a temp folder."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from app import audit, db, forget, main, mcp_server, org, retention
from app.agent import loop, tools
from app.config import get_settings
from app.connectors import base, runner
from app.connectors.base import Item, Maps
from app.ingest.normalize import Artifact
from app.sentinel import live

DAY = 86_400_000
NOW = 1_800_000_000_000


@pytest.fixture
def data(tmp_path, monkeypatch):
    """data/ and the caches in tmp_path, as settings from the environment: they survive the
    settings being re-read (org_mode does), unlike attributes set on one Settings object."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("AUDIT_DIR", str(tmp_path / "audit"))
    get_settings.cache_clear()
    yield tmp_path / "data"
    get_settings.cache_clear()


@pytest.fixture
def org_mode(monkeypatch):
    monkeypatch.setenv("LORE_DATA_MODE", "org")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def maps(**sources) -> Maps:
    return Maps(product="Acme", default_person=None, sources=sources, retention_days=365)


# --- policy -----------------------------------------------------------------------------------
def test_a_tool_keeps_the_default_its_own_days_or_everything(monkeypatch):
    m = maps(slack={"channels": {}}, jira={"retention_days": 90}, github={"retention_days": None})
    assert retention.policy(m) == {"slack": 365.0, "jira": 90.0}
    for bad in (0, -3, "a year", True):
        with pytest.raises(retention.RetentionError, match="number of days above 0"):
            retention.policy(maps(slack={"retention_days": bad}))


def test_only_org_mode_has_a_cutoff(monkeypatch, org_mode):
    m = maps(slack={})
    assert retention.cutoff_ms("slack", NOW, m) == NOW - 365 * DAY
    assert retention.cutoff_ms("notion", NOW, m) is None  # not configured: kept
    monkeypatch.setenv("LORE_DATA_MODE", "demo")
    get_settings.cache_clear()
    assert retention.cutoff_ms("slack", NOW, m) is None  # the demo keeps its data


# --- the door ---------------------------------------------------------------------------------
class Fake:
    name = "slack"

    def __init__(self, items, m):
        self.items, self.maps = items, m

    def fetch(self, since_ms):
        self.asked = since_ms
        return [i for i in self.items if i.updated_ms > since_ms]


def art(n: int, ts: int) -> Artifact:
    return Artifact(id=f"msg:slack-C1-{n}", kind="message", product_hint="Acme", channel_id="c",
                    ts_ms=ts, ts_iso="", author_eid="eid_a1", text="hi", raw_ref="t")  # fmt: skip


@pytest.fixture
def offline(tmp_path, monkeypatch, org_mode):
    monkeypatch.setattr(live, "live_dir", lambda graph: tmp_path / graph)
    monkeypatch.setattr(live, "present_ids", lambda graph, ids: set())
    monkeypatch.setattr(forget, "blocked", lambda artifacts, graph: {})
    monkeypatch.setattr(runner, "prepare", lambda graph, connectors: {})
    monkeypatch.setattr(runner, "deliver", lambda connectors, graph: ([], []))
    monkeypatch.setattr(runner, "fold_times", lambda products, graph: {})
    monkeypatch.setattr(live, "derive_if_due", lambda graph: None)
    monkeypatch.setattr(live, "algorithms_if_due", lambda graph: None)
    monkeypatch.setattr(retention, "run_if_due", lambda graph: None)
    monkeypatch.setattr(runner, "first_cursor", lambda c, now_ms=None: 0)
    went = []

    def batch(items, graph, workers=1):
        went.extend(a.id for a, _, _ in items)
        return [{"alerts": [], "freshness_lag_ms": 1} for _ in items]

    monkeypatch.setattr(live, "ingest_batch", batch)
    return went


def test_an_item_past_its_retention_never_goes_in(offline):
    now = int(time.time() * 1000)
    old, new = art(1, now - 400 * DAY), art(2, now - DAY)
    fake = Fake([Item(old, updated_ms=now - 10), Item(new, updated_ms=now - 5)], maps(slack={}))
    report = runner.sync([fake], "yaan_test_org")
    assert offline == [new.id]  # an edit today of a 400-day-old message stays out
    assert report["refused"] == [{"id": old.id, "why": "older than slack's retention"}]
    assert runner.load_state("yaan_test_org")["slack"]["seen"][old.id] == now - 10


def test_a_backfill_stops_at_the_retention(offline, monkeypatch):
    monkeypatch.setattr(live, "estimate_extraction", lambda arts, graph: {
        "expected_usd": 0.0, "worst_case_usd": 0.0})  # fmt: skip
    fake = Fake([], maps(slack={"retention_days": 30}))
    report = runner.backfill([fake], "yaan_test_org", days=365)
    assert report["sources"]["slack"]["since_ms"] > report["since_ms"]
    assert abs(fake.asked - (time.time() * 1000 - 30 * DAY)) < 60_000  # 30 days, not 365


# --- the audit log -----------------------------------------------------------------------------
def test_the_audit_log_names_items_never_words_and_forgets_an_erased_viewer(data):
    audit.record("g", "/artifact/{artifact_id:path}", "eid_a1", "api", ["msg:1", "msg:1", 7])
    audit.answered("g", {"retrieved": ["msg:2", "eid_b2"], "citations": [{"artifact_id": "msg:2"}],
                         "trace": [{"tool": "search_artifacts"}], "abstain": False},
                   "eid_b2", "web")  # fmt: skip
    audit.record("g", "who_knows", "eid_c3", "mcp", ["eid_b2", "msg:3"])
    lines = audit.read("g")
    assert [r["viewer"] for r in lines] == ["eid_c3", "eid_b2", "eid_a1"]  # newest first
    assert lines[2]["ids"] == ["msg:1"] and lines[1]["cited"] == ["msg:2"]
    assert lines[1]["tools"] == ["search_artifacts"] and lines[1]["abstained"] is False
    assert [r["what"] for r in audit.read("g", item="msg:2")] == ["answer"]
    assert audit.scrub("g", "eid_b2") == 1  # their line, and their id in what others saw
    assert [r["viewer"] for r in audit.read("g")] == ["eid_c3", "eid_a1"]
    assert audit.read("g")[0]["ids"] == ["msg:3"]


def test_old_audit_months_go_whole(data):
    folder = audit.folder("g")
    folder.mkdir(parents=True)
    for month in ("2025-09", "2025-10", "2026-10"):
        (folder / f"{month}.jsonl").write_text("{}\n", encoding="utf-8")
    now = time.mktime((2026, 10, 23, 0, 0, 0, 0, 0, 0))
    assert audit.prune("g", keep_months=13, now=now) == ["2025-09.jsonl"]  # Oct 2025 is month 13


def test_api_reads_and_mcp_tools_are_audited_and_a_failure_never_fails_the_read(
    data, monkeypatch
):
    monkeypatch.setattr(tools, "get_artifact", lambda aid, graph: {"id": aid, "text": "secret"})
    client = TestClient(main.app)
    assert client.get("/artifact/msg:x").json()["id"] == "msg:x"
    monkeypatch.setattr(loop, "call_tool", lambda *a, **k: {"results": [{"id": "msg:y"}]})
    mcp_server.run("search_artifacts", {"query": "q"}, "lore_demo", None)
    found = audit.read(get_settings().api_graph())
    assert [(r["what"], r["via"], r["ids"]) for r in found] == [
        ("search_artifacts", "mcp", ["msg:y"]),
        ("/artifact/{artifact_id:path}", "api", ["msg:x"]),
    ]
    assert "secret" not in json.dumps(found)
    assert client.get("/admin/audit?limit=1").json()["lines"][0]["what"] == "search_artifacts"

    def broken(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(audit, "record", broken)
    assert client.get("/artifact/msg:z").status_code == 200  # logged and counted instead
    assert any(n == "yaan_audit_failures_total" for n, _, _ in main.obs.METRICS.samples())


# --- the daily run, on a real graph -------------------------------------------------------------
@pytest.mark.db
def test_the_daily_run_forgets_what_is_past_and_marks_itself_done(
    empty_graph, data, org_mode, monkeypatch
):
    monkeypatch.setattr(forget, "clear_llm_cache", lambda: "stubbed")  # never the real caches
    monkeypatch.setattr(forget, "forget_vectors", lambda texts: 0)
    monkeypatch.setattr(base, "maps", lambda: maps(slack={}, jira={"retention_days": 30}))
    rows = [
        {"id": "msg:slack-C1-1", "ts": NOW - 400 * DAY},  # past slack's 365
        {"id": "msg:slack-C1-2", "ts": NOW - 100 * DAY},  # kept
        {"id": "msg:jira-KAN-1-0", "ts": NOW - 40 * DAY},  # past jira's 30
        {"id": "msg:github-x", "ts": NOW - 900 * DAY},  # no policy for this tool's prefix
    ]
    db.run_text(
        "UNWIND $rows AS r CREATE (:Artifact:Message {id: r.id, ts_ms: r.ts, text: 'words'})",
        {"rows": rows}, label="t", graph=empty_graph, readonly=False,
    )  # fmt: skip
    dry = retention.run(empty_graph, NOW, apply=False)
    assert dry["due"] == {"jira": 1, "slack": 1} and "forgotten" not in dry
    first = retention.run_if_due(empty_graph, NOW)
    assert first["forgotten"] == {"jira": 1, "slack": 1}
    left = db.run_text("MATCH (a:Artifact) RETURN a.id AS id ORDER BY id LIMIT 10", label="t",
                       graph=empty_graph).rows  # fmt: skip
    assert [r["id"] for r in left] == ["msg:github-x", "msg:slack-C1-2"]
    assert forget.forgotten(["msg:slack-C1-1", "msg:jira-KAN-1-0"], empty_graph) == {
        "msg:slack-C1-1", "msg:jira-KAN-1-0"}  # fmt: skip; tombstones: no sync brings them back
    assert retention.run_if_due(empty_graph, NOW + 1000) is None  # done for today
    assert org.active()
