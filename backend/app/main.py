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

import hmac
import json
import queue
import threading
import time
from collections import Counter, defaultdict, deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

import anyio
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from app import __version__, auth, db, mcp_server
from app.config import REPO_ROOT, get_settings

mcp_http = mcp_server.HttpEndpoint()  # /mcp (17.1): Lore's tools for any MCP client


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    async with mcp_http.running():
        yield


app = FastAPI(title="Lore", version=__version__, lifespan=lifespan)
app.router.add_route("/mcp", mcp_http, include_in_schema=False)
ASK_PER_MINUTE = 6  # per IP; the LLM gateway's daily spend cap is the second guard
STREAM_POLL_S = 0.5  # /ask checks this often whether the browser is still listening


class RateLimiter:
    """A sliding window (one minute by default) per key, usually the client IP. In memory: one
    API process. A swarm of many IPs grows the table, so expired keys are swept now and then."""

    SWEEP_EVERY = 5_000  # calls between sweeps of keys whose window has emptied

    def __init__(self, per_minute: int, window: float = 60):
        self.per_minute = per_minute  # the limit per window (per day for a 24 h window)
        self.window = window
        self.hits: dict[str, deque[float]] = defaultdict(deque)
        self.lock = threading.Lock()
        self.calls = 0

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self.lock:
            self.calls += 1
            if self.calls % self.SWEEP_EVERY == 0:
                stale = [k for k, h in self.hits.items() if not h or now - h[-1] > self.window]
                for k in stale:
                    del self.hits[k]
            hits = self.hits[key]
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.per_minute:
                return False
            hits.append(now)
            return True


_s = get_settings()
ask_limiter = RateLimiter(ASK_PER_MINUTE)
# D081: guards for a public deployment, where an agent swarm can come from many IPs at once.
ask_daily_limiter = RateLimiter(_s.lore_ask_per_ip_per_day, window=86_400)
ask_global_limiter = RateLimiter(_s.lore_ask_global_per_minute)  # one key for everyone
ask_slots = threading.BoundedSemaphore(_s.lore_ask_concurrency)
request_limiter = RateLimiter(_s.lore_requests_per_ip_per_minute)
UNLIMITED_PATHS = {"/health"}
# The routes that read or write the graph (D100). /ingest/artifact has its own secret; /health
# and the built UI's files are public, and the UI signs in when an API call answers 401.
API_PREFIXES = (
    "/ask",
    "/artifact",
    "/decision",
    "/entity",
    "/graph",
    "/eval",
    "/products",
    "/alerts",
    "/demo",
    "/query",
    "/mcp",
    "/me",
)


@app.middleware("http")
async def identify(request: Request, call_next):
    """Who is asking (app/auth.py): the principal every route below reads, never the body.
    Defined before the rate limit, so the limit wraps it and guessing tokens is limited too."""
    request.state.principal = None
    if request.url.path.startswith(API_PREFIXES):
        try:
            request.state.principal = auth.principal(request.headers)
        except auth.AuthError as e:
            return JSONResponse(
                {"detail": str(e), "auth": auth.mode()},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
    return await call_next(request)


@app.middleware("http")
async def limit_requests(request: Request, call_next):
    """Every route but /health: a per-IP budget, so a scraper or a swarm can't flood FalkorDB."""
    if request.url.path not in UNLIMITED_PATHS and not request_limiter.allow(client_ip(request)):
        return JSONResponse(
            {"detail": "too many requests from this address; wait a minute"},
            status_code=429,
            headers={"Retry-After": "60"},
        )
    return await call_next(request)


DEMO_PER_MINUTE = 6  # per IP: injections, resets, approvals and dismissals
demo_limiter = RateLimiter(DEMO_PER_MINUTE)
demo_lock = threading.Lock()  # one demo write at a time: a fold rebuild must not interleave
INJECT_DIR = REPO_ROOT / "exports" / "inject"


def graph() -> str:
    return get_settings().api_graph()


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def principal(request: Request) -> str | None:
    """The signed-in person (the `identify` middleware), or None in the demo."""
    return getattr(request.state, "principal", None)


def as_principal(request: Request, given: str | None, what: str) -> str | None:
    """The person a request acts as: the signed-in one. Naming someone else is refused; in
    the demo (no sign-in) the body's choice stands, a labeled persona switch (D100)."""
    who = principal(request)
    if who is None:
        return given
    if given not in (None, who):
        raise HTTPException(403, f"signed in as {who}: {what} is you, not {given}")
    return who


def for_viewer(request: Request, value: dict) -> dict:
    """`value` as the signed-in person may see it (agent/access.py); unchanged in the demo."""
    who = principal(request)
    if who is None:
        return value
    from app.agent import access

    return access.Access(who, graph()).redact(value)[0]


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
    # W7: answer as this person (an employee id); sources they can't see are hidden
    viewer_id: str | None = Field(default=None, pattern=r"^eid_[0-9a-f]{8}$")


def sse(name: str, data: Any) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


@app.post("/ask")
def ask(body: AskRequest, request: Request) -> StreamingResponse:
    """Answer a question, streaming the tool trace, the answer, citations and evidence."""
    from app.agent import loop

    ip = client_ip(request)
    viewer = as_principal(request, body.viewer_id, "the viewer")
    # The slot first: a request turned away as busy doesn't use up the asker's limits (D098).
    if not ask_slots.acquire(blocking=False):
        raise HTTPException(503, "Lore is busy answering; try again shortly", {"Retry-After": "10"})
    refused = None
    if not ask_limiter.allow(ip):
        refused = f"at most {ASK_PER_MINUTE} questions per minute"
    elif not ask_daily_limiter.allow(ip):
        refused = "this address has asked its questions for today"
    elif not ask_global_limiter.allow("*"):
        refused = "Lore is answering many questions right now; try in a minute"
    if refused:
        ask_slots.release()
        raise HTTPException(429, refused)
    events: queue.Queue = queue.Queue()
    cancel = threading.Event()

    def work() -> None:
        try:
            loop.answer(
                body.question,
                body.product_id,
                graph=graph(),
                on_event=lambda n, d: events.put((n, d)),
                as_of_ms=body.as_of_ms,
                viewer_id=viewer,
                cancel=cancel,
            )
        except Exception as e:  # shown to the viewer as an error event, never a fake answer
            events.put(("error", {"error": type(e).__name__, "message": str(e)[:500]}))
        finally:
            ask_slots.release()
            events.put(None)

    threading.Thread(target=work, daemon=True).start()
    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(
        relay(events, cancel, request), media_type="text/event-stream", headers=headers
    )


async def relay(events: queue.Queue, cancel: threading.Event, request) -> AsyncIterator[str]:
    """The agent's events as SSE until it finishes (None). When the browser goes away, either
    the server closes this generator or the poll sees the disconnect; both reach `finally`,
    which tells the agent to stop before its next model turn (D098)."""
    try:
        while True:
            try:
                item = await anyio.to_thread.run_sync(events.get, True, STREAM_POLL_S)
            except queue.Empty:
                if await request.is_disconnected():
                    return
                continue
            if item is None:
                break
            yield sse(*item)
        yield sse("done", {})
    finally:
        cancel.set()


@app.get("/artifact/{artifact_id:path}")
def artifact(artifact_id: str, request: Request) -> dict:
    """One artifact's text and the people around it (agent tool 3), for citation hovers."""
    from app.agent import tools

    out = for_viewer(request, tools.get_artifact(artifact_id, graph()))
    if "error" in out or out.get("hidden_by_permissions"):  # hidden reads as absent
        raise HTTPException(404, out.get("error") or f"no artifact with id {artifact_id!r}")
    return out


@app.get("/decision/{subject_id}/lineage")
def decision_lineage(
    subject_id: str, request: Request, as_of_ms: int | None = Query(default=None, ge=0, le=MAX_MS)
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
    out = for_viewer(request, tools.decision_lineage(subject_id, product, as_of_ms, graph()))
    if not out.get("subjects"):
        raise HTTPException(404, f"{subject_id!r} has no recorded states")
    out["cypher"] = ["subject_product.cypher", *out["cypher"]]
    return out


@app.get("/decision/{subject_id}/belief")
def decision_belief(
    subject_id: str,
    request: Request,
    known_at_ms: int | None = Query(default=None, ge=0, le=MAX_MS),
) -> dict:
    """What Lore believed about an issue or PR at `known_at_ms` (transaction time; None =
    now): the fold replayed over the events it had learned by then (D101). The Timeline's
    `as_of_ms` is event time; this is when Lore knew it."""
    from app.ingest import fold

    if not subject_id.startswith(("iss:", "pr:")):
        raise HTTPException(404, "a belief belongs to an issue (iss:…) or a pull request (pr:…)")
    rows = db.run("subject_product", {"id": subject_id}, graph=graph()).rows
    product = rows[0]["product_id"] if rows else None
    if not product:
        raise HTTPException(404, f"no issue or pull request {subject_id!r} in the graph")
    out = fold.belief(subject_id, product, graph(), known_at_ms)
    return for_viewer(request, out)


@app.get("/entity/{entity_id}")
def entity(
    entity_id: str, request: Request, product_id: str | None = Query(default=None, max_length=100)
) -> dict:
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
    out = for_viewer(request, out)
    if out.get("hidden_by_permissions"):  # e.g. an issue reported only where they can't see
        raise HTTPException(404, f"no entity {entity_id!r} in the graph")
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
    """The products in the graph with their codenames, for the UI's product picker, and the
    data mode: in org mode the UI leaves out what belongs to the HERB demo (D097)."""
    from app import org

    rows = db.run("list_products", {"limit": 100}, graph=graph()).rows
    mode = "org" if org.active() else "demo"
    return {"products": rows, "mode": mode, "cypher": ["list_products.cypher"]}


@app.get("/me")
def me(request: Request) -> dict:
    """How this API identifies people and who is signed in (D100): the UI hides its persona
    and responder pickers when the answer is a person."""
    from app import org

    who = principal(request)
    you = None
    if who:
        rows = db.run("entity_card", {"id": who}, graph=graph()).rows
        props = rows[0]["props"] if rows else {}
        you = {"id": who, "name": props.get("name"), "role": props.get("role")}
    return {"auth": auth.mode(), "mode": "org" if org.active() else "demo", "you": you}


class AlertResponse(BaseModel):
    alert_id: str = Field(min_length=1, max_length=300)
    # who responds: the signed-in person; named here only in the demo (D100)
    person_id: str | None = Field(default=None, min_length=1, max_length=100)
    note: str | None = Field(default=None, max_length=500)
    action_ids: list[str] | None = Field(default=None, max_length=50)
    # the text a connector will post for each approved action: bounded like a Slack message
    edits: dict[str, Annotated[str, Field(max_length=4000)]] | None = Field(
        default=None, max_length=50
    )


@app.get("/alerts")
def alerts(request: Request, status: str | None = None, product_id: str | None = None) -> dict:
    """The Inbox: Sentinel alerts, newest change first (11.1)."""
    from app.sentinel import engine

    rows = engine.list_alerts(status, product_id, graph=graph())
    out = {"graph": graph(), "alerts": rows, "cypher": ["list_alerts.cypher"]}
    return for_viewer(request, out)


@app.get("/alerts/detail")
def alert_detail(id: str, request: Request) -> dict:
    """One alert with its blast radius (11.2) and drafted actions (11.3)."""
    from app.sentinel import blast_radius, engine

    found = for_viewer(request, {"alerts": engine.list_alerts(graph=graph(), alert_id=id, limit=1)})
    found = found["alerts"]
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
    out = {
        "alert": alert,
        "radius": radius,
        "precedents": precedents,
        "actions": engine.alert_actions(id, graph=graph()),
        "cypher": ["list_alerts.cypher", "impact_of.cypher", "alert_actions.cypher"],
    }
    return for_viewer(request, out)


@app.post("/alerts/approve")
def alert_approve(body: AlertResponse, request: Request) -> dict:
    """Approve an alert's drafts (all, or `action_ids`) as the signed-in person, or in the
    demo as `person_id` (11.4, D100)."""
    from app.sentinel import approve

    person = responder(request, body)
    return demo_write(
        request,
        lambda g: approve.respond(
            body.alert_id,
            "approved",
            person,
            note=body.note,
            action_ids=body.action_ids,
            edits=body.edits,
            graph=g,
        ),
    )


@app.post("/alerts/dismiss")
def alert_dismiss(body: AlertResponse, request: Request) -> dict:
    """Dismiss an alert as the signed-in person (or the demo's `person_id`); recorded as a
    decision too (11.4)."""
    from app.sentinel import approve

    person = responder(request, body)
    return demo_write(
        request,
        lambda g: approve.respond(body.alert_id, "dismissed", person, note=body.note, graph=g),
    )


def responder(request: Request, body: AlertResponse) -> str:
    person = as_principal(request, body.person_id, "the responder")
    if not person:
        raise HTTPException(422, "person_id: who responds (the demo has no sign-in)")
    return person


def injection(name: str) -> dict:
    path = INJECT_DIR / f"{name}.json"
    if not name.replace("-", "").isalnum() or not path.is_file():
        raise HTTPException(404, f"no injection {name!r} in exports/inject/")
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/demo/injections")
def demo_injections() -> dict:
    """The labeled synthetic messages the demo can inject (exports/inject/, 11.7); none in
    org mode, where the graph holds a team's own data (D097)."""
    from app import org
    from app.sentinel import live

    if org.active():
        return {"graph": graph(), "injections": []}
    msgs = {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(INJECT_DIR.glob("*.json"))
    }
    done = live.present_ids(graph(), [m["id"] for m in msgs.values()])  # the graph says (D101)
    out = [{"name": name, **msg, "injected": msg["id"] in done} for name, msg in msgs.items()]
    return {"graph": graph(), "injections": out}


@app.post("/demo/inject/{name}")
def demo_inject(name: str, request: Request) -> dict:
    """Inject one labeled synthetic message live: S1-S8 + Sentinel, with the freshness lag."""
    from app import org
    from app.sentinel import live

    if org.active():
        raise HTTPException(409, "demo injections are off in org mode (LORE_DATA_MODE=org)")
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
    since arbitrary text costs an LLM call; ids start `msg:synthetic-` in the demo (any id in
    org mode)."""
    from app.sentinel import live

    secret = get_settings().lore_ingest_secret
    if not secret:
        raise HTTPException(503, "live ingestion is off: set LORE_INGEST_SECRET")
    given = request.headers.get("x-lore-secret") or ""
    if not hmac.compare_digest(given.encode("utf-8"), secret.encode("utf-8")):
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
    from app.connectors import runner

    # the connector worker's heartbeat (D108): reported, not a reason to stop serving answers
    worker = runner.worker_health(graph())["status"]
    try:
        db.client(readonly=True).connection.ping()
    except Exception as e:  # down, wrong password, users missing: all mean "not serving"
        response.status_code = 503
        return {
            "status": "unavailable",
            "version": __version__,
            "falkordb": type(e).__name__,
            "worker": worker,
        }
    return {"status": "ok", "version": __version__, "falkordb": "ok", "worker": worker}


# The built web UI (the Docker image, R2 W6). Mounted last, so every API route above wins; in
# development Vite serves the UI and proxies the API instead.
DIST = REPO_ROOT / "frontend" / "dist"
if DIST.is_dir():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=DIST, html=True), name="web")
