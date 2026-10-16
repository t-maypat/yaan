import { createContext, type ReactNode, useContext, useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { AnswerEvent, Citation } from "../api";
import { KIND_WORDS, plural, when } from "../words";
import { authors, useArtifact } from "./useArtifact";

type Props = {
  answer: AnswerEvent;
  citations: Citation[];
  active: number | null;
  setActive: (n: number | null) => void;
};

// `[n]` markers become links to `#cite-n`, so react-markdown hands them to CiteMark below.
const MARKER = /\[(\d+)\](?!\()/g;
const PLUGINS = [remarkGfm];

export function Answer({ answer, citations, active, setActive }: Props) {
  const byN = new Map(citations.map((c) => [c.n, c]));
  const dropped = answer.validator?.dropped?.length ?? 0;
  if (answer.abstain) {
    return (
      <div className="border-l-4 border-warn bg-warn-soft px-5 py-4 max-w-[68ch]">
        <p className="font-semibold text-warn">No answer</p>
        <p className="font-serif text-lg mt-1">{answer.answer_markdown}</p>
        <p className="text-sm text-muted mt-2">
          Lore abstains when no source it read supports an answer, rather than guess.
        </p>
      </div>
    );
  }
  return (
    <div>
      <div className="answer">
        <Cites.Provider value={{ byN, active, setActive }}>
          <Markdown remarkPlugins={PLUGINS} components={COMPONENTS}>
            {answer.answer_markdown.replace(MARKER, "[$1](#cite-$1)")}
          </Markdown>
        </Cites.Provider>
      </div>
      {dropped > 0 && (
        <p className="text-sm text-muted mt-1">
          The citation check removed {plural(dropped, "sentence")} whose quote wasn't in its source.
        </p>
      )}
    </div>
  );
}

// The markdown components must keep one identity across renders: an inline function would be
// a new component type each time `active` changes, remounting every marker and closing its popover.
const Cites = createContext<{
  byN: Map<number, Citation>;
  active: number | null;
  setActive: (n: number | null) => void;
}>({ byN: new Map(), active: null, setActive: () => {} });

const COMPONENTS = {
  a: ({ href, children }: { href?: string; children?: ReactNode }) => {
    const n = href?.startsWith("#cite-") ? Number(href.slice(6)) : null;
    if (n !== null) return <Cited n={n} />;
    return (
      <a className="ext" href={href} target="_blank" rel="noreferrer">
        {children}
      </a>
    );
  },
};

function Cited({ n }: { n: number }) {
  const { byN, active, setActive } = useContext(Cites);
  return <CiteMark n={n} citation={byN.get(n)} active={active === n} setActive={setActive} />;
}

function CiteMark(props: {
  n: number;
  citation?: Citation;
  active: boolean;
  setActive: (n: number | null) => void;
}) {
  const { n, citation, active, setActive } = props;
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    const block = ref.current?.closest(".answer p, .answer li");
    if (!block || !active) return;
    block.classList.add("lit");
    return () => block.classList.remove("lit");
  }, [active]);
  const show = () => {
    setOpen(true);
    setActive(n);
  };
  const hide = () => {
    setOpen(false);
    setActive(null);
  };
  return (
    <span ref={ref} className="relative inline-block align-baseline" onMouseEnter={show} onMouseLeave={hide}>
      <a
        href={`#source-${n}`}
        onFocus={show}
        onBlur={hide}
        aria-describedby={open ? `pop-${n}` : undefined}
        className={`font-sans text-[0.72em] font-semibold px-[0.35em] py-[0.05em] ml-[0.1em] rounded-sm align-[0.25em] no-underline transition-colors ${
          active ? "bg-mark text-mark-ink" : "bg-link-soft text-link"
        }`}
      >
        {n}
      </a>
      {open && citation && <Popover id={`pop-${n}`} citation={citation} />}
    </span>
  );
}

function Popover({ id, citation }: { id: string; citation: Citation }) {
  const { artifact, error } = useArtifact(citation.artifact_id);
  return (
    <span
      id={id}
      role="tooltip"
      className="absolute z-20 left-1/2 -translate-x-1/2 top-[1.9em] w-[min(24rem,80vw)] block bg-surface border border-rule shadow-[0_6px_24px_-8px_rgb(0_0_0/0.25)] px-4 py-3 text-left"
    >
      <span className="block font-serif text-base leading-snug">
        <mark className="verbatim">{citation.quote}</mark>
      </span>
      <span className="block font-sans text-[0.8rem] text-muted mt-2 leading-snug">
        {error && <span className="text-warn">Source not loaded: {error}</span>}
        {!artifact && !error && "Loading the source"}
        {artifact && (
          <>
            <span className="block text-ink font-medium">
              {KIND_WORDS[artifact.kind] ?? artifact.kind}
              {artifact.channel ? ` in #${artifact.channel}` : artifact.title ? `: ${artifact.title}` : ""}
            </span>
            <span className="block">
              {[authors(artifact), when(artifact.ts_iso)].filter(Boolean).join(", ")}
            </span>
          </>
        )}
      </span>
    </span>
  );
}
