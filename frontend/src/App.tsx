import { type FormEvent, type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { type AnswerEvent, ask, type Citation, getProducts, type Product, type Subgraph } from "./api";
import { Answer } from "./components/Answer";
import { EvalPage } from "./components/EvalPage";
import { EvidenceGraph } from "./components/EvidenceGraph";
import { GraphHealthPage } from "./components/GraphHealthPage";
import { Inbox } from "./components/Inbox";
import { Sources } from "./components/Sources";
import { Timeline } from "./components/Timeline";
import { type Step, Walk } from "./components/Walk";
import { SUGGESTIONS, type Suggestion } from "./suggestions";
import { when } from "./words";

// W7: answer as a person. HERB has no access rules, so the policy is synthetic
// (permissions.yaml): the PM planning channels are for their members, meetings for attendees.
const VIEWERS: { id: string; label: string }[] = [
  { id: "eid_2d14387c", label: "David Miller, Product Manager (in the PM channel)" },
  { id: "eid_9e9883de", label: "Hannah Garcia, Software Engineer (not in it)" },
];

// The landing page, when the app is deployed next to it (VITE_SITE_URL at build time).
const SITE_URL: string | undefined = import.meta.env.VITE_SITE_URL;

function ThemeToggle() {
  const [dark, setDark] = useState(() => document.documentElement.dataset.theme === "dark");
  const flip = () => {
    const next = dark ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem("lore-theme", next);
    } catch {
      /* storage blocked: the choice lasts this visit */
    }
    setDark(!dark);
  };
  return (
    <button type="button" className="icon-btn" onClick={flip} aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}>
      {dark ? (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M20 14.6A8.3 8.3 0 0 1 9.4 4a8.3 8.3 0 1 0 10.6 10.6Z" />
        </svg>
      ) : (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="12" cy="12" r="4.2" />
          <path d="M12 2.5v2.2M12 19.3v2.2M4.6 4.6l1.6 1.6M17.8 17.8l1.6 1.6M2.5 12h2.2M19.3 12h2.2M4.6 19.4l1.6-1.6M17.8 6.2l1.6-1.6" />
        </svg>
      )}
    </button>
  );
}

type Run = {
  question: string;
  product: string | null;
  viewer: string | null;
  asOfMs: number | null; // answered for the state at this moment (the Timeline's slider)
  steps: Step[];
  answer?: AnswerEvent;
  citations: Citation[];
  subgraph?: Subgraph;
  error?: string;
  running: boolean;
};

const VIEWS = ["ask", "inbox", "health", "eval"] as const;
type View = (typeof VIEWS)[number];
const VIEW_WORDS: Record<View, string> = { ask: "Ask", inbox: "Inbox", health: "Graph health", eval: "Evaluation" };
const viewFromHash = (): View => {
  const h = window.location.hash.slice(1);
  return (VIEWS as readonly string[]).includes(h) ? (h as View) : "ask";
};

export default function App() {
  const [view, setView] = useState<View>(viewFromHash);
  const [openAlerts, setOpenAlerts] = useState<number | null>(null);
  const countAlerts = useCallback((n: number) => setOpenAlerts(n), []);
  useEffect(() => {
    const on = () => setView(viewFromHash());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  const [products, setProducts] = useState<Product[]>([]);
  const [productError, setProductError] = useState<string | null>(null);
  const [product, setProduct] = useState<string | null>(null);
  const [viewer, setViewer] = useState<string | null>(null);
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

  const start = (question: string, productId: string | null, asOfMs: number | null = null) => {
    abort.current?.abort();
    const ctl = new AbortController();
    abort.current = ctl;
    setActive(null);
    setRun({ question, product: productId, viewer, asOfMs, steps: [], citations: [], running: true });
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
      asOfMs,
      viewer,
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
      <div className="query">
        <div className="query-row">
          <svg className="glyph" viewBox="0 0 24 24" aria-hidden="true">
            <circle cx="10.5" cy="10.5" r="6.5" />
            <path d="M15.4 15.4 21 21" />
          </svg>
          <label htmlFor="q" className="sr-only">
            Your question
          </label>
          <input
            id="q"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            maxLength={500}
            placeholder="Which customers are waiting on a fix?"
            className={`query-input ${compact ? "text-[1.15rem] py-1" : "text-[1.3rem] sm:text-[1.55rem] py-1.5"}`}
          />
          {run?.running ? (
            <button type="button" onClick={stop} className="btn btn-line">
              Stop
            </button>
          ) : (
            <button type="submit" disabled={draft.trim().length < 3} className="btn btn-ink">
              <kbd className="hidden sm:inline">Enter</kbd> Ask
            </button>
          )}
        </div>
        <div className="query-scope">
          <ProductPicker products={products} value={product} onChange={setProduct} error={productError} />
          <span className="scope-select" title="Answer as this person: sources they can't see are left out (synthetic policy)">
            <label htmlFor="viewer">view as</label>
            <select id="viewer" value={viewer ?? ""} onChange={(e) => setViewer(e.target.value || null)}>
              <option value="">Anyone</option>
              {VIEWERS.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.label}
                </option>
              ))}
            </select>
          </span>
          <span className="ml-auto text-[0.8rem] text-muted hidden md:inline">
            Every sentence comes with a quote checked word for word.
          </span>
        </div>
      </div>
    </form>
  );

  return (
    <div className="min-h-screen">
      <header className="topbar">
        <div className="max-w-[90rem] mx-auto px-4 sm:px-8 py-2.5 flex flex-wrap items-center gap-x-6 gap-y-2">
          <button
            type="button"
            onClick={() => {
              abort.current?.abort();
              setRun(null);
              window.location.hash = "ask";
            }}
            className="brand"
            aria-label="Lore, back to the start"
          >
            <svg viewBox="0 0 28 28" aria-hidden="true">
              <path className="bm-edge" d="M7 20 L14 8 L21 18 Z" />
              <circle className="bm-node" cx="7" cy="20" r="3.2" />
              <circle className="bm-node" cx="21" cy="18" r="3.2" />
              <rect className="bm-cite" x="10.6" y="4.6" width="6.8" height="6.8" rx="1.6" />
            </svg>
            Lore
          </button>
          <nav aria-label="Views" className="tabs mr-auto overflow-x-auto max-w-full">
            {VIEWS.map((v) => (
              <a key={v} href={`#${v}`} aria-current={view === v ? "page" : undefined} className="tab">
                {VIEW_WORDS[v]}
                {v === "inbox" && openAlerts ? (
                  <span className="count" aria-label={`${openAlerts} open`}>
                    {openAlerts}
                  </span>
                ) : null}
              </a>
            ))}
          </nav>
          <span className={`chip hidden md:inline-flex ${productError ? "text-warn" : ""}`}>
            {productError ? (
              `The graph didn't answer: ${productError}`
            ) : products.length ? (
              <>
                <span className="k">graph</span>
                {artifacts.toLocaleString("en")} artifacts, {products.length} products
              </>
            ) : (
              "Connecting to the graph"
            )}
          </span>
          <ThemeToggle />
          {SITE_URL && (
            <a href={SITE_URL} className="btn btn-line btn-sm hidden sm:inline-flex">
              About Lore
            </a>
          )}
        </div>
      </header>

      <main className="max-w-[90rem] mx-auto px-4 sm:px-8 pb-20">
        {view === "inbox" ? (
          <Inbox onCount={countAlerts} />
        ) : view === "health" ? (
          <GraphHealthPage />
        ) : view === "eval" ? (
          <EvalPage />
        ) : compact ? (
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
        {view === "ask" && run && (
          <RunView
            run={run}
            active={active}
            setActive={setActive}
            onReask={(asOfMs) => start(run.question, run.product, asOfMs)}
          />
        )}
      </main>
    </div>
  );
}

function Opening({ form, onPick, products }: { form: ReactNode; onPick: (s: Suggestion) => void; products: Product[] }) {
  return (
    <section className="pt-14 sm:pt-20 max-w-[64rem] mx-auto rise">
      <h1 className="font-serif text-[2.6rem] sm:text-[4rem] leading-none tracking-[-0.028em] max-w-[18ch] text-balance">
        What's true at the company, and who said so.
      </h1>
      <p className="mt-5 text-[1.08rem] text-muted max-w-[58ch] leading-relaxed">
        Lore answers from a graph of Slack messages, meetings, documents and pull requests. Each sentence of an answer
        links to a quote you can check against its source. When nothing supports an answer, Lore says so.
      </p>
      {form}
      <div className="mt-14">
        <h2 className="font-serif text-[1.6rem] tracking-[-0.01em]">Try a question from the data</h2>
        <p className="text-sm text-muted mt-0.5">
          The data is{" "}
          <a className="text-link underline underline-offset-2" href="https://arxiv.org/abs/2506.23139">
            HERB
          </a>
          , Salesforce AI Research's enterprise benchmark (EMNLP 2025): a synthetic company. Each product also goes by a
          codename in its chats.
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
    <div className="grid gap-4 mt-5 sm:grid-cols-2 lg:grid-cols-3">
      {groups.map((g) => (
        <section key={g} aria-label={g} className="card suggest-card">
          <h3>
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
                  className="font-serif text-[1.06rem] leading-snug text-left py-1.5 px-2 -mx-2 w-[calc(100%+1rem)] rounded-lg hover:bg-(--surface-2) hover:text-link disabled:opacity-50"
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
    <span className={`scope-select ${error ? "err" : ""}`}>
      <label htmlFor="product">product</label>
      <select
        id="product"
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value || null)}
        title={error ? `Products not loaded: ${error}` : undefined}
      >
        <option value="">{error ? "Not loaded" : "Any"}</option>
        {products.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
      </select>
    </span>
  );
}

function RunView(props: {
  run: Run;
  active: number | null;
  setActive: (n: number | null) => void;
  onReask: (asOfMs: number | null) => void;
}) {
  const { run, active, setActive, onReask } = props;
  const llm = run.answer?.llm;
  const [picked, setPicked] = useState<string | null>(null);
  // Issues and PRs in the evidence have a chain of states: cited ones first.
  const subjects = (run.subgraph?.nodes ?? [])
    .filter((n) => n.label === "issue" || n.label === "pr")
    .sort((a, b) => Number(b.cited) - Number(a.cited));
  return (
    <div className="grid gap-5 pt-6 items-start lg:grid-cols-[16rem_minmax(0,1fr)_22rem]">
      <section aria-labelledby="h-walk" className="order-3 lg:order-1 card card-pad lg:sticky lg:top-20">
        <h2 id="h-walk" className="card-h">
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

      <section aria-labelledby="h-answer" className="order-1 lg:order-2 min-w-0 card card-pad sm:px-8 sm:py-7">
        <h2 id="h-answer" className="font-serif text-[1.75rem] leading-[1.15] tracking-[-0.01em] mb-6 max-w-[34ch]">
          {run.question}
        </h2>
        {run.asOfMs !== null && (
          <p className="-mt-4 mb-5 text-sm">
            <span className="bg-mark text-mark-ink px-1.5 py-0.5 rounded font-semibold">As of {when(new Date(run.asOfMs).toISOString())}</span>{" "}
            <span className="text-muted">Lore answers for the state in force at that moment.</span>
          </p>
        )}
        {run.error && (
          <div className="border-l-4 border-warn bg-warn-soft rounded-r-lg px-4 py-3 mb-4 max-w-[68ch]" role="alert">
            <p className="font-semibold text-warn">The question failed</p>
            <p className="mt-0.5">{run.error}</p>
            <p className="text-sm text-muted mt-1">
              {run.error.startsWith("CacheOnly")
                ? "This deployment answers its suggested questions only; it never spends on new ones."
                : run.error.startsWith("429") || run.error.startsWith("503")
                  ? "Lore is limiting requests right now. Wait a minute and ask again."
                  : "Ask again, or check the API log for the cause."}
            </p>
          </div>
        )}
        {run.answer ? (
          <Answer answer={run.answer} citations={run.citations} active={active} setActive={setActive} />
        ) : (
          !run.error && <p className="text-muted">The answer appears once the evidence is in.</p>
        )}

        {!!run.subgraph?.nodes.length && (
          <section aria-labelledby="h-evidence" className="mt-12">
            <h2 id="h-evidence" className="font-serif text-[1.35rem] tracking-[-0.01em]">
              Evidence in the graph
            </h2>
            <p className="text-sm text-muted mt-0.5 mb-3 max-w-[68ch]">
              The cited sources and what they connect to: authors, the product, customers and their companies, and the
              claims and decisions Lore extracted from them.
            </p>
            <EvidenceGraph
              graph={run.subgraph}
              citations={run.citations}
              active={active}
              onPick={setPicked}
              hint={
                subjects.length
                  ? "Point at a node to read it. Yellow nodes are the cited sources. Click an issue or pull request to see its timeline below."
                  : undefined
              }
            />
          </section>
        )}

        {subjects.length > 0 && (
          <section aria-labelledby="h-timeline" className="mt-12">
            <h2 id="h-timeline" className="font-serif text-[1.35rem] tracking-[-0.01em]">
              How it changed over time
            </h2>
            <p className="text-sm text-muted mt-0.5 mb-4 max-w-[68ch]">
              Each state holds from one moment until the next one replaces it. Lore never deletes a replaced state; it
              closes its window. Move the slider to see what was true then, and ask the question again as of that moment.
            </p>
            <Timeline
              subjects={subjects}
              selected={picked}
              onSelect={setPicked}
              question={run.question}
              asOfMs={run.asOfMs}
              onReask={onReask}
              running={run.running}
            />
          </section>
        )}
      </section>

      <section aria-labelledby="h-sources" className={`order-2 lg:order-3 min-w-0 ${run.citations.length ? "card card-pad lg:sticky lg:top-20" : ""}`}>
        {run.citations.length > 0 && (
          <>
            <h2 id="h-sources" className="card-h mb-1">
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
