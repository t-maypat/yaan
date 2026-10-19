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
"""

import hashlib
import hmac
import json
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import get_settings

TOKEN_BYTES = 32


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
    path = tokens_path()
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _write(tokens: dict[str, dict[str, Any]]) -> None:
    path = tokens_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(tokens, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def issue(person_id: str, label: str = "") -> str:
    """A new token for `person_id`; only its hash is kept. Shown once."""
    token = "yaan_" + secrets.token_urlsafe(TOKEN_BYTES)
    tokens = _read()
    tokens[_digest(token)] = {
        "person": person_id,
        "label": label,
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    _write(tokens)
    return token


def revoke(person_id: str) -> int:
    """Remove every token of `person_id`; returns how many."""
    tokens = _read()
    keep = {h: t for h, t in tokens.items() if t["person"] != person_id}
    _write(keep)
    return len(tokens) - len(keep)


def principal(headers: Any) -> str | None:
    """The person behind a request (an eid_), None in mode `none`, or AuthError."""
    m = mode()
    if m == "none":
        return None
    if m == "token":
        given = headers.get("authorization") or ""
        scheme, _, token = given.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise AuthError("sign in: send 'Authorization: Bearer <token>' (lore token issue)")
        digest = _digest(token.strip())
        for known, entry in _read().items():
            if hmac.compare_digest(known, digest):
                return entry["person"]
        raise AuthError("unknown or revoked token")
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
                return eid
        raise AuthError(f"{email} is not in people.yaml")
    raise AuthError(f"unknown LORE_AUTH mode {m!r}")
