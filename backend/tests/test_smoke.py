"""Skeleton smoke tests: the package imports, paths resolve, the API and CLI answer."""

import pytest
from fastapi.testclient import TestClient

from app import __version__
from app.cli import PENDING, main
from app.config import get_settings
from app.main import app


def test_health():
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_paths_resolve_from_repo_root():
    assert (get_settings().queries_dir / "00_schema.cypher").is_file()


@pytest.mark.parametrize("command", sorted(PENDING))
def test_unbuilt_commands_fail_loudly(command, capsys):
    assert main([command]) == 2
    phase, _ = PENDING[command]
    assert phase in capsys.readouterr().err
