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
        "linking": linking(rows("health_links")),
        "mentions": [
            {"labels": ":".join(r["labels"]), "n": r["n"]} for r in rows("health_mentions")
        ],
        "speakers": rows("health_spoke_in"),
        "dead_letters": rows("health_dead_letters"),
    }


def linking(found: list[dict[str, Any]]) -> dict[str, Any]:
    """S4 totals: mentions found in text and the share left unresolved (6.5)."""
    row = found[0] if found else {}
    mentions, unresolved = row.get("mentions") or 0, row.get("unresolved") or 0
    pct = round(100 * unresolved / mentions, 2) if mentions else 0.0
    return {"mentions": mentions, "unresolved": unresolved, "unresolved_pct": pct}


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
    link = r["linking"]
    lines.append(
        f"linking: {link['mentions']:,} mentions found, {link['unresolved']:,} unresolved "
        f"({link['unresolved_pct']}%)"
    )
    lines += [f"  MENTIONS {row['labels']:<21}{row['n']:>6,}" for row in r["mentions"]]
    lines += [f"  SPOKE_IN {row['method']:<21}{row['n']:>6,}" for row in r["speakers"]]
    dead = r.get("dead_letters") or []
    lines.append(f"dead letters: {sum(row['n'] for row in dead):,} (yaan connectors dead)")
    lines += [f"  {row['source']:<12}{row['reason']:<16}{row['n']:>6,}" for row in dead]
    return "\n".join(lines)
