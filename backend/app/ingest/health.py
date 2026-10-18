"""S10 Graph health. v0 built in Phase 3, extended as stages land.

Counts per label and edge type, % isolated nodes, % unresolved mentions, quote pass
rate, duplicate candidates, freshness lag. Spec: docs/CONTEXT.md §9.3 (S10).

v0 (3.7): nodes per label set, edges per type, isolated nodes, PRs with a status chain,
and ids held by more than one node (the label-set mistake unique constraints can't catch
under MERGE, D027). Later phases add their own rows.
"""

from typing import Any

from app import db

HEALTH_TIMEOUT_MS = 30_000  # whole-graph scans; well under the 120 s server cap


def report(graph: str | None = None) -> dict[str, Any]:
    def rows(name: str) -> list[dict[str, Any]]:
        return db.run(name, graph=graph, timeout_ms=HEALTH_TIMEOUT_MS).rows

    nodes = [{"labels": ":".join(r["labels"]), "n": r["n"]} for r in rows("health_labels")]
    isolated = [{"labels": ":".join(r["labels"]), "n": r["n"]} for r in rows("health_isolated")]
    total = sum(r["n"] for r in nodes)
    lonely = sum(r["n"] for r in isolated)
    return {
        "nodes": nodes,
        "edges": rows("health_edges"),
        "total_nodes": total,
        "isolated": isolated,
        "isolated_pct": round(100 * lonely / total, 2) if total else 0.0,
        "pr_chains": rows("health_pr_chains"),
        "duplicate_ids": rows("health_duplicate_ids"),
    }


def render(r: dict[str, Any]) -> str:
    lines = [f"nodes: {r['total_nodes']:,}"]
    lines += [f"  {row['labels']:<28}{row['n']:>8,}" for row in r["nodes"]]
    lines.append(f"edges: {sum(row['n'] for row in r['edges']):,}")
    lines += [f"  {row['type']:<28}{row['n']:>8,}" for row in r["edges"]]
    lines.append(f"isolated nodes: {r['isolated_pct']}%")
    lines += [f"  {row['labels']:<28}{row['n']:>8,}" for row in r["isolated"]]
    lines.append("PRs with a status chain:")
    for row in r["pr_chains"]:
        side = "external" if row["external"] else "internal"
        lines.append(f"  {side:<28}{row['with_chain']:>8,} of {row['prs']:,}")
    dupes = r["duplicate_ids"]
    lines.append(f"duplicate ids: {len(dupes)}" + (f"  e.g. {dupes[0]['id']}" if dupes else ""))
    return "\n".join(lines)
