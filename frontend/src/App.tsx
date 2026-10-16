import { type FormEvent, useEffect, useRef, useState } from "react";
import { type AnswerEvent, ask, type Citation, getProducts, type Product, type Subgraph } from "./api";
import { Answer } from "./components/Answer";
import { EvidenceGraph } from "./components/EvidenceGraph";
import { Sources } from "./components/Sources";
import { type Step, Walk } from "./components/Walk";
import { SUGGESTIONS, type Suggestion } from "./suggestions";

type Run = {
  question: string;
  product: string | null;
  steps: Step[];
  answer?: AnswerEvent;
  citations: Citation[];
  subgraph?: Subgraph;
  error?: string;
  running: boolean;
};

export default function App() {
  const [products, setProducts] = useState<Product[]>([]);
  const [productError, setProductError] = useState<string | null>(null);
  const [product, setProduct] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [run, setRun] = useState<Run | null>(null);
  const [active, setActive] = useState<number | null>(null);
  const abort = useRef<AbortController | null>(null);

  useEffect(() => {
    getProducts().then(
      (r) => setProducts(r.products),
      (e: Error) => setProductError(e.message),
    );
  }, []);

  const start = (question: string, productId: string | null) => {
    abort.current?.abort();
    const ctl = new AbortController();
    abort.current = ctl;
    setActive(null);
    setRun({ question, product: productId, steps: [], citations: [], running: true });
    const update = (f: (r: Run) => Run) => setRun((r) => (r && !ctl.signal.aborted ? f(r) : r));
    ask(
      question,
      productId,
      (e) => {
        switch (e.name) {
          case "tool_call":
            return update((r) => ({ ...r, steps: [...r.steps, { ...e.data, done: false }] }));
          case "tool_result":
            return update((r) => {
              const i = r.steps.findIndex((s) => !s.done && s.tool === e.data.tool);
              if (i < 0) return r;
              const steps = [...r.steps];
              steps[i] = { ...steps[i], done: true, ms: e.data.ms, cypher: e.data.cypher, error: e.data.error };
              return { ...r, steps };
            });
          case "answer":
            return update((r) => ({ ...r, answer: e.data }));
          case "citations":
            return update((r) => ({ ...r, citations: e.data.citations }));
          case "subgraph":
            return update((r) => ({ ...r, subgraph: e.data }));
          case "error":
            return update((r) => ({ ...r, error: `${e.data.error}: ${e.data.message}` }));
          case "done":
            return update((r) => ({ ...r, running: false }));
        }
      },
      ctl.signal,
    ).catch((err: Error) => {
      if (ctl.signal.aborted) return;
      setRun((r) => r && { ...r, running: false, error: err.message });
    });
  };

  const stop = () => {
    abort.current?.abort();
    setRun((r) => r && { ...r, running: false, error: r.answer ? r.error : "Stopped before an answer." });
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const q = draft.trim();
    if (q.length >= 3) start(q, product);
  };

  const pick = (s: Suggestion) => {
    setDraft(s.question);
    setProduct(s.product);
    start(s.question, s.product);
  };

  return (
    <div className="min-h-screen">
      <header className="border-b border-rule">
        <div className="max-w-[90rem] mx-auto px-4 sm:px-6 py-3 flex flex-wrap items-baseline gap-x-5 gap-y-1">
          <h1 className="font-serif text-[1.7rem] font-semibold tracking-tight leading-none">Lore</h1>
          <p className="text-muted text-sm">Answers from your company's graph, each sentence with its source.</p>
        </div>
      </header>

      <main className="max-w-[90rem] mx-auto px-4 sm:px-6 pb-16">
        <form onSubmit={submit} className="pt-6 pb-5 max-w-[56rem]">
          <label htmlFor="q" className="sr-only">
            Your question
          </label>
          <div className="flex flex-col sm:flex-row gap-2">
            <ProductPicker products={products} value={product} onChange={setProduct} error={productError} />
            <input
              id="q"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              maxLength={500}
              placeholder="Ask about people, PRs, bugs, customers or documents"
              className="flex-1 min-w-0 bg-surface border border-rule px-3 py-2.5 text-base placeholder:text-muted focus:border-link"
            />
            {run?.running ? (
              <button type="button" onClick={stop} className="px-5 py-2.5 border border-ink font-medium">
                Stop
              </button>
            ) : (
              <button
                type="submit"
                disabled={draft.trim().length < 3}
                className="px-5 py-2.5 bg-ink text-paper font-medium disabled:opacity-40"
              >
                Ask
              </button>
            )}
          </div>
          <Suggestions onPick={pick} compact={!!run} disabled={!!run?.running} />
        </form>

        {run ? (
          <RunView run={run} active={active} setActive={setActive} />
        ) : (
          <Intro />
        )}
      </main>
    </div>
  );
}

function ProductPicker(props: {
  products: Product[];
  value: string | null;
  onChange: (v: string | null) => void;
  error: string | null;
}) {
  const { products, value, onChange, error } = props;
  return (
    <>
      <label htmlFor="product" className="sr-only">
        Product
      </label>
      <select
        id="product"
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value || null)}
        title={error ? `Products not loaded: ${error}` : undefined}
        className={`bg-surface border px-2.5 py-2.5 text-base ${error ? "border-warn text-warn" : "border-rule"}`}
      >
        <option value="">{error ? "Products not loaded" : "Any product"}</option>
        {products.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
            {p.aliases.length ? ` (${p.aliases.join(", ")})` : ""}
          </option>
        ))}
      </select>
    </>
  );
}

function Suggestions({ onPick, compact, disabled }: { onPick: (s: Suggestion) => void; compact: boolean; disabled: boolean }) {
  return (
    <div className="mt-3">
      {!compact && <p className="text-sm text-muted mb-2">Or try one of these, from the HERB dev products:</p>}
      <ul className="flex flex-wrap gap-2">
        {SUGGESTIONS.map((s) => (
          <li key={s.question}>
            <button
              type="button"
              disabled={disabled}
              onClick={() => onPick(s)}
              title={s.question}
              className="text-[0.85rem] border border-rule bg-surface px-2.5 py-1 hover:border-link hover:text-link disabled:opacity-50 text-left"
            >
              {s.short} <span className="text-muted">{s.product}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Intro() {
  return (
    <section className="max-w-[60ch] border-t border-rule pt-6 text-muted">
      <p className="font-serif text-xl text-ink leading-snug">
        Lore reads a company's Slack, meetings, documents and pull requests as one graph of people, work and decisions.
      </p>
      <p className="mt-3">
        Ask a question and watch it walk that graph. Each sentence of the answer links to a quote copied word for word from
        a source; point at a number to see it. If no source supports an answer, Lore says so instead of guessing.
      </p>
    </section>
  );
}

function RunView({ run, active, setActive }: { run: Run; active: number | null; setActive: (n: number | null) => void }) {
  const llm = run.answer?.llm;
  return (
    <div className="grid gap-x-10 gap-y-8 border-t border-rule pt-6 lg:grid-cols-[16rem_minmax(0,1fr)_21rem]">
      <section aria-labelledby="h-walk" className="order-3 lg:order-1">
        <h2 id="h-walk" className="font-semibold mb-3">
          How Lore found it
        </h2>
        <Walk steps={run.steps} running={run.running && !run.answer} />
        {llm && (
          <p className="text-[0.78rem] text-muted mt-2 leading-snug">
            {llm.model}, {llm.turns} turns,{" "}
            {llm.cached ? "replayed from cache at $0" : `$${llm.cost_usd.toFixed(4)}`}
          </p>
        )}
      </section>

      <section aria-labelledby="h-answer" className="order-1 lg:order-2 min-w-0">
        <h2 id="h-answer" className="font-serif text-[1.65rem] leading-tight mb-5 max-w-[40ch]">
          {run.question}
        </h2>
        {run.error && (
          <div className="border-l-4 border-warn bg-warn-soft px-4 py-3 mb-4 max-w-[68ch]" role="alert">
            <p className="font-semibold text-warn">The question failed</p>
            <p className="mt-0.5">{run.error}</p>
            <p className="text-sm text-muted mt-1">Ask again, or check the API log for the cause.</p>
          </div>
        )}
        {run.answer ? (
          <Answer answer={run.answer} citations={run.citations} active={active} setActive={setActive} />
        ) : (
          !run.error && <p className="text-muted">Writing the answer once the evidence is in.</p>
        )}

        {!!run.subgraph?.nodes.length && (
          <section aria-labelledby="h-evidence" className="mt-10">
            <h2 id="h-evidence" className="font-semibold mb-1">
              Evidence
            </h2>
            <p className="text-sm text-muted mb-3 max-w-[68ch]">
              The cited sources and what the graph connects them to: who wrote them, the product, customers and
              companies they mention, and the claims and decisions Lore extracted from them.
            </p>
            <EvidenceGraph graph={run.subgraph!} citations={run.citations} active={active} />
          </section>
        )}
      </section>

      <section aria-labelledby="h-sources" className="order-2 lg:order-3 min-w-0">
        {run.citations.length > 0 && (
          <>
            <h2 id="h-sources" className="font-semibold mb-3">
              Sources
            </h2>
            <div className="pl-4">
              <Sources citations={run.citations} active={active} setActive={setActive} />
            </div>
          </>
        )}
      </section>
    </div>
  );
}
