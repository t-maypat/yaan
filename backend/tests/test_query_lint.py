"""Mechanical guardrails for backend/queries/*.cypher (FalkorDB is not Neo4j; see CLAUDE.md).

Each query's behavior is covered by its fixture test; this only catches banned syntax.
Extend it when Phase 1's contract tests confirm more unsupported constructs.
"""

import re
from pathlib import Path

import pytest

QUERIES = sorted((Path(__file__).resolve().parents[1] / "queries").glob("*.cypher"))

WRITE_CLAUSE = re.compile(r"\b(CREATE|MERGE|SET|DELETE|REMOVE)\b", re.IGNORECASE)
LABEL_ALTERNATION = re.compile(r"\(\s*\w*\s*:\s*\w+\s*\|")
# `a = b` / `a <> b` between bare variables; property access, params and literals pass.
NODE_EQUALITY = re.compile(r"(?<![\w.$])([A-Za-z_]\w*)\s*(?:=|<>)\s*([A-Za-z_]\w*)(?![\w.(])")


def code(path: Path) -> str:
    """The query text without full-line // comments."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(line for line in lines if not line.lstrip().startswith("//"))


@pytest.mark.parametrize("path", QUERIES, ids=lambda p: p.name)
def test_header_names_file_and_purpose(path):
    first = path.read_text(encoding="utf-8").splitlines()[0]
    assert re.match(rf"//\s*{re.escape(path.name)}\s+[—-]\s+\S", first), (
        f"first line must be '// {path.name} — <purpose>'"
    )


@pytest.mark.parametrize("path", QUERIES, ids=lambda p: p.name)
def test_no_unsupported_syntax(path):
    text = code(path)
    assert "=~" not in text, "regex =~ is unsupported; use STARTS WITH / CONTAINS / toLower()"
    assert "db.idx.fulltext.createNodeIndex" not in text, (
        "the 4.x full-text procedure fails on 6.0; use CREATE FULLTEXT INDEX in 00_schema.cypher"
    )
    assert not LABEL_ALTERNATION.search(text), (
        "label alternation (n:A|B) is unsupported; use an umbrella label or UNION"
    )
    match = NODE_EQUALITY.search(text)
    assert not match, f"{match.group(0)!r} looks like node equality; compare ids (a.id = b.id)"


@pytest.mark.parametrize(
    "path", [p for p in QUERIES if not p.name.startswith("00_")], ids=lambda p: p.name
)
def test_read_queries_have_limit(path):
    text = code(path)
    if re.search(r"\bRETURN\b", text, re.IGNORECASE) and not WRITE_CLAUSE.search(text):
        assert re.search(r"\bLIMIT\b", text, re.IGNORECASE), "read queries need an explicit LIMIT"
