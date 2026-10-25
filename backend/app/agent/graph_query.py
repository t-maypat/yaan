"""Tool 14 `graph_query`: the guarded text-to-Cypher fallback (9.7; CONTEXT §9.6, §9.11).

For questions no curated tool covers. The model writes one read-only Cypher statement
against the schema card (docs/schema_card.md); four locks stand between it and the graph:
1. a static check here: one statement, no write or admin keyword outside string literals, no
   procedure outside a read-only list, bounded paths (at most 6 hops), a final LIMIT <= 200
   (added when missing);
2. EXPLAIN as the reader, so a statement that doesn't parse never runs;
3. GRAPH.RO_QUERY as `yaan_reader`, an ACL user that can't send a write command (A12);
4. a 2 s timeout.
A failure goes back to the model as an error it can fix, at most MAX_ATTEMPTS per answer.
"""

import re
from functools import lru_cache
from typing import Any

from app import db
from app.config import REPO_ROOT

MAX_LIMIT = 200
MAX_HOPS = 6
TIMEOUT_MS = 2000
MAX_ATTEMPTS = 3  # the first try plus 2 retries with the error text (§9.6)
MAX_VALUE_CHARS = 500
WRITE_WORDS = re.compile(
    r"\b(CREATE|MERGE|SET|DELETE|DETACH|REMOVE|DROP|FOREACH|LOAD|GRANT|DENY|REVOKE)\b", re.I
)
CALL = re.compile(r"\bCALL\s+([\w.]+)", re.I)
READ_PROCEDURES = (
    "db.idx.fulltext.querynodes",
    "db.labels",
    "db.relationshiptypes",
    "db.propertykeys",
    "algo.",
)
STRING = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"")
COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
UNBOUNDED = re.compile(r"\*\s*\]|\*\s*\d*\s*\.\.\s*\]")
HOPS = re.compile(r"\*\s*\d*\s*\.\.\s*(\d+)\s*\]|\*\s*(\d+)\s*\]")
FINAL_LIMIT = re.compile(r"\bLIMIT\s+(\d+)\s*$", re.I)


class Refused(ValueError):
    """A statement the guard won't run; the message says why, for the model to fix."""


@lru_cache
def schema_card() -> str:
    """The card between the markers in docs/schema_card.md."""
    text = (REPO_ROOT / "docs" / "schema_card.md").read_text(encoding="utf-8")
    start, end = text.index("<!-- card -->"), text.index("<!-- /card -->")
    return text[start + len("<!-- card -->") : end].strip()


def check(cypher: str) -> str:
    """The statement to run (with LIMIT added if missing), or Refused with the reason."""
    text = COMMENT.sub(" ", cypher).strip().rstrip(";").strip()
    if not text:
        raise Refused("empty query")
    bare = STRING.sub("''", text)  # keywords inside string literals don't count
    if ";" in bare:
        raise Refused("one statement only")
    if m := WRITE_WORDS.search(bare):
        raise Refused(f"read-only: {m[1].upper()} is not allowed")
    for m in CALL.finditer(bare):
        if not m[1].lower().startswith(READ_PROCEDURES):
            raise Refused(f"procedure {m[1]} is not on the read-only list")
    if UNBOUNDED.search(bare):
        raise Refused(f"variable-length paths need an upper bound, at most *..{MAX_HOPS}")
    for m in HOPS.finditer(bare):
        if int(m[1] or m[2]) > MAX_HOPS:
            raise Refused(f"paths are limited to {MAX_HOPS} hops")
    if "=~" in bare:
        raise Refused("no regular expressions: use CONTAINS, STARTS WITH or toLower()")
    limit = FINAL_LIMIT.search(bare)
    if limit is None:
        return f"{text}\nLIMIT {MAX_LIMIT}"
    if int(limit[1]) > MAX_LIMIT:
        raise Refused(f"LIMIT is at most {MAX_LIMIT}")
    return text


def plain(value: Any) -> Any:
    """Graph values as JSON-ready data: nodes and edges as their properties (no vectors)."""
    if hasattr(value, "labels") and hasattr(value, "properties"):  # Node
        props = {k: v for k, v in value.properties.items() if k != "embedding"}
        return {"labels": list(value.labels), **plain(props)}
    if hasattr(value, "relation") and hasattr(value, "properties"):  # Edge
        return {"type": value.relation, **plain(value.properties)}
    if hasattr(value, "nodes") and callable(value.nodes):  # Path
        return {"nodes": [plain(n) for n in value.nodes()]}
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items() if k != "embedding"}
    if isinstance(value, list):
        return [plain(v) for v in value[:MAX_LIMIT]]
    if isinstance(value, str) and len(value) > MAX_VALUE_CHARS:
        return value[:MAX_VALUE_CHARS] + " …"
    return value


def graph_query(cypher: str, graph: str | None = None) -> dict:
    """Run one checked read-only statement. Raises Refused or db.QueryError on failure."""
    statement = check(cypher)
    db.explain_text(statement, label="graph_query", graph=graph)  # parse + plan, runs nothing
    result = db.run_text(statement, label="graph_query", graph=graph, timeout_ms=TIMEOUT_MS)
    return {
        "cypher_run": statement,
        "columns": result.columns,
        "rows": [plain(r) for r in result.rows],
        "row_count": len(result.rows),
        "server_ms": round(result.run_time_ms, 2),
        "cypher": ["graph_query (generated, checked, read-only)"],
    }
