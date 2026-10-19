import { type FormEvent, useEffect, useState } from "react";
import { getToken, SIGN_IN_EVENT, setToken } from "../api";

// Org mode signs people in (D100): when an API call answers 401, ask for the token that
// `lore token issue "<name>"` printed, keep it in this browser, and reload so every view
// asks again as that person. A sign-in proxy never gets here: it sends the identity itself.
export function SignIn() {
  const [needed, setNeeded] = useState(false);
  const [draft, setDraft] = useState("");
  const rejected = needed && getToken() !== null;

  useEffect(() => {
    const on = () => setNeeded(true);
    window.addEventListener(SIGN_IN_EVENT, on);
    return () => window.removeEventListener(SIGN_IN_EVENT, on);
  }, []);

  if (!needed) return null;
  const save = (e: FormEvent) => {
    e.preventDefault();
    setToken(draft.trim());
    window.location.reload();
  };
  return (
    <div role="dialog" aria-modal="true" aria-labelledby="sign-in-title" className="fixed inset-0 z-50 grid place-items-center bg-ink/40 px-4">
      <form onSubmit={save} className="w-full max-w-md bg-paper border border-rule p-6 shadow-xl">
        <h2 id="sign-in-title" className="font-serif text-xl">
          Sign in to Yaan
        </h2>
        <p className="text-sm text-muted mt-2">
          This Yaan holds your team's own data, so it answers as you and leaves out what you can't see.
          {rejected ? " The token this browser had was refused: it may have been revoked." : ""}
        </p>
        <label htmlFor="token" className="block text-[0.78rem] text-muted mt-4">
          Your token, from <code>lore token issue "Your Name"</code>
        </label>
        <input
          id="token"
          type="password"
          autoComplete="off"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          className="w-full bg-transparent border-0 border-b-2 border-ink py-1.5 font-mono text-sm"
          placeholder="yaan_…"
        />
        <div className="mt-5 flex justify-end gap-3">
          {rejected && (
            <button type="button" className="btn btn-line" onClick={() => (setToken(null), window.location.reload())}>
              Forget it
            </button>
          )}
          <button type="submit" disabled={!draft.trim()} className="btn btn-ink">
            Sign in
          </button>
        </div>
      </form>
    </div>
  );
}
