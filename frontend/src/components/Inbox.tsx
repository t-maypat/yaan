import { useCallback, useEffect, useMemo, useState } from "react";
import {
  type Action,
  type Alert,
  type AlertDetail,
  type GraphEdge,
  type GraphNode,
  getAlertDetail,
  getAlerts,
  getInjections,
  getMe,
  type IngestResult,
  type Injection,
  type Me,
  inject,
  type RadiusRow,
  respond,
  resetDemo,
  type ResponseResult,
  type Subgraph,
} from "../api";
import { KIND_WORDS, plural, when } from "../words";
import { EvidenceGraph } from "./EvidenceGraph";
import { authors, useArtifact } from "./useArtifact";

// The Sentinel's Inbox (12.5): alerts from the rule table, each with its blast radius, the drafted
// follow-ups and one place to approve or dismiss. The live demo sits on top: it sends a labeled
// synthetic message through the pipeline and opens the alert it raises.

const ALERT_WORDS: Record<Alert["kind"], string> = {
  regression: "Regression",
  reopened: "Reopened after dismissal",
  reversal: "Decision reversed",
  conflict: "Unauthorized change",
  key_person: "Single point of failure",
  bus_factor: "Bus factor of one",
  outside_group: "Decided outside the working group",
};
// Alerts about a person rather than a change: computed from the expertise layer (11.5, D062).
const isPersonAlert = (a: Alert) => a.kind === "key_person" || a.kind === "bus_factor";
const STATUS_WORDS: Record<Alert["status"], string> = {
  open: "Needs a response",
  approved: "Approved",
  dismissed: "Dismissed",
  withdrawn: "Withdrawn: the change is gone",
};
// Why an item is in the blast radius, as an edge label (impact_of.cypher `why`).
const WHY_WORDS: Record<string, string> = {
  fixes_issue: "fixed by",
  authored_fix: "wrote the fix",
  reviewed_fix: "reviewed the fix",
  reported_issue: "reported by",
  reporter_works_at: "works at",
  assigned: "assigned to",
  same_component: "same component",
  authored_pr: "authored by",
  reviewed_pr: "reviewed by",
  fixed_by_pr: "fixes",
  evidenced_old_state: "said before",
  decided_old_state: "stated the earlier status",
  evidenced_this_state: "the new message",
  works_on_product: "works on the product",
  caused: "led to",
  only_expert: "only expert on",
  could_back_up: "could back up",
  manager: "reports to",
};
const REASON_WORDS: Record<string, string> = {
  authored_fix: "wrote the fix",
  reviewed_fix: "reviewed the fix",
  assigned: "assignee",
  decided_old_state: "stated the earlier status",
  authored_pr: "authored the PR",
  reviewed_pr: "reviewed the PR",
  could_back_up: "could back up one of the areas",
  manager: "their manager",
};
const ACTION_WORDS: Record<Action["kind"], (a: Action) => string> = {
  ticket_comment: (a) => `Comment on ${a.target_kind === "issue" ? "the issue" : "the pull request"}`,
  doc_fix: () => "Correction under the earlier message",
  notify_owner: (a) => `Note to ${a.target_name}`,
};
const synthetic = (id: string | null) => !!id?.startsWith("msg:synthetic-");
const short = (s: string, n = 44) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);
const values = (v: string | null) => (v ?? "").replaceAll("_", " ");

type Filter = "open" | "answered" | "all";

export function Inbox({ onCount }: { onCount: (open: number) => void }) {
  const [alerts, setAlerts] = useState<Alert[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("open");
  const [selected, setSelected] = useState<string | null>(null);
  const [demo, setDemo] = useState(true); // false in org mode: no demo messages to send (D097)
  const demoLoaded = useCallback((injections: number) => setDemo(injections > 0), []);

  const reload = useCallback(
    (select?: string) =>
      getAlerts().then(
        (r) => {
          setAlerts(r.alerts);
          setError(null);
          onCount(r.alerts.filter((a) => a.status === "open").length);
          if (select) {
            setFilter("all");
            setSelected(select);
          }
        },
        (e: Error) => setError(e.message),
      ),
    [onCount],
  );
  useEffect(() => {
    reload();
  }, [reload]);

  const shown = (alerts ?? []).filter((a) =>
    filter === "all" ? true : filter === "open" ? a.status === "open" : a.status !== "open",
  );
  const current = selected ?? shown[0]?.id ?? null;

  return (
    <div className="pt-8">
      <header className="max-w-[64rem]">
        <h1 className="font-serif text-[2.1rem] sm:text-[2.6rem] leading-[1.08] tracking-[-0.012em] font-medium">
          When something the team believed stops being true.
        </h1>
        <p className="mt-3 text-muted max-w-[62ch] leading-relaxed">
          The Sentinel watches every decision in the graph. When a fixed bug comes back, a call on a pull request flips, or
          someone without the authority tries to change a status, it works out who and what the change touches and drafts
          the follow-ups. Nothing is sent until you approve it, and your answer is kept as a decision too.
        </p>
      </header>

      <LiveDemo
        onIngested={(r) => reload(r.alerts[0]?.id)}
        onReset={() => reload().then(() => setSelected(null))}
        onLoaded={demoLoaded}
      />

      {error && (
        <div className="border-l-4 border-warn bg-warn-soft px-4 py-3 mt-8 max-w-[68ch]" role="alert">
          <p className="font-semibold text-warn">The Inbox didn't load</p>
          <p className="mt-0.5">{error}</p>
          <p className="text-sm text-muted mt-1">
            Is the API running, and does the demo graph exist? <code>lore demo reset</code> creates it.
          </p>
        </div>
      )}

      <div className="grid gap-5 pt-2 mt-10 items-start lg:grid-cols-[22rem_minmax(0,1fr)]">
        <section aria-labelledby="h-alerts" className="min-w-0 order-2 lg:order-1 card card-pad lg:sticky lg:top-20">
          <div className="flex items-baseline justify-between gap-3 mb-2">
            <h2 id="h-alerts" className="font-semibold">
              Alerts
            </h2>
            <div className="flex gap-3 text-sm" role="group" aria-label="Show alerts">
              {(["open", "answered", "all"] as Filter[]).map((f) => (
                <button
                  key={f}
                  type="button"
                  aria-pressed={filter === f}
                  onClick={() => {
                    setFilter(f);
                    setSelected(null);
                  }}
                  className={filter === f ? "font-semibold border-b-2 border-ink" : "text-link hover:underline underline-offset-2"}
                >
                  {f === "open" ? "Open" : f === "answered" ? "Answered" : "All"}
                </button>
              ))}
            </div>
          </div>
          {alerts === null && !error && <p className="text-muted text-sm">Loading alerts.</p>}
          {alerts && !shown.length && (
            <p className="text-muted text-sm">
              {filter !== "open"
                ? "Nothing here yet."
                : demo
                  ? "No open alerts. Send a demo message above to raise one."
                  : "No open alerts. New ones appear as the connectors bring in changes."}
            </p>
          )}
          <ul className="divide-y divide-rule border-y border-rule">
            {shown.map((a) => (
              <li key={a.id}>
                <button
                  type="button"
                  onClick={() => setSelected(a.id)}
                  aria-current={a.id === current}
                  className={`w-full text-left py-2.5 px-2 -mx-2 ${a.id === current ? "bg-surface" : "hover:bg-surface/60"}`}
                >
                  <span className="flex items-baseline justify-between gap-2 text-[0.8rem]">
                    <span className={a.severity === "high" ? "text-warn font-semibold" : "text-muted font-semibold"}>
                      {ALERT_WORDS[a.kind]}
                    </span>
                    <span className="text-muted">{when(a.ts_iso).split(",")[0]}</span>
                  </span>
                  <span className="block font-serif text-[1.05rem] leading-snug mt-0.5">{a.subject_title}</span>
                  <span className="block text-[0.8rem] text-muted mt-0.5">
                    {a.product_id}
                    {synthetic(a.artifact_id) && ", from a synthetic demo message"}
                    {a.status !== "open" && `, ${STATUS_WORDS[a.status].toLowerCase()}`}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>

        <section aria-label="The selected alert" className="min-w-0 order-1 lg:order-2 card card-pad sm:px-8 sm:py-7">
          {current ? (
            <AlertView key={current} id={current} onAnswered={() => reload()} />
          ) : (
            alerts && <p className="text-muted">Pick an alert to see what it touches.</p>
          )}
        </section>
      </div>
    </div>
  );
}

function LiveDemo({
  onIngested,
  onReset,
  onLoaded,
}: {
  onIngested: (r: IngestResult) => void;
  onReset: () => void;
  onLoaded: (injections: number) => void;
}) {
  const [items, setItems] = useState<Injection[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<IngestResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(
    () =>
      getInjections().then(
        (r) => {
          setItems(r.injections);
          onLoaded(r.injections.length);
        },
        (e: Error) => setError(e.message),
      ),
    [onLoaded],
  );
  useEffect(() => {
    load();
  }, [load]);

  const send = (name: string) => {
    setBusy(name);
    setError(null);
    inject(name)
      .then((r) => {
        setResult(r);
        onIngested(r);
      }, (e: Error) => setError(e.message))
      .finally(() => {
        setBusy(null);
        load();
      });
  };
  const reset = () => {
    setBusy("reset");
    setError(null);
    resetDemo()
      .then(() => {
        setResult(null);
        onReset();
      }, (e: Error) => setError(e.message))
      .finally(() => {
        setBusy(null);
        load();
      });
  };

  // No injections to offer: org mode (D097), where the graph is a team's own data, not the demo.
  if (items && items.length === 0) return null;

  return (
    <section aria-labelledby="h-live" className="mt-10 max-w-[64rem] card card-pad">
      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
        <h2 id="h-live" className="font-semibold">
          Try it live
        </h2>
        <button
          type="button"
          onClick={reset}
          disabled={!!busy}
          className="text-sm text-link hover:underline underline-offset-2 disabled:opacity-50"
        >
          {busy === "reset" ? "Resetting the demo graph" : "Reset the demo graph"}
        </button>
      </div>
      <p className="text-sm text-muted mt-0.5 max-w-[68ch]">
        These two Slack messages are synthetic, written for this demo and labeled as such; they are not HERB data. Sending
        one runs it through the same pipeline as the rest of the graph: extraction, the quote check, the authority policy,
        then the Sentinel.
      </p>
      {error && <p className="text-warn text-sm mt-2" role="alert">{error}</p>}
      <ol className="mt-4 grid gap-4 sm:grid-cols-2">
        {(items ?? []).map((m, i) => (
          <li key={m.name} className="border-l-2 border-rule pl-3">
            <p className="text-[0.8rem] text-muted">
              {i === 0 ? "First" : "Then"}: a message in #{m.channel_name}, {when(m.ts_iso)}
            </p>
            <p className="font-serif text-[1.05rem] leading-snug mt-1">{m.text}</p>
            <button
              type="button"
              onClick={() => send(m.name)}
              disabled={!!busy || m.injected || (i > 0 && !items?.[i - 1]?.injected)}
              className="mt-2 btn btn-line btn-sm"
            >
              {busy === m.name ? "Running the pipeline" : m.injected ? "Sent" : "Send this message"}
            </button>
          </li>
        ))}
      </ol>
      {result && (
        <p className="text-sm mt-4" aria-live="polite">
          <span className="font-semibold">
            In the graph {(result.freshness_lag_ms / 1000).toFixed(1)} s after it arrived
          </span>
          <span className="text-muted">
            {" "}
            ({result.llm_cost_usd > 0 ? `extraction $${result.llm_cost_usd.toFixed(4)}` : "extraction replayed from cache at $0"}
            ). {result.alerts.length ? `The Sentinel raised ${plural(result.alerts.length, "alert")}, opened below.` : "No alert: the change was normal progress."}
          </span>
        </p>
      )}
    </section>
  );
}

function AlertView({ id, onAnswered }: { id: string; onAnswered: () => void }) {
  const [detail, setDetail] = useState<AlertDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => getAlertDetail(id).then(setDetail, (e: Error) => setError(e.message)), [id]);
  useEffect(() => {
    load();
  }, [load]);
  // One graph object per loaded detail: a new object each render would redraw Cytoscape mid-layout.
  const graph = useMemo(() => detail && radiusGraph(detail.alert, detail.radius.rows), [detail]);
  if (error) return <p className="text-warn" role="alert">This alert didn't load: {error}</p>;
  if (!detail) return <p className="text-muted">Loading the alert.</p>;
  const { alert, radius, actions } = detail;
  return (
    <article>
      <p className="text-[0.85rem]">
        <span className={alert.severity === "high" ? "text-warn font-semibold" : "font-semibold"}>{ALERT_WORDS[alert.kind]}</span>
        <span className="text-muted">
          {" "}
          in {alert.product_id}. {STATUS_WORDS[alert.status]}.
        </span>
      </p>
      <h2 className="font-serif text-[1.6rem] leading-[1.2] tracking-[-0.01em] mt-1 max-w-[40ch]">{alert.summary}</h2>
      {isPersonAlert(alert) ? <Computed alert={alert} /> : <Trigger alert={alert} />}

      <section aria-labelledby="h-radius" className="mt-10">
        <h3 id="h-radius" className="font-semibold">
          {isPersonAlert(alert) ? "What depends on them" : "What this change touches"}
        </h3>
        <p className="text-sm text-muted mt-0.5 mb-3 max-w-[68ch]">
          {isPersonAlert(alert)
            ? `${plural(radius.size, "item")}: the areas only ${alert.subject_title} has a record of work on, who else knows each one, and their manager.`
            : `${plural(radius.size, "item")} within a few hops of the change, each for a stated reason: the fix and who made it, who reported it, the message that said otherwise, and other issues in the same component.`}
        </p>
        <EvidenceGraph
          graph={graph ?? { nodes: [], edges: [] }}
          root={alert.subject_id}
          citations={[]}
          active={null}
          hint={
            isPersonAlert(alert)
              ? "Point at a node to read it and see why it is linked. The person is in the middle, ringed by the areas only they know."
              : "Point at a node to read it and see why it is linked. The changed issue is in the middle; the yellow node is the new message."
          }
          label={`Blast radius: ${radius.size} items around ${alert.subject_title}`}
        />
        <RadiusList detail={detail} />
      </section>

      {detail.precedents.length > 0 && <Precedents detail={detail} />}

      <Respond detail={detail} actions={actions} onDone={() => (load(), onAnswered())} />
    </article>
  );
}

// A person-level alert has no trigger message: say how it was computed instead.
function Computed({ alert }: { alert: Alert }) {
  return (
    <div className="mt-5 border-l-2 border-link pl-4 max-w-[68ch] text-[0.95rem]">
      <p>
        No message raised this. Yaan computed it from the graph: who made the deliveries in each area (degree of
        authorship over bug fixes and pull requests), then the truck factor, which removes the person holding the most
        areas until more than half of a product's areas have no author left.
      </p>
      {alert.reason && <p className="text-sm text-muted mt-1">Current {alert.reason}.</p>}
    </div>
  );
}

function Trigger({ alert }: { alert: Alert }) {
  const { artifact } = useArtifact(alert.artifact_id ?? "", !!alert.artifact_id);
  return (
    <figure className="mt-5 border-l-2 border-mark pl-4 max-w-[68ch]">
      <blockquote className="font-serif text-[1.12rem] leading-snug">
        <mark className="verbatim">{alert.quote}</mark>
      </blockquote>
      <figcaption className="text-[0.82rem] text-muted mt-1.5">
        {artifact ? (
          <>
            {KIND_WORDS[artifact.kind] ?? artifact.kind}
            {artifact.channel ? ` in #${artifact.channel}` : ""}, {[authors(artifact), when(artifact.ts_iso)].filter(Boolean).join(", ")}
          </>
        ) : (
          alert.artifact_id
        )}
        {synthetic(alert.artifact_id) && (
          <span className="ml-2 px-1.5 py-px border border-muted rounded-sm text-[0.75rem]">synthetic demo message</span>
        )}
      </figcaption>
      {alert.kind === "conflict" && alert.reason && (
        <p className="text-sm mt-2">
          <span className="font-semibold">Why it didn't change the status: </span>
          the authority policy says {alert.reason.replace(/^eid_[0-9a-f]+ /, `${alert.actor_name ?? "the speaker"} `)}.
        </p>
      )}
    </figure>
  );
}

function radiusGraph(alert: Alert, rows: RadiusRow[]): Subgraph {
  const nodes = new Map<string, GraphNode>();
  const subject = alert.subject_id;
  const subjectLabel = isPersonAlert(alert) ? "person" : subject.startsWith("iss:") ? "issue" : "pr";
  nodes.set(subject, { id: subject, label: subjectLabel, name: alert.subject_title, cited: false });
  const label = (r: RadiusRow) => (r.kind === "artifact" ? (r.id.startsWith("pr:") ? "pr" : "message") : r.kind);
  const name = (r: RadiusRow) =>
    r.kind === "artifact" && r.quote ? `"${short(r.quote, 60)}"` : r.kind === "issue" ? `${r.name} (${values(r.detail)})` : (r.name ?? r.id);
  for (const r of rows) {
    if (!nodes.has(r.id)) nodes.set(r.id, { id: r.id, label: label(r), name: name(r), cited: r.why === "evidenced_this_state" });
  }
  const edges = new Map<string, GraphEdge>();
  for (const r of rows) {
    const source = r.via && nodes.has(r.via) ? r.via : subject;
    if (source === r.id) continue;
    const key = `${source}>${r.id}`;
    const rel = WHY_WORDS[r.why] ?? r.why;
    const had = edges.get(key);
    edges.set(key, { source, target: r.id, rel: had ? `${had.rel}; ${rel}` : rel });
  }
  return { nodes: [...nodes.values()], edges: [...edges.values()] };
}

// Earlier decisions of the same kind on similar issues or PRs (PRECEDENT_FOR by similarity, D066).
function Precedents({ detail }: { detail: AlertDetail }) {
  return (
    <section aria-labelledby="h-precedents" className="mt-10">
      <h3 id="h-precedents" className="font-semibold">
        Similar past cases
      </h3>
      <p className="text-sm text-muted mt-0.5 mb-3 max-w-[68ch]">
        The same kind of decision, taken earlier in {detail.alert.product_id} on a similar issue or pull request, matched
        by meaning, not by keywords.
      </p>
      <ul className="space-y-3 max-w-[72ch]">
        {detail.precedents.map((p) => (
          <li key={p.id} className="border-l-2 border-rule pl-3">
            <p>
              <span className="font-serif text-[1.05rem]">{p.subject}</span>
              <span className="text-muted">
                {" "}
                was {values(p.value)}
                {p.decided_by ? ` by ${p.decided_by}` : ""}, {when(new Date(p.ts_ms).toISOString()).split(",")[0]}. Similarity{" "}
                {p.similarity.toFixed(2)}.
              </span>
            </p>
            {p.quote && (
              <p className="font-serif text-[0.98rem] mt-1">
                <mark className="verbatim">{p.quote}</mark>
              </p>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

function RadiusList({ detail }: { detail: AlertDetail }) {
  const { radius } = detail;
  const people = radius.people;
  const customers = radius.rows.filter((r) => r.why === "reported_issue");
  const company = (cid: string) => radius.rows.find((r) => r.why === "reporter_works_at" && r.via === cid)?.name;
  const open = radius.open_issues_same_component;
  if (isPersonAlert(detail.alert)) {
    const areas = radius.rows.filter((r) => r.why === "only_expert");
    const backup = (aid: string) => radius.rows.find((r) => r.why === "could_back_up" && r.via === aid);
    const closeness = (h: number | null | undefined) =>
      h === 1 ? "works with them directly" : h ? `${h} hops away` : "no working tie yet";
    return (
      <dl className="grid gap-x-8 gap-y-4 sm:grid-cols-2 mt-5 text-[0.92rem]">
        <div>
          <dt className="text-muted text-sm">Only they know, and who else comes closest</dt>
          <dd>
            <ul>
              {areas.map((a) => (
                <li key={a.id}>
                  {a.name}
                  <span className="text-muted">
                    , {backup(a.id) ? `next: ${backup(a.id)?.name} (${closeness(backup(a.id)?.hops)})` : "nobody else on record"}
                  </span>
                </li>
              ))}
            </ul>
          </dd>
        </div>
        <div>
          <dt className="text-muted text-sm">People to tell</dt>
          <dd>
            {people
              .filter((p) => p.reasons.includes("manager"))
              .map((p) => (
                <span key={p.id} className="block">
                  {p.name}
                  <span className="text-muted">, {p.role}, their manager</span>
                </span>
              ))}
            <span className="block">
              {detail.alert.subject_title}
              <span className="text-muted">, the key person</span>
            </span>
          </dd>
        </div>
      </dl>
    );
  }
  return (
    <dl className="grid gap-x-8 gap-y-4 sm:grid-cols-3 mt-5 text-[0.92rem]">
      <div>
        <dt className="text-muted text-sm">People to tell</dt>
        <dd>
          {people.length ? (
            <ul>
              {people.map((p) => (
                <li key={p.id}>
                  {p.name}
                  <span className="text-muted">, {p.reasons.map((r) => REASON_WORDS[r] ?? r).join(", ")}</span>
                </li>
              ))}
            </ul>
          ) : (
            "Nobody on record"
          )}
        </dd>
      </div>
      <div>
        <dt className="text-muted text-sm">Customers</dt>
        <dd>
          {customers.length
            ? customers.map((c) => (
                <span key={c.id} className="block">
                  {c.name}
                  {company(c.id) && <span className="text-muted">, {company(c.id)}</span>}
                </span>
              ))
            : "None reported it"}
        </dd>
      </div>
      <div>
        <dt className="text-muted text-sm">Still open in the same component</dt>
        <dd>{open.length ? open.map((i) => <span key={i.id} className="block">{i.name}</span>) : "None"}</dd>
      </div>
    </dl>
  );
}

function Respond({ detail, actions, onDone }: { detail: AlertDetail; actions: Action[]; onDone: () => void }) {
  const { alert, radius } = detail;
  const people = radius.people;
  const lead = people.find((p) => /lead|manager|vp/i.test(p.role ?? "")) ?? people[0];
  const [person, setPerson] = useState(lead?.id ?? "");
  // Signed in (org mode, D100), you respond as yourself: the API takes the person from the
  // sign-in and the picker goes. The demo keeps it, a labeled persona switch.
  const [me, setMe] = useState<Me | null>(null);
  useEffect(() => {
    getMe().then(setMe, () => setMe(null));
  }, []);
  const you = me?.you ?? null;
  const [chosen, setChosen] = useState<Set<string>>(() => new Set(actions.map((a) => a.id)));
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ResponseResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const open = alert.status === "open";
  const changed = useMemo(
    () => Object.fromEntries(Object.entries(edits).filter(([id, text]) => text !== actions.find((a) => a.id === id)?.draft)),
    [edits, actions],
  );

  const go = (verdict: "approve" | "dismiss") => {
    setBusy(true);
    setError(null);
    respond(verdict, {
      alert_id: alert.id,
      ...(you ? {} : { person_id: person }),
      note: note || undefined,
      ...(verdict === "approve" ? { action_ids: [...chosen], edits: changed } : {}),
    })
      .then((r) => {
        setResult(r);
        onDone();
      }, (e: Error) => setError(e.message))
      .finally(() => setBusy(false));
  };

  return (
    <section aria-labelledby="h-drafts" className="mt-12">
      <h3 id="h-drafts" className="font-semibold">
        Drafted follow-ups
      </h3>
      <p className="text-sm text-muted mt-0.5 mb-4 max-w-[68ch]">
        Written from the graph, not by a model: every name, date and status comes from a node, and the only quote is the
        new message, word for word. {open ? "Edit or untick any of them before you approve." : ""}
      </p>
      <ul className="space-y-4 max-w-[72ch]">
        {actions.map((a) => (
          <li key={a.id} className="border-l-2 border-rule pl-3">
            <label className="flex items-center gap-2 text-sm font-semibold">
              {open && (
                <input
                  type="checkbox"
                  checked={chosen.has(a.id)}
                  onChange={(e) => {
                    const next = new Set(chosen);
                    if (e.target.checked) next.add(a.id);
                    else next.delete(a.id);
                    setChosen(next);
                  }}
                />
              )}
              {ACTION_WORDS[a.kind](a)}
              {!open && <span className="font-normal text-muted">, {a.status}</span>}
            </label>
            {open ? (
              <textarea
                value={edits[a.id] ?? a.draft}
                onChange={(e) => setEdits({ ...edits, [a.id]: e.target.value })}
                disabled={!chosen.has(a.id)}
                rows={Math.min(8, Math.max(2, Math.ceil(a.draft.length / 90)))}
                className="mt-1.5 w-full bg-surface border border-rule p-2 text-[0.92rem] leading-snug disabled:opacity-45"
                aria-label={ACTION_WORDS[a.kind](a)}
              />
            ) : (
              <p className="mt-1 text-[0.92rem] whitespace-pre-wrap">{a.draft}</p>
            )}
          </li>
        ))}
      </ul>

      {open && !result && (
        <div className="mt-6 flex flex-wrap items-end gap-x-4 gap-y-3 border-t border-rule pt-4">
          {you ? (
            <p className="text-sm">
              <span className="block text-[0.78rem] text-muted">Responding as</span>
              {you.name ?? you.id}
              {you.role ? `, ${you.role}` : ""}
            </p>
          ) : (
          <div>
            <label htmlFor="as" className="block text-[0.78rem] text-muted">
              Respond as
            </label>
            <select
              id="as"
              value={person}
              onChange={(e) => setPerson(e.target.value)}
              className="bg-transparent border-0 border-b-2 border-ink py-1.5 pr-1"
            >
              {people.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                  {p.role ? `, ${p.role}` : ""}
                </option>
              ))}
            </select>
          </div>
          )}
          <div className="flex-1 min-w-[12rem]">
            <label htmlFor="note" className="block text-[0.78rem] text-muted">
              Note (kept with the decision)
            </label>
            <input
              id="note"
              value={note}
              maxLength={500}
              onChange={(e) => setNote(e.target.value)}
              className="w-full bg-transparent border-0 border-b-2 border-ink py-1.5"
            />
          </div>
          <button
            type="button"
            disabled={busy || !person || !chosen.size}
            onClick={() => go("approve")}
            className="btn btn-ink"
          >
            Approve and send {chosen.size}
          </button>
          <button
            type="button"
            disabled={busy || !person}
            onClick={() => go("dismiss")}
            className="btn btn-line"
          >
            Dismiss
          </button>
        </div>
      )}
      {error && <p className="text-warn mt-3" role="alert">The response wasn't recorded: {error}</p>}
      {result && (
        <div className="mt-6 border-l-4 border-link bg-link-soft px-4 py-3 max-w-[68ch]" aria-live="polite">
          <p className="font-semibold">{result.status === "approved" ? "Approved" : "Dismissed"}, and kept as a decision</p>
          <p className="mt-0.5">{result.decision.statement}</p>
          <p className="text-sm text-muted mt-1">
            It links to who decided, the message that triggered it, and the change that caused it, so the next alert like this
            one can show what was done.
          </p>
        </div>
      )}
    </section>
  );
}
