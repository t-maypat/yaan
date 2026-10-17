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
    assert "empty string" in tools.REGISTRY["who_knows"].spec["input_schema"]["properties"][
        "product_id"
    ]["description"]
    monkeypatch.setattr(get_settings(), "lore_mcp_ask", False)
    assert "ask" not in [t.name for t in mcp_server.tool_list()]
    with pytest.raises(loop.ToolError, match="unknown tool"):
        mcp_server.run("ask", {"question": "Who?"}, "g")


def test_ask_returns_the_checked_answer_compact(monkeypatch):
    def answer(question, product_id=None, graph=None, as_of_ms=None):
        assert (question, product_id, graph) == ("Who fixed it?", None, "g")
        assert tools.iso(as_of_ms) == "2027-01-15T23:59:59Z"
        return {
            "answer_markdown": "Ann fixed it [1].",
            "abstain": False,
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
