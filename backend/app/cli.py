"""Lore command line: `lore <command>` (or `python -m app.cli <command>`).

The Makefile wraps these commands for Linux/macOS; on Windows call them directly.
A command that isn't built yet exits with status 2 and names the phase that builds it.
When a phase implements a command, it moves from PENDING to a `cmd_*` function in COMMANDS.
"""

import argparse
import sys

# command -> (docs/PLAN.md phase that builds it, help text)
PENDING: dict[str, tuple[str, str]] = {
    "normalize": ("Phase 2", "HERB products -> data/normalized/<product>/artifacts.jsonl"),
    "ingest": ("Phase 3", "load normalized products into a graph (stages S2-S10)"),
    "health": ("Phase 3", "print the graph-health report"),
    "ask": ("Phase 4", "answer a question (baseline RAG in Phase 4, Lore agent in Phase 9)"),
    "eval": ("Phase 5", "run and score a system on an eval split"),
    "seed": ("Phase 15", "rebuild the demo graph from exports/ without LLM calls"),
}


def cmd_schema(args: argparse.Namespace) -> int:
    from app import schema
    from app.config import get_settings

    graph = args.graph or get_settings().lore_graph
    for spec, outcome in schema.apply(graph):
        print(f"  {outcome:<8} {spec}")
    found = schema.verify(graph)
    for problem in found:
        print(f"  ! {problem}", file=sys.stderr)
    if found:
        print(f"lore schema: graph {graph!r} does not match {schema.SCHEMA_FILE}", file=sys.stderr)
        return 1
    print(f"graph {graph!r}: all {len(schema.declared())} indexes present and operational")
    return 0


def cmd_fetch_herb(args: argparse.Namespace) -> int:
    from app.ingest.herb_fetch import HERB_REVISION, fetch_herb, product_files

    dest = fetch_herb()
    print(f"HERB @ {HERB_REVISION[:8]} in {dest}: {len(product_files(dest))} products + metadata")
    return 0


def cmd_rank_dev(args: argparse.Namespace) -> int:
    from app.eval.split import DEV_COUNT, SIGNALS, rank_products, write_ranking

    rows = rank_products()
    print(f"{'#':>2}  {'product':<22}" + "".join(f"{s:>22}" for s in SIGNALS) + f"{'score':>8}")
    for row in rows:
        mark = "  <- dev" if row["rank"] <= DEV_COUNT else ""
        counts = "".join(f"{row[s]:>22}" for s in SIGNALS)
        print(f"{row['rank']:>2}  {row['product']:<22}{counts}{row['score']:>8.3f}{mark}")
    print(f"\nwrote {write_ranking(rows)}")
    return 0


# command -> (help text, handler)
COMMANDS = {
    "schema": ("create and verify the indexes for a graph (queries/00_schema.cypher)", cmd_schema),
    "fetch-herb": ("download the HERB dataset (pinned revision) into data/herb", cmd_fetch_herb),
    "rank-dev": ("rank products by change signals to pick the dev products (D017)", cmd_rank_dev),
}

# command -> [(flag, argparse options)]
OPTIONS: dict[str, list[tuple[str, dict]]] = {
    "schema": [("--graph", {"help": "graph name (default: LORE_GRAPH, e.g. lore_herb)"})],
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="lore", description="Lore: a time-aware company brain on FalkorDB."
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="<command>")
    for name, (help_text, _) in COMMANDS.items():
        sub = commands.add_parser(name, help=help_text)
        for flag, options in OPTIONS.get(name, []):
            sub.add_argument(flag, **options)
    for name, (phase, help_text) in PENDING.items():
        commands.add_parser(name, help=f"{help_text} [not built yet: {phase}]")
    args, _ = parser.parse_known_args(argv)

    if args.command in COMMANDS:
        return COMMANDS[args.command][1](args)
    phase, _ = PENDING[args.command]
    print(
        f"lore {args.command}: not built yet; it arrives in {phase} (docs/PLAN.md).",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
