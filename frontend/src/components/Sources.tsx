import { useState } from "react";
import type { Citation } from "../api";
import { KIND_WORDS, when } from "../words";
import { authors, useArtifact } from "./useArtifact";

type Props = { citations: Citation[]; active: number | null; setActive: (n: number | null) => void };

export function Sources({ citations, active, setActive }: Props) {
  if (!citations.length) return null;
  const sorted = [...citations].sort((a, b) => a.n - b.n);
  return (
    <ol className="space-y-3">
      {sorted.map((c) => (
        <SourceCard key={c.n} c={c} active={active === c.n} setActive={setActive} />
      ))}
    </ol>
  );
}

function SourceCard({ c, active, setActive }: { c: Citation; active: boolean; setActive: Props["setActive"] }) {
  const { artifact, error } = useArtifact(c.artifact_id);
  const [full, setFull] = useState(false);
  return (
    <li
      id={`source-${c.n}`}
      onMouseEnter={() => setActive(c.n)}
      onMouseLeave={() => setActive(null)}
      className={`relative border-l-2 pl-3 pr-1 py-1 scroll-mt-24 transition-colors ${
        active ? "border-mark bg-surface" : "border-rule"
      }`}
    >
      <span
        className={`absolute -left-[0.95rem] top-1 text-[0.72rem] font-semibold w-[1.6rem] text-center rounded-sm ${
          active ? "bg-mark text-mark-ink" : "bg-link-soft text-link"
        }`}
        aria-hidden
      >
        {c.n}
      </span>
      <p className="font-serif text-[1.02rem] leading-snug">
        <mark className="verbatim">{c.quote}</mark>
      </p>
      <p className={`text-[0.78rem] mt-1 ${c.quote_ok === false ? "text-warn" : "text-muted"}`}>
        {c.quote_ok === false ? "Quote not found in this source" : "Found word for word in the source"}
      </p>
      <div className="text-[0.82rem] text-muted mt-1.5 leading-snug">
        {error && <span className="text-warn">Source not loaded: {error}</span>}
        {artifact && (
          <>
            <span className="block text-ink">
              {KIND_WORDS[artifact.kind] ?? artifact.kind}
              {artifact.channel ? ` in #${artifact.channel}` : artifact.title ? `: ${artifact.title}` : ""}
            </span>
            <span className="block">{[authors(artifact), when(artifact.ts_iso)].filter(Boolean).join(", ")}</span>
            <span className="flex gap-4 mt-1">
              <button
                type="button"
                className="text-link hover:underline underline-offset-2"
                onClick={() => setFull((v) => !v)}
                aria-expanded={full}
              >
                {full ? "Hide full text" : "Read in context"}
              </button>
              {artifact.url && (
                <a className="text-link hover:underline underline-offset-2" href={artifact.url} target="_blank" rel="noreferrer">
                  Open original
                </a>
              )}
            </span>
            {full && <InContext text={artifact.text} quote={c.quote} />}
          </>
        )}
      </div>
    </li>
  );
}

// The source's full text with the quoted span marked where it occurs.
function InContext({ text, quote }: { text: string; quote: string }) {
  let at = text.indexOf(quote);
  if (at < 0) at = text.toLowerCase().indexOf(quote.toLowerCase());
  return (
    <p className="font-serif text-[0.95rem] text-ink whitespace-pre-wrap mt-2 max-h-72 overflow-auto bg-paper border border-rule p-3">
      {at < 0 ? (
        text
      ) : (
        <>
          {text.slice(0, at)}
          <mark className="verbatim">{text.slice(at, at + quote.length)}</mark>
          {text.slice(at + quote.length)}
        </>
      )}
    </p>
  );
}
