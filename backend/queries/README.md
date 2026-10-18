# Cypher queries

Every query the product depends on lives here, one file per query. The submission README must list them (rule 7); Phase 16 generates that table from the header lines.

Rules (CLAUDE.md; CONTEXT §3.6):
- First line: `// <file>.cypher — <one-line purpose>`.
- One statement per file, parameterized with `$params`. Never format values into Cypher strings. The exception is `00_schema.cypher`: DDL, one statement per line, run line by line.
- Read queries carry a `LIMIT` (`$k` or a constant). Writes use `UNWIND $rows AS r MERGE …` with a fixed label set per node type.
- No node equality, label alternation, `=~`, temporal arithmetic or unbounded var-length paths.
- Each query has a fixture test, and `GRAPH.EXPLAIN` should show index use.

`backend/tests/test_query_lint.py` checks the mechanical rules on every file here.
