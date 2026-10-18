"""Render docs/diagrams/*.mmd into SVG images that display everywhere.

Raw ```mermaid blocks don't render in VS Code's Markdown preview (you see the syntax),
so the docs embed pre-rendered SVGs instead. For each source this:
  1. appends the shared draw.io-style palette (docs/diagrams/_palette.mmd),
  2. renders it with a pinned mermaid-cli, labels as SVG text (no HTML) so any viewer
     can draw them,
  3. gives the SVG an explicit size and a white backdrop (readable in dark themes too),
  4. stamps it with a hash of its inputs, so backend/tests/test_docs.py catches stale images.

Usage, from the repo root:
    uv run --project backend python scripts/render_diagrams.py [name ...] [--preview DIR]
--preview also writes PNG copies to DIR, so you (or Claude) can eyeball the layout.
Needs Node.js. The first run downloads mermaid-cli and a headless browser (~5 minutes).
"""

import argparse
import hashlib
import math
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MERMAID_CLI = "@mermaid-js/mermaid-cli@12.0.0"
ROOT = Path(__file__).resolve().parents[1]
DIAGRAMS = ROOT / "docs" / "diagrams"
PALETTE = DIAGRAMS / "_palette.mmd"
CONFIG = DIAGRAMS / "mermaid.config.json"


def sources() -> list[Path]:
    return sorted(p for p in DIAGRAMS.glob("*.mmd") if not p.name.startswith("_"))


def source_hash(source: Path) -> str:
    """Hash of everything that shapes the rendered image; line endings normalized."""
    digest = hashlib.sha256(MERMAID_CLI.encode())
    for path in (source, PALETTE, CONFIG):
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def stamp(source: Path) -> str:
    return f"<!-- lore-diagram sha256={source_hash(source)} -->"


def mmdc(source_text: str, output: Path) -> None:
    npx = shutil.which("npx")
    if npx is None:
        sys.exit("npx not found: install Node.js to render diagrams")
    with tempfile.TemporaryDirectory() as tmp:
        combined = Path(tmp) / "diagram.mmd"
        combined.write_text(source_text, encoding="utf-8")
        subprocess.run(
            [npx, "--yes", MERMAID_CLI, "--input", str(combined), "--output", str(output),
             "--configFile", str(CONFIG), "--backgroundColor", "white", "--quiet"]
            + (["--scale", "2"] if output.suffix == ".png" else []),
            check=True,
        )


def finish(svg: str, source: Path) -> str:
    """Explicit size (an <img> of a width="100%" SVG can collapse), white backdrop, stamp."""
    view_box = re.search(r'viewBox="([-\d.]+) ([-\d.]+) ([\d.]+) ([\d.]+)"', svg)
    if view_box is None:
        sys.exit(f"{source.name}: rendered SVG has no viewBox")
    x, y, w, h = (float(v) for v in view_box.groups())
    svg, sized = re.subn(r'\bwidth="100%"', f'width="{math.ceil(w)}" height="{math.ceil(h)}"',
                         svg, count=1)
    if not sized:
        sys.exit(f'{source.name}: expected width="100%" on the root <svg>')
    svg = re.sub(r"(<svg\b[^>]*>)",
                 rf'\1<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="#ffffff"/>',
                 svg, count=1)
    return stamp(source) + "\n" + svg + "\n"


def render(source: Path, preview: Path | None) -> None:
    text = source.read_text(encoding="utf-8").rstrip() + "\n\n" + PALETTE.read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "out.svg"
        mmdc(text, out)
        svg = finish(out.read_text(encoding="utf-8"), source)
    source.with_suffix(".svg").write_text(svg, encoding="utf-8", newline="\n")
    if preview is not None:
        preview.mkdir(parents=True, exist_ok=True)
        mmdc(text, preview / f"{source.stem}.png")
    print(f"rendered {source.with_suffix('.svg').relative_to(ROOT)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("names", nargs="*", help="diagram names (default: all)")
    parser.add_argument("--preview", type=Path, help="also write PNG copies here")
    args = parser.parse_args()
    chosen = [DIAGRAMS / f"{n}.mmd" for n in args.names] if args.names else sources()
    missing = [p.name for p in chosen if not p.is_file()]
    if missing:
        sys.exit(f"no such diagram source: {missing}")
    for source in chosen:
        render(source, args.preview)


if __name__ == "__main__":
    main()
