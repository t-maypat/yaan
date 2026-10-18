"""Docs guardrails: diagrams display everywhere and stay fresh; learning guides keep up.

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


def test_every_started_phase_has_a_learning_guide():
    plan = (DOCS / "PLAN.md").read_text(encoding="utf-8")
    started = re.findall(r"^\| (\d+) \|.*\| (?:✅|🔄) \|$", plan, re.MULTILINE)
    assert started, "no started phases found in the PLAN.md overview table"
    for phase in started:
        guides = list((DOCS / "learn").glob(f"{int(phase):02d}-*.md"))
        assert guides, f"Phase {phase} has started but docs/learn/{int(phase):02d}-*.md is missing"


def test_learning_guides_are_indexed():
    index = (DOCS / "learn" / "README.md").read_text(encoding="utf-8")
    for guide in sorted((DOCS / "learn").glob("[0-9][0-9]-*.md")):
        assert f"({guide.name})" in index, f"add {guide.name} to docs/learn/README.md"
