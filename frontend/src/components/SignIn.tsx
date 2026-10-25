import { type FormEvent, useEffect, useState } from "react";
import { SIGN_IN_EVENT, signIn, signOut } from "../api";

// Org mode signs people in (D100): when an API call answers 401, ask for the token that
// `yaan token issue "<name>"` printed, trade it for a session cookie this page can't read
// (D111), and reload so every view asks again as that person. A sign-in proxy never gets
// here: it sends the identity itself.
export function SignIn() {
  const [needed, setNeeded] = useState(false);
  const [draft, setDraft] = useState("");
  const [refused, setRefused] = useState<string | null>(null);

  useEffect(() => {
    const on = () => setNeeded(true);
    window.addEventListener(SIGN_IN_EVENT, on);
    return () => window.removeEventListener(SIGN_IN_EVENT, on);
  }, []);

  if (!needed) return null;
  const save = async (e: FormEvent) => {
    e.preventDefault();
    const answer = await signIn(draft.trim());
    if (answer === "ok") window.location.reload();
    else setRefused(answer);
  };
  return (
    <div role="dialog" aria-modal="true" aria-labelledby="sign-in-title" className="fixed inset-0 z-50 grid place-items-center bg-ink/40 px-4">
      <form onSubmit={save} className="w-full max-w-md bg-paper border border-rule p-6 shadow-xl">
        <h2 id="sign-in-title" className="font-serif text-xl">
          Sign in to Yaan
        </h2>
        <p className="text-sm text-muted mt-2">
          This Yaan holds your team's own data, so it answers as you and leaves out what you can't see.
          {refused ? ` That token was refused: ${refused}` : ""}
        </p>
        <label htmlFor="token" className="block text-[0.78rem] text-muted mt-4">
          Your token, from <code>yaan token issue "Your Name"</code>
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
          {refused && (
            <button type="button" className="btn btn-line" onClick={() => void signOut().then(() => window.location.reload())}>
              Sign out
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
