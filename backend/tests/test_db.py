"""app/db.py without a server: query loading, read timeouts, write batching, error mapping.

A fake client stands in for FalkorDB. The same paths against the real server are covered by the
contract tests (Phase 1.7).
"""

import pytest
from redis.exceptions import ResponseError

from app import db
from app.config import Settings
from tests.conftest import require_falkordb


class FakeResult:
    """Mimics falkordb's QueryResult in compact mode."""

    def __init__(self, header=(), rows=(), run_time_ms=1.0, **stats):
        self.header = [[1, name] for name in header]
        self.result_set = [list(r) for r in rows]
        self.run_time_ms = run_time_ms
        for field in db.STAT_FIELDS:
            setattr(self, field, float(stats.get(field, 0)))


class FakePlan:
    plan = ["Limit", "    Node By Index Scan | (p:Person)"]


class FakeGraph:
    def __init__(self, fail_on_call=None, error="boom"):
        self.calls = []
        self.fail_on_call, self.error = fail_on_call, error

    def _record(self, kind, text, params, timeout=None):
        self.calls.append({"kind": kind, "text": text, "params": params, "timeout": timeout})
        if len(self.calls) == self.fail_on_call:
            raise ResponseError(self.error)

    def ro_query(self, text, params, timeout):
        self._record("ro_query", text, params, timeout)
        return FakeResult(["id", "n"], [["a", 1], ["b", 2]])

    def query(self, text, params):
        self._record("query", text, params)
        return FakeResult(nodes_created=len(params.get("rows", [])), properties_set=1)

    def explain(self, text, params):
        self._record("explain", text, params)
        return FakePlan()


@pytest.fixture
def fake(monkeypatch, tmp_path):
    """Points db at tmp query files and a fake client; returns {readonly: FakeGraph}."""
    (tmp_path / "get_people.cypher").write_text(
        "// get_people.cypher — t\nMATCH (p) RETURN p", encoding="utf-8"
    )
    (tmp_path / "upsert_people.cypher").write_text(
        "// upsert_people.cypher — t\nUNWIND $rows", encoding="utf-8"
    )
    settings = Settings(queries_dir=tmp_path, lore_graph="g_test", query_timeout_ms=2000)
    monkeypatch.setattr(db, "get_settings", lambda: settings)
    graphs = {True: FakeGraph(), False: FakeGraph()}

    class FakeClient:
        def __init__(self, readonly):
            self.readonly = readonly

        def select_graph(self, name):
            graphs[self.readonly].selected = name
            return graphs[self.readonly]

    monkeypatch.setattr(db, "client", lambda readonly: FakeClient(readonly))
    return graphs


def test_load_query_returns_the_file_text(fake):
    assert db.load_query("get_people").startswith("// get_people.cypher")


@pytest.mark.parametrize("name", ["../secrets", "Get", "get_people.cypher", ""])
def test_load_query_rejects_names_that_are_not_plain_file_stems(fake, name):
    with pytest.raises(ValueError, match="bad query name"):
        db.load_query(name)


def test_load_query_fails_loudly_on_a_missing_file(fake):
    with pytest.raises(FileNotFoundError, match="no_such_query"):
        db.load_query("no_such_query")


def test_read_goes_through_ro_query_with_the_default_timeout(fake):
    result = db.run("get_people", {"k": 5})
    call = fake[True].calls[0]
    assert call["kind"] == "ro_query" and call["timeout"] == 2000
    assert call["params"] == {"k": 5}
    assert fake[True].selected == "g_test"
    assert result.columns == ["id", "n"]
    assert result.rows == [{"id": "a", "n": 1}, {"id": "b", "n": 2}]
    assert result.batches == 1 and fake[False].calls == []


def test_read_passes_a_caller_timeout_and_graph(fake):
    db.run("get_people", graph="other", timeout_ms=120_000)
    assert fake[True].calls[0]["timeout"] == 120_000
    assert fake[True].selected == "other"


@pytest.mark.parametrize("timeout_ms", [0, -1, 120_001])
def test_read_timeout_outside_the_server_range_is_refused(fake, timeout_ms):
    with pytest.raises(ValueError, match="read timeout"):
        db.run("get_people", timeout_ms=timeout_ms)
    assert fake[True].calls == []


def test_write_refuses_a_timeout_because_the_server_ignores_it(fake):
    with pytest.raises(ValueError, match="A17"):
        db.run("upsert_people", {"rows": []}, readonly=False, timeout_ms=5000)


def test_write_splits_rows_into_batches_and_sums_counters(fake):
    rows = [{"id": i} for i in range(1201)]
    result = db.run("upsert_people", {"rows": rows, "now": 7}, readonly=False, batch_rows=500)
    calls = fake[False].calls
    assert [len(c["params"]["rows"]) for c in calls] == [500, 500, 201]
    assert [c["params"]["rows"][0]["id"] for c in calls] == [0, 500, 1000]
    assert all(c["params"]["now"] == 7 and c["kind"] == "query" for c in calls)
    assert result.batches == 3
    assert result.stats["nodes_created"] == 1201 and result.stats["properties_set"] == 3
    assert result.run_time_ms == pytest.approx(3.0)
    assert fake[True].calls == []


def test_write_with_no_rows_sends_nothing(fake):
    result = db.run("upsert_people", {"rows": []}, readonly=False)
    assert fake[False].calls == [] and result.batches == 0
    assert result.stats["nodes_created"] == 0


def test_write_without_a_rows_param_runs_once(fake):
    db.run("upsert_people", {"id": "x"}, readonly=False)
    assert len(fake[False].calls) == 1


def test_a_failed_batch_names_the_query_graph_and_rows(fake):
    fake[False].fail_on_call = 2
    with pytest.raises(db.QueryError) as caught:
        db.run("upsert_people", {"rows": [{"id": i} for i in range(1201)]}, readonly=False)
    message = str(caught.value)
    assert "upsert_people" in message and "g_test" in message and "rows 500-999 of 1201" in message
    assert not isinstance(caught.value, db.QueryTimeout)
    assert len(fake[False].calls) == 2  # stops at the failed batch


def test_a_server_timeout_becomes_query_timeout(fake):
    fake[True].fail_on_call, fake[True].error = 1, "Query timed out"
    with pytest.raises(db.QueryTimeout, match="get_people"):
        db.run("get_people")


@pytest.mark.parametrize(("readonly", "key"), [(True, "READER"), (False, "WRITER")])
def test_client_fails_loudly_without_a_password(monkeypatch, readonly, key):
    settings = Settings(
        _env_file=None, falkordb_reader_password=None, falkordb_writer_password=None
    )
    monkeypatch.setattr(db, "get_settings", lambda: settings)
    db.client.cache_clear()
    try:
        with pytest.raises(RuntimeError, match=f"FALKORDB_{key}_PASSWORD"):
            db.client(readonly)
    finally:
        db.client.cache_clear()


def test_explain_returns_plan_lines_through_the_reader(fake):
    assert db.explain("get_people", {"k": 1}) == FakePlan.plan
    assert fake[True].calls[0]["kind"] == "explain"


def test_require_falkordb_errors_instead_of_skipping(monkeypatch):
    class Down:
        class connection:  # noqa: N801 (mimics the client attribute)
            @staticmethod
            def ping():
                raise ConnectionError("connection refused")

    monkeypatch.setattr(db, "client", lambda readonly: Down())
    with pytest.raises(pytest.fail.Exception, match="docker compose up -d --wait"):
        require_falkordb()
