import { useEffect, useRef, useState } from "react";
import { getQuery } from "../api";
import { TOOL_WORDS } from "../words";

export type Step = {
  tool: string;
  input: Record<string, unknown>;
  done: boolean;
  ms?: number;
  cypher?: string[];
  error?: string;
};

// The agent's tool calls in the order it made them: each is one walk through the graph.
export function Walk({ steps, running }: { steps: Step[]; running: boolean }) {
  return (
    <ol className="relative">
      {steps.map((s, i) => (
        <StepItem key={i} i={i + 1} s={s} last={i === steps.length - 1 && !running} />
      ))}
      {running && (
        <li className="pl-8 relative text-muted text-sm py-1">
          <span className="absolute left-[0.45rem] top-2 size-2.5 rounded-full border-2 border-link animate-pulse" />
          {steps.length ? "Thinking about the next step" : "Reading the question"}
        </li>
      )}
    </ol>
  );
}

function StepItem({ i, s, last }: { i: number; s: Step; last: boolean }) {
  const [open, setOpen] = useState(false);
  const args = Object.entries(s.input).filter(([, v]) => v !== null && v !== undefined && v !== "");
  return (
    <li className="step-in relative pl-8 pb-4">
      {!last && <span className="absolute left-[0.72rem] top-6 bottom-0 w-px bg-rule" aria-hidden />}
      <span
        className={`absolute left-0 top-0.5 size-6 rounded-full grid place-items-center text-[0.72rem] font-semibold ${
          s.error ? "bg-warn-soft text-warn" : "bg-link-soft text-link"
        }`}
        aria-hidden
      >
        {i}
      </span>
      <p className="font-medium leading-tight">{TOOL_WORDS[s.tool] ?? s.tool}</p>
      {args.length > 0 && (
        <dl className="text-[0.82rem] text-muted mt-0.5 leading-snug">
          {args.map(([k, v]) => (
            <div key={k} className="flex gap-1.5">
              <dt className="shrink-0">{k.replace(/_/g, " ")}</dt>
              <dd className="text-ink wrap-break-word min-w-0">{typeof v === "string" ? v : JSON.stringify(v)}</dd>
            </div>
          ))}
        </dl>
      )}
      {s.error && <p className="text-[0.82rem] text-warn mt-1">{s.error}</p>}
      {s.done && (
        <p className="text-[0.78rem] text-muted mt-1 flex gap-3 items-baseline">
          <span>{s.ms !== undefined ? `${Math.round(s.ms)} ms` : ""}</span>
          {!!s.cypher?.length && (
            <button
              type="button"
              onClick={() => setOpen(true)}
              aria-haspopup="dialog"
              className="text-link hover:underline underline-offset-2"
            >
              Show the Cypher
            </button>
          )}
        </p>
      )}
      {open && s.cypher && <CypherDialog step={s} names={s.cypher} onClose={() => setOpen(false)} />}
    </li>
  );
}

// The exact query files the tool ran, wide enough to read, with the parameters the agent chose.
function CypherDialog({ step, names, onClose }: { step: Step; names: string[]; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    ref.current?.showModal();
  }, []);
  return (
    <dialog
      ref={ref}
      onClose={onClose}
      onClick={(e) => e.target === ref.current && ref.current?.close()}
      aria-labelledby="cypher-title"
      className="m-auto w-[min(60rem,94vw)] max-h-[88vh] bg-paper text-ink border border-rule p-0 backdrop:bg-black/40"
    >
      <div className="px-5 py-4 border-b border-rule flex items-baseline justify-between gap-4">
        <h3 id="cypher-title" className="font-semibold">
          {TOOL_WORDS[step.tool] ?? step.tool}: the Cypher FalkorDB ran
        </h3>
        <button type="button" onClick={() => ref.current?.close()} className="text-link hover:underline underline-offset-2">
          Close
        </button>
      </div>
      <div className="px-5 py-4 space-y-4 overflow-auto max-h-[calc(88vh-4rem)]">
        <p className="text-sm text-muted">
          The agent's arguments:{" "}
          <code className="font-mono text-[0.8rem] text-ink">{JSON.stringify(step.input)}</code>. The tool fills
          in the product being asked about and the row limits.
        </p>
        {names.map((n) => (
          <CypherFile key={n} name={n} />
        ))}
      </div>
    </dialog>
  );
}

function CypherFile({ name }: { name: string }) {
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    getQuery(name).then(
      (q) => setText(q.text),
      (e: Error) => setError(e.message),
    );
  }, [name]);
  return (
    <figure>
      <figcaption className="text-[0.8rem] text-muted mb-1">backend/queries/{name}</figcaption>
      <pre className="font-mono text-[0.8rem] leading-relaxed bg-surface border border-rule p-3 overflow-x-auto whitespace-pre [font-variant-ligatures:none]">
        {error ? <span className="text-warn">{error}</span> : (text ?? "Loading")}
      </pre>
    </figure>
  );
}
