"""MCP server (Phase 17.1, D073; spec docs/CONTEXT.md §9.8).

Any MCP client (Claude Code, Claude Desktop, Cursor, another agent) gets Yaan's read-only graph
tools with the same names, descriptions and JSON schemas the answer agent uses: they are built
from `tools.REGISTRY`, and every call goes through `loop.call_tool`, so product ids are checked
and errors come back as tool errors exactly as for the agent. `graph_query` stays inside the
agent's guard (§9.8). `ask` (on unless YAAN_MCP_ASK=0) runs the whole agent and returns an answer
whose quotes were checked word for word, behind the daily spend cap and a per-minute limit.

Two transports: stdio (`yaan mcp`) and streamable HTTP at `/mcp` on the API (`HttpEndpoint`,
started in the API's lifespan). Everything reads `YAAN_API_GRAPH`, like the API.
"""

import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import anyio
import mcp_types as types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings

from app import __version__, audit, db, llm
from app.agent import loop, tools
from app.config import get_settings
from app.limits import RateLimiter, person_limits

EXCLUDED = frozenset({"graph_query"})  # raw Cypher stays behind the agent's guard (§9.8)
ASK_PER_MINUTE = 10  # stdio and anonymous clients together; signed-in people: their own
PRODUCT = {
    "type": "string",
    "description": "a product id (the demo's include VizForce); resolve_entity maps names and "
    "codenames to ids",
}
# What a client is told the graph holds (D128): the demo's synthetic company, or a team's own
# tools in org mode, which hold no HERB data and must never be described as if they did.
ORG_INSTRUCTIONS = (
    "Yaan is a time-aware graph of your team's own tools (Slack, GitHub, Jira, Notion and the "
    "others connected): messages, pull requests, tickets and pages, linked to people, issues "
    "and decisions with validity windows. Every tool is read-only and returns artifact ids "
    "with their text: cite those ids for any claim. Results are limited to what the signed-in "
    "person may read. Most tools need a product_id (resolve_entity finds it). `ask` returns a "
    "complete answer whose quotes Yaan has already checked word for word against the sources."
)
INSTRUCTIONS = (
    "Yaan is a time-aware graph of a software company's Slack messages, meetings, documents "
    "and pull requests, linked to people, customers, issues and decisions with validity "
    "windows (HERB, a public synthetic dataset, plus labeled synthetic demo messages). Every "
    "tool is read-only and returns artifact ids with their text: cite those ids for any claim. "
    "Most tools need a product_id (resolve_entity finds it). `ask` returns a complete answer "
    "whose quotes Yaan has already checked word for word against the sources."
)
def instructions() -> str:
    return ORG_INSTRUCTIONS if get_settings().yaan_data_mode == "org" else INSTRUCTIONS


ASK = types.Tool(
    name="ask",
    description=(
        "Ask Yaan a question in plain language. Yaan's own agent picks the graph tools, answers, "
        "and checks every quote word for word against its source; sentences it can't support "
        "are dropped, and with nothing supported it says 'Not found in Yaan's graph'. Returns "
        "the answer in markdown with numbered citations (artifact id + verbatim quote). Costs "
        "an LLM call; prefer the specific tools for simple lookups."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "the question, 3-500 characters"},
            "product_id": {
                "type": "string",
                "description": "the product the question is about (recommended)",
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
    if get_settings().yaan_mcp_ask:
        out.append(ASK)
    return out


Limiter = RateLimiter
ask_limiter = Limiter(ASK_PER_MINUTE)  # one budget for stdio and anonymous HTTP clients
# a signed-in HTTP client's person: the same per-person limits as /ask and Slack (D124)
ask_person_limiter, ask_person_daily_limiter = person_limits()


def ask(args: dict[str, Any], graph_name: str, principal: str | None = None) -> dict:
    """The full agent for one question: the answer and its checked citations, compact.
    `principal`: the signed-in person, whose limits and daily budget it counts against."""
    question = str(args.get("question") or "").strip()
    if not 3 <= len(question) <= 500:
        raise loop.ToolError("question must be 3-500 characters")
    try:
        as_of_ms = tools.parse_as_of(args.get("as_of")) if args.get("as_of") else None
    except ValueError as e:
        raise loop.ToolError(str(e)) from e
    viewer = args.get("viewer_id") or None
    if viewer and not re.fullmatch(r"eid_[0-9a-f]{8}(?:[0-9a-f]{8})?", viewer):
        raise loop.ToolError(f"viewer_id must be an employee id like eid_1a2b3c4d, not {viewer!r}")
    product = args.get("product_id") or None
    if product and product not in tools.product_ids(graph_name, refresh=True):
        raise loop.ToolError(f"unknown product_id {product!r}: resolve_entity finds the id")
    # last: a malformed request doesn't use up a slot
    if principal:
        if not ask_person_limiter.allow(principal):
            raise loop.ToolError(
                f"at most {ask_person_limiter.per_minute} questions a minute: try again shortly"
            )
        if not ask_person_daily_limiter.allow(principal):
            raise loop.ToolError(f"you have asked {ask_person_daily_limiter.per_minute} today")
    elif not ask_limiter.allow():
        raise loop.ToolError(f"at most {ASK_PER_MINUTE} questions a minute: try again shortly")
    out = loop.answer(
        question,
        product,
        graph=graph_name,
        as_of_ms=as_of_ms,
        viewer_id=viewer,
        payer=principal,
    )
    audit.safely(lambda: audit.answered(graph_name, out, principal, "mcp"))
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


def run(
    name: str, args: dict[str, Any], graph_name: str | None = None, viewer: str | None = None
) -> dict:
    """One MCP tool call, blocking (the handlers run it in a worker thread). `viewer` is the
    signed-in person of an HTTP client (D100): `ask` answers as them and every tool result is
    redacted to what they may see. stdio has no viewer: it is the local user's own process."""
    graph_name = graph_name or graph()
    if name == "ask" and get_settings().yaan_mcp_ask:
        if viewer:
            if args.get("viewer_id") not in (None, "", viewer):
                raise loop.ToolError(f"signed in as {viewer}: answers are for you")
            args = {**args, "viewer_id": viewer}
        return ask(args, graph_name, viewer)
    from app.agent import access

    allowed = frozenset(tools.REGISTRY) - EXCLUDED
    if not viewer:
        out = loop.call_tool(name, args, tools.Scope(None, None, graph_name), allowed)
    else:
        seen = access.Access(viewer, graph_name)
        scope = tools.Scope(None, None, graph_name, seen.reach())
        out = seen.redact(loop.call_tool(name, args, scope, allowed))[0]
    audit.safely(lambda: audit.record(graph_name, name, viewer, "mcp", access.ids(out)))
    return out


def viewer_of(ctx: Any) -> str | None:
    """The signed-in person behind an MCP request over HTTP (main.identify set it)."""
    request = getattr(ctx, "request", None)
    state = getattr(request, "state", None)
    return getattr(state, "principal", None)


def build(graph_name: str | None = None) -> Server:
    """The MCP server: list the tools, run a call, return its JSON (or a tool error)."""

    async def list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=tool_list())

    async def call_tool(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        args = params.arguments or {}
        try:
            out = await anyio.to_thread.run_sync(
                run, params.name, args, graph_name, viewer_of(ctx)
            )
        except (loop.ToolError, db.QueryError, llm.LLMError) as e:
            # shown to the client, as to the agent: it can fix its arguments and retry
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"Error: {e}")], is_error=True
            )
        text = json.dumps(loop.fence(out), ensure_ascii=False, default=str)
        return types.CallToolResult(content=[types.TextContent(type="text", text=text)])

    return Server(
        "yaan",
        version=__version__,
        title="Yaan",
        description="A time-aware company brain on FalkorDB: answers with verbatim sources.",
        instructions=instructions(),
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
        hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*", *get_settings().yaan_mcp_hosts]
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
