"""MCP server (Phase 17.1, D073; spec docs/CONTEXT.md §9.8).

Any MCP client (Claude Code, Claude Desktop, Cursor, another agent) gets Lore's read-only graph
tools with the same names, descriptions and JSON schemas the answer agent uses: they are built
from `tools.REGISTRY`, and every call goes through `loop.call_tool`, so product ids are checked
and errors come back as tool errors exactly as for the agent. `graph_query` stays inside the
agent's guard (§9.8). `ask` (on unless LORE_MCP_ASK=0) runs the whole agent and returns an answer
whose quotes were checked word for word, behind the daily spend cap and a per-minute limit.

Two transports: stdio (`lore mcp`) and streamable HTTP at `/mcp` on the API (`HttpEndpoint`,
started in the API's lifespan). Everything reads `LORE_API_GRAPH`, like the API.
"""

import json
import re
import threading
import time
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import anyio
import mcp_types as types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings

from app import __version__, db, llm
from app.agent import loop, tools
from app.config import get_settings

EXCLUDED = frozenset({"graph_query"})  # raw Cypher stays behind the agent's guard (§9.8)
ASK_PER_MINUTE = 10  # all MCP clients together; the daily spend cap is the second guard
PRODUCT = {
    "type": "string",
    "description": "a product id such as VizForce; resolve_entity maps product names and "
    "codenames to ids",
}
INSTRUCTIONS = (
    "Lore is a time-aware graph of a software company's Slack messages, meetings, documents "
    "and pull requests, linked to people, customers, issues and decisions with validity "
    "windows (HERB, a public synthetic dataset, plus labeled synthetic demo messages). Every "
    "tool is read-only and returns artifact ids with their text: cite those ids for any claim. "
    "Most tools need a product_id (resolve_entity finds it). `ask` returns a complete answer "
    "whose quotes Lore has already checked word for word against the sources."
)
ASK = types.Tool(
    name="ask",
    description=(
        "Ask Lore a question in plain language. Lore's own agent picks the graph tools, answers, "
        "and checks every quote word for word against its source; sentences it can't support "
        "are dropped, and with nothing supported it says 'Not found in Lore's graph'. Returns "
        "the answer in markdown with numbered citations (artifact id + verbatim quote). Costs "
        "an LLM call; prefer the specific tools for simple lookups."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "the question, 3-500 characters"},
            "product_id": {
                "type": "string",
                "description": "the product the question is about (recommended), e.g. VizForce",
            },
            "as_of": {
                "type": "string",
                "description": "answer for the state at this moment, ISO date or datetime (UTC)",
            },
            "viewer_id": {
                "type": "string",
                "description": "answer as this employee (eid_...): sources they may not see are "
                "hidden, and the result says how many",
            },
        },
        "required": ["question"],
        "additionalProperties": False,
    },
    annotations=types.ToolAnnotations(read_only_hint=True, open_world_hint=False),
)


def graph() -> str:
    return get_settings().api_graph()


def tool_list() -> list[types.Tool]:
    """The registry's tools as MCP tools (product_id described for a client with no question
    scope), plus `ask` when it is on."""
    out = []
    for name, tool in tools.REGISTRY.items():
        if name in EXCLUDED:
            continue
        schema = json.loads(json.dumps(tool.spec["input_schema"]))  # a copy we may edit
        if "product_id" in schema["properties"]:
            schema["properties"]["product_id"] = PRODUCT
        out.append(
            types.Tool(
                name=name,
                description=tool.spec["description"],
                input_schema=schema,
                annotations=types.ToolAnnotations(read_only_hint=True, open_world_hint=False),
            )
        )
    if get_settings().lore_mcp_ask:
        out.append(ASK)
    return out


class Limiter:
    """A sliding one-minute window shared by every MCP client of this process."""

    def __init__(self, per_minute: int):
        self.per_minute, self.hits, self.lock = per_minute, deque(), threading.Lock()

    def allow(self) -> bool:
        now = time.monotonic()
        with self.lock:
            while self.hits and now - self.hits[0] > 60:
                self.hits.popleft()
            if len(self.hits) >= self.per_minute:
                return False
            self.hits.append(now)
            return True


ask_limiter = Limiter(ASK_PER_MINUTE)


def ask(args: dict[str, Any], graph_name: str) -> dict:
    """The full agent for one question: the answer and its checked citations, compact."""
    question = str(args.get("question") or "").strip()
    if not 3 <= len(question) <= 500:
        raise loop.ToolError("question must be 3-500 characters")
    try:
        as_of_ms = tools.parse_as_of(args.get("as_of")) if args.get("as_of") else None
    except ValueError as e:
        raise loop.ToolError(str(e)) from e
    viewer = args.get("viewer_id") or None
    if viewer and not re.fullmatch(r"eid_[0-9a-f]{8}", viewer):
        raise loop.ToolError(f"viewer_id must be an employee id like eid_1a2b3c4d, not {viewer!r}")
    product = args.get("product_id") or None
    if product and product not in tools.product_ids(graph_name, refresh=True):
        raise loop.ToolError(f"unknown product_id {product!r}: resolve_entity finds the id")
    if not ask_limiter.allow():  # last: a malformed request doesn't use up a slot
        raise loop.ToolError(f"at most {ASK_PER_MINUTE} questions a minute: try again shortly")
    out = loop.answer(question, product, graph=graph_name, as_of_ms=as_of_ms, viewer_id=viewer)
    return {
        "answer_markdown": out["answer_markdown"],
        "abstain": out["abstain"],
        "citations": [
            {"n": c["n"], "artifact_id": c["artifact_id"], "quote": c["quote"]}
            for c in out["citations"]
        ],
        "as_of": tools.iso(as_of_ms),
        "viewer_id": viewer,
        "hidden_sources": out["hidden_sources"],
        "tools_used": [{"tool": t["tool"], "cypher": t.get("cypher", [])} for t in out["trace"]],
    }


def run(name: str, args: dict[str, Any], graph_name: str | None = None) -> dict:
    """One MCP tool call, blocking (the handlers run it in a worker thread)."""
    graph_name = graph_name or graph()
    if name == "ask" and get_settings().lore_mcp_ask:
        return ask(args, graph_name)
    allowed = frozenset(tools.REGISTRY) - EXCLUDED
    return loop.call_tool(name, args, tools.Scope(None, None, graph_name), allowed)


def build(graph_name: str | None = None) -> Server:
    """The MCP server: list the tools, run a call, return its JSON (or a tool error)."""

    async def list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=tool_list())

    async def call_tool(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        args = params.arguments or {}
        try:
            out = await anyio.to_thread.run_sync(run, params.name, args, graph_name)
        except (loop.ToolError, db.QueryError, llm.LLMError) as e:
            # shown to the client, as to the agent: it can fix its arguments and retry
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"Error: {e}")], is_error=True
            )
        text = json.dumps(loop.fence(out), ensure_ascii=False, default=str)
        return types.CallToolResult(content=[types.TextContent(type="text", text=text)])

    return Server(
        "lore",
        version=__version__,
        title="Lore",
        description="A time-aware company brain on FalkorDB: answers with verbatim sources.",
        instructions=INSTRUCTIONS,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


async def serve_stdio() -> None:
    from mcp.server.stdio import stdio_server

    server = build()
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


class HttpEndpoint:
    """`/mcp` on the API: a stateless streamable-HTTP endpoint (JSON responses). Its session
    manager can run once only, so each API lifespan builds a fresh one (`running`)."""

    def __init__(self) -> None:
        self.manager: StreamableHTTPSessionManager | None = None

    @asynccontextmanager
    async def running(self) -> AsyncIterator[None]:
        hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*", *get_settings().lore_mcp_hosts]
        manager = StreamableHTTPSessionManager(
            app=build(),
            json_response=True,
            stateless=True,
            security_settings=TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=hosts,
                allowed_origins=[f"http://{h}" for h in hosts] + [f"https://{h}" for h in hosts],
            ),
        )
        async with manager.run():
            self.manager = manager
            try:
                yield
            finally:
                self.manager = None

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if self.manager is None:  # the API was started without its lifespan
            body = b'{"detail": "the MCP endpoint is not running"}'
            await send({"type": "http.response.start", "status": 503, "headers": []})
            await send({"type": "http.response.body", "body": body})
            return
        await self.manager.asgi_app(scope, receive, send)
