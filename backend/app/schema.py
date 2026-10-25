"""The indexes every Yaan graph needs: declare, apply, verify. Built in Phase 1.5.

backend/queries/00_schema.cypher declares them, one DDL statement per line. `apply` runs each
line as the writer. On FalkorDB 6.0.1 a repeated line fails with "Attribute '<p>' is already
indexed", the only error tolerated, so rerunning is safe. That message hides a trap: asking
for a 768-d vector index over an existing 384-d one also says "already indexed" and keeps the
old one. So `verify` compares what the server reports (list_indexes.cypher) with what the
file declares (kind, and dimension / similarity for vectors) and reports every mismatch.
Spec: docs/CONTEXT.md §9.14; decisions.md D019.
"""

import re
import time
from dataclasses import dataclass, field
from typing import Any

from app import db
from app.config import get_settings

SCHEMA_FILE = "00_schema.cypher"
ALREADY_INDEXED = re.compile(r"^Attribute '\w+' is already indexed$")
INDEX_DDL = re.compile(
    r"^CREATE (?:(?P<kind>FULLTEXT|VECTOR) )?INDEX FOR \(\w+:(?P<label>\w+)\) "
    r"ON \(\w+\.(?P<prop>\w+)\)(?P<rest>.*)$"
)
VECTOR_OPTIONS = {
    "dimension": re.compile(r"dimension:\s*(\d+)"),
    "similarityFunction": re.compile(r"similarityFunction:\s*'(\w+)'"),
}
OPERATIONAL = "OPERATIONAL"


@dataclass(frozen=True)
class IndexSpec:
    label: str
    prop: str
    kind: str  # RANGE, FULLTEXT or VECTOR, as db.indexes() reports it
    options: dict[str, Any] = field(default_factory=dict)  # vector only
    statement: str = ""

    def __str__(self) -> str:
        extra = "".join(f" {k}={v}" for k, v in self.options.items())
        return f"{self.kind} {self.label}.{self.prop}{extra}"


def statements() -> list[str]:
    """The DDL lines of 00_schema.cypher (comments and blank lines skipped)."""
    text = (get_settings().queries_dir / SCHEMA_FILE).read_text(encoding="utf-8")
    return [s for line in text.splitlines() if (s := line.strip()) and not s.startswith("//")]


def declared() -> list[IndexSpec]:
    """Every index the schema file declares. A line that isn't an index statement is an error."""
    specs = []
    for statement in statements():
        m = INDEX_DDL.match(statement)
        if not m:
            raise ValueError(f"{SCHEMA_FILE}: not an index statement: {statement}")
        kind = m["kind"] or "RANGE"
        options = {}
        if kind == "VECTOR":
            for key, pattern in VECTOR_OPTIONS.items():
                found = pattern.search(m["rest"])
                if not found:
                    raise ValueError(f"{SCHEMA_FILE}: vector index without {key}: {statement}")
                options[key] = int(found[1]) if key == "dimension" else found[1]
        specs.append(IndexSpec(m["label"], m["prop"], kind, options, statement))
    return specs


def apply(graph: str) -> list[tuple[IndexSpec, str]]:
    """Run every schema line as the writer; returns (index, "created" | "exists")."""
    outcome = []
    for spec in declared():
        try:
            db.run_text(spec.statement, label=f"{SCHEMA_FILE} {spec}", graph=graph, readonly=False)
            outcome.append((spec, "created"))
        except db.QueryError as e:
            if not ALREADY_INDEXED.match(e.server_message):
                raise
            outcome.append((spec, "exists"))
    return outcome


def problems(specs: list[IndexSpec], rows: list[dict[str, Any]]) -> list[str]:
    """Mismatches between declared indexes and list_indexes rows (empty = all good)."""
    nodes = {row["label"]: row for row in rows if row["entitytype"] == "NODE"}
    found = []
    for spec in specs:
        row = nodes.get(spec.label)
        kinds = (row or {}).get("types", {}).get(spec.prop, [])
        if spec.kind not in kinds:
            found.append(f"missing: {spec}")
            continue
        actual = row["options"].get(spec.prop, {})
        for key, want in spec.options.items():
            if actual.get(key) != want:
                found.append(f"mismatch: {spec}, but the server has {key}={actual.get(key)}")
        if row["status"] != OPERATIONAL:
            found.append(f"not ready: {spec} is {row['status']}")
    return found


def verify(graph: str, wait_s: float = 60.0) -> list[str]:
    """Check the live indexes against the file, waiting up to `wait_s` for any still building."""
    specs = declared()
    deadline = time.monotonic() + wait_s
    while True:
        found = problems(specs, db.run("list_indexes", graph=graph).rows)
        building = found and all(p.startswith("not ready:") for p in found)
        if not building or time.monotonic() >= deadline:
            return found
        time.sleep(1)
