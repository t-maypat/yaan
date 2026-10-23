"""Logs and metrics (22.4): JSON log lines with request ids, Prometheus text at /metrics for
admins or a scrape token, and the worker's counters carried on its heartbeat. No database:
FalkorDB's INFO and the graph reads are faked."""

import json
import logging

import pytest
from fastapi.testclient import TestClient

from app import auth, db, main, obs
from app.config import get_settings
from app.connectors import runner
from tests.test_auth import signed  # noqa: F401 (a fixture: org mode with token sign-in)


def test_a_log_line_is_one_json_object_with_the_request_id_and_fields():
    record = logging.LogRecord("yaan.api", logging.WARNING, __file__, 1, "GET /x 200", None, None)
    record.fields = {"status": 200, "person": None}
    token = obs.request_id.set("abc123")
    try:
        line = json.loads(obs.JsonFormatter().format(record))
    finally:
        obs.request_id.reset(token)
    assert line["level"] == "warning" and line["component"] == "yaan.api"
    assert line["msg"] == "GET /x 200" and line["request_id"] == "abc123"
    assert line["status"] == 200 and line["ts"].endswith("Z")


def test_prometheus_text_names_each_family_once_and_escapes_labels():
    m = obs.Metrics()
    m.inc("yaan_http_requests_total", route="/a", method="GET", status="200")
    m.inc("yaan_http_requests_total", route="/a", method="GET", status="200")
    m.observe("yaan_http_request_seconds", 0.25, route="/a")
    samples = [*m.samples(), ("yaan_odd", {"why": 'say "hi"\\\n'}, 1.5)]
    text = obs.render(samples, {"yaan_odd": ("gauge", "an odd one")})
    assert text.count("# TYPE yaan_http_requests_total counter") == 1
    assert 'yaan_http_requests_total{method="GET",route="/a",status="200"} 2' in text
    lines = text.splitlines()
    summary = [i for i, ln in enumerate(lines) if ln.startswith("yaan_http_request_seconds")]
    assert summary == list(range(summary[0], summary[0] + 2))  # sum and count together
    assert "# TYPE yaan_http_request_seconds summary" in text
    assert 'yaan_odd{why="say \\"hi\\"\\\\\\n"} 1.5' in text


@pytest.fixture
def quiet(monkeypatch):
    """The graph-side readings faked: FalkorDB at 1 of 2 GB, a worker that ran 3 rounds."""
    monkeypatch.setattr(db, "storage", lambda: {
        "used_bytes": 2**30, "cap_bytes": 2**31, "last_save_ts": 1_800_000_000, "aof": False,
        "last_save_ok": True, "used_mb": 1024, "cap_mb": 2048, "used_pct": 50.0,
    })  # fmt: skip
    monkeypatch.setattr(runner, "dead_letters", lambda g: [{"id": "dead:1"}])
    monkeypatch.setattr(runner, "worker_health", lambda g: {"status": "ok", "last_round_ms": 5000})
    monkeypatch.setattr(runner, "heartbeat_metrics", lambda g: {
        "rounds_total": 3, "items": {"ingested": 7, "dead": 1}, "round_seconds": 0.4,
    })  # fmt: skip
    monkeypatch.setattr(runner, "load_state", lambda g: {
        "slack": {"since_ms": 1, "retry_ms": 2**62}, "github": {"since_ms": 1}, "slack_cards": {},
    })  # fmt: skip


def test_a_request_gets_an_id_a_counter_and_one_log_line_without_its_query(quiet, caplog):
    client = TestClient(main.app)
    with caplog.at_level(logging.INFO, logger="yaan"):
        r = client.get("/health?secret=words")
    rid = r.headers["x-request-id"]
    assert len(rid) == 16
    line = next(x for x in caplog.records if x.name == "yaan.api" and "/health" in x.getMessage())
    assert line.fields["path"] == "/health" and "secret" not in line.getMessage()
    assert line.fields["route"] == "/health" and line.fields["status"] == 200
    assert client.get("/health", headers={"X-Request-ID": "trace-42"}).headers["x-request-id"] == (
        "trace-42"
    )  # a plain id is kept, anything else replaced
    assert client.get("/health", headers={"X-Request-ID": "a b\n"}).headers["x-request-id"] != "a b"
    text = client.get("/metrics").text  # the demo: no sign-in, everyone is an admin
    assert 'yaan_http_requests_total{method="GET",route="/health",status="200"}' in text


def test_metrics_carry_the_graph_spend_and_the_workers_counters(quiet):
    text = TestClient(main.app).get("/metrics").text
    for line in (
        "yaan_graph_memory_bytes 1073741824",
        "yaan_graph_memory_cap_bytes 2147483648",
        "yaan_dead_letters 1",
        "yaan_worker_up 1",
        "yaan_worker_last_round_timestamp_seconds 5",
        "yaan_worker_rounds_total 3",
        'yaan_worker_items_total{outcome="ingested"} 7',
        'yaan_connector_backoff{source="slack"} 1',
        'yaan_connector_backoff{source="github"} 0',
        'yaan_llm_spend_usd{kind="all"}',
        "# TYPE yaan_worker_items_total counter",
    ):
        assert line in text, line
    assert "slack_cards" not in text  # state that isn't a connector


def test_metrics_need_an_admin_or_the_scrape_token(signed, quiet, monkeypatch):  # noqa: F811
    from app import org

    anon = TestClient(main.app)
    assert anon.get("/metrics").status_code == 401
    priya = next(e for e, p in org.roster().items() if p["name"] == "Priya Shah")
    ken = TestClient(main.app, headers={"Authorization": f"Bearer {auth.issue('eid_0000beef')}"})
    assert ken.get("/metrics").status_code == 403
    admin = TestClient(main.app, headers={"Authorization": f"Bearer {auth.issue(priya)}"})
    assert admin.get("/metrics").status_code == 200
    monkeypatch.setenv("LORE_METRICS_TOKEN", "scrape-secret")
    get_settings.cache_clear()
    scraper = TestClient(main.app, headers={"Authorization": "Bearer scrape-secret"})
    assert scraper.get("/metrics").status_code == 200
    assert TestClient(main.app, headers={"Authorization": "Bearer nope"}).get(
        "/metrics"
    ).status_code == 401


def test_the_worker_counts_rounds_and_writes_them_on_its_heartbeat(tmp_path, monkeypatch):
    from app.sentinel import live

    monkeypatch.setattr(live, "live_dir", lambda graph: tmp_path / graph)
    monkeypatch.setattr(runner, "TOTALS", {"rounds_total": 0, "items": {}})
    report = {
        "ingested": [{"fold_ms": 1200}, {"fold_ms": 300}],
        "dead": [{}],
        "forgotten": [{"items": 4}],
    }
    runner.count_round(report)
    runner.count_round({"ingested": [{"fold_ms": 10}]})
    assert runner.TOTALS["rounds_total"] == 2
    assert runner.TOTALS["items"]["ingested"] == 3 and runner.TOTALS["items"]["forgotten"] == 4
    assert runner.TOTALS["fold_seconds_max"] == 0.01  # the last round's slowest
    runner.beat("g", True, 30, metrics=runner.TOTALS)
    assert runner.heartbeat_metrics("g")["items"]["dead"] == 1
    assert runner.worker_health("g")["status"] == "ok"  # the health check reads it as before
