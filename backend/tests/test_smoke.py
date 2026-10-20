"""Skeleton smoke tests: the package imports, paths resolve, the API and CLI answer."""

import pytest
from fastapi.testclient import TestClient

from app import __version__, db
from app.cli import PENDING, main
from app.config import get_settings
from app.main import app


class FakeClient:
    def __init__(self, error=None):
        self.error = error
        self.connection = self

    def ping(self):
        if self.error:
            raise self.error
        return True


@pytest.fixture
def no_worker(monkeypatch):
    from app.connectors import runner

    monkeypatch.setattr(runner, "worker_health", lambda graph: {"status": "absent"})


def test_health_is_ok_when_falkordb_answers(monkeypatch, no_worker):
    monkeypatch.setattr(db, "client", lambda readonly: FakeClient())
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "version": __version__,
        "falkordb": "ok",
        "worker": "absent",
    }


def test_health_is_503_when_falkordb_is_down(monkeypatch, no_worker):
    error = ConnectionError("Error 10061 connecting to localhost:6379")
    monkeypatch.setattr(db, "client", lambda readonly: FakeClient(error))
    response = TestClient(app).get("/health")
    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "version": __version__,
        "falkordb": "ConnectionError",  # the type only: no host or port details leak
        "worker": "absent",
    }


def test_health_reports_a_stalled_worker_but_keeps_serving(monkeypatch):
    from app.connectors import runner

    monkeypatch.setattr(db, "client", lambda readonly: FakeClient())
    monkeypatch.setattr(runner, "worker_health", lambda graph: {"status": "stale"})
    response = TestClient(app).get("/health")
    assert response.status_code == 200 and response.json()["worker"] == "stale"


@pytest.mark.db
def test_health_against_the_real_server(falkordb):
    assert TestClient(app).get("/health").json()["falkordb"] == "ok"


def test_paths_resolve_from_repo_root():
    assert (get_settings().queries_dir / "00_schema.cypher").is_file()


def test_unbuilt_commands_fail_loudly(capsys, monkeypatch):
    # every planned command is built now (seed was last, 15.2); the guard still has to hold
    monkeypatch.setitem(PENDING, "teleport", ("Phase 99", "not a real command"))
    assert main(["teleport"]) == 2
    assert "Phase 99" in capsys.readouterr().err
