"""Shared test fixtures (Phase 1.6).

Database tests are marked `db` (by hand, or by the hook below for any test using a database
fixture) and get their own throwaway graph, `yaan_test_<random>`, with the
full schema applied and checked. They ERROR, never skip, when FalkorDB is down: a skipped test
reads as "fine" in a summary, and a database that silently isn't there is exactly what we must
notice. Without Docker, run `pytest -m "not db"` on purpose.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from app import db, schema
from app.config import get_settings

TEST_GRAPH_PREFIX = "yaan_test_"
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session", autouse=True)
def own_caches(tmp_path_factory) -> Iterator[None]:
    """No test reaches the real disk caches or audit log (D139). In org mode `forget` empties
    the LLM cache, and the org fixtures moved DATA_DIR but not CACHE_DIR (a setting of its
    own): every DB run of test_forget wiped data/cache/llm, the eval's free replay. API tests
    would write audit lines into data/audit. A test that sets either itself still wins."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("CACHE_DIR", str(tmp_path_factory.mktemp("cache")))
        mp.setenv("AUDIT_DIR", str(tmp_path_factory.mktemp("audit")))
        get_settings.cache_clear()
        yield
    get_settings.cache_clear()


def require_falkordb() -> None:
    """Fail the test (an error, not a skip) unless both app users can reach FalkorDB."""
    try:
        for readonly in (False, True):
            db.client(readonly).connection.ping()
    except Exception as e:  # any reason: server down, wrong password, users missing
        pytest.fail(
            f"FalkorDB is not reachable as the writer and reader: {type(e).__name__}: {e}\n"
            "Start it with `docker compose up -d --wait`, or deselect these tests with "
            '`-m "not db"`.',
            pytrace=False,
        )


@pytest.hookimpl(tryfirst=True)  # before `-m` deselects, so the marker it adds counts
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Every test that reaches FalkorDB through a fixture is a `db` test, marked or not: a
    forgotten marker made `pytest -m "not db"` error on a machine without Docker."""
    for item in items:
        if "falkordb" in getattr(item, "fixturenames", ()):
            item.add_marker(pytest.mark.db)


@pytest.fixture(scope="session")
def falkordb() -> None:
    """Checks the server once per run and sweeps test graphs left behind by an interrupted run."""
    require_falkordb()
    writer = db.client(readonly=False)
    for name in writer.list_graphs():
        if name.startswith(TEST_GRAPH_PREFIX):
            writer.select_graph(name).delete()


@pytest.fixture
def empty_graph(falkordb) -> Iterator[str]:
    """A fresh graph with every index from 00_schema.cypher and no data; deleted afterwards."""
    name = f"{TEST_GRAPH_PREFIX}{uuid.uuid4().hex[:12]}"
    try:
        schema.apply(name)
        problems = schema.verify(name, wait_s=10)
        assert not problems, f"schema did not apply cleanly: {problems}"
        yield name
    finally:
        writer = db.client(readonly=False)
        if name in writer.list_graphs():  # deleting a graph that was never created is an error
            writer.select_graph(name).delete()


@pytest.fixture
def graph(empty_graph) -> str:
    """`empty_graph` plus the tiny company in fixtures/tiny_company.cypher."""
    text = (FIXTURES / "tiny_company.cypher").read_text(encoding="utf-8")
    db.run_text(text, label="tiny_company", graph=empty_graph, readonly=False)
    return empty_graph
