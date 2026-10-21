"""`yaan export` and `yaan import` (H6, D120): a graph as plain JSONL, readable by anything.

A snapshot (snapshot.py) is FalkorDB's own DUMP payload: fast, but it restores only on the
same FalkorDB version. An export is the graph itself, one JSON object per line:

    {"yaan_export": 1, "graph": ..., "created_utc": ..., "nodes": n, "edges": m}
    {"node": {"labels": ["Person", "Entity"], "props": {"id": "eid_…", ...}}}
    {"edge": {"type": "AUTHORED", "from": ["Person", "eid_…"], "to": ["Message", "msg:…"],
              "props": {...}}}

Nodes are read in pages by internal id and named by their own `id` (every node has one), with
their type label; edges name their endpoints that way, so another tool (or another Yaan) can
load them without FalkorDB's internal ids. Chunk embeddings are left out unless asked for
(1.5 KB a chunk); an import without them can re-embed. `import` writes into a new, empty
graph: the schema first, then nodes grouped by label set, then edges grouped by type and
endpoint labels, so every endpoint lookup uses an index.
"""

import gzip
import io
import json
import re
import time
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from app import db

FORMAT = 1
PAGE = 5_000  # rows per read
UMBRELLA = ("Entity", "Artifact")  # the labels that aren't a node's type (CONTEXT, Conventions)
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")  # a label or type safe to write into Cypher
READ_TIMEOUT_MS = 120_000


class ExportError(RuntimeError):
    pass


def type_label(labels: list[str]) -> str:
    own = [x for x in labels if x not in UMBRELLA]
    return own[0] if own else labels[0]


def nodes(graph: str, embeddings: bool = False) -> Iterator[dict[str, Any]]:
    start = 0  # `id(n) > -1` matches no node on 6.0.1 (A22): page from 0, inclusive
    while True:
        rows = db.run_text(
            "MATCH (n) WHERE id(n) >= $start "
            "RETURN id(n) AS nid, labels(n) AS labels, properties(n) AS props "
            "ORDER BY nid LIMIT $page",
            {"start": start, "page": PAGE},
            label="export_nodes",
            graph=graph,
            timeout_ms=READ_TIMEOUT_MS,
        ).rows
        for r in rows:
            props = dict(r["props"])
            if not embeddings:
                props.pop("embedding", None)
            yield {"labels": list(r["labels"]), "props": props}
        if len(rows) < PAGE:
            return
        start = rows[-1]["nid"] + 1


def edges(graph: str) -> Iterator[dict[str, Any]]:
    start = 0  # paged like nodes, though edges don't need it (A22)
    while True:
        rows = db.run_text(
            "MATCH (a)-[e]->(b) WHERE id(e) >= $start "
            "RETURN id(e) AS eid, type(e) AS type, properties(e) AS props, "
            "labels(a) AS al, a.id AS a, labels(b) AS bl, b.id AS b "
            "ORDER BY eid LIMIT $page",
            {"start": start, "page": PAGE},
            label="export_edges",
            graph=graph,
            timeout_ms=READ_TIMEOUT_MS,
        ).rows
        for r in rows:
            yield {
                "type": r["type"],
                "from": [type_label(r["al"]), r["a"]],
                "to": [type_label(r["bl"]), r["b"]],
                "props": dict(r["props"]),
            }
        if len(rows) < PAGE:
            return
        start = rows[-1]["eid"] + 1


def _open(path: Path, mode: str):
    if path.suffix == ".gz":
        return io.TextIOWrapper(gzip.open(path, mode + "b"), encoding="utf-8")
    return path.open(mode, encoding="utf-8", newline="\n")


def export(graph: str, out: Path, embeddings: bool = False) -> dict[str, Any]:
    """Write `graph` to `out` (.jsonl, or .jsonl.gz); returns the header."""
    n_nodes = n_edges = 0
    body = out.with_name(out.name + ".part")
    with _open(body, "w") as f:
        for node in nodes(graph, embeddings):
            f.write(json.dumps({"node": node}, ensure_ascii=False) + "\n")
            n_nodes += 1
        for edge in edges(graph):
            f.write(json.dumps({"edge": edge}, ensure_ascii=False) + "\n")
            n_edges += 1
    header = {
        "yaan_export": FORMAT,
        "graph": graph,
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "nodes": n_nodes,
        "edges": n_edges,
        "embeddings": embeddings,
    }
    with _open(out, "w") as f, _open(body, "r") as rest:
        f.write(json.dumps(header) + "\n")
        for line in rest:
            f.write(line)
    body.unlink()
    return header | {"file": str(out)}


def load(path: Path, graph: str) -> dict[str, Any]:
    """Load an export into `graph`, which must not exist yet. Returns the counts written."""
    from app import schema

    if graph in db.client(readonly=False).list_graphs():
        raise ExportError(f"graph {graph!r} already exists: import into a new name")
    with _open(path, "r") as f:
        header = json.loads(f.readline())
        if header.get("yaan_export") != FORMAT:
            raise ExportError(f"{path} isn't a Yaan export (format {FORMAT})")
        by_labels: dict[tuple[str, ...], list[dict]] = defaultdict(list)
        by_type: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
        for line in f:
            row = json.loads(line)
            if "node" in row:
                labels = tuple(row["node"]["labels"])
                by_labels[labels].append(row["node"]["props"])
            else:
                e = row["edge"]
                key = (e["type"], e["from"][0], e["to"][0])
                by_type[key].append({"a": e["from"][1], "b": e["to"][1], "props": e["props"]})
    schema.apply(graph)
    written = {"nodes": 0, "edges": 0}
    for labels, rows in by_labels.items():
        for name in labels:
            if not NAME.match(name):
                raise ExportError(f"label {name!r} can't be written safely")
        vectors = [{"id": r["id"], "v": r.pop("embedding")} for r in rows if "embedding" in r]
        made = db.run_text(
            f"UNWIND $rows AS r CREATE (n:{':'.join(labels)}) SET n = r",
            {"rows": rows},
            label="import_nodes",
            graph=graph,
            readonly=False,
        )
        if vectors:  # a vector goes in through vecf32, or the index leaves it out (A15)
            db.run_text(
                f"UNWIND $rows AS r MATCH (n:{type_label(list(labels))} {{id: r.id}}) "
                "SET n.embedding = vecf32(r.v)",
                {"rows": vectors},
                label="import_vectors",
                graph=graph,
                readonly=False,
            )
        written["nodes"] += made.stats["nodes_created"]
    for (kind, a, b), rows in by_type.items():
        for name in (kind, a, b):
            if not NAME.match(name):
                raise ExportError(f"name {name!r} can't be written safely")
        made = db.run_text(
            f"UNWIND $rows AS r MATCH (x:{a} {{id: r.a}}) MATCH (y:{b} {{id: r.b}}) "
            f"CREATE (x)-[e:{kind}]->(y) SET e = r.props",
            {"rows": rows},
            label="import_edges",
            graph=graph,
            readonly=False,
        )
        written["edges"] += made.stats["relationships_created"]
    if (written["nodes"], written["edges"]) != (header["nodes"], header["edges"]):
        raise ExportError(f"wrote {written}, the export holds {header['nodes']}/{header['edges']}")
    return {"graph": graph, "source": header["graph"], **written}
