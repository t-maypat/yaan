"""Who is asking (D100). The demo serves anyone; org mode serves a team's own data, so every
read and write names a person, and viewer and approver come from that person, never from the
request body.

Modes (`LORE_AUTH`, default `auto` = `token` in org mode, `none` in the demo):
- `none`: no principal. Demo only: "view as" is a labeled persona switch over synthetic data.
- `token`: `Authorization: Bearer <token>`. `lore token issue <person>` mints a token for a
  people.yaml entry and stores only its SHA-256 (data/tokens.json); `lore token revoke` removes
  a person's tokens.
- `header`: a single-sign-on proxy (oauth2-proxy, Cloudflare Access ...) in front of Lore sets
  the user's email in `LORE_AUTH_HEADER` and `X-Lore-Proxy-Secret` to `LORE_AUTH_PROXY_SECRET`.
  Without the secret, anyone who reached Lore around the proxy could name themselves.

The web UI never keeps the token where page scripts can read it (H2, D111): it trades the token
once for a session cookie (`POST /auth/session`), HttpOnly and SameSite=Strict, signed with a
key in data/ and naming the token it came from, so revoking or expiring the token ends the
session too. A request the cookie authenticates must also carry `X-Yaan-CSRF` when it changes
anything: a page on another site can't add that header without a CORS preflight Yaan refuses.
API and MCP clients keep sending the bearer token. Tokens expire (90 days by default) and
remember when they were last used.

tokens.json is read on every request, so it is cached until the file changes, and every change
is a locked read-modify-write (app/filestate.py, D123): concurrent requests can't collide on a
temp file or corrupt it, and a `last_used` stamp can't put back a token revoked meanwhile.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app import filestate
from app.config import get_settings

TOKEN_BYTES = 32
TOKEN_DAYS = 90  # a token's default lifetime: `lore token issue --days` changes it
SESSION_DAYS = 30  # a browser session's longest life; never past its token's expiry
SESSION_COOKIE = "yaan_session"
CSRF_HEADER = "x-yaan-csrf"
LAST_USED_EVERY_S = 3600  # last_used is written at most this often per token
STAMP_WAIT_S = 0.5  # how long a request waits for the file to stamp last_used, then skips it
_touched: dict[str, float] = {}  # token digest -> when last_used was last written


class AuthError(Exception):
    """No valid principal: 401, with what to do."""


def mode() -> str:
    s = get_settings()
    if s.lore_auth != "auto":
        return s.lore_auth
    return "token" if s.lore_data_mode == "org" else "none"


def tokens_path() -> Path:
    return get_settings().data_dir / "tokens.json"


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _read() -> dict[str, dict[str, Any]]:
    """The token table, read-only (cached until the file changes): change it through
    `filestate.update`, never by editing this dict."""
    return filestate.cached(tokens_path(), {})


def _update(timeout: float = filestate.LOCK_TIMEOUT_S):
    return filestate.update(tokens_path(), timeout=timeout, sort_keys=True)


def now() -> datetime:
    return datetime.now(UTC)


def stamp(t: datetime) -> str:
    return t.isoformat(timespec="seconds")


def issue(person_id: str, label: str = "", days: int = TOKEN_DAYS) -> str:
    """A new token for `person_id`, valid for `days`; only its hash is kept. Shown once."""
    if days < 1:
        raise ValueError(f"a token lives at least a day, not {days}")
    token = "yaan_" + secrets.token_urlsafe(TOKEN_BYTES)
    with _update() as tokens:
        tokens[_digest(token)] = {
            "person": person_id,
            "label": label,
            "created": stamp(now()),
            "expires": stamp(now() + timedelta(days=days)),
        }
    return token


def listing() -> list[dict[str, Any]]:
    """Every token, without its secret: a short id (for revoke --id), whose, when."""
    return [
        {"id": digest[:12], **{k: entry.get(k) for k in ("person", "label", "created", "expires")}}
        | {"last_used": entry.get("last_used")}
        for digest, entry in sorted(_read().items(), key=lambda kv: kv[1].get("created", ""))
    ]


def revoke(person_id: str) -> int:
    """Remove every token of `person_id`; returns how many."""
    with _update() as tokens:
        gone = [h for h, t in tokens.items() if t["person"] == person_id]
        for h in gone:
            del tokens[h]
    return len(gone)


def revoke_id(short: str) -> int:
    """Remove the one token whose id (from `listing`) starts with `short`."""
    with _update() as tokens:
        hits = [h for h in tokens if h.startswith(short)] if len(short) >= 6 else []
        if len(hits) != 1:
            raise AuthError(
                f"{short!r} names {len(hits)} tokens: give at least 6 characters of one id"
            )
        del tokens[hits[0]]
    return 1


def expired(entry: dict[str, Any], at: datetime | None = None) -> bool:
    """Tokens issued before expiry existed (no `expires`) count as valid until revoked."""
    ends = entry.get("expires")
    return bool(ends) and datetime.fromisoformat(ends) <= (at or now())


def check_token(token: str) -> tuple[str, dict[str, Any]]:
    """(digest, entry) of a valid token, or AuthError. The lookup is by the token's SHA-256,
    which tells an attacker nothing about other tokens."""
    digest = _digest(token.strip())
    entry = _read().get(digest)
    if entry is None:
        raise AuthError("unknown or revoked token")
    if expired(entry):
        raise AuthError("this token has expired: ask for a new one (lore token issue)")
    if time.monotonic() - _touched.get(digest, -LAST_USED_EVERY_S) >= LAST_USED_EVERY_S:
        _touched[digest] = time.monotonic()
        stamp_used(digest)
    return digest, entry


def stamp_used(digest: str, timeout: float = STAMP_WAIT_S) -> None:
    """Record a token's use. Bookkeeping, so it never fails the request: a busy file skips it
    (the next request tries again), and a token revoked meanwhile stays revoked."""
    try:
        with _update(timeout) as tokens:
            if digest in tokens:
                tokens[digest]["last_used"] = stamp(now())
    except filestate.Busy:
        _touched.pop(digest, None)


# --- browser sessions --------------------------------------------------------------------------
_keys: dict[str, bytes] = {}


def _key() -> bytes:
    """The session-signing key, made once per install (data/session.key), read once."""
    path = get_settings().data_dir / "session.key"
    if str(path) not in _keys:
        with filestate.locked(path):  # two processes or threads making it: the first one's stays
            if not path.is_file():
                tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
                tmp.write_bytes(secrets.token_bytes(32))
                filestate.replace(tmp, path)
        _keys[str(path)] = path.read_bytes()
    return _keys[str(path)]


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def open_session(token: str) -> tuple[str, str, int]:
    """Trade a valid token for (cookie value, person, max-age seconds)."""
    digest, entry = check_token(token)
    ends = now() + timedelta(days=SESSION_DAYS)
    if entry.get("expires"):
        ends = min(ends, datetime.fromisoformat(entry["expires"]))
    body = json.dumps({"p": entry["person"], "t": digest, "e": int(ends.timestamp())})
    payload = _b64(body.encode("utf-8"))
    sig = _b64(hmac.new(_key(), payload.encode("ascii"), hashlib.sha256).digest())
    return f"{payload}.{sig}", entry["person"], max(1, int((ends - now()).total_seconds()))


def session_person(cookie: str) -> str:
    """The person a session cookie names, or AuthError: a bad signature, an ended session, or
    a token since revoked or expired."""
    payload, _, sig = cookie.partition(".")
    try:  # anything a client sends: odd bytes or a mangled body are a refusal, never a 500
        want = _b64(hmac.new(_key(), payload.encode("ascii"), hashlib.sha256).digest())
        if not sig or not hmac.compare_digest(sig, want):
            raise ValueError("bad signature")
        body = json.loads(_unb64(payload))
        body["e"], body["t"]
    except (ValueError, TypeError, KeyError) as e:
        raise AuthError("sign in again: this session isn't valid") from e
    if body["e"] <= now().timestamp():
        raise AuthError("sign in again: this session has ended")
    entry = _read().get(body["t"])
    if entry is None or expired(entry):
        raise AuthError("sign in again: the token behind this session was revoked or expired")
    return entry["person"]


def principal(headers: Any, cookies: Any = None) -> str | None:
    """The person behind a request (an eid_), None in mode `none`, or AuthError."""
    return identify(headers, cookies)[0]


def identify(headers: Any, cookies: Any = None) -> tuple[str | None, str | None]:
    """(person, how): how is `bearer`, `cookie` (needs X-Yaan-CSRF to change anything),
    `header` (the sign-in proxy) or None in mode `none`."""
    m = mode()
    if m == "none":
        return None, None
    if m == "token":
        given = headers.get("authorization") or ""
        scheme, _, token = given.partition(" ")
        if scheme.lower() == "bearer" and token.strip():
            return check_token(token)[1]["person"], "bearer"
        session = (cookies or {}).get(SESSION_COOKIE)
        if session:
            return session_person(session), "cookie"
        raise AuthError("sign in: send 'Authorization: Bearer <token>' (lore token issue)")
    if m == "header":
        s = get_settings()
        if not s.lore_auth_proxy_secret:
            raise AuthError("LORE_AUTH=header needs LORE_AUTH_PROXY_SECRET, sent by the proxy")
        sent = (headers.get("x-lore-proxy-secret") or "").encode("utf-8")
        if not hmac.compare_digest(sent, s.lore_auth_proxy_secret.encode("utf-8")):
            raise AuthError("identity headers are accepted only from the sign-in proxy")
        email = (headers.get(s.lore_auth_header.lower()) or "").strip().casefold()
        if not email:
            raise AuthError(f"the sign-in proxy sent no {s.lore_auth_header} header")
        from app import org

        for eid, person in org.roster().items():
            if (person.get("email") or "").casefold() == email:
                return eid, "header"
        raise AuthError(f"{email} is not in people.yaml")
    raise AuthError(f"unknown LORE_AUTH mode {m!r}")
