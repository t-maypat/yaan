import { type FormEvent, type ReactNode, useEffect, useRef, useState } from "react";
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
    window.scrollTo({ top: 0 });
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

  const compact = !!run;
  const artifacts = products.reduce((n, p) => n + p.artifacts, 0);
  const form = (
    <form onSubmit={submit} className={compact ? "" : "mt-10"}>
      <label htmlFor="q" className={compact ? "sr-only" : "block text-sm text-muted mb-1"}>
        Your question
      </label>
      <div className="flex flex-col sm:flex-row sm:items-end gap-x-4 gap-y-3">
        <input
          id="q"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          maxLength={500}
          placeholder="Which customers are waiting on a fix?"
          className={`flex-1 min-w-0 bg-transparent border-0 border-b-2 border-ink px-0 font-serif placeholder:text-muted/70 focus:outline-none focus-visible:outline-none focus:border-link ${
            compact ? "text-lg py-1.5" : "text-[1.3rem] sm:text-[1.6rem] py-2"
          }`}
        />
        <div className="flex gap-3 items-end">
          <ProductPicker products={products} value={product} onChange={setProduct} error={productError} />
          {run?.running ? (
            <button type="button" onClick={stop} className="px-5 py-2 border-2 border-ink font-medium">
              Stop
            </button>
          ) : (
            <button
              type="submit"
              disabled={draft.trim().length < 3}
              className="px-6 py-2 border-2 border-ink bg-ink text-paper font-medium disabled:opacity-35"
            >
              Ask
            </button>
          )}
        </div>
      </div>
    </form>
  );

  return (
    <div className="min-h-screen">
      <header className="border-b border-rule">
        <div className="max-w-[90rem] mx-auto px-4 sm:px-8 py-3 flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
          <button
            type="button"
            onClick={() => {
              abort.current?.abort();
              setRun(null);
            }}
            className="font-serif text-[1.6rem] font-semibold tracking-tight leading-none"
            aria-label="Lore, back to the start"
          >
            Lore
          </button>
          <p className="text-sm text-muted">
            {productError
              ? `The graph didn't answer: ${productError}`
              : products.length
                ? `${artifacts.toLocaleString("en")} artifacts from ${products.length} products in the graph`
                : "Connecting to the graph"}
          </p>
        </div>
      </header>

      <main className="max-w-[90rem] mx-auto px-4 sm:px-8 pb-20">
        {compact ? (
          <div className="pt-5 pb-4 max-w-[64rem]">
            {form}
            <details className="mt-3 group">
              <summary className="text-sm text-link cursor-pointer w-fit hover:underline underline-offset-2">
                Try another question
              </summary>
              <QuestionIndex onPick={pick} disabled={!!run?.running} products={products} />
            </details>
          </div>
        ) : (
          <Opening form={form} onPick={pick} products={products} />
        )}
        {run && <RunView run={run} active={active} setActive={setActive} />}
      </main>
    </div>
  );
}

function Opening({ form, onPick, products }: { form: ReactNode; onPick: (s: Suggestion) => void; products: Product[] }) {
  return (
    <section className="pt-14 sm:pt-20 max-w-[64rem]">
      <h1 className="font-serif text-[2.4rem] sm:text-[3.4rem] leading-[1.05] tracking-[-0.015em] font-medium max-w-[20ch] text-balance">
        What's true at the company, and who said so.
      </h1>
      <p className="mt-5 text-[1.05rem] text-muted max-w-[58ch] leading-relaxed">
        Lore answers from a graph of Slack messages, meetings, documents and pull requests. Each sentence of an answer
        links to a quote you can check against its source. When nothing supports an answer, Lore says so.
      </p>
      {form}
      <div className="mt-14">
        <h2 className="font-semibold">Try a question from the data</h2>
        <p className="text-sm text-muted mt-0.5">
          The data is HERB, a public synthetic company. Each product also goes by a codename in its chats.
        </p>
        <QuestionIndex onPick={onPick} products={products} />
      </div>
    </section>
  );
}

// Suggested questions grouped by product: the product is a question's scope.
function QuestionIndex({ onPick, disabled, products }: { onPick: (s: Suggestion) => void; disabled?: boolean; products: Product[] }) {
  const groups = [...new Set(SUGGESTIONS.map((s) => s.product))];
  const alias = (id: string) => products.find((p) => p.id === id)?.aliases[0];
  return (
    <div className="grid gap-x-10 gap-y-6 mt-5 sm:grid-cols-2 lg:grid-cols-3">
      {groups.map((g) => (
        <section key={g} aria-label={g}>
          <h3 className="text-sm pb-1.5 mb-1 border-b border-rule">
            <span className="font-semibold">{g}</span>
            {alias(g) && <span className="text-muted">, called {alias(g)} in chats</span>}
          </h3>
          <ul>
            {SUGGESTIONS.filter((s) => s.product === g).map((s) => (
              <li key={s.question}>
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() => onPick(s)}
                  title={s.question}
                  className="font-serif text-[1.08rem] leading-snug text-left py-1.5 w-full hover:text-link hover:underline underline-offset-[0.2em] decoration-1 disabled:opacity-50 disabled:no-underline"
                >
                  {s.short}
                </button>
              </li>
            ))}
          </ul>
        </section>
      ))}
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
    <div>
      <label htmlFor="product" className="block text-[0.78rem] text-muted">
        Product
      </label>
      <select
        id="product"
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value || null)}
        title={error ? `Products not loaded: ${error}` : undefined}
        className={`bg-transparent border-0 border-b-2 py-1.5 pr-1 ${error ? "border-warn text-warn" : "border-ink"}`}
      >
        <option value="">{error ? "Not loaded" : "Any"}</option>
        {products.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
      </select>
    </div>
  );
}

function RunView({ run, active, setActive }: { run: Run; active: number | null; setActive: (n: number | null) => void }) {
  const llm = run.answer?.llm;
  return (
    <div className="grid gap-x-12 gap-y-10 border-t border-rule pt-8 lg:grid-cols-[15rem_minmax(0,1fr)_21rem]">
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
        <h2 id="h-answer" className="font-serif text-[1.75rem] leading-[1.15] tracking-[-0.01em] mb-6 max-w-[34ch]">
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
          !run.error && <p className="text-muted">The answer appears once the evidence is in.</p>
        )}

        {!!run.subgraph?.nodes.length && (
          <section aria-labelledby="h-evidence" className="mt-12">
            <h2 id="h-evidence" className="font-semibold">
              Evidence in the graph
            </h2>
            <p className="text-sm text-muted mt-0.5 mb-3 max-w-[68ch]">
              The cited sources and what they connect to: authors, the product, customers and their companies, and the
              claims and decisions Lore extracted from them.
            </p>
            <EvidenceGraph graph={run.subgraph} citations={run.citations} active={active} />
          </section>
        )}
      </section>

      <section aria-labelledby="h-sources" className="order-2 lg:order-3 min-w-0">
        {run.citations.length > 0 && (
          <>
            <h2 id="h-sources" className="font-semibold">
              Sources
            </h2>
            <p className="text-sm text-muted mt-0.5 mb-4">
              Each quote was checked word for word against its source before the answer was shown. Point at one to see
              the sentence it supports.
            </p>
            <div className="pl-4">
              <Sources citations={run.citations} active={active} setActive={setActive} />
            </div>
          </>
        )}
      </section>
    </div>
  );
}
