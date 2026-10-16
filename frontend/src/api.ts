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
): Promise<void> {
  const r = await fetch("/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, product_id: productId }),
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

export const getArtifact = (id: string) => getJson<Artifact>(`/artifact/${encodeURIComponent(id)}`);
export const getQuery = (name: string) =>
  getJson<{ name: string; text: string }>(`/query/${encodeURIComponent(name)}`);
export const getProducts = () => getJson<{ products: Product[] }>("/products");
