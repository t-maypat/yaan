"""The MCP server (17.1): the agent's registry as MCP tools, `ask`, and the /mcp endpoint.
An in-process MCP client runs the real protocol against the fixture graph of agent_company."""

import json
from pathlib import Path

import anyio
import pytest
from fastapi.testclient import TestClient
from mcp import Client

from app import db, main, mcp_server
from app.agent import loop, tools
from app.config import get_settings

FIXTURES = Path(__file__).parent / "fixtures"
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@pytest.fixture
def company(empty_graph):
    text = (FIXTURES / "agent_company.cypher").read_text(encoding="utf-8")
    db.run_text(text, label="agent_company", graph=empty_graph, readonly=False)
    return empty_graph


def call(graph: str, name: str, args: dict):
    async def go():
        async with Client(mcp_server.build(graph)) as client:
            return await client.call_tool(name, args)

    return anyio.run(go)


# --- no database -----------------------------------------------------------------------------
def test_the_tools_are_the_agents_minus_raw_cypher_plus_ask(monkeypatch):
    names = [t.name for t in mcp_server.tool_list()]
    assert names == [n for n in tools.REGISTRY if n != "graph_query"] + ["ask"]
    who = next(t for t in mcp_server.tool_list() if t.name == "who_knows")
    assert who.description == tools.REGISTRY["who_knows"].spec["description"]
    assert who.input_schema["properties"]["product_id"] == mcp_server.PRODUCT  # no "" default
    assert who.annotations.read_only_hint is True
    # the registry's own schema is untouched (the agent still means "" = the question's product)
    assert (
        "empty string"
        in tools.REGISTRY["who_knows"].spec["input_schema"]["properties"]["product_id"][
            "description"
        ]
    )
    monkeypatch.setattr(get_settings(), "yaan_mcp_ask", False)
    assert "ask" not in [t.name for t in mcp_server.tool_list()]
    with pytest.raises(loop.ToolError, match="unknown tool"):
        mcp_server.run("ask", {"question": "Who?"}, "g")


def test_a_call_that_breaks_its_schema_is_a_tool_error_not_a_crash():
    # MCP clients aren't held to strict schemas: a missing or mistyped argument used to reach
    # the handler as a KeyError or a ValueError (D098)
    with pytest.raises(loop.ToolError, match="who_knows: 'topic' is a required property"):
        mcp_server.run("who_knows", {"product_id": None}, "g")
    with pytest.raises(loop.ToolError, match="metric: 'most_bugs' is not one of"):
        mcp_server.run("rank", {"metric": "most_bugs"}, "g")
    with pytest.raises(loop.ToolError, match="k: '5' is not of type 'integer'"):
        mcp_server.run("search_artifacts", {"query": "x", "k": "5"}, "g")
    with pytest.raises(loop.ToolError, match="Additional properties are not allowed"):
        mcp_server.run("get_artifact", {"artifact_id": "msg:1", "graph": "lore_herb"}, "g")


def test_null_values_and_a_left_out_product_pass_the_schema_check():
    spec = tools.REGISTRY["list_issues"].spec
    assert tools.input_problem(spec, {"status": None, "product_id": None}) is None
    assert tools.input_problem(spec, {}) is None  # the question's product fills in
    assert tools.input_problem(spec, []) == "arguments must be an object"


def test_ask_returns_the_checked_answer_compact(monkeypatch):
    def answer(question, product_id=None, graph=None, as_of_ms=None, viewer_id=None, payer=None):
        assert (question, product_id, graph, viewer_id) == ("Who fixed it?", None, "g", None)
        assert tools.iso(as_of_ms) == "2027-01-15T23:59:59Z"
        return {
            "answer_markdown": "Ann fixed it [1].",
            "abstain": False,
            "hidden_sources": 0,
            "citations": [{"n": 1, "artifact_id": "pr:1", "quote": "fix", "quote_ok": True}],
            "trace": [{"tool": "list_prs", "cypher": ["product_prs.cypher"], "ms": 3}],
        }

    monkeypatch.setattr(loop, "answer", answer)
    monkeypatch.setattr(mcp_server, "ask_limiter", mcp_server.Limiter(1))
    out = mcp_server.run("ask", {"question": "Who fixed it?", "as_of": "2027-01-15"}, "g")
    assert out["citations"] == [{"n": 1, "artifact_id": "pr:1", "quote": "fix"}]
    assert out["tools_used"] == [{"tool": "list_prs", "cypher": ["product_prs.cypher"]}]
    with pytest.raises(loop.ToolError, match="a minute"):  # the limiter
        mcp_server.run("ask", {"question": "Who fixed it?"}, "g")
    with pytest.raises(loop.ToolError, match="viewer_id must be"):
        mcp_server.run("ask", {"question": "Who fixed it?", "viewer_id": "boss"}, "g")
    with pytest.raises(loop.ToolError, match="3-500"):
        mcp_server.run("ask", {"question": "?"}, "g")


def test_mcp_over_http_lists_tools_and_refuses_foreign_hosts(monkeypatch):
    rpc = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    with TestClient(main.app, base_url="http://127.0.0.1:8000") as client:  # runs the lifespan
        listed = client.post("/mcp", headers=HEADERS, json=rpc).json()["result"]["tools"]
        names = [t["name"] for t in listed]
        assert "who_knows" in names and "graph_query" not in names
        evil = client.post("/mcp", headers={**HEADERS, "Host": "evil.example.com"}, json=rpc)
        assert evil.status_code == 421  # DNS-rebinding guard
    # without the lifespan the endpoint says so instead of hanging
    assert TestClient(main.app).post("/mcp", headers=HEADERS, json=rpc).status_code == 503


# --- on the fixture graph --------------------------------------------------------------------
@pytest.mark.db
def test_a_client_calls_a_graph_tool_through_mcp(company):
    result = call(company, "list_prs", {"product_id": "Widget"})
    assert not result.is_error
    prs = json.loads(result.content[0].text)["prs"]
    assert {p["id"] for p in prs} == {"pr:w1", "pr:w3"}


@pytest.mark.db
def test_mcp_errors_are_tool_errors_the_client_can_fix(company):
    bad = call(company, "list_prs", {"product_id": "Nope"})
    assert bad.is_error and "unknown product_id" in bad.content[0].text
    raw = call(company, "graph_query", {"cypher": "MATCH (n) RETURN n LIMIT 1"})
    assert raw.is_error and "unknown tool" in raw.content[0].text
    scoped = call(company, "who_knows", {"topic": "Export", "product_id": ""})
    assert scoped.is_error and "needs a product" in scoped.content[0].text


@pytest.mark.db
def test_a_signed_in_mcp_client_sees_only_what_its_person_may(company, tmp_path, monkeypatch):
    """D100: over HTTP the tools run for the token's person, redacted like the agent's own."""
    from app import auth
    from app.agent import access

    monkeypatch.setenv("YAAN_AUTH", "token")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    monkeypatch.setattr(mcp_server, "graph", lambda: company)
    policy = tmp_path / "permissions.yaml"
    policy.write_text("private_channels: ['bug-*']\nmeetings: attendees\n", encoding="utf-8")
    monkeypatch.setattr(access, "POLICY", policy)  # Access reads it through access.policy()
    access.policy.cache_clear()
    try:
        ann = auth.issue("eid_a1")  # never posted in bug-Widget
        rpc = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "get_artifact", "arguments": {"artifact_id": "msg:r1"}},
        }
        with TestClient(main.app, base_url="http://127.0.0.1:8000") as client:
            assert client.post("/mcp", headers=HEADERS, json=rpc).status_code == 401
            signed = {**HEADERS, "Authorization": f"Bearer {ann}"}
            got = client.post("/mcp", headers=signed, json=rpc).json()["result"]
            assert json.loads(got["content"][0]["text"]) == {"hidden_by_permissions": True}
            rpc["params"]["arguments"] = {"artifact_id": "msg:m1"}  # the open planning channel
            got = client.post("/mcp", headers=signed, json=rpc).json()["result"]
            assert json.loads(got["content"][0]["text"])["id"] == "msg:m1"
    finally:
        get_settings.cache_clear()
        access.policy.cache_clear()


def test_a_signed_in_clients_questions_count_against_their_person(monkeypatch):
    """D124: MCP asks over HTTP are limited and charged per signed-in person, not shared by
    every client of the process."""
    from app import limits

    payers = []
    empty = {"answer_markdown": "", "abstain": True, "citations": [], "trace": []}

    def answer(*a, payer=None, **k):
        payers.append(payer)
        return {**empty, "hidden_sources": 0}

    monkeypatch.setattr(loop, "answer", answer)
    monkeypatch.setattr(mcp_server, "ask_person_limiter", limits.RateLimiter(1))
    monkeypatch.setattr(mcp_server, "ask_limiter", limits.RateLimiter(0))  # anonymous: none left
    mcp_server.run("ask", {"question": "Who fixed it?"}, "g", viewer="eid_0000beef")
    with pytest.raises(loop.ToolError, match="a minute"):
        mcp_server.run("ask", {"question": "Who fixed it?"}, "g", viewer="eid_0000beef")
    mcp_server.run("ask", {"question": "Who fixed it?"}, "g", viewer="eid_12345678")
    assert payers == ["eid_0000beef", "eid_12345678"]


def test_an_org_graph_is_never_described_as_herb(monkeypatch):
    from app.config import get_settings

    assert "HERB" in mcp_server.instructions()  # the demo says what it is
    monkeypatch.setenv("YAAN_DATA_MODE", "org")
    get_settings.cache_clear()
    try:
        said = mcp_server.instructions()
        assert "HERB" not in said and "synthetic" not in said and "your team's own" in said
    finally:
        monkeypatch.delenv("YAAN_DATA_MODE")
        get_settings.cache_clear()
