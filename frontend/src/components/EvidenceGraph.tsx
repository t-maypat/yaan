import cytoscape, { type Core } from "cytoscape";
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import type { Citation, Subgraph } from "../api";
import { ARTIFACT_KINDS, NODE_WORDS } from "../words";

type Props = {
  graph: Subgraph;
  citations: Citation[];
  active: number | null;
  hint?: string; // the line under the graph when nothing is pointed at
  label?: string; // the graph's accessible name
  root?: string; // draw rings around this node (the Inbox's blast radius) instead of a force layout
  onPick?: (id: string) => void; // a click on an issue or PR node (opens its Timeline, 12.4)
};
const PICKABLE = new Set(["issue", "pr"]);

// Node shapes carry the type (so it reads without color); color separates people and
// organizations from artifacts and from what Yaan extracted (claims, issues, decisions).
const SHAPE: Record<string, cytoscape.Css.NodeShape> = {
  person: "ellipse",
  product: "hexagon",
  customer: "pentagon",
  company: "round-rectangle",
  claim: "diamond",
  issue: "triangle",
  decision: "star",
  team: "octagon",
  area: "tag",
};
const GROUP = (label: string) =>
  ARTIFACT_KINDS.has(label)
    ? "artifact"
    : ["claim", "issue", "decision"].includes(label)
      ? "extracted"
      : "actor";

const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const short = (s: string | null, n = 26) => (!s ? "" : s.length > n ? `${s.slice(0, n - 1)}…` : s);

export function EvidenceGraph({ graph, citations, active, hint, label, root, onPick }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const cy = useRef<Core | null>(null);
  const pick = useRef(onPick);
  pick.current = onPick;
  const [hovered, setHovered] = useState<{ label: string; name: string } | null>(null);
  const scheme = useScheme();
  const marks = useMemo(() => {
    const m = new Map<string, number[]>();
    for (const c of citations) m.set(c.artifact_id, [...(m.get(c.artifact_id) ?? []), c.n]);
    return m;
  }, [citations]);

  useEffect(() => {
    if (!box.current || !graph.nodes.length) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const [ink, muted, rule, mark, link, surface] = ["--ink", "--muted", "--rule", "--mark", "--link", "--surface"].map(css);
    const paper = surface;
    const instance = cytoscape({
      container: box.current,
      elements: [
        ...graph.nodes.map((n) => ({
          data: {
            id: n.id,
            kind: n.label,
            name: n.name ?? n.id,
            text: marks.has(n.id)
              ? `[${(marks.get(n.id) ?? []).join("] [")}]`
              : GROUP(n.label) === "extracted" && !root
                ? ""
                : short(n.name ?? n.id),
          },
          classes: [GROUP(n.label), n.cited ? "cited" : "", n.id === root ? "root" : ""].join(" "),
        })),
        ...graph.edges.map((e) => ({ data: { source: e.source, target: e.target, rel: e.rel } })),
      ],
      style: [
        {
          selector: "node",
          style: {
            label: "data(text)",
            "font-family": "Schibsted Grotesk Variable, system-ui, sans-serif",
            "font-size": 13,
            color: muted,
            "text-valign": "bottom",
            "text-margin-y": 4,
            "text-background-color": paper,
            "text-background-opacity": 0.85,
            "text-background-padding": "1px",
            width: 18,
            height: 18,
            "background-color": surface,
            "border-width": 1.5,
            "border-color": muted,
            shape: "ellipse",
          },
        },
        ...Object.entries(SHAPE).map(([kind, shape]) => ({ selector: `node[kind = "${kind}"]`, style: { shape } })),
        { selector: "node.artifact", style: { shape: "round-rectangle", "border-color": link } },
        { selector: "node.actor", style: { "background-color": link, "border-color": link } },
        { selector: "node.extracted", style: { "border-color": ink, "border-style": "dashed" } },
        {
          selector: "node.cited",
          style: { width: 28, height: 28, "background-color": mark, "border-color": ink, "border-width": 2, color: ink, "font-weight": 600 },
        },
        { selector: "node.root", style: { width: 26, height: 26, "border-width": 2.5, color: ink, "font-weight": 600 } },
        { selector: "node.lit", style: { "underlay-color": mark, "underlay-opacity": 0.55, "underlay-padding": 8 } },
        {
          selector: "edge",
          style: {
            width: 1.2,
            "line-color": rule,
            "target-arrow-color": rule,
            "target-arrow-shape": "triangle",
            "arrow-scale": 0.7,
            "curve-style": "bezier",
          },
        },
        { selector: "edge.hover", style: { label: "data(rel)", "font-size": 9, color: muted, "line-color": muted } },
      ],
      layout: { name: "preset" },
      wheelSensitivity: 0.2,
      minZoom: 0.3,
      maxZoom: 2.5,
    });
    // Fitting a big graph zooms out until labels are unreadable: keep them ~12 px on screen.
    const scaleLabels = () => {
      const z = instance.zoom();
      instance.batch(() => {
        instance.nodes().style({ "font-size": 12 / z, "text-margin-y": 4 / z });
        instance.edges().style({ "font-size": 10 / z });
      });
    };
    instance.on("zoom", scaleLabels);
    instance.on("layoutstop", scaleLabels);
    scaleLabels();
    instance.on("mouseover", "node", (e) => {
      const n = e.target;
      n.connectedEdges().addClass("hover");
      setHovered({ label: n.data("kind"), name: n.data("name") });
    });
    instance.on("tap", "node", (e) => {
      if (PICKABLE.has(e.target.data("kind"))) pick.current?.(e.target.id());
    });
    instance.on("mouseout", "node", (e) => {
      e.target.connectedEdges().removeClass("hover");
      setHovered(null);
    });
    // Run the layout separately so an unmount can stop it: a destroyed graph with a layout still
    // animating throws on every remaining frame.
    const layout = instance.layout(
      root
        ? { name: "breadthfirst", roots: [root], circle: true, spacingFactor: 1.15, padding: 24, animate: false }
        : { name: "cose", animate: !reduce, animationDuration: 400, padding: 24, nodeRepulsion: () => 16000, idealEdgeLength: () => 80 },
    );
    layout.run();
    cy.current = instance;
    // Refit when the box changes size (window resize, phone rotation).
    const resize = new ResizeObserver(() => {
      instance.resize();
      instance.fit(undefined, 24);
    });
    resize.observe(box.current);
    return () => {
      resize.disconnect();
      layout.stop();
      instance.destroy();
    };
  }, [graph, marks, scheme, root]);

  useEffect(() => {
    const c = cy.current;
    if (!c) return;
    c.nodes().removeClass("lit");
    if (active === null) return;
    const id = citations.find((x) => x.n === active)?.artifact_id;
    if (id) c.getElementById(id).addClass("lit");
  }, [active, citations]);

  if (!graph.nodes.length) {
    return <p className="text-muted text-sm">No evidence to draw: the answer cites no source.</p>;
  }
  const kinds = [...new Set(graph.nodes.map((n) => n.label))];
  return (
    <div>
      <div
        ref={box}
        className="h-[30rem] w-full bg-surface border border-rule"
        role="img"
        aria-label={
          label ?? `Evidence graph: ${graph.nodes.length} nodes and ${graph.edges.length} links around the cited sources`
        }
      />
      <p className="text-[0.82rem] min-h-[1.4em] mt-1.5" aria-live="polite">
        {hovered ? (
          <>
            <span className="text-muted">{NODE_WORDS[hovered.label] ?? hovered.label}: </span>
            {hovered.name}
          </>
        ) : (
          <span className="text-muted">
            {hint ?? "Point at a node to read it. Yellow nodes are the cited sources, numbered as in the answer."}
          </span>
        )}
      </p>
      <ul className="flex flex-wrap gap-x-4 gap-y-1 text-[0.78rem] text-muted mt-1">
        {kinds.map((k) => (
          <li key={k} className="flex items-center gap-1.5">
            <Swatch kind={k} />
            {NODE_WORDS[k] ?? k}
          </li>
        ))}
      </ul>
    </div>
  );
}

// The legend draws each node type with the same shape and fill as the graph.
function Swatch({ kind }: { kind: string }) {
  const g = GROUP(kind);
  const shape = ARTIFACT_KINDS.has(kind) ? "round-rectangle" : (SHAPE[kind] ?? "ellipse");
  const props =
    g === "actor"
      ? { fill: "var(--link)", stroke: "var(--link)" }
      : g === "artifact"
        ? { fill: "var(--surface)", stroke: "var(--link)" }
        : { fill: "var(--surface)", stroke: "var(--ink)", strokeDasharray: "2 1.5" };
  const paths: Record<string, ReactNode> = {
    ellipse: <circle cx="7" cy="7" r="5" />,
    hexagon: <polygon points="7,1.5 12,4.3 12,9.7 7,12.5 2,9.7 2,4.3" />,
    pentagon: <polygon points="7,1.5 12.5,5.5 10.4,12 3.6,12 1.5,5.5" />,
    "round-rectangle": <rect x="2" y="3" width="10" height="8" rx="2" />,
    diamond: <polygon points="7,1.5 12.5,7 7,12.5 1.5,7" />,
    triangle: <polygon points="7,1.5 12.5,12 1.5,12" />,
    star: <polygon points="7,1 8.6,5.2 13,5.4 9.5,8.1 10.7,12.5 7,10 3.3,12.5 4.5,8.1 1,5.4 5.4,5.2" />,
    tag: <polygon points="1.5,3 9.5,3 12.5,7 9.5,11 1.5,11" />,
    octagon: <polygon points="4.7,1.5 9.3,1.5 12.5,4.7 12.5,9.3 9.3,12.5 4.7,12.5 1.5,9.3 1.5,4.7" />,
  };
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden strokeWidth="1.4" {...props}>
      {paths[shape]}
    </svg>
  );
}

// The colors are read from the CSS tokens when the graph is drawn, so redraw on a theme change.
function useScheme(): string {
  const query = "(prefers-color-scheme: dark)";
  const [dark, setDark] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const m = window.matchMedia(query);
    const on = () => setDark(m.matches);
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, []);
  return dark ? "dark" : "light";
}
