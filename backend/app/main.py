"""FastAPI app. Endpoints follow docs/CONTEXT.md §9.9.

Phase 12 step 1 (R1-8): `/ask` streams the agent's fixed event sequence as Server-Sent
Events: `tool_call`, `tool_result` (one pair per tool), `answer`, `citations`, `subgraph`,
then `done`, or `error` if the agent fails (fail loudly: no canned fallback answer).
"""

import json
import queue
import threading
import time
from collections import defaultdict, deque
from collections.abc import Iterator
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app import __version__, db

app = FastAPI(title="Lore", version=__version__)
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


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    product_id: str | None = Field(default=None, max_length=100)


def sse(name: str, data: Any) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


@app.post("/ask")
def ask(body: AskRequest, request: Request) -> StreamingResponse:
    """Answer a question, streaming the tool trace, the answer, citations and evidence."""
    from app.agent import loop

    ip = request.client.host if request.client else "unknown"
    if not ask_limiter.allow(ip):
        raise HTTPException(429, f"at most {ASK_PER_MINUTE} questions per minute")
    events: queue.Queue = queue.Queue()

    def work() -> None:
        try:
            loop.answer(body.question, body.product_id, on_event=lambda n, d: events.put((n, d)))
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

    out = tools.get_artifact(artifact_id)
    if "error" in out:
        raise HTTPException(404, out["error"])
    return out


@app.get("/products")
def products() -> dict:
    """The products in the graph with their codenames, for the UI's product picker."""
    rows = db.run("list_products", {"limit": 100}).rows
    return {"products": rows, "cypher": ["list_products.cypher"]}


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
