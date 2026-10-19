"""Live-vs-batch parity (D102): does the live path extract what the evaluated batch did?

The batch extraction reads whole HERB conversations; org mode runs only the live path, which
reads each new message with up to live.CONTEXT_MESSAGES earlier messages of its channel. This
replays one product's bug and develop messages in time order through the live extraction
(same windows as live.conversation, read from the normalized files, so the graph is never
written) and compares:
- events: matched by (message, type, issue or PR as resolved), precision and recall of the
  live events against the batch ones;
- outcome: each issue's and PR's final state when both event sets are folded the same way.

    uv run --project backend python -m app.eval.parity <product> [--dry-run]

`--dry-run` counts the units and characters and prints the worst-case and expected cost
without calling the model. Dev products only (rule 3).
"""

import argparse
import json
import sys
from typing import Any

from app import llm
from app.config import get_settings
from app.eval.split import read_split
from app.ingest import extract, fold, resolve
from app.ingest.normalize import Artifact, normalized_dir, read_jsonl
from app.sentinel import live

OUTPUT_TOKENS_EXPECTED = 150  # measured: the batch extraction averaged 109-137 per unit


def windows(product: str) -> list[tuple[Artifact, extract.Unit]]:
    """Every message the live path would extract, with the unit it would be read in."""
    arts = read_jsonl(normalized_dir(product) / "artifacts.jsonl")
    msgs = sorted(
        (a for a in arts if a.kind == "message" and not a.system and a.channel_id),
        key=lambda a: (a.ts_ms or 0, a.id),
    )
    by_channel: dict[str, list[Artifact]] = {}
    out = []
    for a in msgs:
        earlier = by_channel.setdefault(a.channel_id, [])
        if extract.channel_kind(a) in extract.CHANNEL_KINDS:
            since = (a.ts_ms or 0) - live.CONTEXT_WINDOW_MS
            context = [m for m in earlier if (m.ts_ms or 0) >= since][-live.CONTEXT_MESSAGES :]
            budget = extract.UNIT_CHARS - len(a.text)
            while context and sum(len(m.text) + 20 for m in context) > budget:
                context.pop(0)
            channel = a.meta.get("channel_name") or a.channel_id
            unit = extract.Unit(
                f"unit:{product}:{channel}:live:{a.id}", product, channel, (*context, a)
            )
            out.append((a, unit))
        earlier.append(a)
    return out


def estimate(units: list[extract.Unit]) -> dict[str, Any]:
    """Expected and worst-case cost at list price (no cache hits assumed)."""
    s = get_settings()
    chars = sum(len(extract.SYSTEM) + len(u.render()) for u in units)
    tokens_in = int(chars / llm.CHARS_PER_TOKEN_WORST)
    expected = llm.cost_usd(s.lore_extract_model, tokens_in, OUTPUT_TOKENS_EXPECTED * len(units))
    worst = llm.cost_usd(s.lore_extract_model, tokens_in, 8000 * len(units))
    return {
        "units": len(units),
        "input_tokens_worst": tokens_in,
        "expected_usd": round(expected, 4),
        "worst_usd": round(worst, 4),
        "model": s.lore_extract_model,
    }


def key(e: dict[str, Any]) -> tuple:
    return (e["artifact_id"], e["type"], e.get("issue_id") or e.get("pr_id"))


def compare(product: str, live_events: list[dict], batch_events: list[dict], graph: str | None):
    """Precision / recall of the live events, and agreement of the folded final states."""
    ctx, prs = fold.graph_context(product, graph)
    res_live = resolve.resolve(live_events, {product: prs})
    res_batch = resolve.resolve(batch_events, {product: prs})
    got, want = {key(e) for e in res_live.events}, {key(e) for e in res_batch.events}
    both = got & want

    def finals(res) -> dict[str, str]:
        rows, _ = fold.rebuild(res, ctx)
        out: dict[str, str] = {}
        for d in sorted(rows["upsert_decisions"], key=lambda d: d["valid_from_ms"]):
            out[d["topic"]] = d["value"]
        return out

    fl, fb = finals(res_live), finals(res_batch)
    topics = set(fl) | set(fb)
    return {
        "events_live": len(got),
        "events_batch": len(want),
        "precision": round(len(both) / len(got), 3) if got else None,
        "recall": round(len(both) / len(want), 3) if want else None,
        "topics": len(topics),
        "final_state_agreement": round(sum(fl.get(t) == fb.get(t) for t in topics) / len(topics), 3)
        if topics
        else None,
        "only_batch": sorted(want - got)[:20],
        "only_live": sorted(got - want)[:20],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="parity")
    parser.add_argument("product")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--graph", help="read the fold context from this graph (lore_herb)")
    parser.add_argument("--workers", type=int, default=6, help="parallel LLM calls")
    args = parser.parse_args(argv)
    if args.product not in read_split()["dev"]:
        print("parity: dev products only (rule 3)", file=sys.stderr)
        return 1
    todo = windows(args.product)
    est = estimate([u for _, u in todo])
    print(json.dumps(est, indent=1))
    if args.dry_run:
        return 0
    new_message = {unit.id: a.id for a, unit in todo}
    kept, _, stats = extract.run([u for _, u in todo], workers=args.workers)
    # each unit's context is only context: keep what its newest message states
    live_events = [e for e in kept if e["artifact_id"] == new_message[e["unit_id"]]]
    print(f"spent ${stats['cost_usd']:.4f} on {stats['units']} units", file=sys.stderr)
    batch = extract.read_events(args.product)
    out = compare(args.product, live_events, batch, args.graph or get_settings().lore_graph)
    path = get_settings().eval_dir / "results" / f"parity_{args.product}.json"
    path.write_text(
        json.dumps({"estimate": est, **out}, indent=1) + "\n", encoding="utf-8", newline="\n"
    )
    print(json.dumps({k: v for k, v in out.items() if not k.startswith("only_")}, indent=1))
    print(f"details: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
