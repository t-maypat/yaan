import { useEffect, useState } from "react";
import { type GraphHealth, getGraphHealth } from "../api";

// Graph Health (12.6): what the graph holds and how well it is connected, from the Phase 3 health
// report (`/graph/health`) on the graph the API serves.

const fmt = (n: number) => n.toLocaleString("en");

export function GraphHealthPage() {
  const [health, setHealth] = useState<GraphHealth | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    getGraphHealth().then(setHealth, (e: Error) => setError(e.message));
  }, []);

  if (error)
    return (
      <p className="mt-10 border-l-4 border-warn bg-warn-soft px-4 py-3 max-w-[68ch]" role="alert">
        The health report failed: {error}
      </p>
    );
  if (!health) return <p className="mt-10 text-muted">Counting the graph…</p>;

  const edges = health.edges.reduce((n, e) => n + e.n, 0);
  const own = health.pr_chains.find((r) => r.external === false) as { prs: number; with_chain: number } | undefined;
  const tiles: [string, string, string][] = [
    ["Nodes", fmt(health.total_nodes), `${health.nodes.length} label sets`],
    ["Relationships", fmt(edges), `${health.edges.length} types`],
    ["Isolated nodes", `${health.isolated_pct}%`, "nodes with no relationship at all"],
    [
      "Unresolved mentions",
      `${health.linking.unresolved_pct}%`,
      `${fmt(health.linking.unresolved)} of ${fmt(health.linking.mentions)} names in text matched no one`,
    ],
    ["Duplicate ids", fmt(health.duplicate_ids.length), "the same id on two nodes"],
    ["PRs with a status chain", own ? `${fmt(own.with_chain)} / ${fmt(own.prs)}` : "n/a", "the product's own pull requests"],
  ];
  return (
    <section className="pt-10 max-w-[64rem]" aria-labelledby="h-health">
      <h1 id="h-health" className="font-serif text-[2rem] leading-tight">
        Graph health
      </h1>
      <p className="text-muted mt-2 max-w-[62ch]">
        What the graph <span className="font-mono text-[0.9em]">{health.graph}</span> holds and how well its parts are
        linked. Computed live by Cypher queries in FalkorDB (the <span className="font-mono text-[0.9em]">health_*.cypher</span>{" "}
        files).
      </p>
      <dl className="grid gap-px bg-rule border border-rule mt-6 sm:grid-cols-2 lg:grid-cols-3">
        {tiles.map(([label, value, note]) => (
          <div key={label} className="bg-surface px-4 py-3">
            <dt className="text-sm text-muted">{label}</dt>
            <dd className="text-[1.6rem] font-semibold leading-tight mt-0.5">{value}</dd>
            <dd className="text-[0.8rem] text-muted mt-0.5">{note}</dd>
          </div>
        ))}
      </dl>
      <div className="grid gap-10 mt-10 md:grid-cols-2 items-start">
        <CountTable caption="Nodes by label" head="Labels" rows={health.nodes.map((r) => [r.labels, r.n])} />
        <CountTable caption="Relationships by type" head="Type" rows={health.edges.map((r) => [r.type, r.n])} />
      </div>
    </section>
  );
}

function CountTable({ caption, head, rows }: { caption: string; head: string; rows: [string, number][] }) {
  return (
    <table className="w-full text-[0.92rem]">
      <caption className="text-left font-semibold mb-2">{caption}</caption>
      <thead>
        <tr className="border-b border-ink text-left">
          <th className="font-medium py-1">{head}</th>
          <th className="font-medium py-1 text-right">Count</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([k, n]) => (
          <tr key={k} className="border-b border-rule">
            <td className="py-1 font-mono text-[0.85em]">{k}</td>
            <td className="py-1 text-right tabular-nums">{fmt(n)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
