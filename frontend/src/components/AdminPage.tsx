import { useCallback, useEffect, useState } from "react";
import { type AdminOverview, type AuditLine, getAdmin, getAudit, retryDead, revokeToken } from "../api";

// Admin (21.7, D133): what someone running Yaan checks, for people.yaml's `admin: true` (everyone
// in the demo, which has no sign-in). The same facts as `yaan connectors status | dead`,
// `yaan token list` and `yaan people`; issuing a token stays in the CLI, where it is shown once.

const usd = (n: number | null | undefined) => (n == null ? "none" : `$${n.toFixed(2)}`);
const day = (iso: string | null | undefined) => (iso ? iso.slice(0, 16).replace("T", " ") : "never");

export function AdminPage() {
  const [data, setData] = useState<AdminOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [said, setSaid] = useState<string | null>(null);
  const load = useCallback(() => {
    getAdmin().then(setData, (e: Error) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  const act = async (key: string, work: () => Promise<string>) => {
    setBusy(key);
    setSaid(null);
    try {
      setSaid(await work());
      load();
    } catch (e) {
      setSaid((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  if (error)
    return (
      <p className="mt-10 border-l-4 border-warn bg-warn-soft px-4 py-3 max-w-[68ch]" role="alert">
        The admin page failed: {error}
      </p>
    );
  if (!data) return <p className="mt-10 text-muted">Reading Yaan's state…</p>;

  const { worker, spend, storage } = data;
  const memory = storage.error
    ? "unknown"
    : storage.cap_mb
      ? `${storage.used_mb} / ${storage.cap_mb} MB`
      : `${storage.used_mb} MB, no cap`;
  const disk = storage.error
    ? storage.error
    : `${storage.aof ? "append-only file + snapshots" : "snapshots"}; last saved ${day(storage.last_save_utc)}${storage.last_save_ok ? "" : ", the last save FAILED"}`;
  const people = Object.entries(spend.by_person);
  const snippet = data.unlisted.length
    ? "people:\n" +
      data.unlisted
        .map((p) => {
          const lines = [`  - name: ${p.name}`, `    id: ${p.id}`];
          if (p.email) lines.push(`    email: ${p.email}`);
          for (const [tool, account] of Object.entries(p.accounts ?? {})) lines.push(`    accounts: {${tool}: ${account}}`);
          return lines.join("\n");
        })
        .join("\n")
    : "";

  return (
    <section className="pt-10 max-w-[64rem]" aria-labelledby="h-admin">
      <h1 id="h-admin" className="font-serif text-[2rem] leading-tight">
        Admin
      </h1>
      <p className="text-muted mt-2 max-w-[62ch]">
        The connector worker, items it couldn't ingest, sign-in tokens, today's model spend and the people Yaan met
        but <span className="font-mono text-[0.9em]">people.yaml</span> doesn't list.
      </p>
      {said && (
        <p className="mt-4 border-l-4 border-rule bg-surface px-4 py-2 max-w-[68ch]" role="status">
          {said}
        </p>
      )}

      <dl className="grid gap-px bg-rule border border-rule mt-6 sm:grid-cols-2 lg:grid-cols-3">
        <Tile label="Connector worker" value={worker.status} note={worker.why ?? worker.warning ?? "every round on time"} />
        <Tile label="Spent today" value={`${usd(spend.spent_usd)} / ${usd(spend.cap_usd)}`} note={`answers ${usd(spend.answers_usd)} of ${usd(spend.answer_cap_usd)}`} />
        <Tile label="Dead letters" value={String(data.dead_letters.length)} note="items a connector couldn't ingest" />
        <Tile label="Graph memory" value={memory} note={storage.warning ?? disk} />
      </dl>

      <h2 className="font-semibold mt-10 mb-2">Dead letters</h2>
      {data.dead_letters.length ? (
        <>
          <table className="w-full text-[0.92rem]">
            <thead>
              <tr className="border-b border-ink text-left">
                <th className="font-medium py-1">Item</th>
                <th className="font-medium py-1">Why</th>
                <th className="font-medium py-1 text-right">Tries</th>
                <th className="py-1" />
              </tr>
            </thead>
            <tbody>
              {data.dead_letters.map((d) => (
                <tr key={d.id} className="border-b border-rule align-top">
                  <td className="py-1 font-mono text-[0.85em] break-all">{d.artifact_id ?? d.id}</td>
                  <td className="py-1">{d.why}</td>
                  <td className="py-1 text-right">{d.attempts ?? 1}</td>
                  <td className="py-1 text-right">
                    <button type="button" className="btn btn-line btn-sm" disabled={!!busy}
                      onClick={() => act(d.id, async () => { const r = await retryDead(d.id); return `${r.ingested.length} in, ${r.failed.length} failed again`; })}>
                      Retry
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <button type="button" className="btn btn-line btn-sm mt-3" disabled={!!busy}
            onClick={() => act("all", async () => { const r = await retryDead(); return `${r.ingested.length} in, ${r.failed.length} failed again`; })}>
            Retry all
          </button>
        </>
      ) : (
        <p className="text-muted">None: every item the connectors brought is in the graph.</p>
      )}

      <h2 className="font-semibold mt-10 mb-2">Sign-in tokens</h2>
      {data.tokens.length ? (
        <table className="w-full text-[0.92rem]">
          <thead>
            <tr className="border-b border-ink text-left">
              <th className="font-medium py-1">Person</th>
              <th className="font-medium py-1">Label</th>
              <th className="font-medium py-1">Expires</th>
              <th className="font-medium py-1">Last used</th>
              <th className="py-1" />
            </tr>
          </thead>
          <tbody>
            {data.tokens.map((t) => (
              <tr key={t.id} className="border-b border-rule">
                <td className="py-1 font-mono text-[0.85em]">{t.person}</td>
                <td className="py-1">{t.label || "–"}</td>
                <td className="py-1">{day(t.expires)}</td>
                <td className="py-1">{day(t.last_used)}</td>
                <td className="py-1 text-right">
                  <button type="button" className="btn btn-line btn-sm" disabled={!!busy}
                    onClick={() => act(t.id, async () => `revoked ${(await revokeToken(t.id)).revoked}`)}>
                    Revoke
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="text-muted">
          None. Issue one with <span className="font-mono text-[0.9em]">yaan token issue "&lt;name&gt;"</span>.
        </p>
      )}

      <h2 className="font-semibold mt-10 mb-2">Answers by person today</h2>
      {people.length ? (
        <ul className="text-[0.92rem]">
          {people.map(([who, spent]) => (
            <li key={who} className="flex gap-4 border-b border-rule py-1">
              <span className="font-mono text-[0.85em]">{who}</span>
              <span className="ml-auto">{usd(spent)} {spend.person_cap_usd != null && `of ${usd(spend.person_cap_usd)}`}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-muted">No answers charged to a person today.</p>
      )}

      <h2 className="font-semibold mt-10 mb-2">Met in your tools, not in people.yaml</h2>
      {snippet ? (
        <>
          <p className="text-muted max-w-[62ch]">
            Paste into <span className="font-mono text-[0.9em]">people.yaml</span> and give each a role (and an email to sign in
            with); the id keeps their items theirs.
          </p>
          <pre className="card card-pad mt-2 text-[0.85rem] overflow-x-auto">{snippet}</pre>
        </>
      ) : (
        <p className="text-muted">Nobody: everyone Yaan met is listed (or this is the demo).</p>
      )}

      <AuditLog />
    </section>
  );
}

// The access audit log (22.5): item ids only, never words; `yaan audit` reads the same lines.
function AuditLog() {
  const [person, setPerson] = useState("");
  const [lines, setLines] = useState<AuditLine[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback((who: string) => {
    getAudit(who.trim() || undefined).then((r) => { setLines(r.lines); setError(null); }, (e: Error) => setError(e.message));
  }, []);
  useEffect(() => load(""), [load]);

  return (
    <>
      <h2 className="font-semibold mt-10 mb-2">Who was shown what</h2>
      <form className="flex gap-2 mb-3 max-w-lg" onSubmit={(e) => { e.preventDefault(); load(person); }}>
        <input className="flex-1 min-w-0 bg-transparent border-0 border-b-2 border-ink py-1 font-mono text-sm" placeholder="a person's eid_ id (empty: everyone)" value={person}
          onChange={(e) => setPerson(e.target.value)} aria-label="Filter by person" />
        <button type="submit" className="btn btn-line btn-sm">Show</button>
      </form>
      {error ? (
        <p className="text-muted">The audit log failed: {error}</p>
      ) : !lines ? (
        <p className="text-muted">Reading the audit log…</p>
      ) : lines.length ? (
        <div className="overflow-x-auto">
          <table className="w-full text-[0.92rem]">
            <thead>
              <tr className="border-b border-ink text-left">
                <th className="font-medium py-1">When (UTC)</th>
                <th className="font-medium py-1">Who</th>
                <th className="font-medium py-1">Through</th>
                <th className="font-medium py-1">What</th>
                <th className="font-medium py-1 text-right">Items shown</th>
              </tr>
            </thead>
            <tbody>
              {lines.map((l, i) => (
                <tr key={`${l.ts}-${i}`} className="border-b border-rule align-top">
                  <td className="py-1 whitespace-nowrap">{day(l.ts)}</td>
                  <td className="py-1 font-mono text-[0.85em]">{l.viewer ?? "–"}</td>
                  <td className="py-1">{l.via}</td>
                  <td className="py-1 font-mono text-[0.85em] break-all">{l.what}{l.abstained ? " (abstained)" : ""}</td>
                  <td className="py-1 text-right" title={l.ids.slice(0, 20).join("\n")}>{l.ids.length + (l.ids_more ?? 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-muted">Nothing recorded yet.</p>
      )}
    </>
  );
}

function Tile({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <div className="bg-surface px-4 py-3">
      <dt className="text-sm text-muted">{label}</dt>
      <dd className="text-[1.6rem] font-semibold leading-tight mt-0.5">{value}</dd>
      <dd className="text-[0.8rem] text-muted mt-0.5">{note}</dd>
    </div>
  );
}
