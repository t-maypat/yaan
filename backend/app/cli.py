"""Lore command line: `lore <command>` (or `python -m app.cli <command>`).

The Makefile wraps these commands for Linux/macOS; on Windows call them directly.
A command that isn't built yet exits with status 2 and names the phase that builds it.
When a phase implements a command, it replaces that command's PENDING entry.
"""

import argparse
import sys

# command -> (docs/PLAN.md phase that builds it, help text)
PENDING: dict[str, tuple[str, str]] = {
    "schema": ("Phase 1", "create the indexes for a graph (queries/00_schema.cypher)"),
    "fetch-herb": ("Phase 2", "download the HERB dataset into data/herb"),
    "normalize": ("Phase 2", "HERB products -> data/normalized/<product>/artifacts.jsonl"),
    "ingest": ("Phase 3", "load normalized products into a graph (stages S2-S10)"),
    "health": ("Phase 3", "print the graph-health report"),
    "ask": ("Phase 4", "answer a question (baseline RAG in Phase 4, Lore agent in Phase 9)"),
    "eval": ("Phase 5", "run and score a system on an eval split"),
    "seed": ("Phase 15", "rebuild the demo graph from exports/ without LLM calls"),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="lore", description="Lore: a time-aware company brain on FalkorDB."
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="<command>")
    for name, (phase, help_text) in PENDING.items():
        commands.add_parser(name, help=f"{help_text} [not built yet: {phase}]")
    args, _ = parser.parse_known_args(argv)

    phase, _ = PENDING[args.command]
    print(f"lore {args.command}: not built yet; it arrives in {phase} (docs/PLAN.md).",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
