"""FastAPI app. Endpoints follow docs/CONTEXT.md §9.9.

Phase 12 step 1 (R1-8): `/ask` streams the agent's fixed event sequence as Server-Sent
Events: `tool_call`, `tool_result` (one pair per tool), `answer`, `citations`, `subgraph`,
then `done`, or `error` if the agent fails (fail loudly: no canned fallback answer).

Phase 17.1: `/mcp` serves Lore's read-only tools (and `ask`) over streamable HTTP to any MCP
client (app/mcp_server.py); stdio is `lore mcp`.

R2 W4: `/ask` takes `as_of_ms` (the Timeline's slider re-asks for the state at a moment);
`/decision/{id}/lineage`, `/entity/{id}`, `/graph/health` and `/eval/latest` serve the
Timeline, the entity panel and the Graph Health and Eval pages (12.1, 12.4, 12.6).

Step 2 (with Phase 11): the Inbox (`/alerts`, `/alerts/detail`, `/alerts/approve`,
`/alerts/dismiss`) and the live demo (`/demo/injections`, `/demo/inject/{name}`, `/demo/reset`;
`/ingest/artifact` behind a secret header). Everything reads and writes `LORE_API_GRAPH`
(`lore_demo`, a copy of `lore_herb`), so Ask sees what the demo injects; writes refuse the
main graph (live.demo_graph). Public demo writes are limited to the labeled files in
exports/inject/, rate-limited per IP and serialized by one lock (D058).
"""

import json
import queue
import threading
import time
from collections import Counter, defaultdict, deque
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app import __version__, db, mcp_server
from app.config import REPO_ROOT, get_settings

mcp_http = mcp_server.HttpEndpoint()  # /mcp (17.1): Lore's tools for any MCP client


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    async with mcp_http.running():
        yield


app = FastAPI(title="Lore", version=__version__, lifespan=lifespan)
app.router.add_route("/mcp", mcp_http, include_in_schema=False)
ASK_PER_MINUTE = 6  # per IP; the LLM gateway's daily spend cap is the second guard


class RateLimiter:
    """A sliding one-minute window per key (client IP). In memory: one API process."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self.hits: dict[str, deque[float]] = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self.lock:
            hits = self.hits[key]
            while hits and now - hits[0] > 60:
                hits.popleft()
            if len(hits) >= self.per_minute:
                return False
            hits.append(now)
            return True


ask_limiter = RateLimiter(ASK_PER_MINUTE)
DEMO_PER_MINUTE = 6  # per IP: injections, resets, approvals and dismissals
demo_limiter = RateLimiter(DEMO_PER_MINUTE)
demo_lock = threading.Lock()  # one demo write at a time: a fold rebuild must not interleave
INJECT_DIR = REPO_ROOT / "exports" / "inject"


def graph() -> str:
    return get_settings().lore_api_graph


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def demo_write(request: Request, work):
    """Run one demo write: rate-limited, serialized, and refused on the main graph."""
    from app.sentinel import approve, live

    if not demo_limiter.allow(client_ip(request)):
        raise HTTPException(429, f"at most {DEMO_PER_MINUTE} demo actions per minute")
    try:
        target = live.demo_graph(graph())
        with demo_lock:
            return work(target)
    except (live.LiveError, approve.AlertError) as e:
        raise HTTPException(409, str(e)) from e


MAX_MS = 4_102_444_800_000  # 2100-01-01: epoch ms beyond this is a unit mistake


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    product_id: str | None = Field(default=None, max_length=100)
    # the Timeline's as-of slider (12.4): answer for the state at this moment (UTC epoch ms)
    as_of_ms: int | None = Field(default=None, ge=0, le=MAX_MS)


def sse(name: str, data: Any) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


@app.post("/ask")
def ask(body: AskRequest, request: Request) -> StreamingResponse:
    """Answer a question, streaming the tool trace, the answer, citations and evidence."""
    from app.agent import loop

    if not ask_limiter.allow(client_ip(request)):
        raise HTTPException(429, f"at most {ASK_PER_MINUTE} questions per minute")
    events: queue.Queue = queue.Queue()

    def work() -> None:
        try:
            loop.answer(
                body.question,
                body.product_id,
                graph=graph(),
                on_event=lambda n, d: events.put((n, d)),
                as_of_ms=body.as_of_ms,
            )
        except Exception as e:  # shown to the viewer as an error event, never a fake answer
            events.put(("error", {"error": type(e).__name__, "message": str(e)[:500]}))
        finally:
            events.put(None)

    threading.Thread(target=work, daemon=True).start()

    def stream() -> Iterator[str]:
        while (item := events.get()) is not None:
            yield sse(*item)
        yield sse("done", {})

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(stream(), media_type="text/event-stream", headers=headers)


@app.get("/artifact/{artifact_id:path}")
def artifact(artifact_id: str) -> dict:
    """One artifact's text and the people around it (agent tool 3), for citation hovers."""
    from app.agent import tools

    out = tools.get_artifact(artifact_id, graph())
    if "error" in out:
        raise HTTPException(404, out["error"])
    return out


@app.get("/decision/{subject_id}/lineage")
def decision_lineage(
    subject_id: str, as_of_ms: int | None = Query(default=None, ge=0, le=MAX_MS)
) -> dict:
    """How an issue's or PR's state changed, who decided each step and why (agent tool 6),
    for the Timeline (12.4). The product comes from the subject itself."""
    from app.agent import tools

    if not subject_id.startswith(("iss:", "pr:")):
        raise HTTPException(404, "a lineage belongs to an issue (iss:…) or a pull request (pr:…)")
    rows = db.run("subject_product", {"id": subject_id}, graph=graph()).rows
    product = rows[0]["product_id"] if rows else None
    if not product:
        raise HTTPException(404, f"no issue or pull request {subject_id!r} in the graph")
    out = tools.decision_lineage(subject_id, product, as_of_ms, graph())
    if not out["subjects"]:
        raise HTTPException(404, f"{subject_id!r} has no recorded states")
    out["cypher"] = ["subject_product.cypher", *out["cypher"]]
    return out


@app.get("/entity/{entity_id}")
def entity(entity_id: str, product_id: str | None = Query(default=None, max_length=100)) -> dict:
    """One person, customer, company, product, issue or area: its properties and links
    counted by type; a person also gets their profile (agent tool 4). Artifacts have
    `/artifact/{id}`."""
    from app.agent import loop, tools

    if loop.ARTIFACT_ID.match(entity_id):
        raise HTTPException(404, f"{entity_id!r} is an artifact: use /artifact/{entity_id}")
    rows = db.run("entity_card", {"id": entity_id}, graph=graph()).rows
    if not rows:
        raise HTTPException(404, f"no entity {entity_id!r} in the graph")
    row = rows[0]
    out: dict[str, Any] = {
        "id": entity_id,
        "labels": row["labels"],
        "properties": row["props"],
        "links": dict(Counter(row["rels"]).most_common()),
        "cypher": ["entity_card.cypher"],
    }
    if "Person" in row["labels"]:
        out["profile"] = tools.person_profile(entity_id, product_id, graph())
    return out


@app.get("/graph/health")
def graph_health() -> dict:
    """The Graph Health page (12.6): node and edge counts, isolated nodes, PR chains,
    duplicate ids, unresolved mentions (Phase 3 health report) on the API's graph."""
    from app.ingest import health

    return {"graph": graph(), **health.report(graph())}


@app.get("/eval/latest")
def eval_latest() -> dict:
    """The Eval page (12.6): the newest results per system (eval/results/*.json), their
    HERB-Drift rows, the who_knows benchmark and HERB's published reference row."""
    from app.eval import report

    latest, drift = report.latest_results()
    experts = report.results_dir() / "experts_dev.json"
    return {
        "systems": [r for _, r in sorted(latest.items())],
        "drift": drift,
        "experts": json.loads(experts.read_text(encoding="utf-8")) if experts.is_file() else None,
        "reference": report.HERB_REF,
    }


@app.get("/products")
def products() -> dict:
    """The products in the graph with their codenames, for the UI's product picker."""
    rows = db.run("list_products", {"limit": 100}, graph=graph()).rows
    return {"products": rows, "cypher": ["list_products.cypher"]}


class AlertResponse(BaseModel):
    alert_id: str = Field(min_length=1, max_length=300)
    person_id: str = Field(min_length=1, max_length=100)
    note: str | None = Field(default=None, max_length=500)
    action_ids: list[str] | None = Field(default=None, max_length=50)
    edits: dict[str, str] | None = None


@app.get("/alerts")
def alerts(status: str | None = None, product_id: str | None = None) -> dict:
    """The Inbox: Sentinel alerts, newest change first (11.1)."""
    from app.sentinel import engine

    rows = engine.list_alerts(status, product_id, graph=graph())
    return {"graph": graph(), "alerts": rows, "cypher": ["list_alerts.cypher"]}


@app.get("/alerts/detail")
def alert_detail(id: str) -> dict:
    """One alert with its blast radius (11.2) and drafted actions (11.3)."""
    from app.sentinel import blast_radius, engine

    found = [a for a in engine.list_alerts(graph=graph()) if a["id"] == id]
    if not found:
        raise HTTPException(404, f"no alert {id!r}")
    alert = found[0]
    radius = (
        blast_radius.person_radius(alert["subject_id"], graph=graph())
        if alert["kind"] in ("key_person", "bus_factor")
        else blast_radius.impact_of(alert["decision_id"], graph=graph())
    )
    precedents = (
        db.run(
            "decision_precedents", {"decision_id": alert["decision_id"], "limit": 5}, graph=graph()
        ).rows
        if alert["decision_id"]
        else []
    )
    return {
        "alert": alert,
        "radius": radius,
        "precedents": precedents,
        "actions": engine.alert_actions(id, graph=graph()),
        "cypher": ["list_alerts.cypher", "impact_of.cypher", "alert_actions.cypher"],
    }


@app.post("/alerts/approve")
def alert_approve(body: AlertResponse, request: Request) -> dict:
    """Approve an alert's drafts (all, or `action_ids`) as `person_id` (11.4)."""
    from app.sentinel import approve

    return demo_write(
        request,
        lambda g: approve.respond(
            body.alert_id,
            "approved",
            body.person_id,
            note=body.note,
            action_ids=body.action_ids,
            edits=body.edits,
            graph=g,
        ),
    )


@app.post("/alerts/dismiss")
def alert_dismiss(body: AlertResponse, request: Request) -> dict:
    """Dismiss an alert as `person_id`; recorded as a decision too (11.4)."""
    from app.sentinel import approve

    return demo_write(
        request,
        lambda g: approve.respond(
            body.alert_id, "dismissed", body.person_id, note=body.note, graph=g
        ),
    )


def injection(name: str) -> dict:
    path = INJECT_DIR / f"{name}.json"
    if not name.replace("-", "").isalnum() or not path.is_file():
        raise HTTPException(404, f"no injection {name!r} in exports/inject/")
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/demo/injections")
def demo_injections() -> dict:
    """The labeled synthetic messages the demo can inject (exports/inject/, 11.7)."""
    from app.sentinel import live

    done = live.injected_ids(graph())
    out = []
    for path in sorted(INJECT_DIR.glob("*.json")):
        msg = json.loads(path.read_text(encoding="utf-8"))
        out.append({"name": path.stem, **msg, "injected": msg["id"] in done})
    return {"graph": graph(), "injections": out}


@app.post("/demo/inject/{name}")
def demo_inject(name: str, request: Request) -> dict:
    """Inject one labeled synthetic message live: S1-S8 + Sentinel, with the freshness lag."""
    from app.sentinel import live

    msg = injection(name)
    return demo_write(request, lambda g: live.ingest(msg, g))


@app.post("/demo/reset")
def demo_reset(request: Request) -> dict:
    """lore_demo := a fresh copy of lore_herb; the injected messages are gone."""
    from app.sentinel import live

    return demo_write(request, live.reset_demo)


@app.post("/ingest/artifact")
def ingest_artifact(msg: dict[str, Any], request: Request) -> dict:
    """Ingest any message live (11.6). Needs LORE_INGEST_SECRET in the X-Lore-Secret header,
    since arbitrary text costs an LLM call; ids must still start `msg:synthetic-`."""
    from app.sentinel import live

    secret = get_settings().lore_ingest_secret
    if not secret:
        raise HTTPException(503, "live ingestion is off: set LORE_INGEST_SECRET")
    if request.headers.get("x-lore-secret") != secret:
        raise HTTPException(401, "wrong or missing X-Lore-Secret")
    return demo_write(request, lambda g: live.ingest(msg, g))


@app.get("/query/{name}")
def query_text(name: str) -> dict:
    """The text of one backend/queries file, for "Show the Cypher": the exact statement a
    tool ran (tool results name their files; their parameters are in `tool_call.input`)."""
    try:
        return {"name": name, "text": db.load_query(name.removesuffix(".cypher"))}
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(404, str(e)) from e


@app.get("/health")
def health(response: Response) -> dict[str, str]:
    """200 when FalkorDB answers the reader's PING, else 503 (Phase 1.9).

    Uptime checks (Phase 15) watch this. The error names only its type, not connection details.
    """
    try:
        db.client(readonly=True).connection.ping()
    except Exception as e:  # down, wrong password, users missing: all mean "not serving"
        response.status_code = 503
        return {"status": "unavailable", "version": __version__, "falkordb": type(e).__name__}
    return {"status": "ok", "version": __version__, "falkordb": "ok"}
