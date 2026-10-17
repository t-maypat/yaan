"""Lore command line: `lore <command>` (or `python -m app.cli <command>`).

The Makefile wraps these commands for Linux/macOS; on Windows call them directly.
A command that isn't built yet exits with status 2 and names the phase that builds it.
When a phase implements a command, it moves from PENDING to a `cmd_*` function in COMMANDS.
"""

import argparse
import json
import sys

# command -> (docs/PLAN.md phase that builds it, help text)
PENDING: dict[str, tuple[str, str]] = {
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


INGEST_STAGES = (
    "structural",
    "lexical",
    "link",
    "fold",
    "derive",
    "sentinel",
)  # fold: events -> claims


def cmd_ingest(args: argparse.Namespace) -> int:
    from app import schema
    from app.config import get_settings
    from app.eval.split import read_split
    from app.ingest import chunk_embed, health, linker, structural
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
    if "link" in args.stages:
        rows, unresolved = linker.build_rows(structural.load_metadata(), artifacts)
        report |= linker.load(rows, graph)
        log = linker.write_unresolved(unresolved)
        print(f"unresolved mentions: {len(unresolved)} -> {log}", file=sys.stderr)
    if "fold" in args.stages:
        # every product with extracted events (`lore extract`), so a partial run never
        # drops another product's claims: clear_extracted wipes the whole derived layer
        from app.ingest import extract, fold, resolve

        have = sorted(p.name for p in extract.out_dir("x").parent.glob("*") if p.name[0] != "_")
        events = [e for p in have for e in extract.read_events(p)]
        arts = {
            p: artifacts.get(p) or read_jsonl(normalized_dir(p) / "artifacts.jsonl") for p in have
        }
        res = resolve.resolve(
            events, {p: [a for a in v if a.kind == "pr"] for p, v in arts.items()}
        )
        rows, stats = fold.rebuild(res, fold.Context.build(structural.load_metadata(), arts))
        report |= fold.load(rows, graph)
        print(
            f"fold over {', '.join(have)}: {stats}; unresolved {len(res.unresolved)}",
            file=sys.stderr,
        )
    if "derive" in args.stages:
        from app.ingest import derive

        report["derive_works_on"] = derive.works_on(graph)
        out = derive.collab(graph)
        report |= out["report"]
        print(f"collab: {out['pairs']} pairs; PageRank top {out['top']}", file=sys.stderr)
        out = derive.communities(graph)
        report |= out["report"]
        print(f"communities: {out['communities']}; bridges {out['bridges']}", file=sys.stderr)
        out = derive.expertise(graph)
        report |= out["report"]
        print(f"expertise: {out['areas']} areas; truck factor {out['truck']}", file=sys.stderr)
    if "sentinel" in args.stages:  # after fold and derive: the radius reads WORKS_ON
        from app.sentinel import engine

        out = engine.run(graph)
        report |= out.pop("report")
        print(f"sentinel: {out}", file=sys.stderr)
    print(f"{'query':<24}{'rows':>8}{'nodes+':>9}{'edges+':>9}{'props set':>11}")
    for query, r in report.items():
        print(
            f"{query:<24}{r['rows']:>8}{r['nodes_created']:>9}"
            f"{r['relationships_created']:>9}{r['properties_set']:>11}"
        )
    print(f"\ngraph {graph!r}, products {', '.join(products)}, stages {', '.join(args.stages)}\n")
    print(health.render(health.report(graph)))
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    from app.eval.split import read_split
    from app.ingest import extract
    from app.ingest.normalize import normalized_dir, read_jsonl

    products = args.products or read_split()["dev"]
    todo = {
        p: extract.units(p, read_jsonl(normalized_dir(p) / "artifacts.jsonl")) for p in products
    }
    if args.sample:
        # every k-th unit across the products, so the sample spans channels and months
        flat = [u for p in products for u in todo[p]]
        step = max(1, len(flat) // args.sample)
        todo = {"_sample": flat[::step][: args.sample]}
    for name, units in todo.items():
        kept, dropped, stats = extract.run(units, workers=args.workers)
        folder = extract.write(name, kept, dropped, stats)
        print(f"{name}: {json.dumps(stats)} -> {folder}")
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
    import json

    from app import llm
    from app.agent import loop
    from app.eval import baseline_rag

    question = " ".join(args.question)
    try:
        if args.baseline:
            out = baseline_rag.answer(question, product_id=args.product, graph=args.graph)
        else:
            out = loop.answer(question, product_id=args.product, graph=args.graph)
    except llm.LLMError as e:
        print(f"lore ask: {e}", file=sys.stderr)
        return 1
    print(out["answer_markdown"], end="\n\n")
    for c in out["citations"]:
        mark = "ok" if c["quote_ok"] else "QUOTE NOT FOUND" if c["retrieved"] else "NOT RETRIEVED"
        print(f'[{c["n"]}] {c["artifact_id"]} ({mark}): "{c["quote"]}"')
    for step in out.get("trace", []):
        status = f"ERROR {step['error']}" if "error" in step else ", ".join(step.get("cypher", []))
        print(f"  tool {step['tool']}({json.dumps(step['input'])}) {step['ms']} ms: {status}")
    if out.get("validator", {}).get("dropped"):
        print(f"  validator dropped: {out['validator']['dropped']}")
    if out["llm"]:
        u = out["llm"]
        cached = ", cached" if u["cached"] else ""
        print(
            f"\n{u['model']}: {u['input_tokens']} in / {u['output_tokens']} out tokens, "
            f"${u['cost_usd']:.5f}{cached} | today ${llm.spent_today():.4f}"
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


def cmd_eval(args: argparse.Namespace) -> int:
    import json
    from pathlib import Path

    from app import llm
    from app.eval import report, run_herb

    def say(msg: str) -> None:
        print(f"\r  {msg:<40}", end="", file=sys.stderr, flush=True)

    try:
        if args.score_only:
            run = Path(args.score_only)
        else:
            run = run_herb.run(
                args.system, args.split, args.products, args.limit, args.workers, progress=say
            )
            print(f"\nanswers: {run}", file=sys.stderr)
        results = report.score_run(run, workers=args.workers)
    except llm.LLMError as e:
        print(f"\nlore eval: {e}", file=sys.stderr)
        return 1
    r = json.loads(results.read_text(encoding="utf-8"))
    print(f"results: {results}")
    for key, label in report.CATEGORIES:
        c = r["categories"][key]
        print(f"  {label:<9} {report.fmt(c['score'], none='-'):>6}  (n={c['n']})")
    print(f"  {'Avg':<9} {report.fmt(r['avg'], none='-'):>6}")
    print(
        f"  Unanswerable {report.fmt(r['unanswerable'], none='-')}% left unanswered "
        f"(n={r['questions']['unanswerable']})"
    )
    print(
        f"  errors {r['errors']} | citations quote-ok {r['citations']['quote_ok_pct']}% | "
        f"p50 {r['latency_s']['p50']} s | ${r['cost_usd']['answers_this_run']} answers + "
        f"${r['cost_usd']['judge_this_run']} judge | today ${llm.spent_today():.4f}"
    )
    if r["errors"]:
        print(f"lore eval: {r['errors']} answers failed; see answers.jsonl", file=sys.stderr)
    if results.name != "results.json":  # a full run: refresh the table
        print(f"table: {report.write_table()}")
    return 0


def cmd_eval_report(args: argparse.Namespace) -> int:
    from app.eval import report

    path = report.write_table()
    print(path.read_text(encoding="utf-8"))
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    from app.config import get_settings
    from app.ingest import health

    print(health.render(health.report(args.graph or get_settings().lore_graph)))
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    from app.sentinel import live

    if args.action == "reset":
        print(json.dumps(live.reset_demo(args.graph)))
        return 0
    if not args.file:
        print("lore demo inject: name an exports/inject/*.json file", file=sys.stderr)
        return 1
    msg = json.loads(open(args.file, encoding="utf-8").read())
    out = live.ingest(msg, args.graph)
    for a in out["alerts"]:
        print(f"[{a['severity']}] {a['kind']}: {a['summary']}")
        print(f"  notifies: {', '.join(p['name'] for p in a['notifies']) or 'nobody'}")
    if not out["alerts"]:
        print(f"no alert; fold: {out['fold']}")
    stages = ", ".join(f"{k} {v:.0f}" for k, v in out["stages_ms"].items())
    print(f"freshness lag {out['freshness_lag_ms'] / 1000:.1f} s ({stages} ms)")
    print(f"LLM cost ${out['llm_cost_usd']:.4f}; events kept {len(out['events'])}")
    return 0


def cmd_bench_experts(args: argparse.Namespace) -> int:
    from app.eval import experts

    result = experts.bench(args.graph)
    for r in result["rows"]:
        area = r["area"] or "-"
        print(f"{r['component'][:40]:<42}{area:<42}lore {r['lore']}  naive {r['naive']}")
    print()
    print(f"{result['questions']} questions; mean {json.dumps(result['mean'])}")
    print(f"-> {experts.write(result)}")
    return 0


# command -> (help text, handler)
COMMANDS = {
    "schema": ("create and verify the indexes for a graph (queries/00_schema.cypher)", cmd_schema),
    "fetch-herb": ("download the HERB dataset (pinned revision) into data/herb", cmd_fetch_herb),
    "rank-dev": ("rank products by change signals to pick the dev products (D017)", cmd_rank_dev),
    "normalize": ("HERB products -> data/normalized/<product>/artifacts.jsonl (S1)", cmd_normalize),
    "split": ("draw the held-out test products once (seeded) into eval/split.json", cmd_split),
    "ingest": ("load normalized products into a graph (stages: structural)", cmd_ingest),
    "extract": ("LLM event extraction + quote check over bug/develop chats (S5-S6)", cmd_extract),
    "health": ("print the graph-health report (S10)", cmd_health),
    "search": ("hybrid search over the graph's chunks (agent tool 1)", cmd_search),
    "bench-search": ("search hit@k / precision@k and latency on dev questions", cmd_bench_search),
    "ask": ("answer a question with the Lore agent; --baseline = hybrid RAG", cmd_ask),
    "llm-check": ("one tiny LLM call: is the API key set and does it have credit?", cmd_llm_check),
    "eval": ("answer a split's HERB questions and score them (Phase 5)", cmd_eval),
    "eval-report": ("rebuild eval/results/table.md from the results files", cmd_eval_report),
    "bench-experts": (
        "who_knows vs HERB's component-expert answers on dev (10.8)",
        cmd_bench_experts,
    ),
    "demo": ("reset the demo graph, or inject a synthetic message live (11.6)", cmd_demo),
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
    "extract": [
        ("--products", {"nargs": "+", "help": "product names (default: the dev products)"}),
        ("--sample", {"type": int, "help": "only N units spread over the products -> _sample"}),
        ("--workers", {"type": int, "default": 6, "help": "parallel LLM calls"}),
    ],
    "bench-experts": [("--graph", {"help": "graph name (default: LORE_GRAPH)"})],
    "demo": [
        ("action", {"choices": ("reset", "inject")}),
        ("file", {"nargs": "?", "help": "inject: an exports/inject/*.json message"}),
        ("--graph", {"help": "graph name (default: LORE_DEMO_GRAPH, e.g. lore_demo)"}),
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
    "eval": [
        ("--system", {"choices": ("baseline", "lore"), "default": "baseline"}),
        ("--split", {"choices": ("dev",), "default": "dev"}),
        ("--products", {"nargs": "+", "help": "a subset of the split's products"}),
        ("--limit", {"type": int, "help": "only the first N questions (a cheap smoke run)"}),
        ("--workers", {"type": int, "default": 4, "help": "parallel LLM calls"}),
        ("--score-only", {"metavar": "RUN_DIR", "help": "re-judge an existing eval/runs/ folder"}),
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
