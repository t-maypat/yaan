// The Lore API as the UI sees it (backend/app/main.py). `/ask` streams Server-Sent Events over
// a POST, which EventSource can't send, so the stream is read with fetch and parsed here.

export type ToolCall = { tool: string; input: Record<string, unknown> };
export type ToolResult = ToolCall & {
  ms: number;
  cypher?: string[];
  error?: string;
  chars?: number;
};
export type Citation = {
  n: number;
  artifact_id: string;
  quote: string;
  retrieved?: boolean;
  quote_ok?: boolean;
};
export type AnswerEvent = {
  answer_markdown: string;
  abstain: boolean;
  validator?: { dropped?: unknown[]; model_abstained?: boolean };
  llm?: { model: string; turns: number; cost_usd: number; cached: boolean };
  viewer_id?: string | null; // W7: answered as this person
  hidden_sources?: number; // sources that person may not see, left out
};
export type GraphNode = { id: string; label: string; name: string | null; cited: boolean };
export type GraphEdge = { source: string; target: string; rel: string };
export type Subgraph = { nodes: GraphNode[]; edges: GraphEdge[] };

export type AskEvent =
  | { name: "tool_call"; data: ToolCall }
  | { name: "tool_result"; data: ToolResult }
  | { name: "answer"; data: AnswerEvent }
  | { name: "citations"; data: { citations: Citation[] } }
  | { name: "subgraph"; data: Subgraph }
  | { name: "error"; data: { error: string; message: string } }
  | { name: "done"; data: Record<string, never> };

export type Person = { id: string; name: string; role: string | null; relation: string };
export type Artifact = {
  id: string;
  kind: string;
  title?: string;
  url?: string;
  ts_iso?: string;
  text: string;
  product_id?: string;
  channel?: string;
  people: Person[];
};
export type Product = { id: string; name: string; aliases: string[]; artifacts: number };

async function failLoudly(r: Response): Promise<never> {
  let detail = r.statusText;
  try {
    detail = (await r.json()).detail ?? detail;
  } catch {
    /* not JSON: keep the status text */
  }
  throw new Error(`${r.status}: ${detail}`);
}

export async function ask(
  question: string,
  productId: string | null,
  onEvent: (e: AskEvent) => void,
  signal?: AbortSignal,
  asOfMs?: number | null,
  viewerId?: string | null,
): Promise<void> {
  const r = await fetch("/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      question,
      product_id: productId,
      as_of_ms: asOfMs ?? null,
      viewer_id: viewerId ?? null,
    }),
    signal,
  });
  if (!r.ok || !r.body) return failLoudly(r);
  const reader = r.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    let end: number;
    while ((end = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      const name = block.match(/^event: (.+)$/m)?.[1];
      const data = block.match(/^data: (.*)$/m)?.[1];
      if (name && data !== undefined) onEvent({ name, data: JSON.parse(data) } as AskEvent);
    }
  }
}

const cache = new Map<string, Promise<unknown>>();
function getJson<T>(path: string): Promise<T> {
  if (!cache.has(path)) {
    const p = fetch(path).then((r) => (r.ok ? r.json() : failLoudly(r)));
    p.catch(() => cache.delete(path)); // a failure is retried on the next hover
    cache.set(path, p);
  }
  return cache.get(path) as Promise<T>;
}

// The Inbox (Sentinel alerts) and the live demo. Not cached: they change as the demo runs.
export type Notified = { id: string; name: string; role: string | null; reasons: string[] };
export type Alert = {
  id: string;
  kind: "regression" | "reopened" | "reversal" | "conflict" | "key_person" | "bus_factor" | "outside_group";
  severity: "high" | "medium";
  status: "open" | "approved" | "dismissed" | "withdrawn";
  summary: string;
  decision_id: string;
  subject_id: string;
  subject_title: string;
  product_id: string;
  from_value: string;
  to_value: string;
  ts_iso: string;
  artifact_id: string | null;
  quote: string | null;
  actor_name: string | null;
  actor_role: string | null;
  reason: string | null;
  radius_size: number;
  notifies: Notified[];
};
export type RadiusRow = {
  kind: string;
  id: string;
  name: string | null;
  role: string | null;
  why: string;
  via: string | null;
  detail: string | null;
  quote: string | null;
  hops?: number | null; // a key person's backup: COLLAB hops to them (null = not connected)
};
export type Radius = {
  people: Notified[];
  open_issues_same_component: RadiusRow[];
  rows: RadiusRow[];
  size: number;
};
export type Action = {
  id: string;
  kind: "ticket_comment" | "doc_fix" | "notify_owner";
  status: "proposed" | "approved" | "skipped" | "dismissed";
  target_kind: "issue" | "artifact" | "person";
  target_id: string;
  target_name: string;
  draft: string;
};
export type Precedent = {
  id: string;
  value: string;
  ts_ms: number;
  subject: string;
  similarity: number;
  decided_by: string | null;
  artifact_id: string | null;
  quote: string | null;
};
export type AlertDetail = { alert: Alert; radius: Radius; actions: Action[]; precedents: Precedent[] };
export type Injection = {
  name: string;
  id: string;
  _label: string;
  author_eid: string;
  channel_name: string;
  ts_iso: string;
  text: string;
  injected: boolean;
};
export type IngestResult = {
  artifact_id: string;
  alerts: Alert[];
  freshness_lag_ms: number;
  stages_ms: Record<string, number>;
  llm_cost_usd: number;
  events: unknown[];
};
export type ResponseResult = { status: string; decision: { id: string; statement: string } };

async function send<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(path, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return r.ok ? r.json() : failLoudly(r);
}

export const getAlerts = () => send<{ alerts: Alert[]; graph: string }>("GET", "/alerts");
export const getAlertDetail = (id: string) =>
  send<AlertDetail>("GET", `/alerts/detail?id=${encodeURIComponent(id)}`);
export const respond = (
  verdict: "approve" | "dismiss",
  body: { alert_id: string; person_id: string; note?: string; action_ids?: string[]; edits?: Record<string, string> },
) => send<ResponseResult>("POST", `/alerts/${verdict}`, body);
export const getInjections = () => send<{ injections: Injection[] }>("GET", "/demo/injections");
export const inject = (name: string) => send<IngestResult>("POST", `/demo/inject/${encodeURIComponent(name)}`);
export const resetDemo = () => send<{ ms: number }>("POST", "/demo/reset");

export const getArtifact = (id: string) => getJson<Artifact>(`/artifact/${encodeURIComponent(id)}`);
export const getQuery = (name: string) =>
  getJson<{ name: string; text: string }>(`/query/${encodeURIComponent(name)}`);
// "org": the graph holds a team's own data (LORE_DATA_MODE=org), so the HERB demo's
// suggested questions and example viewers don't apply.
export type DataMode = "demo" | "org";
export const getProducts = () => getJson<{ products: Product[]; mode?: DataMode }>("/products");

// The Timeline (12.4): an issue's or PR's chain of states, each with its validity window.
export type Decider = { id: string; name: string | null; role?: string | null };
export type Evidence = { artifact_id: string; quote: string; ts_iso?: string | null };
export type ChainState = {
  decision_id: string;
  value: string;
  statement: string | null;
  from: string | null;
  to: string | null;
  in_force: boolean;
  origin: string | null;
  decided_by: Decider[];
  evidence: Evidence[];
  said: (Evidence & {
    by?: string | null;
    by_name?: string | null;
    by_role?: string | null;
    outcome?: string | null;
    authority?: string | null;
    authority_reason?: string | null;
  })[];
  refused?: { id?: string; artifact_id?: string; quote?: string; reason?: string }[];
  alerts?: { id: string; kind: string; status?: string }[];
  time_inferred?: boolean;
};
export type LineageSubject = {
  id: string;
  kind: "issue" | "pr";
  title?: string;
  number?: number;
  url?: string;
  status?: string;
  status_chain?: ChainState[];
  action_chain?: ChainState[];
};
export type Lineage = { subject: string; product_id: string; subjects: LineageSubject[]; as_of?: string };
export const getLineage = (id: string) => getJson<Lineage>(`/decision/${encodeURIComponent(id)}/lineage`);

// The Graph Health and Eval pages (12.6).
export type LabelCount = { labels: string; n: number };
export type GraphHealth = {
  graph: string;
  total_nodes: number;
  nodes: LabelCount[];
  edges: { type: string; n: number }[];
  isolated: LabelCount[];
  isolated_pct: number;
  duplicate_ids: { id: string; n: number }[];
  linking: { mentions: number; unresolved: number; unresolved_pct: number };
  pr_chains: Record<string, unknown>[];
};
export const getGraphHealth = () => send<GraphHealth>("GET", "/graph/health");

export type SystemResult = {
  run_id: string;
  system: string;
  split: string;
  products: string[];
  questions: { answerable: number; unanswerable: number };
  categories: Record<string, { n: number; score: number | null }>;
  avg: number | null;
  unanswerable: number | null;
  citations: { total: number; retrieved_pct: number | null; quote_ok_pct: number | null } | null;
  latency_s: { p50: number; p95: number };
  tokens_per_answer: number | null;
  cost_usd?: Record<string, number>;
};
export type DriftResult = { system: string; n: number; correct_pct: number; stale_answer_rate: number | null };
export type EvalLatest = {
  systems: SystemResult[];
  drift: Record<string, DriftResult>;
  experts: { questions: number; mean: Record<string, Record<string, number>> } | null;
  reference: { system: string; avg: number; unanswerable: number };
};
export const getEvalLatest = () => send<EvalLatest>("GET", "/eval/latest");
