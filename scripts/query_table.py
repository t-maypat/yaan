"""The table of every Cypher query Yaan depends on (CONTEXT §12 item 7, a submission
requirement), built from each file's first line: `// <name>.cypher — <purpose>`. It lives in
docs/queries.md; the README links to it and lists the key queries.

    uv run --project backend python scripts/query_table.py           # rewrite the table in docs/queries.md
    uv run --project backend python scripts/query_table.py --check   # exit 1 if it is stale

The table sits between `<!-- queries -->` and `<!-- /queries -->` in docs/queries.md. The
query count the README and the site state ("193 Cypher queries") is rewritten too (D106).
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUERIES = ROOT / "backend" / "queries"
LIST = ROOT / "docs" / "queries.md"
START, END = "<!-- queries -->", "<!-- /queries -->"
PUBLIC = [ROOT / "README.md", ROOT / "site" / "index.html", ROOT / "site" / "docs.html"]
COUNT = re.compile(r"\b\d+(?= (?:graph |Cypher )?queries)")  # = test_docs.QUERY_COUNT
HEADER = re.compile(r"^//\s*(?P<name>[\w.]+\.cypher)\s*[—-]+\s*(?P<purpose>.+)$")

# (group title, test on the query name), first match wins
GROUPS = [
    ("Schema (indexes, applied and verified by `yaan schema`)", lambda n: n == "00_schema"),
    ("Search (full-text and vector, inside FalkorDB)", lambda n: n.startswith("search_")),
    ("Graph health", lambda n: n.startswith("health_")),
    (
        "Writes: ingestion, linking, the fold, derived layers, the Sentinel",
        lambda n: n.startswith(("upsert_", "link_", "set_", "clear_", "mark_", "derive_", "delete_")),
    ),
    ("Reads: agent tools, the API, the Sentinel, connectors, permissions", lambda n: True),
]


def rows() -> list[tuple[str, str, str]]:
    out = []
    for path in sorted(QUERIES.glob("*.cypher")):
        first = path.read_text(encoding="utf-8").splitlines()[0]
        m = HEADER.match(first)
        if not m or m["name"] != path.name:
            raise SystemExit(f"{path.name}: first line must be '// {path.name} — <purpose>'")
        group = next(title for title, test in GROUPS if test(path.stem))
        out.append((group, path.name, m["purpose"].strip()))
    return out


def table() -> str:
    found = rows()
    lines = [f"{len(found)} queries, one parameterized statement per file in "
             "[`backend/queries/`](../backend/queries/), each with a fixture test.", ""]
    for title, _ in GROUPS:
        mine = [r for r in found if r[0] == title]
        if not mine:
            continue
        lines += [f"**{title}** ({len(mine)})", "", "| Query | Purpose |", "|---|---|"]
        for _, name, purpose in mine:
            lines.append(f"| [`{name}`](../backend/queries/{name}) | {purpose.replace('|', '/')} |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    text = LIST.read_text(encoding="utf-8")
    if START not in text or END not in text:
        raise SystemExit(f"docs/queries.md needs the markers {START} and {END}")
    head, rest = text.split(START, 1)
    _, tail = rest.split(END, 1)
    new = f"{head}{START}\n{table()}{END}{tail}"
    n = str(len(rows()))
    pages = {page: COUNT.sub(n, page.read_text(encoding="utf-8")) for page in PUBLIC}
    stale = [page for page, body in pages.items() if body != page.read_text(encoding="utf-8")]
    if "--check" in sys.argv:
        if new != text or stale:
            names = ", ".join(p.relative_to(ROOT).as_posix() for p in [LIST] * (new != text) + stale)
            print(f"stale query count or table in {names}: run scripts/query_table.py", file=sys.stderr)
            return 1
        return 0
    LIST.open("w", encoding="utf-8", newline="\n").write(new)
    for page in stale:
        page.open("w", encoding="utf-8", newline="\n").write(pages[page])
    print(f"docs/queries.md and {len(stale)} public pages: {n} queries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
