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

import hashlib
import hmac
import json
import logging
import queue
import re
import threading
import time
import uuid
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

import anyio
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app import __version__, auth, db, mcp_server, obs
from app.config import REPO_ROOT, get_settings
from app.limits import RateLimiter, person_limits

mcp_http = mcp_server.HttpEndpoint()  # /mcp (17.1): Lore's tools for any MCP client


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    finish_stuck_responses()
    async with mcp_http.running():
        yield


def finish_stuck_responses() -> None:
    """Approvals a stopped API left half-written are finished at start (D110). The API still
    starts if FalkorDB isn't there yet: /health says so, and the worker retries every round."""
    from app.sentinel import approve

    try:
        for alert_id in approve.complete_stuck(get_settings().api_graph()):
            obs.event("api", f"finished the response to {alert_id}", alert=alert_id)
    except Exception as e:
        obs.event("api", f"couldn't check for unfinished responses: {type(e).__name__}: {e}",
                  logging.WARNING)  # fmt: skip


obs.setup_logging()
app = FastAPI(title="Lore", version=__version__, lifespan=lifespan)
app.router.add_route("/mcp", mcp_http, include_in_schema=False)
ASK_PER_MINUTE = 6  # per IP when nobody is signed in; the daily spend caps are the second guard
STREAM_POLL_S = 0.5  # /ask checks this often whether the browser is still listening

_s = get_settings()
# Anonymous askers (the demo) are counted per IP; a signed-in person on their own (D124), so an
# office behind one address, or everyone behind one TLS proxy, doesn't share one budget.
ask_limiter = RateLimiter(ASK_PER_MINUTE)
ask_daily_limiter = RateLimiter(_s.lore_ask_per_ip_per_day, window=86_400)
ask_person_limiter, ask_person_daily_limiter = person_limits()
# D081: guards for a public deployment, where an agent swarm can come from many IPs at once.
ask_global_limiter = RateLimiter(_s.lore_ask_global_per_minute)  # one key for everyone
ask_slots = threading.BoundedSemaphore(_s.lore_ask_concurrency)
request_limiter = RateLimiter(_s.lore_requests_per_ip_per_minute)
person_request_limiter = RateLimiter(_s.lore_requests_per_person_per_minute)
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
    "/admin",
)
# What the request budget counts: the API, sign-in (token guessing) and the ingest endpoint.
# /health and the web UI's own files aren't counted: a page load is dozens of them.
LIMITED_PREFIXES = (*API_PREFIXES, "/auth", "/ingest", "/hooks", "/metrics")


SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# The web UI's own files and nothing else (H2, D111): no inline script (the theme script is a
# file), no frames, no plugins, no posting forms to other sites. Styles may be inline: React
# and the graph view set element styles.
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; font-src 'self' data:; connect-src 'self'; object-src 'none'; "
        "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
}


@app.middleware("http")
async def identify(request: Request, call_next):
    """Who is asking (app/auth.py), then their request budget. The principal is what every
    route below reads, never the body. The budget is the person's own when they signed in,
    else their address's, and a failed sign-in counts against the address, so guessing tokens
    is limited too (D124). A browser session (cookie) may change something only with the
    X-Yaan-CSRF header (D111)."""
    request.state.principal = None
    path = request.url.path
    who = how = refused = None
    # /metrics signs in on its own: a scraper's token is not a person's ("/metrics" also
    # starts with "/me", so it must be named)
    if path.startswith(API_PREFIXES) and path != "/metrics":
        try:  # in a thread: a token's last-use stamp may wait briefly for the token file
            who, how = await run_in_threadpool(auth.identify, request.headers, request.cookies)
        except auth.AuthError as e:
            refused = e
    if path.startswith(LIMITED_PREFIXES):
        if who:
            allowed = person_request_limiter.allow(who)
        else:
            allowed = request_limiter.allow(client_ip(request))
        if not allowed:
            source = "you" if who else "this address"
            return JSONResponse(
                {"detail": f"too many requests from {source}; wait a minute"},
                status_code=429,
                headers={"Retry-After": "60"},
            )
    if refused is not None:
        return JSONResponse(
            {"detail": str(refused), "auth": auth.mode()},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
    if how == "cookie" and request.method not in SAFE_METHODS:
        if not request.headers.get(auth.CSRF_HEADER):
            return JSONResponse(
                {"detail": f"a browser session needs the {auth.CSRF_HEADER} header"},
                status_code=403,
            )
    request.state.principal = who
    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")  # a caller's id is kept only when plain


@app.middleware("http")
async def observe(request: Request, call_next):
    """Outermost (added last): a request id on the response and in every log line it causes,
    then one access line and the request counters (22.4). Routes are labelled by their
    template (`/artifact/{id}`), so the counters stay a fixed set; paths without a route (the
    UI's files) count as `static`. Logged: the path, never its query string or a body."""
    given = request.headers.get("x-request-id") or ""
    rid = given if REQUEST_ID.match(given) else uuid.uuid4().hex[:16]
    token = obs.request_id.set(rid)
    t0 = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = rid
        return response
    finally:
        seconds = time.perf_counter() - t0
        found = request.scope.get("route")
        route = getattr(found, "path", None) or "static"
        obs.METRICS.inc("yaan_http_requests_total", route=route, method=request.method,
                        status=str(status))  # fmt: skip
        obs.METRICS.observe("yaan_http_request_seconds", seconds, route=route)
        if route != "static":  # page loads are dozens of files: not worth a line each
            obs.event("api", f"{request.method} {request.url.path} {status}",
                      method=request.method, path=request.url.path, route=route, status=status,
                      ms=round(seconds * 1000, 1), person=principal(request))  # fmt: skip
        obs.request_id.reset(token)


@app.post("/auth/session")
def open_session(request: Request, response: Response) -> dict:
    """The web UI's sign-in (D111): the token, sent once as a bearer header, becomes an
    HttpOnly session cookie, so no page script ever holds it."""
    if auth.mode() != "token":
        raise HTTPException(404, f"sign-in sessions are for LORE_AUTH=token, not {auth.mode()!r}")
    scheme, _, token = (request.headers.get("authorization") or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(401, "send the token as 'Authorization: Bearer <token>'")
    try:
        cookie, person, max_age = auth.open_session(token)
    except auth.AuthError as e:
        raise HTTPException(401, str(e)) from e
    response.set_cookie(
        auth.SESSION_COOKIE,
        cookie,
        max_age=max_age,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        path="/",
    )
    return {"person": person, "expires_in_s": max_age}


@app.delete("/auth/session")
def close_session(response: Response) -> dict:
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return {"signed_out": True}


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


def admin_only(request: Request) -> str | None:
    """The ops routes (D133): graph health, the eval, dead letters, tokens, spend (query texts
    stay open: "Show the Cypher" is every reader's, and the queries are public).
    Everyone may with no sign-in (the demo); else only people.yaml's `admin: true`."""
    who = principal(request)
    if not auth.is_admin(who):
        raise HTTPException(403, "for Yaan's admins only (admin: true in people.yaml)")
    return who


def for_viewer(request: Request, value: dict) -> dict:
    """`value` as the signed-in person may see it (agent/access.py); unchanged in the demo."""
    who = principal(request)
    if who is None:
        return value
    from app.agent import access

    return access.Access(who, graph()).redact(value)[0]


def refresh_derived(target: str) -> None:
    """The derived layer, at most every few minutes after a live write (D112). The write the
    request made has already happened, so a failure here is logged, never turned into an
    error for it: the next write or the worker's next round tries again."""
    from app.sentinel import live

    try:
        live.derive_if_due(target)
        live.algorithms_if_due(target)  # org mode's collaboration graph, hourly (D127)
    except Exception as e:
        obs.event("api", f"the derived layer wasn't refreshed: {type(e).__name__}: {e}",
                  logging.WARNING, graph=target)  # fmt: skip


def demo_write(request: Request, work):
    """Run one demo write: rate-limited, serialized, and refused on the main graph."""
    from app.sentinel import approve, live

    # per person when signed in (a team behind one proxy shares an address, D111), else per IP
    if not demo_limiter.allow(principal(request) or client_ip(request)):
        raise HTTPException(429, f"at most {DEMO_PER_MINUTE} demo actions per minute")
    try:
        target = live.demo_graph(graph())
        with demo_lock:
            out = work(target)
            refresh_derived(target)
            return out
    except (live.LiveError, approve.AlertError) as e:
        raise HTTPException(409, str(e)) from e


MAX_MS = 4_102_444_800_000  # 2100-01-01: epoch ms beyond this is a unit mistake


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    product_id: str | None = Field(default=None, max_length=100)
    # the Timeline's as-of slider (12.4): answer for the state at this moment (UTC epoch ms)
    as_of_ms: int | None = Field(default=None, ge=0, le=MAX_MS)
    # W7: answer as this person (an employee id); sources they can't see are hidden
    viewer_id: str | None = Field(default=None, pattern=r"^eid_[0-9a-f]{8}(?:[0-9a-f]{8})?$")


def sse(name: str, data: Any) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


@app.post("/ask")
def ask(body: AskRequest, request: Request) -> StreamingResponse:
    """Answer a question, streaming the tool trace, the answer, citations and evidence."""
    from app.agent import loop

    who = principal(request)
    viewer = as_principal(request, body.viewer_id, "the viewer")
    # The slot first: a request turned away as busy doesn't use up the asker's limits (D098).
    if not ask_slots.acquire(blocking=False):
        raise HTTPException(503, "Lore is busy answering; try again shortly", {"Retry-After": "10"})
    # a signed-in person's own limits; an anonymous asker's address's (D124)
    key = who or client_ip(request)
    minute, day = (ask_person_limiter, ask_person_daily_limiter)
    if not who:
        minute, day = ask_limiter, ask_daily_limiter
    refused = None
    if not minute.allow(key):
        refused = f"at most {minute.per_minute} questions per minute"
    elif not day.allow(key):
        asker = "you have" if who else "this address has"
        refused = f"{asker} asked {day.per_minute} questions today: the limit for a day"
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
                payer=who,
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
def graph_health(request: Request) -> dict:
    """The Graph Health page (12.6): node and edge counts, isolated nodes, PR chains,
    duplicate ids, unresolved mentions (Phase 3 health report) on the API's graph."""
    from app.ingest import health

    admin_only(request)
    return {"graph": graph(), **health.report(graph())}


@app.get("/eval/latest")
def eval_latest(request: Request) -> dict:
    """The Eval page (12.6): the newest results per system (eval/results/*.json), their
    HERB-Drift rows, the who_knows benchmark and HERB's published reference row."""
    from app.eval import report

    admin_only(request)
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
    mode = "org" if org.active() else "demo"
    return {"auth": auth.mode(), "mode": mode, "you": you, "admin": auth.is_admin(who)}


class DeadRetry(BaseModel):
    id: str | None = Field(default=None, min_length=1, max_length=300)  # None: every one


class TokenRevoke(BaseModel):
    id: str = Field(min_length=6, max_length=64)


@app.get("/admin/overview")
def admin_overview(request: Request) -> dict:
    """What an admin checks (D133): the worker and why it waits, the graph's memory and disk
    (D136), dead letters (without their items), sign-in tokens (without secrets), today's
    spend, and people met but not listed."""
    from app import llm, org
    from app.connectors import runner

    admin_only(request)
    g = graph()
    try:  # the graph's memory against its cap and how it persists (22.3)
        store = db.storage()
        store["warning"] = db.memory_warning(store)
    except Exception as e:  # the rest of the page still helps when INFO fails
        store = {"error": f"{type(e).__name__}: {e}"[:200]}
    return {
        "worker": runner.worker_health(g),
        "storage": store,
        "dead_letters": runner.dead_letters(g),
        "tokens": auth.listing(),
        "spend": llm.spending(),
        "unlisted": org.unlisted(g) if org.active() else [],
    }


@app.post("/admin/dead/retry")
def admin_retry(body: DeadRetry, request: Request) -> dict:
    """Replay dead letters (all, or one) through the live path, as `yaan connectors retry`."""
    from app.connectors import runner

    admin_only(request)
    try:
        with db.writer_lock(graph()):
            return runner.retry(graph(), body.id)
    except db.LockBusy as e:
        raise HTTPException(503, f"the graph is busy: {e}", {"Retry-After": "10"}) from e


@app.post("/admin/tokens/revoke")
def admin_revoke(body: TokenRevoke, request: Request) -> dict:
    admin_only(request)
    return {"revoked": auth.revoke_id(body.id)}


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


# A webhook only wakes the worker (D132): Yaan still reads what changed from the tool's API, so
# the payload is never trusted. Its HMAC (`sha256=<hex>` of the body) is checked anyway, so
# nobody else can make the worker poll.
HOOK_SIGNATURES = {"github": "x-hub-signature-256", "jira": "x-hub-signature"}


@app.post("/hooks/{source}", status_code=202)
async def hook(source: str, request: Request) -> dict:
    from app.connectors import runner
    from app.sentinel import live

    st = get_settings()
    secret = {"github": st.github_webhook_secret, "jira": st.jira_webhook_secret}.get(source)
    if not secret:
        raise HTTPException(404, f"no webhook for {source!r}: set {source.upper()}_WEBHOOK_SECRET")
    body = await request.body()
    want = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    given = request.headers.get(HOOK_SIGNATURES[source]) or ""
    if not hmac.compare_digest(given.encode(), want.encode()):
        raise HTTPException(401, "bad or missing signature")
    await run_in_threadpool(runner.poke, live.demo_graph(None), source)
    return {"woke": source}


METRIC_HELP = {
    "yaan_build_info": ("gauge", "Yaan's version, as a label"),
    "yaan_graph_memory_bytes": ("gauge", "FalkorDB used memory, every graph on the server"),
    "yaan_graph_memory_cap_bytes": ("gauge", "FalkorDB maxmemory (absent: no cap)"),
    "yaan_persistence_aof": ("gauge", "1 when FalkorDB keeps an append-only file"),
    "yaan_persistence_last_save_ok": ("gauge", "1 when FalkorDB's last save succeeded"),
    "yaan_persistence_last_save_timestamp_seconds": ("gauge", "when FalkorDB last saved"),
    "yaan_falkordb_up": ("gauge", "1 when FalkorDB answered INFO for this scrape"),
    "yaan_llm_spend_usd": ("gauge", "model spend today (UTC), by kind"),
    "yaan_llm_spend_cap_usd": ("gauge", "the daily spend cap"),
    "yaan_worker_up": ("gauge", "1 while the connector worker's rounds come on time"),
    "yaan_worker_healthy": ("gauge", "1 when the worker's last round had nothing waiting"),
    "yaan_worker_last_round_timestamp_seconds": ("gauge", "when the worker's last round ended"),
    "yaan_worker_rounds_total": ("counter", "worker rounds since it started"),
    "yaan_worker_items_total": ("counter", "items the worker handled since it started, by outcome"),
    "yaan_worker_round_seconds": ("gauge", "how long the worker's last round took"),
    "yaan_worker_fold_seconds_max": ("gauge", "the slowest fold in the worker's last round"),
    "yaan_dead_letters": ("gauge", "items a connector couldn't ingest (listing caps at 200)"),
    "yaan_connector_backoff": ("gauge", "1 while a tool is backed off after failing, by source"),
}


@app.get("/metrics")
def metrics(request: Request) -> Response:
    """Prometheus text (22.4): what this process counted, plus the graph's memory and disk,
    today's spend, and the connector worker's counters from its heartbeat. For admins, or a
    scraper sending LORE_METRICS_TOKEN as a bearer token (not a person's token)."""
    from app import llm
    from app.connectors import runner

    s = get_settings()
    scheme, _, sent = (request.headers.get("authorization") or "").partition(" ")
    scraper = bool(s.lore_metrics_token) and scheme.lower() == "bearer"
    if not (scraper and hmac.compare_digest(sent.strip(), s.lore_metrics_token or "")):
        try:
            who, _ = auth.identify(request.headers, request.cookies)
        except auth.AuthError as e:
            raise HTTPException(401, str(e), {"WWW-Authenticate": "Bearer"}) from e
        if not auth.is_admin(who):
            raise HTTPException(403, "for Yaan's admins, or LORE_METRICS_TOKEN")
    g = graph()
    out = [*obs.METRICS.samples(), ("yaan_build_info", {"version": __version__}, 1)]
    try:
        st = db.storage()
        out += [
            ("yaan_falkordb_up", {}, 1),
            ("yaan_graph_memory_bytes", {}, st["used_bytes"]),
            ("yaan_persistence_aof", {}, int(st["aof"])),
            ("yaan_persistence_last_save_ok", {}, int(st["last_save_ok"])),
            ("yaan_persistence_last_save_timestamp_seconds", {}, st["last_save_ts"]),
        ]
        if st["cap_bytes"]:
            out.append(("yaan_graph_memory_cap_bytes", {}, st["cap_bytes"]))
        out.append(("yaan_dead_letters", {}, len(runner.dead_letters(g))))
    except Exception:  # FalkorDB down: said by this gauge, and /health
        out.append(("yaan_falkordb_up", {}, 0))
    spend = llm.spending()
    out += [
        ("yaan_llm_spend_usd", {"kind": "all"}, spend["spent_usd"]),
        ("yaan_llm_spend_usd", {"kind": "answers"}, spend["answers_usd"]),
        ("yaan_llm_spend_cap_usd", {}, spend["cap_usd"]),
    ]
    beat = runner.worker_health(g)
    if beat["status"] != "absent":
        out += [
            ("yaan_worker_up", {}, int(beat["status"] in ("ok", "waiting"))),
            ("yaan_worker_healthy", {}, int(beat["status"] == "ok")),
            ("yaan_worker_last_round_timestamp_seconds", {}, beat["last_round_ms"] / 1000),
        ]
        counted = runner.heartbeat_metrics(g)
        for outcome, n in sorted(counted.get("items", {}).items()):
            out.append(("yaan_worker_items_total", {"outcome": outcome}, n))
        for name in ("rounds_total", "round_seconds", "fold_seconds_max"):
            if name in counted:
                out.append((f"yaan_worker_{name}", {}, counted[name]))
    now_ms = time.time() * 1000
    for source, mine in sorted(runner.load_state(g).items()):
        if isinstance(mine, dict) and "since_ms" in mine:
            out.append(("yaan_connector_backoff", {"source": source},
                        int(mine.get("retry_ms", 0) > now_ms)))  # fmt: skip
    return Response(obs.render(out, METRIC_HELP), media_type="text/plain; version=0.0.4")


@app.get("/health")
def health(response: Response) -> dict[str, str]:
    """200 when FalkorDB answers the reader's PING, else 503 (Phase 1.9).

    Uptime checks (Phase 15) watch this. The error names only its type, not connection details.
    """
    from app.connectors import runner

    # the connector worker's heartbeat (D108): reported, not a reason to stop serving answers
    beat = runner.worker_health(graph())
    worker = beat["status"]
    if beat.get("why"):  # say why rounds wait (e.g. the spend cap pausing extraction, D124)
        worker = f"{worker}: {beat['why']}"[:200]
    elif beat.get("warning"):  # e.g. a slow fold (review F3, D128)
        worker = f"{worker}, warning: {beat['warning']}"[:200]
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
    try:  # near the memory cap answers still work, writes soon won't (22.3): said, still 200
        memory = db.memory_warning() or "ok"
    except Exception as e:  # INFO refused or odd: reported, not a reason to stop serving
        memory = f"unknown: {type(e).__name__}"
    return {
        "status": "ok",
        "version": __version__,
        "falkordb": "ok",
        "memory": memory,
        "worker": worker,
    }


# The built web UI (the Docker image, R2 W6). Mounted last, so every API route above wins; in
# development Vite serves the UI and proxies the API instead.
DIST = REPO_ROOT / "frontend" / "dist"
if DIST.is_dir():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=DIST, html=True), name="web")
