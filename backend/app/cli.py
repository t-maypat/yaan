"""Yaan command line: `yaan <command>` or `python -m app.cli <command>`.

The Makefile wraps these commands for Linux/macOS; on Windows call them directly.
A command that isn't built yet exits with status 2 and names the phase that builds it.
When a phase implements a command, it moves from PENDING to a `cmd_*` function in COMMANDS.
"""

import argparse
import json
import sys

# command -> (docs/PLAN.md phase that builds it, help text)
PENDING: dict[str, tuple[str, str]] = {}


def cmd_schema(args: argparse.Namespace) -> int:
    from app import schema
    from app.config import get_settings

    graph = args.graph or get_settings().yaan_graph
    for spec, outcome in schema.apply(graph):
        print(f"  {outcome:<8} {spec}")
    found = schema.verify(graph)
    for problem in found:
        print(f"  ! {problem}", file=sys.stderr)
    if found:
        print(f"yaan schema: graph {graph!r} does not match {schema.SCHEMA_FILE}", file=sys.stderr)
        return 1
    print(f"graph {graph!r}: all {len(schema.declared())} indexes present and operational")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    """Org mode (D097): create your graph with its indexes, then the product, the channels
    connectors.yaml declares and the people in people.yaml. Safe to run again."""
    from app import org, schema
    from app.connectors import base, runner
    from app.sentinel import live

    if not org.active():
        print(
            "yaan init sets up a graph for your own tools: set YAAN_DATA_MODE=org in .env first "
            "(the demo graph comes from `yaan seed`)",
            file=sys.stderr,
        )
        return 1
    try:
        graph = live.demo_graph(args.graph)
        org.check(base.maps().product, base.maps().sources)  # before the graph is touched
        for spec, outcome in schema.apply(graph):
            print(f"  {outcome:<8} {spec}")
        if found := schema.verify(graph):
            for problem in found:
                print(f"  ! {problem}", file=sys.stderr)
            return 1
        made = runner.prepare(graph)
    except (base.ConnectorError, org.OrgError, live.LiveError) as e:
        print(f"yaan init: {e}", file=sys.stderr)
        return 1
    print(
        f"graph {graph!r} ready: product {base.maps().product!r}, {made['channels']} channels, "
        f"{made['people']} {'person' if made['people'] == 1 else 'people'} listed in "
        f"{org.PEOPLE_FILE.name}"
    )
    return 0


def cmd_people(args: argparse.Namespace) -> int:
    """Org mode (D130): the people Yaan met in your tools who aren't in people.yaml, as entries
    to paste into it (give each a role, and an email to sign in with)."""
    import yaml

    from app import org
    from app.sentinel import live

    if not org.active():
        print("yaan people: org mode only (YAAN_DATA_MODE=org)", file=sys.stderr)
        return 1
    graph = live.demo_graph(args.graph)
    found = org.unlisted(graph)
    print(f"# {len(org.roster())} in people.yaml; {len(found)} met in your tools, not listed")
    if found:
        print(yaml.safe_dump({"people": found}, sort_keys=False, allow_unicode=True), end="")
    return 0


def cmd_token(args: argparse.Namespace) -> int:
    """Sign-in tokens (D100, D111): issue one for a people.yaml person (shown once; only its
    hash is kept; it expires after --days), list them, or revoke one (--id) or all of a
    person's tokens."""
    from app import auth, org

    if args.action == "list":
        mine = [t for t in auth.listing() if args.person in (None, "", t["person"])]
        for t in mine:
            used, label = t["last_used"] or "never", t["label"] or "-"
            print(f"{t['id']}  {t['person']}  {label}  expires {t['expires']}  used {used}")
        return 0
    if not args.person:
        print("yaan token: name the person (people.yaml name, email or id)", file=sys.stderr)
        return 1
    want = args.person.strip().casefold()
    try:
        people = list(org.roster().values())
    except org.OrgError as e:
        print(f"yaan token: {e}", file=sys.stderr)
        return 1
    found = [
        p
        for p in people
        if want in (p["id"].casefold(), (p.get("email") or "").casefold(), p["name"].casefold())
    ]
    if len(found) != 1:
        what = "nobody" if not found else f"{len(found)} people"
        print(f"yaan token: {what} in people.yaml matches {args.person!r}", file=sys.stderr)
        return 1
    person = found[0]
    if args.action == "issue":
        token = auth.issue(person["id"], label=args.label or "", days=args.days)
        print(f"token for {person['name']} ({person['id']}), shown once; keep it secret:")
        print(token)
        print(f"it expires in {args.days} days (--days)", file=sys.stderr)
        if auth.mode() != "token":
            print(
                f"note: YAAN_AUTH is {auth.mode()!r}; tokens are read in token mode",
                file=sys.stderr,
            )
    elif args.id:
        auth.revoke_id(args.id)
        print(f"revoked token {args.id} of {person['name']}")
    else:
        print(f"revoked {auth.revoke(person['id'])} token(s) of {person['name']}")
    return 0


def cmd_belief(args: argparse.Namespace) -> int:
    """What Yaan believed about an issue or PR at a moment (D101): the fold replayed over the
    events it had learned by then. Read-only."""
    from app import db
    from app.agent.tools import parse_as_of
    from app.config import get_settings
    from app.ingest import fold

    graph = args.graph or get_settings().yaan_graph
    known_at = parse_as_of(args.at) if args.at else None
    rows = db.run("subject_product", {"id": args.subject}, graph=graph).rows
    if not rows or not rows[0]["product_id"]:
        print(
            f"yaan belief: no issue or pull request {args.subject!r} in {graph!r}", file=sys.stderr
        )
        return 1
    out = fold.belief(args.subject, rows[0]["product_id"], graph, known_at)
    when = args.at or "now"
    if not out["chains"]:
        print(f"{args.subject}: Yaan knew nothing about it at {when}")
        return 0
    for facet, chain in out["chains"].items():
        print(f"{args.subject} {facet}, as Yaan knew it at {when}:")
        for d in chain:
            print(f"  {d['value']:<10} {d['status']:<10} {d['statement']}")
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


def derived_stages(args: argparse.Namespace, graph: str, report: dict) -> None:
    """`yaan ingest`'s fold, derive and sentinel stages: the derived layer (D101)."""
    if "fold" in args.stages:
        # every product with extracted events (`yaan extract`) becomes graph events; then each
        # product with events is folded from the graph, writing only what changed (D101)
        from app.ingest import events as event_store
        from app.ingest import extract, fold

        have = sorted(p.name for p in extract.out_dir("x").parent.glob("*") if p.name[0] != "_")
        report["upsert_events"] = event_store.load(
            [e for p in have for e in extract.read_events(p)], graph
        )
        if gone := fold.migrate_legacy(graph):
            print(f"derived layer from before D101: {gone} nodes rewritten", file=sys.stderr)
        for product in event_store.products(graph):
            out = fold.fold_product(product, graph)
            print(
                f"fold {product}: {out['stats']}; unresolved {out['unresolved']}; {out['write']}",
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
        report |= derive.uses(graph)
        out = derive.precedents(graph)
        report |= out["report"]
        print(
            f"precedents: {out['links']} links over {out['decisions']} decisions", file=sys.stderr
        )
    if "sentinel" in args.stages:  # after fold and derive: the radius reads WORKS_ON
        from app.sentinel import engine

        out = engine.run(graph)
        report |= out.pop("report")
        print(f"sentinel: {out}", file=sys.stderr)


def cmd_ingest(args: argparse.Namespace) -> int:
    from app import schema
    from app.config import get_settings
    from app.eval.split import read_split
    from app.ingest import chunk_embed, health, linker, structural
    from app.ingest.normalize import normalized_dir, read_jsonl

    graph = args.graph or get_settings().yaan_graph
    products = args.products or read_split()["dev"]
    schema.apply(graph)  # indexes before any MERGE (load order matters, decisions.md)
    problems = schema.verify(graph)
    if problems:
        print(f"yaan ingest: schema problems on {graph!r}: {problems}", file=sys.stderr)
        return 1
    artifacts = {}
    for name in products:
        path = normalized_dir(name) / "artifacts.jsonl"
        if not path.is_file():
            print(f"yaan ingest: {path} is missing: run `yaan normalize`", file=sys.stderr)
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
    from app import db

    if {"fold", "derive", "sentinel"} & set(args.stages):
        with db.writer_lock(graph):  # the derived layer has one writer at a time (D101)
            derived_stages(args, graph, report)
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
            out = loop.answer(
                question, product_id=args.product, graph=args.graph, viewer_id=args.viewer
            )
    except llm.LLMError as e:
        print(f"yaan ask: {e}", file=sys.stderr)
        return 1
    print(out["answer_markdown"], end="\n\n")
    for c in out["citations"]:
        mark = "ok" if c["quote_ok"] else "QUOTE NOT FOUND" if c["retrieved"] else "NOT RETRIEVED"
        print(f'[{c["n"]}] {c["artifact_id"]} ({mark}): "{c["quote"]}"')
    for step in out.get("trace", []):
        status = f"ERROR {step['error']}" if "error" in step else ", ".join(step.get("cypher", []))
        print(f"  tool {step['tool']}({json.dumps(step['input'])}) {step['ms']} ms: {status}")
    if out.get("hidden_sources"):
        print(f"  {out['hidden_sources']} sources hidden from viewer {out['viewer_id']}")
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
        print(f"yaan llm-check: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # auth, credit and network errors from the SDK, shown as they are
        print(f"yaan llm-check: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    print(f"{out.model} answered {out.text.strip()!r} for ${out.cost_usd:.6f}")
    print(f"today's spend ${llm.spent_today():.4f} of the ${s.yaan_daily_spend_cap_usd:.2f} cap")
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
        elif args.split == "test":
            from app.eval import split as split_guard

            if not args.heldout or args.limit or args.products or args.ids:
                print(
                    "yaan eval: --split test is the single held-out run (13.3): pass --heldout, "
                    "and no --limit, --products or --ids",
                    file=sys.stderr,
                )
                return 2
            with split_guard.heldout_run(args.system, note="yaan eval --split test (13.3)"):
                run = run_herb.run(args.system, "test", workers=args.workers, progress=say)
        else:
            ids = Path(args.ids).read_text(encoding="utf-8").split() if args.ids else None
            run = run_herb.run(
                args.system,
                args.split,
                args.products,
                args.limit,
                args.workers,
                progress=say,
                ids=ids,
            )
            print(f"\nanswers: {run}", file=sys.stderr)
        results = report.score_run(run, workers=args.workers, variant=args.variant)
    except (llm.LLMError, ValueError) as e:
        print(f"\nyaan eval: {e}", file=sys.stderr)
        return 1
    r = json.loads(results.read_text(encoding="utf-8"))
    print(f"results: {results}")
    if r["split"] == "drift":
        print(
            f"  HERB-Drift n={r['n']}: {r['correct_pct']}% right, stale-answer rate "
            f"{r['stale_answer_rate']}% of answered, {r['abstained']} abstained, {r['wrong']} wrong"
        )
        for kind, b in r["by_kind"].items():
            print(
                f"    {kind:<15} n={b['n']:<3} right {b['correct_pct']}%  "
                f"stale {b['stale_answer_rate']}%"
            )
        print(
            f"  ${r['cost_usd']['answers_this_run']} answers + "
            f"${r['cost_usd']['judge_this_run']} judge"
        )
        print(f"table: {report.write_table()}")
        return 0
    for key, label in report.CATEGORIES:
        c = r["categories"][key]
        print(f"  {label:<9} {report.fmt(c['score'], none='-'):>6}  (n={c['n']})")
    print(f"  {'Avg':<9} {report.fmt(r['avg'], none='-'):>6}")
    print(
        f"  Unanswerable {report.fmt(r['unanswerable'], none='-')}% left unanswered "
        f"(n={r['questions']['unanswerable']})"
    )
    print(
        f"  errors {r['errors']} | citations quote-ok "
        f"{(r['citations'] or {}).get('quote_ok_pct', 'n/a')}% | "
        f"p50 {r['latency_s']['p50']} s | ${r['cost_usd']['answers_this_run']} answers + "
        f"${r['cost_usd']['judge_this_run']} judge | today ${llm.spent_today():.4f}"
    )
    if r["errors"]:
        print(f"yaan eval: {r['errors']} answers failed; see answers.jsonl", file=sys.stderr)
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

    print(health.render(health.report(args.graph or get_settings().yaan_graph)))
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    from app import org
    from app.sentinel import live

    if org.active():
        print("yaan demo: the HERB demo is off in org mode (YAAN_DATA_MODE=org)", file=sys.stderr)
        return 1

    if args.action == "reset":
        print(json.dumps(live.reset_demo(args.graph)))
        return 0
    if not args.file:
        print("yaan demo inject: name an exports/inject/*.json file", file=sys.stderr)
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
        print(f"{r['component'][:40]:<42}{area:<42}yaan {r['yaan']}  naive {r['naive']}")
    print()
    print(f"{result['questions']} questions; mean {json.dumps(result['mean'])}")
    print(f"-> {experts.write(result)}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """A graph as plain JSONL (D120): portable, unlike a snapshot's DUMP payload."""
    import json
    from pathlib import Path

    from app import export
    from app.config import get_settings

    graph = args.graph or get_settings().api_graph()
    try:
        out = export.export(graph, Path(args.out), embeddings=args.with_embeddings)
    except (export.ExportError, OSError) as e:
        print(f"yaan export: {e}", file=sys.stderr)
        return 1
    print(json.dumps(out, indent=1))
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    import json
    from pathlib import Path

    from app import export

    try:
        out = export.load(Path(args.path), args.graph)
    except (export.ExportError, OSError) as e:
        print(f"yaan import: {e}", file=sys.stderr)
        return 1
    print(json.dumps(out, indent=1))
    return 0


def cmd_snapshot(args: argparse.Namespace) -> int:
    import json

    from app import snapshot

    try:
        out = snapshot.snapshot(tuple(args.graphs) if args.graphs else None)
    except (snapshot.SnapshotError, OSError) as e:
        print(f"yaan snapshot: {e}", file=sys.stderr)
        return 1
    print(json.dumps(out, indent=1))
    return 0


def cmd_backup(args: argparse.Namespace) -> int:
    """The graph and every state file of a deployment in one checked file (22.2, F16)."""
    import json
    from pathlib import Path

    from app import backup, db, snapshot

    try:
        out = backup.backup(
            args.graph,
            Path(args.out) if args.out else None,
            keep=args.keep,
            files_only=args.files_only,
            block_ok=args.block_ok,
        )
    except (backup.BackupError, snapshot.SnapshotError, db.LockBusy, OSError) as e:
        print(f"yaan backup: {e}", file=sys.stderr)
        return 1
    print(json.dumps(out, indent=1))
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    import json
    from pathlib import Path

    from app import backup, snapshot

    try:
        out = backup.restore(
            Path(args.path), force=args.force, unpinned=args.unpinned, as_graph=args.as_graph
        )
    except (backup.BackupError, snapshot.SnapshotError, OSError) as e:
        print(f"yaan restore: {e}", file=sys.stderr)
        return 1
    print(json.dumps(out, indent=1))
    return 0


def cmd_persistence(args: argparse.Namespace) -> int:
    """How FalkorDB keeps the graph on disk, and its memory against the cap (22.3)."""
    import json

    from app import backup, db, snapshot

    try:
        if args.action == "aof":
            out = backup.switch_to_aof()
        else:
            out = db.storage() | {"warning": db.memory_warning()}
    except (backup.BackupError, snapshot.SnapshotError) as e:
        print(f"yaan persistence: {e}", file=sys.stderr)
        return 1
    print(json.dumps(out, indent=1))
    return 0


def cmd_seed(args: argparse.Namespace) -> int:
    import json

    from app import org, snapshot

    if org.active():
        print(
            "yaan seed loads the HERB demo, which is off in org mode: set YAAN_DATA_MODE=demo "
            "to try it, or run `yaan init` for your own graph",
            file=sys.stderr,
        )
        return 1
    try:
        out = snapshot.seed(
            args.source or snapshot.SNAPSHOT, force=args.force, unpinned=args.unpinned
        )
    except (snapshot.SnapshotError, OSError) as e:
        print(f"yaan seed: {e}", file=sys.stderr)
        return 1
    print(json.dumps(out, indent=1, default=str))
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    """Serve Yaan's tools over MCP on stdin/stdout, e.g. for Claude Code:
    claude mcp add yaan -- uv run --project backend yaan mcp"""
    import anyio

    from app import mcp_server

    anyio.run(mcp_server.serve_stdio)
    return 0


def seed_sandboxes(act: str, connectors: list) -> dict:
    """`yaan connectors seed --act setup|regression` with each connector's own client."""
    from app.connectors import base, seed

    by = {c.name: c for c in connectors}
    if act == "regression":
        if "slack" not in by:
            raise base.ConnectorError("seed regression needs Slack: SLACK_BOT_TOKEN")
        slack = by["slack"]
        channel = next(iter(slack.conf.get("channels") or {}), None)
        cid = slack.channel_ids().get(channel or "")
        if not cid:
            raise base.ConnectorError(f"the bot can't see #{channel}: /invite @yaan there")
        return {"slack_ts": seed.regression(slack.client, cid)}
    missing = [n for n in ("jira", "github") if n not in by]
    if missing:
        raise base.ConnectorError(f"seed setup needs {missing}: see docs/connectors.md")
    notion = by.get("notion")
    return seed.setup(
        by["jira"].http,
        by["jira"].project,
        by["github"].http,
        by["github"].repo,
        notion.http if notion else None,
        notion.page_id if notion else None,
    )


def cmd_connectors(args: argparse.Namespace) -> int:
    import json

    from app.connectors import base, runner
    from app.sentinel import live

    try:
        graph = live.demo_graph(args.graph)
        if args.action == "run":
            runner.run_forever(graph, every_s=args.every)
            return 0
        connectors = runner.configured(graph)
        if args.action == "seed" and not base.is_demo():
            print("yaan connectors seed writes the demo story: off in org mode", file=sys.stderr)
            return 1
        if args.action == "seed":
            print(json.dumps(seed_sandboxes(args.act, connectors), indent=1))
            return 0
        if args.action == "health":  # the worker container's healthcheck (H1, D107)
            health = runner.worker_health(graph)
            print(json.dumps(health))
            return 0 if health["status"] in ("ok", "waiting") else 1
        if args.action == "dead":
            rows = runner.dead_letters(graph, args.id)
            print(json.dumps(rows, indent=1) if rows else "no dead letters")
            return 0
        if args.action == "retry":
            print(json.dumps(runner.retry(graph, args.id), indent=1))
            return 0
        if args.action == "backfill":  # a dry run unless --yes (D114)
            out = runner.backfill(runner.configured(graph), graph, args.days, spend=args.yes)
            print(json.dumps(out, indent=1, default=str))
            if not args.yes:
                print(
                    f"dry run: about ${out['expected_usd']:.2f} expected, at most "
                    f"${out['worst_case_usd']:.2f}; add --yes to ingest it",
                    file=sys.stderr,
                )
                for product, secs in out.get("fold_s", {}).items():
                    if secs * 1000 > runner.FOLD_WARN_MS:
                        print(
                            f"warning: one fold of {product} already takes {secs:.0f} s, and "
                            "every round that touches it pays that under the writer lock; a "
                            "longer history makes it slower (review F3)",
                            file=sys.stderr,
                        )
            return 0
        if args.action == "status":
            mode = "demo" if base.is_demo() else "org"
            print(f"mode: {mode}, graph {graph!r}")
            print(f"maps: {base.CONFIG.name}, product {base.maps().product}")
            print(f"configured: {[c.name for c in connectors] or 'none (see docs/connectors.md)'}")
            print(json.dumps(runner.load_state(graph), indent=1)[:2000])
            return 0
        days = args.days if args.action == "reconcile" else None
        print(json.dumps(runner.sync(connectors, graph, sweep_days=days), indent=1, default=str))
        return 0
    except (base.ConnectorError, live.LiveError) as e:
        print(f"yaan connectors: {e}", file=sys.stderr)
        return 1


def cmd_forget(args: argparse.Namespace) -> int:
    """Remove items from Yaan for good (F2, D125): every copy of their words goes (text,
    chunks, vectors, the words of their events, quotes in alerts and drafts, dead letters, the
    LLM cache in org mode), the decisions are rebuilt without them, and a content-free
    tombstone keeps each id so no sync brings it back. A dry run unless --yes."""
    from app import db, forget
    from app.sentinel import live

    try:
        graph = live.demo_graph(args.graph)
        if not args.yes:
            present = live.present_ids(graph, args.ids)
            print(
                f"dry run: {len(present)} of {len(args.ids)} items are in {graph!r}. With --yes "
                "every copy of their words goes, their events are closed (not deleted), the "
                "decisions are rebuilt without them, and a tombstone keeps each id so no sync "
                "brings it back. This can't be undone.",
                file=sys.stderr,
            )
            return 0
        print(json.dumps(forget.forget(args.ids, graph, reason=args.reason), indent=1))
        return 0
    except (live.LiveError, db.LockBusy) as e:
        print(f"yaan forget: {e}", file=sys.stderr)
        return 1


def cmd_erase(args: argparse.Namespace) -> int:
    """Erase one person from Yaan (F2, D125): everything they wrote is forgotten, their node,
    tokens and directory entry deleted, their @-mentions in others' words replaced, and a hash
    of their id kept so their items stay out. Plain-text mentions of their name are listed for
    review. A dry run unless --yes."""
    from app import db, forget, org
    from app.sentinel import live

    try:
        graph = live.demo_graph(args.graph)
        want = args.person.strip().casefold()
        found = sorted(
            eid
            for eid, p in org.directory(graph).items()
            if want in (eid.casefold(), (p.get("name") or "").casefold(),
                        (p.get("email") or "").casefold())
        )
        if len(found) != 1:
            what = "nobody" if not found else f"{len(found)} people ({', '.join(found)})"
            print(f"yaan erase: {what} in {graph!r} matches {args.person!r}", file=sys.stderr)
            return 1
        person = found[0]
        if person in org.roster():
            print(
                f"yaan erase: {args.person!r} is listed in {org.PEOPLE_FILE.name}: remove the "
                "entry first, or Yaan registers them again from it",
                file=sys.stderr,
            )
            return 1
        if not args.yes:
            wrote = db.run_all("person_authored", {"id": person}, cap=200_000, graph=graph)
            named = db.run_all("person_mentions", {"id": person}, cap=200_000, graph=graph)
            print(
                f"dry run: {person} wrote {len(wrote)} items and is @-mentioned in "
                f"{len(named)}. With --yes the items are forgotten (yaan forget), the mentions "
                "replaced, the person, their tokens and directory entry deleted, and their "
                "items kept out from now on. This can't be undone.",
                file=sys.stderr,
            )
            return 0
        print(json.dumps(forget.erase_person(person, graph), indent=1))
        return 0
    except (live.LiveError, org.OrgError, db.LockBusy) as e:
        print(f"yaan erase: {e}", file=sys.stderr)
        return 1


def cmd_retention(args: argparse.Namespace) -> int:
    """Forget what is past each tool's retention now (22.5); the worker does it daily. A dry
    run unless --yes."""
    from app import db, retention
    from app.config import get_settings

    try:
        graph = args.graph or get_settings().yaan_org_graph
        print(json.dumps(retention.run(graph, apply=args.yes), indent=1))
        return 0
    except (retention.RetentionError, db.LockBusy) as e:
        print(f"yaan retention: {e}", file=sys.stderr)
        return 1


def cmd_audit(args: argparse.Namespace) -> int:
    """Who was shown what (22.5): the newest lines of the access audit log first."""
    from app import audit
    from app.config import get_settings

    graph = args.graph or get_settings().api_graph()
    for row in audit.read(graph, viewer=args.person, item=args.item, limit=args.limit):
        print(json.dumps(row, ensure_ascii=False))
    return 0


# command -> (help text, handler)
COMMANDS = {
    "schema": ("create and verify the indexes for a graph (queries/00_schema.cypher)", cmd_schema),
    "init": ("org mode: create your graph, its product, channels and people (D097)", cmd_init),
    "token": ("org mode: issue or revoke a person's sign-in token (D100)", cmd_token),
    "people": ("org mode: people met in your tools, not yet in people.yaml (D130)", cmd_people),
    "forget": ("remove items for good: every copy of their words, no re-sync (D125)", cmd_forget),
    "erase": ("erase a person: what they wrote, their mentions, their account (D125)", cmd_erase),
    "retention": ("org mode: forget what is past each tool's retention (22.5)", cmd_retention),
    "audit": ("who was shown what: the access audit log, newest first (22.5)", cmd_audit),
    "belief": ("what Yaan believed about an issue or PR at a moment (D101)", cmd_belief),
    "fetch-herb": ("download the HERB dataset (pinned revision) into data/herb", cmd_fetch_herb),
    "rank-dev": ("rank products by change signals to pick the dev products (D017)", cmd_rank_dev),
    "normalize": ("HERB products -> data/normalized/<product>/artifacts.jsonl (S1)", cmd_normalize),
    "split": ("draw the held-out test products once (seeded) into eval/split.json", cmd_split),
    "ingest": ("load normalized products into a graph (stages: structural)", cmd_ingest),
    "extract": ("LLM event extraction + quote check over bug/develop chats (S5-S6)", cmd_extract),
    "health": ("print the graph-health report (S10)", cmd_health),
    "search": ("hybrid search over the graph's chunks (agent tool 1)", cmd_search),
    "bench-search": ("search hit@k / precision@k and latency on dev questions", cmd_bench_search),
    "ask": ("answer a question with the Yaan agent; --baseline = hybrid RAG", cmd_ask),
    "llm-check": ("one tiny LLM call: is the API key set and does it have credit?", cmd_llm_check),
    "eval": ("answer a split's HERB questions and score them (Phase 5)", cmd_eval),
    "eval-report": ("rebuild eval/results/table.md from the results files", cmd_eval_report),
    "bench-experts": (
        "who_knows vs HERB's component-expert answers on dev (10.8)",
        cmd_bench_experts,
    ),
    "demo": ("reset the demo graph, or inject a synthetic message live (11.6)", cmd_demo),
    "mcp": ("serve Yaan's read-only tools (and ask) over MCP on stdio (17.1)", cmd_mcp),
    "snapshot": ("save the graph + live-path data to snapshots/ (no LLM; 15.2)", cmd_snapshot),
    "seed": ("restore a snapshot (file or URL) and the demo graph, no API key (15.2)", cmd_seed),
    "backup": ("the graph + tokens, cursors, people, ledger: one checked file (22.2)", cmd_backup),
    "restore": ("bring a `yaan backup` file back: graph, then state files (22.2)", cmd_restore),
    "persistence": ("memory vs cap, disk persistence; aof: switch on live (22.3)", cmd_persistence),
    "export": ("write a graph to plain JSONL, readable anywhere (D120)", cmd_export),
    "import": ("load a JSONL export into a new graph (D120)", cmd_import),
    "connectors": (
        "company tools in, approved actions out: sync once, run, or status (17.2)",
        cmd_connectors,
    ),
}

# command -> [(flag, argparse options)]
OPTIONS: dict[str, list[tuple[str, dict]]] = {
    "schema": [("--graph", {"help": "graph name (default: YAAN_GRAPH, e.g. lore_herb)"})],
    "init": [("--graph", {"help": "graph name (default: YAAN_ORG_GRAPH, e.g. yaan_org)"})],
    "belief": [
        ("subject", {"help": "an issue (iss:...) or PR (pr:...) id"}),
        ("--at", {"help": "ISO date or datetime (UTC): what Yaan knew then; default now"}),
        ("--graph", {"help": "graph name (default: YAAN_GRAPH, e.g. lore_herb)"}),
    ],
    "forget": [
        ("ids", {"nargs": "+", "help": "item ids, e.g. msg:slack-C123-1730000000.000100"}),
        ("--reason", {"default": "removed by an admin", "help": "kept on each tombstone"}),
        ("--yes", {"action": "store_true", "help": "do it (default: a dry run)"}),
        ("--graph", {"help": "default: YAAN_ORG_GRAPH in org mode, else YAAN_DEMO_GRAPH"}),
    ],
    "erase": [
        ("person", {"help": "an eid_ id, a name or an email (quote a full name)"}),
        ("--yes", {"action": "store_true", "help": "do it (default: a dry run)"}),
        ("--graph", {"help": "default: YAAN_ORG_GRAPH in org mode, else YAAN_DEMO_GRAPH"}),
    ],
    "people": [("--graph", {"help": "default: YAAN_ORG_GRAPH"})],
    "retention": [
        ("--yes", {"action": "store_true", "help": "forget them (default: a dry run)"}),
        ("--graph", {"help": "default: YAAN_ORG_GRAPH"}),
    ],
    "audit": [
        ("--person", {"help": "only what this person (an eid_ id) was shown"}),
        ("--item", {"help": "only the lines that showed this item id"}),
        ("--limit", {"type": int, "default": 50}),
        ("--graph", {"help": "default: the graph the API serves"}),
    ],
    "token": [
        ("action", {"choices": ("issue", "revoke", "list")}),
        (
            "person",
            {"nargs": "?", "help": "a people.yaml name, email or eid_ id (quote a full name)"},
        ),
        ("--label", {"help": "issue: what the token is for, e.g. 'laptop' or 'claude code'"}),
        ("--days", {"type": int, "default": 90, "help": "issue: days until it expires"}),
        ("--id", {"help": "revoke: only this token (an id from `yaan token list`)"}),
    ],
    "normalize": [
        ("--products", {"nargs": "+", "help": "product names (default: the dev products)"})
    ],
    "ingest": [
        ("--products", {"nargs": "+", "help": "product names (default: the dev products)"}),
        ("--stages", {"nargs": "+", "choices": INGEST_STAGES, "default": list(INGEST_STAGES)}),
        ("--graph", {"help": "graph name (default: YAAN_GRAPH, e.g. lore_herb)"}),
    ],
    "extract": [
        ("--products", {"nargs": "+", "help": "product names (default: the dev products)"}),
        ("--sample", {"type": int, "help": "only N units spread over the products -> _sample"}),
        ("--workers", {"type": int, "default": 6, "help": "parallel LLM calls"}),
    ],
    "bench-experts": [("--graph", {"help": "graph name (default: YAAN_GRAPH)"})],
    "demo": [
        ("action", {"choices": ("reset", "inject")}),
        ("file", {"nargs": "?", "help": "inject: an exports/inject/*.json message"}),
        ("--graph", {"help": "graph name (default: YAAN_DEMO_GRAPH, e.g. lore_demo)"}),
    ],
    "health": [("--graph", {"help": "graph name (default: YAAN_GRAPH, e.g. lore_herb)"})],
    "search": [
        ("query", {"nargs": "+", "help": "free text"}),
        ("--product", {"help": "product id, e.g. VizForce"}),
        ("--k", {"type": int, "default": 10}),
        ("--mode", {"choices": ("hybrid", "fulltext", "vector"), "default": "hybrid"}),
        ("--graph", {"help": "graph name (default: YAAN_GRAPH)"}),
    ],
    "bench-search": [
        ("--k", {"type": int, "default": 10}),
        ("--graph", {"help": "graph name (default: YAAN_GRAPH)"}),
    ],
    "ask": [
        ("question", {"nargs": "+", "help": "free text"}),
        ("--baseline", {"action": "store_true", "help": "hybrid RAG, no graph tools"}),
        ("--product", {"help": "product id, e.g. VizForce"}),
        ("--graph", {"help": "graph name (default: YAAN_GRAPH)"}),
        ("--viewer", {"help": "answer as this employee (eid_...): hidden sources left out"}),
    ],
    "snapshot": [("--graphs", {"nargs": "+", "help": "graphs to save (default: YAAN_GRAPH)"})],
    "export": [
        ("--out", {"required": True, "help": "file to write: .jsonl, or .jsonl.gz"}),
        ("--graph", {"help": "default: the graph the API serves"}),
        ("--with-embeddings", {"action": "store_true", "help": "keep chunk vectors (1.5 KB each)"}),
    ],
    "backup": [
        ("--graph", {"help": "default: the graph the API serves"}),
        ("--out", {"help": "file to write (default: data/backups/<graph>-<UTC time>.tar.gz)"}),
        ("--keep", {"type": int, "help": "then keep only the newest N of this graph's backups"}),
        (
            "--files-only",
            {
                "action": "store_true",
                "help": "a big graph: the state files, then a background save; copy FalkorDB's "
                "volume afterwards (DUMP would block the server)",
            },
        ),
        ("--block-ok", {"action": "store_true", "help": "DUMP even if it blocks FalkorDB > 30 s"}),
    ],
    "restore": [
        ("path", {"help": "a backup file (its .sha256 beside it)"}),
        ("--force", {"action": "store_true", "help": "replace an existing graph and files"}),
        ("--unpinned", {"action": "store_true", "help": "no .sha256 beside it: skip that check"}),
        (
            "--as",
            {"dest": "as_graph", "help": "a trial: the graph alone under this new name, no files"},
        ),
    ],
    "persistence": [
        (
            "action",
            {
                "nargs": "?",
                "choices": ("status", "aof"),
                "default": "status",
                "help": "aof: turn the append-only file on a running server, keeping its data",
            },
        )
    ],
    "import": [
        ("path", {"help": "an export file (.jsonl or .jsonl.gz)"}),
        ("--graph", {"required": True, "help": "a new graph to load it into"}),
    ],
    "seed": [
        ("--from", {"dest": "source", "help": "snapshot path or URL (default: snapshots/)"}),
        ("--force", {"action": "store_true", "help": "replace existing graphs and files"}),
        (
            "--unpinned",
            {
                "action": "store_true",
                "help": "load a snapshot whose sha256 no SHA256SUMS pins (one you made yourself)",
            },
        ),
    ],
    "connectors": [
        (
            "action",
            {
                "choices": (
                    "sync",
                    "run",
                    "status",
                    "seed",
                    "dead",
                    "retry",
                    "health",
                    "backfill",
                    "reconcile",
                ),
                "help": "what to do (dead: items that couldn't go in; retry: replay them; "
                "health: is the connector loop running; backfill: reach --days back, a dry "
                "run with its cost unless --yes; reconcile: compare the last --days of Slack "
                "with the graph: missed replies and edits in, deletions forgotten)",
            },
        ),
        ("--id", {"help": "dead / retry: one dead letter's id (default: all)"}),
        ("--days", {"type": float, "default": 30, "help": "backfill: how far back"}),
        ("--yes", {"action": "store_true", "help": "backfill: ingest it (spends on the model)"}),
        ("--act", {"choices": ("setup", "regression"), "default": "setup", "help": "seed act"}),
        ("--graph", {"help": "default: YAAN_DEMO_GRAPH, or YAAN_ORG_GRAPH in org mode"}),
        ("--every", {"type": float, "default": 30, "help": "run: seconds between polls"}),
    ],
    "eval": [
        (
            "--system",
            {
                "choices": ("baseline", "baseline-meta", "yaan", "yaan-search"),
                "default": "baseline",
            },
        ),
        ("--split", {"choices": ("dev", "drift", "test"), "default": "dev"}),
        (
            "--heldout",
            {
                "action": "store_true",
                "help": "with --split test: confirm this is the single logged held-out run (13.3)",
            },
        ),
        ("--products", {"nargs": "+", "help": "a subset of the split's products"}),
        ("--limit", {"type": int, "help": "only the first N questions (a cheap smoke run)"}),
        (
            "--ids",
            {"metavar": "FILE", "help": "only the question ids in FILE, one per line (a check)"},
        ),
        ("--workers", {"type": int, "default": 4, "help": "parallel LLM calls"}),
        ("--score-only", {"metavar": "RUN_DIR", "help": "re-judge an existing eval/runs/ folder"}),
        (
            "--variant",
            {
                "choices": ("unvalidated",),
                "help": "with --score-only: score the answers before the citation validator",
            },
        ),
    ],
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="yaan", description="Yaan (formerly Lore): a time-aware company brain on FalkorDB."
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
        f"yaan {args.command}: not built yet; it arrives in {phase} (docs/PLAN.md).",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
