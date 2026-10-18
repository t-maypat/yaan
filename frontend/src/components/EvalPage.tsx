import { useEffect, useState } from "react";
import { type EvalLatest, getEvalLatest, type SystemResult } from "../api";

// The Eval page (12.6): the newest committed results per system (`/eval/latest`, eval/results/*.json).
// One series (Avg per system), so one hue and no legend; HERB's published result is a reference line.

const SYSTEM_WORDS: Record<string, string> = {
  lore: "Lore",
  "lore-unvalidated": "Lore's first answers, as written (no validator, no abstention line)",
  "lore-search": "Lore without graph tools (search and read only)",
  baseline: "Plain RAG baseline",
};
const CATS: [string, string][] = [
  ["content", "Content"],
  ["person", "People"],
  ["company", "Customer"],
  ["url", "Artifact"],
];
const num = (v: number | null | undefined, d = 1) => (v === null || v === undefined ? "—" : v.toFixed(d));

export function EvalPage() {
  const [data, setData] = useState<EvalLatest | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    getEvalLatest().then(setData, (e: Error) => setError(e.message));
  }, []);

  if (error)
    return (
      <p className="mt-10 border-l-4 border-warn bg-warn-soft px-4 py-3 max-w-[68ch]" role="alert">
        The results didn't load: {error}
      </p>
    );
  if (!data) return <p className="mt-10 text-muted">Reading the results…</p>;

  const pick = (split: string) =>
    data.systems.filter((s) => s.split === split).sort((a, b) => (b.avg ?? 0) - (a.avg ?? 0));
  const dev = pick("dev");
  const test = pick("test");
  // The held-out run leads once it exists; dev rows stay below it for comparison.
  const main = test.length ? test : dev;
  const mainWord = test.length ? "held-out" : "dev";
  const rows = test.length ? [...test, ...dev] : dev;
  const lore = main.find((s) => s.system === "lore");
  const base = main.find((s) => s.system === "baseline");
  return (
    <section className="pt-10 max-w-[64rem]" aria-labelledby="h-eval">
      <h1 id="h-eval" className="font-serif text-[2rem] leading-tight">
        Evaluation
      </h1>
      <p className="text-muted mt-2 max-w-[64ch]">
        {test.length ? "HERB's held-out questions, from 7 products never read while building Lore, run once" : "HERB's dev questions"} (
        {lore ? `${lore.questions.answerable} answerable, ${lore.questions.unanswerable} unanswerable` : `${mainWord} split`}
        ), scored with HERB's own judge prompts. Each row is one change away from Lore, so the gap between rows is what that part
        is worth.
      </p>
      {lore && base && (
        <p className="mt-5 text-[1.1rem] max-w-[64ch]">
          Lore scores <span className="font-semibold">{num(lore.avg)}</span> against the baseline's{" "}
          <span className="font-semibold">{num(base.avg)}</span>, and every quote it shows was checked word for word (
          {num(lore.citations?.quote_ok_pct, 0)}%).
        </p>
      )}

      <AvgBars systems={main} reference={data.reference} splitWord={mainWord} />

      <div className="overflow-x-auto mt-10">
        <table className="w-full text-[0.9rem] min-w-[44rem]">
          <caption className="text-left font-semibold mb-2">All scores{test.length ? " (held-out, then dev)" : " (dev)"}</caption>
          <thead>
            <tr className="border-b border-ink text-left align-bottom">
              <th className="font-medium py-1 pr-3">System</th>
              {CATS.map(([, label]) => (
                <th key={label} className="font-medium py-1 px-2 text-right">
                  {label}
                </th>
              ))}
              <th className="font-medium py-1 px-2 text-right">Avg</th>
              <th className="font-medium py-1 px-2 text-right">Unanswerable declined</th>
              <th className="font-medium py-1 px-2 text-right">Drift: right / stale</th>
              <th className="font-medium py-1 pl-2 text-right">p50 answer</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((s) => {
              // The drift set is built from dev products: it belongs to the dev rows only.
              const d = s.split === "dev" ? data.drift[s.system] : undefined;
              return (
                <tr key={`${s.system}-${s.split}`} className="border-b border-rule">
                  <td className={`py-1.5 pr-3 ${s.system === "lore" ? "font-semibold" : ""}`}>
                    {SYSTEM_WORDS[s.system] ?? s.system}
                    {test.length > 0 && <span className="text-muted font-normal"> · {s.split === "test" ? "held-out" : "dev"}</span>}
                  </td>
                  {CATS.map(([k]) => (
                    <td key={k} className="py-1.5 px-2 text-right tabular-nums">
                      {num(s.categories[k]?.score)}
                    </td>
                  ))}
                  <td className="py-1.5 px-2 text-right tabular-nums font-semibold">{num(s.avg)}</td>
                  <td className="py-1.5 px-2 text-right tabular-nums">{num(s.unanswerable)}%</td>
                  <td className="py-1.5 px-2 text-right tabular-nums">
                    {d ? `${num(d.correct_pct, 0)}% / ${num(d.stale_answer_rate)}%` : "—"}
                  </td>
                  <td className="py-1.5 pl-2 text-right tabular-nums">{num(s.latency_s.p50)} s</td>
                </tr>
              );
            })}
            <tr className="border-b border-rule text-muted">
              <td className="py-1.5 pr-3">{data.reference.system} (published; different model and setup)</td>
              {CATS.map(([k]) => (
                <td key={k} className="py-1.5 px-2 text-right">
                  —
                </td>
              ))}
              <td className="py-1.5 px-2 text-right tabular-nums">{num(data.reference.avg)}</td>
              <td className="py-1.5 px-2 text-right tabular-nums">{num(data.reference.unanswerable)}%</td>
              <td className="py-1.5 px-2 text-right">—</td>
              <td className="py-1.5 pl-2 text-right">—</td>
            </tr>
          </tbody>
        </table>
      </div>

      <dl className="mt-10 grid gap-x-10 gap-y-4 sm:grid-cols-2 text-[0.92rem] max-w-[60rem]">
        <div>
          <dt className="font-semibold">How to read it</dt>
          <dd className="text-muted mt-0.5">
            Content is a 0–100 judge score; People, Customer and Artifact are F1 × 100; Avg is their mean. Unanswerable is the
            share of unanswerable questions left unanswered. Drift is Lore's hand-labeled set of 24 questions about what was
            true now and then; stale = answered with a state that no longer held.
          </dd>
        </div>
        {data.experts && (
          <div>
            <dt className="font-semibold">Who knows a component ({data.experts.questions} questions, no LLM)</dt>
            <dd className="text-muted mt-0.5">
              Lore's expertise ranking puts a right engineer first {num(100 * (data.experts.mean.lore?.["hit@1"] ?? 0), 0)}% of
              the time; "whoever posted most" does {num(100 * (data.experts.mean.naive?.["hit@1"] ?? 0), 0)}%.
            </dd>
          </div>
        )}
        <div>
          <dt className="font-semibold">Limits</dt>
          <dd className="text-muted mt-0.5">
            One run per system: a rerun moves Avg by a few points. The judge is Claude Haiku 5.5 standing in for GPT-4o. Lore
            was built looking at dev products only; held-out is the honest number. The held-out products share HERB's question
            templates with dev.
          </dd>
        </div>
      </dl>
    </section>
  );
}

// Avg per system as horizontal bars: <= 24px thick, 4px rounded data-end, value at the tip in a text token,
// a solid hairline for HERB's published result. Hover (or focus) shows the categories.
function AvgBars({
  systems,
  reference,
  splitWord,
}: {
  systems: SystemResult[];
  reference: EvalLatest["reference"];
  splitWord: string;
}) {
  const [hover, setHover] = useState<string | null>(null);
  const max = 100;
  return (
    <figure className="mt-8" aria-labelledby="h-avg">
      <figcaption id="h-avg" className="font-semibold">
        Average score per system ({splitWord}, 0–100)
      </figcaption>
      <div className="relative mt-3 pr-14">
        <div
          aria-hidden
          className="absolute top-0 bottom-0 border-l border-muted"
          style={{ left: `calc((100% - 3.5rem) * ${reference.avg / max})` }}
        />
        <ul className="space-y-3">
          {systems.map((s) => {
            const w = ((s.avg ?? 0) / max) * 100;
            const tip = CATS.map(([k, label]) => `${label} ${num(s.categories[k]?.score)}`).join(" · ");
            return (
              <li key={s.system}>
                <p className={`text-[0.85rem] ${s.system === "lore" ? "font-semibold" : "text-muted"}`}>
                  {SYSTEM_WORDS[s.system] ?? s.system}
                </p>
                <div
                  className="relative flex items-center gap-2 h-6 outline-none"
                  tabIndex={0}
                  onMouseEnter={() => setHover(s.system)}
                  onMouseLeave={() => setHover(null)}
                  onFocus={() => setHover(s.system)}
                  onBlur={() => setHover(null)}
                  aria-label={`${SYSTEM_WORDS[s.system] ?? s.system}: Avg ${num(s.avg)}. ${tip}`}
                >
                  <div className="h-5 bg-link rounded-r" style={{ width: `${w}%`, opacity: s.system === "lore" ? 1 : 0.55 }} />
                  <span className="text-[0.9rem] font-semibold tabular-nums">{num(s.avg)}</span>
                  {hover === s.system && (
                    <span className="absolute left-0 -top-7 z-10 bg-ink text-paper text-[0.78rem] px-2 py-0.5 rounded-sm whitespace-nowrap">
                      {tip}
                    </span>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      </div>
      <p className="text-[0.8rem] text-muted mt-2">
        The vertical line is HERB's published best product-specific agent ({num(reference.avg)}), a different model and
        setup, shown for context only.
      </p>
    </figure>
  );
}
