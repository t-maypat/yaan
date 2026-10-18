import { useEffect, useMemo, useState } from "react";
import { type ChainState, type GraphNode, getLineage, type Lineage, type LineageSubject } from "../api";
import { when } from "../words";

// The decision Timeline (12.4): the chain of states of an issue or pull request in the evidence,
// each holding from one moment until the next replaced it (validity windows, guide 08). The slider
// picks a moment, marks the state in force then, and re-asks the question as of that moment.

type Props = {
  subjects: GraphNode[]; // the evidence graph's issue and PR nodes
  selected: string | null;
  onSelect: (id: string) => void;
  question: string;
  asOfMs: number | null; // the moment the current answer is for (null = now)
  onReask: (asOfMs: number | null) => void;
  running: boolean;
};

const HOUR = 3_600_000;
const ms = (iso: string | null) => (iso ? Date.parse(iso) : null);
const inForceAt = (s: ChainState, t: number) => {
  const from = ms(s.from);
  const to = ms(s.to);
  return from !== null && from <= t && (to === null || t < to);
};

export function Timeline({ subjects, selected, onSelect, question, asOfMs, onReask, running }: Props) {
  const current = selected ?? subjects[0]?.id ?? null;
  const [lineage, setLineage] = useState<Lineage | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!current) return;
    let live = true;
    setLineage(null);
    setError(null);
    getLineage(current).then(
      (l) => live && setLineage(l),
      (e: Error) => live && setError(e.message),
    );
    return () => {
      live = false;
    };
  }, [current]);

  if (!current) return null;
  const subject = lineage?.subjects.find((s) => s.id === current) ?? lineage?.subjects[0];
  return (
    <div>
      {subjects.length > 1 && (
        <div className="flex flex-wrap gap-x-4 gap-y-1 mb-4" role="group" aria-label="Issues and pull requests in the evidence">
          {subjects.map((n) => (
            <button
              key={n.id}
              type="button"
              onClick={() => onSelect(n.id)}
              aria-pressed={n.id === current}
              className={`text-left text-[0.95rem] ${
                n.id === current ? "font-semibold border-b-2 border-ink" : "text-link hover:underline underline-offset-2"
              }`}
            >
              {n.label === "pr" ? "PR: " : "Issue: "}
              {n.name ?? n.id}
            </button>
          ))}
        </div>
      )}
      {error && (
        <p className="border-l-4 border-warn bg-warn-soft px-3 py-2 text-sm" role="alert">
          The timeline didn't load: {error}
        </p>
      )}
      {!error && !lineage && <p className="text-muted text-sm">Reading the chain of states from the graph…</p>}
      {subject && (
        <Chain subject={subject} question={question} asOfMs={asOfMs} onReask={onReask} running={running} />
      )}
    </div>
  );
}

function Chain(props: {
  subject: LineageSubject;
  question: string;
  asOfMs: number | null;
  onReask: (asOfMs: number | null) => void;
  running: boolean;
}) {
  const { subject, question, asOfMs, onReask, running } = props;
  const chain = useMemo(
    () => [...(subject.status_chain ?? []), ...(subject.action_chain ?? [])].sort((a, b) => (ms(a.from) ?? 0) - (ms(b.from) ?? 0)),
    [subject],
  );
  const starts = chain.map((s) => ms(s.from)).filter((t): t is number => t !== null);
  const lo = starts.length ? Math.min(...starts) - 24 * HOUR : 0;
  const hi = starts.length ? Math.max(...starts) + 24 * HOUR : 0;
  const [t, setT] = useState<number>(() => asOfMs ?? hi);
  useEffect(() => setT(asOfMs ?? hi), [asOfMs, hi]);

  if (!chain.length) return <p className="text-muted text-sm">No recorded states for this item.</p>;
  const facet = (s: ChainState) => (subject.action_chain?.includes(s) ? "what the team said to do" : "status");
  return (
    <div>
      <p className="text-sm text-muted mb-3">
        {subject.kind === "pr" ? `Pull request #${subject.number}` : "Issue"}{" "}
        <span className="text-ink font-medium">{subject.title}</span>
        {subject.url && (
          <>
            {" "}
            <a href={subject.url} target="_blank" rel="noreferrer" className="text-link hover:underline underline-offset-2">
              open
            </a>
          </>
        )}
      </p>
      <ol className="border-l-2 border-rule ml-1.5">
        {chain.map((s) => {
          const lit = inForceAt(s, t);
          return (
            <li key={s.decision_id} className="relative pl-5 pb-5 last:pb-1">
              <span
                aria-hidden
                className={`absolute -left-[0.45rem] top-1.5 w-3 h-3 rounded-full border-2 ${
                  lit ? "bg-mark border-ink" : "bg-surface border-muted"
                }`}
              />
              <p className="text-[0.82rem] text-muted">
                {when(s.from ?? undefined)} {s.to ? `until ${when(s.to)}` : "· still in force"}
                {s.time_inferred && " · time inferred"}
              </p>
              <p className="mt-0.5">
                <span className={`font-semibold px-1 ${lit ? "bg-mark text-mark-ink" : ""}`}>{s.value}</span>
                <span className="text-sm text-muted"> ({facet(s)})</span>
                {lit && <span className="text-sm"> · in force at the chosen moment</span>}
              </p>
              {s.decided_by.length > 0 && (
                <p className="text-sm mt-0.5">
                  Decided by{" "}
                  {s.decided_by.map((p, i) => (
                    <span key={p.id}>
                      {i > 0 && ", "}
                      {p.name ?? p.id}
                      {p.role && <span className="text-muted"> ({p.role})</span>}
                    </span>
                  ))}
                </p>
              )}
              {s.evidence.slice(0, 1).map((e) => (
                <blockquote key={e.artifact_id} className="font-serif text-[1rem] leading-snug mt-1.5 max-w-[60ch]">
                  “{e.quote}”
                  <span className="block font-sans text-[0.75rem] text-muted mt-0.5">{e.artifact_id}</span>
                </blockquote>
              ))}
              {s.said
                .filter((c) => c.authority && c.authority !== "authorized")
                .map((c) => (
                  <p key={c.artifact_id + c.quote} className="text-sm mt-1 border-l-2 border-warn pl-2">
                    Claim by {c.by_name ?? c.by}: {c.authority}
                    {c.authority_reason && <span className="text-muted"> ({c.authority_reason})</span>}
                  </p>
                ))}
              {s.refused?.map((r) => (
                <p key={(r.id ?? r.artifact_id ?? "") + (r.reason ?? "")} className="text-sm mt-1 border-l-2 border-warn pl-2">
                  Refused change: {r.reason ?? r.quote}
                </p>
              ))}
              {!!s.alerts?.length && (
                <p className="text-sm mt-1">
                  Raised {s.alerts.length === 1 ? "an alert" : `${s.alerts.length} alerts`}:{" "}
                  {s.alerts.map((a) => a.kind).join(", ")} (see the Inbox)
                </p>
              )}
            </li>
          );
        })}
      </ol>

      <div className="mt-4 border-t border-rule pt-3">
        <label htmlFor="as-of" className="block text-sm">
          As of <span className="font-semibold">{when(new Date(t).toISOString())}</span>
        </label>
        <input
          id="as-of"
          type="range"
          min={lo}
          max={hi}
          step={HOUR}
          value={Math.min(Math.max(t, lo), hi)}
          onChange={(e) => setT(Number(e.target.value))}
          className="w-full max-w-[40rem] accent-[var(--link)]"
        />
        <div className="flex flex-wrap gap-3 mt-2">
          <button
            type="button"
            disabled={running || t === asOfMs}
            onClick={() => onReask(t)}
            className="btn btn-line btn-sm"
            title={question}
          >
            Ask again as of this moment
          </button>
          {asOfMs !== null && (
            <button
              type="button"
              disabled={running}
              onClick={() => onReask(null)}
              className="btn btn-line btn-sm"
            >
              Ask again for now
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
