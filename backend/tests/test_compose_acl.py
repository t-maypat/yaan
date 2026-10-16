"""The Redis ACL users in docker-compose.yml grant exactly what D018 says, nothing more.

A static check: any new permission must be added here on purpose. The live behavior (what the
server refuses) is covered by the contract tests (A12).
"""

import re

import pytest

from app.config import REPO_ROOT

READER = {"+graph.ro_query", "+graph.explain", "+graph.list", "+ping", "+info", "+client|setinfo"}
WRITER = READER | {
    "+graph.query",
    "+graph.profile",
    "+graph.delete",
    "+graph.copy",
    "+graph.constraint",
    "+graph.memory",
}


def users() -> dict[str, list[str]]:
    """{user variable: its rule tokens} parsed from the REDIS_ARGS block."""
    text = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    block = re.search(r"REDIS_ARGS: >-\n((?: {8}.*\n)+)", text)
    assert block, "REDIS_ARGS block not found in docker-compose.yml"
    args = " ".join(block.group(1).split())
    parsed = {}
    for chunk in args.split("--user ")[1:]:
        name, *rules = chunk.split()
        parsed[re.sub(r"[${}]|:-.*", "", name)] = rules
    return parsed


def grants(rules: list[str]) -> set[str]:
    return {r for r in rules if r.startswith("+")}


def test_reader_may_only_read_and_plan():
    rules = users()["FALKORDB_READER_USER"]
    assert "-@all" in rules and "allcommands" not in rules
    assert grants(rules) == READER


def test_writer_has_graph_commands_only():
    rules = users()["FALKORDB_WRITER_USER"]
    assert "-@all" in rules and "allcommands" not in rules
    assert grants(rules) == WRITER


@pytest.mark.parametrize("user", ["FALKORDB_READER_USER", "FALKORDB_WRITER_USER"])
def test_app_users_need_a_password_from_env(user):
    password = next(r for r in users()[user] if r.startswith(">"))
    assert ":?" in password, "the password must come from .env and fail loudly when missing"


def test_default_user_is_password_protected():
    rules = users()["default"]
    assert any(r.startswith(">${FALKORDB_ADMIN_PASSWORD:?") for r in rules)
    assert "nopass" not in rules
