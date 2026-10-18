"""Lore command line: `lore <command>` (or `python -m app.cli <command>`).

The Makefile wraps these commands for Linux/macOS; on Windows call them directly.
A command that isn't built yet exits with status 2 and names the phase that builds it.
When a phase implements a command, it moves from PENDING to a `cmd_*` function in COMMANDS.
"""

import argparse
import sys

# command -> (docs/PLAN.md phase that builds it, help text)
PENDING: dict[str, tuple[str, str]] = {
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


def cmd_split(args: argparse.Namespace) -> int:
    from app.eval.split import SEED, eval_path, make_split

    split, written = make_split()
    print(f"seed {SEED} (from the HERB revision)")
    for key in ("dev", "test", "rest"):
        print(f"  {key:<5}{len(split[key]):>3}  {', '.join(split[key])}")
    state = "wrote" if written else "checked (unchanged)"
    print(f"\n{state} {eval_path('split.json')}")
    return 0


def cmd_normalize(args: argparse.Namespace) -> int:
    from app.eval.split import read_split
    from app.ingest.normalize import KINDS, normalize

    products = args.products or read_split()["dev"]
    report = normalize(products)
    print(f"{'product':<22}" + "".join(f"{k:>14}" for k in KINDS) + f"{'dup links':>11}")
    for name, row in report.items():
        counts = "".join(f"{row['counts'][k]:>14}" for k in KINDS)
        print(f"{name:<22}{counts}{row['duplicate_links_dropped']:>11}")
    print(f"\nwrote data/normalized/<product>/artifacts.jsonl for {len(report)} products")
    return 0


INGEST_STAGES = ("structural", "lexical")  # later phases add link, extract, …


def cmd_ingest(args: argparse.Namespace) -> int:
    from app import schema
    from app.config import get_settings
    from app.eval.split import read_split
    from app.ingest import chunk_embed, health, structural
    from app.ingest.normalize import normalized_dir, read_jsonl

    graph = args.graph or get_settings().lore_graph
    products = args.products or read_split()["dev"]
    schema.apply(graph)  # indexes before any MERGE (load order matters, decisions.md)
    problems = schema.verify(graph)
    if problems:
        print(f"lore ingest: schema problems on {graph!r}: {problems}", file=sys.stderr)
        return 1
    artifacts = {}
    for name in products:
        path = normalized_dir(name) / "artifacts.jsonl"
        if not path.is_file():
            print(f"lore ingest: {path} is missing: run `lore normalize`", file=sys.stderr)
            return 1
        artifacts[name] = read_jsonl(path)
    report = {}
    if "structural" in args.stages:
        rows = structural.build_rows(structural.load_metadata(), artifacts)
        report |= structural.load(rows, graph)
    if "lexical" in args.stages:
        report["upsert_chunks"] = chunk_embed.load(chunk_embed.chunk_products(artifacts), graph)
    print(f"{'query':<24}{'rows':>8}{'nodes+':>9}{'edges+':>9}{'props set':>11}")
    for query, r in report.items():
        print(
            f"{query:<24}{r['rows']:>8}{r['nodes_created']:>9}"
            f"{r['relationships_created']:>9}{r['properties_set']:>11}"
        )
    print(f"\ngraph {graph!r}, products {', '.join(products)}, stages {', '.join(args.stages)}\n")
    print(health.render(health.report(graph)))
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    import json

    from app.agent.tools import search_artifacts

    out = search_artifacts(
        " ".join(args.query), product_id=args.product, k=args.k, mode=args.mode, graph=args.graph
    )
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


def cmd_bench_search(args: argparse.Namespace) -> int:
    import json

    from app.eval import search_bench

    result = search_bench.run(k=args.k, graph=args.graph)
    k = result["k"]
    print(f"{'mode':<10}{'questions':>10}{f'hit@{k}':>9}{f'prec@{k}':>9}{'p50 ms':>9}{'p95 ms':>9}")
    for mode, m in result["modes"].items():
        print(
            f"{mode:<10}{m['questions']:>10}{m[f'hit@{k}']:>9}{m[f'precision@{k}']:>9}"
            f"{m['p50_ms']:>9}{m['p95_ms']:>9}"
        )
    print(json.dumps({m: r["by_type"] for m, r in result["modes"].items()}, indent=1))
    print(json.dumps(result["latency"], indent=1))
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    from app import llm
    from app.eval import baseline_rag

    if not args.baseline:
        print("lore ask: the Lore agent arrives in Phase 9; use --baseline", file=sys.stderr)
        return 2
    try:
        out = baseline_rag.answer(
            " ".join(args.question), product_id=args.product, graph=args.graph
        )
    except llm.LLMError as e:
        print(f"lore ask: {e}", file=sys.stderr)
        return 1
    print(out["answer_markdown"], end="\n\n")
    for c in out["citations"]:
        mark = "ok" if c["quote_ok"] else "QUOTE NOT FOUND" if c["retrieved"] else "NOT RETRIEVED"
        print(f'[{c["n"]}] {c["artifact_id"]} ({mark}): "{c["quote"]}"')
    if out["llm"]:
        u = out["llm"]
        cached = ", cached" if u["cached"] else ""
        print(
            f"\n{u['model']}: {u['input_tokens']} in / {u['output_tokens']} out tokens, "
            f"${u['cost_usd']:.5f}{cached} · today ${llm.spent_today():.4f}"
        )
    return 0


def cmd_llm_check(args: argparse.Namespace) -> int:
    from app import llm
    from app.config import get_settings

    s = get_settings()
    try:
        out = llm.complete(
            role="answer",
            system="Reply with the single word OK.",
            user="ping",
            purpose="llm-check",
            max_tokens=512,
            use_cache=False,
        )
    except llm.LLMError as e:
        print(f"lore llm-check: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # auth, credit and network errors from the SDK, shown as they are
        print(f"lore llm-check: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    print(f"{out.model} answered {out.text.strip()!r} for ${out.cost_usd:.6f}")
    print(f"today's spend ${llm.spent_today():.4f} of the ${s.lore_daily_spend_cap_usd:.2f} cap")
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    from app.config import get_settings
    from app.ingest import health

    print(health.render(health.report(args.graph or get_settings().lore_graph)))
    return 0


# command -> (help text, handler)
COMMANDS = {
    "schema": ("create and verify the indexes for a graph (queries/00_schema.cypher)", cmd_schema),
    "fetch-herb": ("download the HERB dataset (pinned revision) into data/herb", cmd_fetch_herb),
    "rank-dev": ("rank products by change signals to pick the dev products (D017)", cmd_rank_dev),
    "normalize": ("HERB products -> data/normalized/<product>/artifacts.jsonl (S1)", cmd_normalize),
    "split": ("draw the held-out test products once (seeded) into eval/split.json", cmd_split),
    "ingest": ("load normalized products into a graph (stages: structural)", cmd_ingest),
    "health": ("print the graph-health report (S10)", cmd_health),
    "search": ("hybrid search over the graph's chunks (agent tool 1)", cmd_search),
    "bench-search": ("search hit@k / precision@k and latency on dev questions", cmd_bench_search),
    "ask": ("answer a question: --baseline = hybrid RAG (Phase 4); the agent in Phase 9", cmd_ask),
    "llm-check": ("one tiny LLM call: is the API key set and does it have credit?", cmd_llm_check),
}

# command -> [(flag, argparse options)]
OPTIONS: dict[str, list[tuple[str, dict]]] = {
    "schema": [("--graph", {"help": "graph name (default: LORE_GRAPH, e.g. lore_herb)"})],
    "normalize": [
        ("--products", {"nargs": "+", "help": "product names (default: the dev products)"})
    ],
    "ingest": [
        ("--products", {"nargs": "+", "help": "product names (default: the dev products)"}),
        ("--stages", {"nargs": "+", "choices": INGEST_STAGES, "default": list(INGEST_STAGES)}),
        ("--graph", {"help": "graph name (default: LORE_GRAPH, e.g. lore_herb)"}),
    ],
    "health": [("--graph", {"help": "graph name (default: LORE_GRAPH, e.g. lore_herb)"})],
    "search": [
        ("query", {"nargs": "+", "help": "free text"}),
        ("--product", {"help": "product id, e.g. VizForce"}),
        ("--k", {"type": int, "default": 10}),
        ("--mode", {"choices": ("hybrid", "fulltext", "vector"), "default": "hybrid"}),
        ("--graph", {"help": "graph name (default: LORE_GRAPH)"}),
    ],
    "bench-search": [
        ("--k", {"type": int, "default": 10}),
        ("--graph", {"help": "graph name (default: LORE_GRAPH)"}),
    ],
    "ask": [
        ("question", {"nargs": "+", "help": "free text"}),
        ("--baseline", {"action": "store_true", "help": "hybrid RAG, no graph tools"}),
        ("--product", {"help": "product id, e.g. VizForce"}),
        ("--graph", {"help": "graph name (default: LORE_GRAPH)"}),
    ],
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
