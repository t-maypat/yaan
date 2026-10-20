"""Docs guardrails: diagrams display everywhere and stay fresh; docs/queries.md lists every query.

Raw ```mermaid blocks show up as plain syntax in VS Code's preview, so the docs embed
SVGs rendered by scripts/render_diagrams.py. These tests fail on a raw Mermaid block, a
broken image link, an SVG older than its source, or a started phase without a guide.
"""

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
SKIP_DIRS = {".venv", "node_modules", "third_party", "data", ".git"}

_spec = importlib.util.spec_from_file_location(
    "render_diagrams", ROOT / "scripts" / "render_diagrams.py"
)
render_diagrams = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(render_diagrams)

MARKDOWN = sorted(
    p for p in ROOT.rglob("*.md") if not SKIP_DIRS.intersection(p.relative_to(ROOT).parts)
)
IMAGE_LINK = re.compile(r"!\[[^\]]*\]\(([^)\s]+)")
# An actual fenced block (```mermaid or ~~~mermaid at the start of a line), not a mention.
MERMAID_FENCE = re.compile(r"^\s*(?:```|~~~)\s*mermaid\b", re.MULTILINE)


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


@pytest.mark.parametrize("source", render_diagrams.sources(), ids=lambda p: p.name)
def test_diagram_is_rendered_and_fresh(source):
    svg = source.with_suffix(".svg")
    fix = f"run: uv run --project backend python scripts/render_diagrams.py {source.stem}"
    assert svg.is_file(), f"{rel(svg)} is missing; {fix}"
    first_line = svg.read_text(encoding="utf-8").split("\n", 1)[0]
    assert first_line == render_diagrams.stamp(source), f"{rel(svg)} is stale; {fix}"


@pytest.mark.parametrize("doc", MARKDOWN, ids=rel)
def test_markdown_renders_everywhere(doc):
    text = doc.read_text(encoding="utf-8")
    assert not MERMAID_FENCE.search(text), (
        "raw Mermaid shows as syntax in VS Code: add a docs/diagrams/*.mmd and embed its SVG"
    )
    for target in IMAGE_LINK.findall(text):
        if "://" not in target:
            assert (doc.parent / target).resolve().is_file(), f"broken image link: {target}"


PUBLIC_PAGES = [ROOT / "README.md", ROOT / "site" / "index.html", ROOT / "site" / "docs.html"]
QUERY_COUNT = re.compile(r"(\d+) (?:graph |Cypher )?queries")
TOOL_COUNT = re.compile(r"(\d+) read-only (?:graph |agent )?tools(?! plus `ask`)|(\d+) agent tools")
MCP_TOOL_COUNT = re.compile(r"(\d+) read-only graph tools plus `ask`")
BADGE = re.compile(r'<span class="lg-n">([^<]+)<span class="lg-x">experimental</span>')


def numbers(pattern: re.Pattern[str]) -> dict[str, set[int]]:
    found: dict[str, set[int]] = {}
    for page in PUBLIC_PAGES:
        for m in pattern.finditer(page.read_text(encoding="utf-8")):
            found.setdefault(rel(page), set()).add(int(next(g for g in m.groups() if g)))
    return found


def test_public_pages_count_what_the_repo_holds():
    """The README and the site state counts a reader can check: they must match the code
    (the site once said 166 queries while the repo held 193)."""
    from app.agent import tools
    from app.mcp_server import EXCLUDED

    queries = len(list((ROOT / "backend" / "queries").glob("*.cypher")))
    for page, said in numbers(QUERY_COUNT).items():
        assert said == {queries}, f"{page} says {said} queries; backend/queries holds {queries}"
    agent = len(tools.REGISTRY)
    for page, said in numbers(TOOL_COUNT).items():
        assert said == {agent}, f"{page} says {said} agent tools; the registry holds {agent}"
    mcp = len(set(tools.REGISTRY) - EXCLUDED)
    for page, said in numbers(MCP_TOOL_COUNT).items():
        assert said == {mcp}, f"{page} says {said} MCP tools; the server offers {mcp}"


def test_the_site_marks_every_experimental_connector():
    """Only Slack, GitHub, Jira and Notion have run against the live tools (D090): the site
    shows the other eight as experimental."""
    from app.connectors.runner import SOURCES
    from app.org import SOURCE_NAMES

    experimental = {SOURCE_NAMES[s] for s in SOURCES[4:]}
    page = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
    assert set(BADGE.findall(page)) == experimental


def test_the_query_list_is_complete():
    """Submission rule: docs/queries.md, linked from the README, lists every .cypher file
    (scripts/query_table.py builds it)."""
    import subprocess
    import sys

    script = Path(__file__).resolve().parents[2] / "scripts" / "query_table.py"
    done = subprocess.run([sys.executable, str(script), "--check"], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
