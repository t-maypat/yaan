"""FalkorDB access: the one place Lore's Python code talks to the graph. Built in Phase 1.3.

Two clients (CONTEXT §9.11), each logged in as its own Redis ACL user (D018):
- the writer, for ingestion, sends GRAPH.QUERY; its user may run graph commands only;
- the reader, for agent tools and eval, sends GRAPH.RO_QUERY, which the server refuses to run
  if the query would write. Its user can't even send GRAPH.QUERY: two locks, not one.

Queries live in backend/queries/<name>.cypher, one parameterized statement each, and are sent
exactly as written (FalkorDB accepts // comments).

Limits (A17, D014):
- every read carries an explicit timeout: QUERY_TIMEOUT_MS (2 s) unless the caller asks for
  another, at most READ_TIMEOUT_MAX_MS;
- writes can't take one. The server gives each write statement TIMEOUT_DEFAULT (10 s) and rolls
  it back in full when it runs over, so `run` splits a write's `$rows` into batches.

Errors propagate as QueryError (QueryTimeout for a timeout), naming the query, graph and batch.
"""

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from falkordb import FalkorDB
from redis.exceptions import ResponseError

from app.config import get_settings

READ_TIMEOUT_MAX_MS = 120_000  # TIMEOUT_MAX in docker-compose.yml (D014)
WRITE_BATCH_ROWS = 500  # rows per write statement, well under the 10 s write budget
SOCKET_TIMEOUT_S = 130  # the server stops any query by 120 s; this only catches a dead connection
CONNECT_TIMEOUT_S = 5  # an unreachable host fails fast (e.g. /health) instead of hanging

# Change counters summed across a write's batches (falkordb QueryResult properties).
STAT_FIELDS = (
    "nodes_created",
    "nodes_deleted",
    "relationships_created",
    "relationships_deleted",
    "properties_set",
    "properties_removed",
    "labels_added",
    "indices_created",
    "indices_deleted",
)

QUERY_NAME = re.compile(r"^[a-z0-9_]+$")


class QueryError(RuntimeError):
    """A query failed on the server. Carries the query name, graph and (for writes) batch."""

    def __init__(self, name: str, graph: str, message: str, batch: str | None = None):
        where = f"{name} on graph {graph!r}" + (f", batch {batch}" if batch else "")
        super().__init__(f"{where}: {message}")
        self.name, self.graph, self.batch, self.server_message = name, graph, batch, message


class QueryTimeout(QueryError):
    """A read ran past its timeout, or a write past the 10 s write budget (rolled back)."""


@dataclass(frozen=True)
class Result:
    columns: list[str]
    rows: list[dict[str, Any]]
    stats: dict[str, int]  # change counters; all zero for reads
    run_time_ms: float  # server-side execution time, summed across batches
    batches: int = 1


def load_query(name: str) -> str:
    """The text of backend/queries/<name>.cypher, read once per process."""
    if not QUERY_NAME.match(name):
        raise ValueError(f"bad query name {name!r}: use the file name without .cypher")
    return _read(get_settings().queries_dir / f"{name}.cypher")


@lru_cache
def _read(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"no query file {path}")
    return path.read_text(encoding="utf-8")


@lru_cache
def client(readonly: bool) -> FalkorDB:
    """The shared reader or writer connection (a connection pool, safe across threads)."""
    s = get_settings()
    if readonly:
        user, password, key = s.falkordb_reader_user, s.falkordb_reader_password, "READER"
    else:
        user, password, key = s.falkordb_writer_user, s.falkordb_writer_password, "WRITER"
    if not password:
        raise RuntimeError(
            f"FALKORDB_{key}_PASSWORD is not set. Copy the FalkorDB user lines from .env.example "
            "into .env, then restart FalkorDB (docker compose up -d) so it creates the users."
        )
    return FalkorDB(
        host=s.falkordb_host,
        port=s.falkordb_port,
        username=user,
        password=password,
        socket_timeout=SOCKET_TIMEOUT_S,
        socket_connect_timeout=CONNECT_TIMEOUT_S,
    )


def run(
    name: str,
    params: dict[str, Any] | None = None,
    *,
    graph: str | None = None,
    readonly: bool = True,
    timeout_ms: int | None = None,
    batch_rows: int = WRITE_BATCH_ROWS,
) -> Result:
    """Run queries/<name>.cypher with `params` and return its rows and change counters.

    Reads (the default) go through the reader with a timeout. Writes need `readonly=False`;
    a write whose params hold a `rows` list runs once per `batch_rows` slice of it. Each batch
    commits on its own, so a failure stops at that batch and earlier ones stay: write queries
    MERGE, so rerunning the whole call is safe.
    """
    return run_text(
        load_query(name),
        params,
        label=name,
        graph=graph,
        readonly=readonly,
        timeout_ms=timeout_ms,
        batch_rows=batch_rows,
    )


def run_text(
    text: str,
    params: dict[str, Any] | None = None,
    *,
    label: str,
    graph: str | None = None,
    readonly: bool = True,
    timeout_ms: int | None = None,
    batch_rows: int = WRITE_BATCH_ROWS,
) -> Result:
    """`run` for a statement that isn't a query file; `label` names it in errors.

    For the schema's DDL lines (1.5) and, later, the agent's checked Cypher (Phase 9).
    Everything else uses `run`, so the queries the product depends on stay in files.
    """
    graph = graph or get_settings().lore_graph
    params = params or {}
    name = label

    if readonly:
        timeout = get_settings().query_timeout_ms if timeout_ms is None else timeout_ms
        if not 0 < timeout <= READ_TIMEOUT_MAX_MS:
            raise ValueError(f"read timeout must be 1..{READ_TIMEOUT_MAX_MS} ms, got {timeout}")
        g = client(readonly=True).select_graph(graph)
        result = _execute(name, graph, None, lambda: g.ro_query(text, params, timeout=timeout))
        return _collect([result])

    if timeout_ms is not None:
        raise ValueError(
            "writes can't take a per-query timeout: the server caps each write statement at "
            "TIMEOUT_DEFAULT (10 s) and rolls it back (A17). Use smaller batches instead."
        )
    if batch_rows < 1:
        raise ValueError(f"batch_rows must be at least 1, got {batch_rows}")
    g = client(readonly=False).select_graph(graph)
    rows = params.get("rows")
    if rows is None:
        return _collect([_execute(name, graph, None, lambda: g.query(text, params))])

    results = []
    for start in range(0, len(rows), batch_rows):
        batch_params = {**params, "rows": rows[start : start + batch_rows]}
        end = min(start + batch_rows, len(rows))
        label = f"rows {start}-{end - 1} of {len(rows)}"
        results.append(_execute(name, graph, label, lambda p=batch_params: g.query(text, p)))
    return _collect(results)


def explain(
    name: str, params: dict[str, Any] | None = None, *, graph: str | None = None
) -> list[str]:
    """The execution plan of queries/<name>.cypher, one operation per line (nothing runs).

    An indexed lookup shows as `Node By Index Scan`; a full scan as `Node By Label Scan`.
    """
    return explain_text(load_query(name), params, label=name, graph=graph)


def explain_text(
    text: str, params: dict[str, Any] | None = None, *, label: str, graph: str | None = None
) -> list[str]:
    """`explain` for a statement that isn't a query file (see `run_text`)."""
    graph = graph or get_settings().lore_graph
    g = client(readonly=True).select_graph(graph)
    plan = _execute(label, graph, None, lambda: g.explain(text, params or {}))
    return list(plan.plan)


def _execute(name: str, graph: str, batch: str | None, call):
    try:
        return call()
    except ResponseError as e:
        message = str(e)
        error = QueryTimeout if message == "Query timed out" else QueryError
        raise error(name, graph, message, batch) from e


def _collect(results: list) -> Result:
    if not results:
        return Result(
            columns=[], rows=[], stats=dict.fromkeys(STAT_FIELDS, 0), run_time_ms=0.0, batches=0
        )
    # In the client's compact mode each header entry is [column type, column name].
    columns = [col[1] if isinstance(col, list | tuple) else col for col in results[-1].header]
    rows = [dict(zip(columns, row, strict=True)) for r in results for row in r.result_set]
    stats = {f: sum(int(getattr(r, f)) for r in results) for f in STAT_FIELDS}
    run_time_ms = sum(r.run_time_ms for r in results)
    return Result(columns, rows, stats, run_time_ms, batches=len(results))
