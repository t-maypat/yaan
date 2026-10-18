"""FastAPI app. Endpoints follow docs/CONTEXT.md §9.9; until Phase 12 only /health exists."""

from fastapi import FastAPI, Response

from app import __version__, db

app = FastAPI(title="Lore", version=__version__)


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
