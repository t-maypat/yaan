"""FastAPI app. Endpoints follow docs/CONTEXT.md §9.9; until Phase 12 only /health exists.

Phase 1 makes /health report FalkorDB reachability.
"""

from fastapi import FastAPI

from app import __version__

app = FastAPI(title="Lore", version=__version__)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
