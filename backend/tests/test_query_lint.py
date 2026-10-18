"""Mechanical guardrails for backend/queries/*.cypher (FalkorDB is not Neo4j; see CLAUDE.md).

Each query's behavior is covered by its fixture test; this only catches syntax that fails, or
silently misbehaves, on the pinned server. Every rule cites the contract test that proved it
(decisions.md A-table, D021). `problems()` is tested on samples below, then run on every file.
"""

import re
from pathlib import Path

import pytest

QUERIES = sorted((Path(__file__).resolve().parents[1] / "queries").glob("*.cypher"))

WRITE_CLAUSE = re.compile(r"\b(CREATE|MERGE|SET|DELETE|REMOVE)\b", re.IGNORECASE)
LABEL_ALTERNATION = re.compile(r"\(\s*\w*\s*:\s*\w+\s*\|")
# `a = b` / `a <> b` between bare variables; property access, params and literals pass.
NODE_EQUALITY = re.compile(r"(?<![\w.$])([A-Za-z_]\w*)\s*(?:=|<>)\s*([A-Za-z_]\w*)(?![\w.(])")
EXISTS_SUBQUERY = re.compile(r"\bEXISTS\s*\{", re.IGNORECASE)
SHORTEST_IN_MATCH = re.compile(r"\bMATCH\s+\w+\s*=\s*shortestPath\s*\(", re.IGNORECASE)
# {embedding: x} or SET n.embedding = x (not `=~`, and not the index's `ON (c.embedding)`)
EMBEDDING_ASSIGNMENT = re.compile(r"\bembedding\s*(?::|=(?!~))")
UNBOUNDED_PATH = re.compile(r"\*\s*\]|\*\s*\d*\s*\.\.\s*\]")  # [*] or [*2..] (no upper bound)
# A WHERE holding a pattern predicate, then MATCH / OPTIONAL MATCH with no WITH between (A18).
PREDICATE_THEN_MATCH = re.compile(
    r"\bWHERE\b(?:(?!\b(?:MATCH|WITH|RETURN|UNWIND|CALL)\b).)*?\(\w*\)\s*<?-\[[^\]]*\]->?"
    r"(?:(?!\b(?:MATCH|WITH|RETURN|UNWIND|CALL)\b).)*?\b(?:OPTIONAL\s+)?MATCH\b",
    re.IGNORECASE | re.DOTALL,
)
# A19: grouping an aggregate by a property some nodes lack can crash the 6.0.1 server, so a
# RETURN / WITH that aggregates may group only by variables, ids, coalesce() or functions.
AGGREGATE = re.compile(
    r"\b(?:collect|count|sum|avg|min|max|percentileCont|percentileDisc|stDev)\s*\(", re.I
)
CLAUSE = re.compile(
    r"\b(RETURN|WITH)\b(.*?)(?=\b(?:MATCH|OPTIONAL|WITH|RETURN|UNWIND|ORDER|LIMIT|SKIP|WHERE"
    r"|CALL|UNION|MERGE|CREATE|SET|DELETE)\b|$)",
    re.IGNORECASE | re.DOTALL,
)
SAFE_GROUP_KEY = re.compile(
    r"^(?:DISTINCT\s+)?(?:\w+|\w+\.id|\$\w+|coalesce\(.*\)|labels\(\w+\)|type\(\w+\)"
    r"|toInteger\(.*\)|'[^']*'|\d+)(?:\s+AS\s+\w+)?$",
    re.IGNORECASE | re.DOTALL,
)


def top_level_items(body: str) -> list[str]:
    """Split a RETURN / WITH body at commas that aren't inside (), [] or {}."""
    items, depth, current = [], 0, ""
    for ch in body:
        depth += ch in "([{"
        depth -= ch in ")]}"
        if ch == "," and depth == 0:
            items.append(current.strip())
            current = ""
        else:
            current += ch
    return [*items, current.strip()] if current.strip() else items


def property_group_keys(text: str) -> list[str]:
    """Grouping keys of aggregating clauses that read a property (other than an id)."""
    keys = []
    for m in CLAUSE.finditer(text):
        if AGGREGATE.search(m.group(2)):
            keys += [
                item
                for item in top_level_items(m.group(2))
                if not AGGREGATE.search(item) and not SAFE_GROUP_KEY.match(item)
            ]
    return keys


def code(text: str) -> str:
    """The query text without full-line // comments."""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("//"))


def problems(text: str) -> list[str]:
    """Every rule the query breaks (empty = clean)."""
    text = code(text)
    found = []
    if "=~" in text:
        found.append("regex =~: use STARTS WITH / CONTAINS / toLower() (A10, D021)")
    if LABEL_ALTERNATION.search(text):
        found.append("label alternation (n:A|B) is unsupported: use an umbrella label (A9)")
    if m := NODE_EQUALITY.search(text):
        found.append(f"{m.group(0)!r} looks like node equality: compare ids (a.id = b.id)")
    if EXISTS_SUBQUERY.search(text):
        found.append("EXISTS { … } subqueries fail: use a pattern predicate in WHERE (A2)")
    if SHORTEST_IN_MATCH.search(text):
        found.append("shortestPath() only works in WITH or RETURN (A5)")
    if UNBOUNDED_PATH.search(text):
        found.append("unbounded variable-length path: give an upper bound, e.g. [*..4]")
    if PREDICATE_THEN_MATCH.search(text):
        found.append("a pattern predicate in WHERE right before MATCH misbehaves: add WITH (A18)")
    if keys := property_group_keys(text):
        found.append(
            f"aggregation grouped by {keys}: a property some nodes lack can crash the server; "
            "aggregate by the node in a WITH, then read properties (A19)"
        )
    if "db.idx.fulltext.createNodeIndex" in text:
        found.append("the 4.x full-text procedure fails on 6.0: CREATE FULLTEXT INDEX (D019)")
    if EMBEDDING_ASSIGNMENT.search(text) and "vecf32" not in text:
        found.append("embeddings must be written with vecf32(): a plain list isn't indexed (A15)")
    return found


@pytest.mark.parametrize(
    "bad",
    [
        "MATCH (p:Person) WHERE p.name =~ 'A.*' RETURN p LIMIT 1",
        "MATCH (n:Person|Team) RETURN n LIMIT 1",
        "MATCH (a), (b) WHERE a = b RETURN a LIMIT 1",
        "MATCH (p) WHERE NOT EXISTS { MATCH (p)-->() } RETURN p LIMIT 1",
        "MATCH (a {id: $a}), (b {id: $b}) MATCH p = shortestPath((a)-[*..4]-(b)) RETURN p LIMIT 1",
        "MATCH (a)-[*]->(b) RETURN b LIMIT 1",
        "MATCH (a)-[:R*2..]->(b) RETURN b LIMIT 1",
        "CALL db.idx.fulltext.createNodeIndex('Chunk', 'text')",
        "UNWIND $rows AS r MERGE (c:Chunk {id: r.id}) SET c.embedding = r.v",
        "UNWIND $rows AS r CREATE (:Chunk {id: r.id, embedding: r.v})",
        "MATCH (m) WHERE (m)-[:MENTIONS]->(:Customer) MATCH (m)-[:MENTIONS]->(c) RETURN c LIMIT 1",
        "MATCH (a:A) RETURN a.id, a.title, count(a) LIMIT 1",
        "MATCH (a)-[:R]->(b) RETURN a.name AS name, collect(b.id) AS bs LIMIT 1",
    ],
)
def test_problems_catches(bad):
    assert problems(bad), bad


@pytest.mark.parametrize(
    "good",
    [
        "MATCH (p:Person)-[:AUTHORED|REVIEWED]->(a:PR) RETURN p.id LIMIT 5",  # A1 works
        "MATCH (p:Person) WHERE NOT (p)-[:AUTHORED]->() RETURN p.id LIMIT 5",  # A2 works
        "MATCH (a), (b) WHERE a.id = b.id RETURN a.id LIMIT 1",
        "MATCH (a {id: $a}), (b {id: $b}) RETURN length(shortestPath((a)-[*..4]-(b))) LIMIT 1",
        "MATCH (a)-[:R*2]->(b) RETURN b.id LIMIT 1",
        "MATCH (a)-[:R*1..3]->(b) RETURN b.id LIMIT 1",
        "UNWIND $rows AS r MERGE (c:Chunk {id: r.id}) SET c.embedding = vecf32(r.v)",
        "CREATE VECTOR INDEX FOR (c:Chunk) ON (c.embedding) OPTIONS {dimension: 384}",
        "// a comment may say =~ freely\nMATCH (n) RETURN n.id LIMIT 1",
        "MATCH (m) WHERE (m)-[:MENTIONS]->(:C) WITH m MATCH (m)-[:R]->(c) RETURN c.id LIMIT 1",
        "MATCH (a) WHERE $p IS NULL OR (a)-[:IN]->(:P {id: $p}) RETURN a.id LIMIT 1",
        "MATCH (a)-[:R]->(b) WITH a, collect(b.id) AS bs RETURN a.name, a.title, bs LIMIT 1",
        "MATCH (a:A) RETURN coalesce(a.kind, 'none') AS kind, count(a) AS n LIMIT 5",
        "MATCH (a:A) RETURN labels(a) AS l, a.id, count(*) LIMIT 5",
    ],
)
def test_problems_allows(good):
    assert problems(good) == [], good


@pytest.mark.parametrize("path", QUERIES, ids=lambda p: p.name)
def test_header_names_file_and_purpose(path):
    first = path.read_text(encoding="utf-8").splitlines()[0]
    assert re.match(rf"//\s*{re.escape(path.name)}\s+[—-]\s+\S", first), (
        f"first line must be '// {path.name} — <purpose>'"
    )


@pytest.mark.parametrize("path", QUERIES, ids=lambda p: p.name)
def test_no_unsupported_syntax(path):
    assert problems(path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    "path", [p for p in QUERIES if not p.name.startswith("00_")], ids=lambda p: p.name
)
def test_read_queries_have_limit(path):
    text = code(path.read_text(encoding="utf-8"))
    if re.search(r"\bRETURN\b", text, re.IGNORECASE) and not WRITE_CLAUSE.search(text):
        assert re.search(r"\bLIMIT\b", text, re.IGNORECASE), "read queries need an explicit LIMIT"
