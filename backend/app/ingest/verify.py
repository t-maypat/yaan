"""S6 quote verification (Phase 7).

An event is kept only if its quote is a verbatim substring, ignoring case and runs of
whitespace (llm.normalize), of the message it is pinned to. A quote found in another message
of the same unit is re-pinned there (logged as `moved`). Everything else is dropped with a
reason, and the pass rate is reported (target >= 95%, rethink below 90%).
Char offsets are stored against the original message text.
Spec: docs/CONTEXT.md §9.3 (S6).
"""

import re
from typing import TYPE_CHECKING, Any

from app.llm import normalize

if TYPE_CHECKING:
    from app.ingest.extract import Unit

MAX_QUOTE = 400  # the prompt asks for <= 300; allow some slack before calling it a paste


def offsets(quote: str, text: str) -> tuple[int, int] | None:
    """Start and end of `quote` in `text`, matching case-insensitively across whitespace."""
    words = quote.split()
    if not words:
        return None
    pattern = r"\s+".join(re.escape(w) for w in words)
    m = re.search(pattern, text, flags=re.IGNORECASE)
    return (m.start(), m.end()) if m else None


def check(event: dict[str, Any], unit: "Unit") -> tuple[bool, dict[str, Any]]:
    """Verify one extracted event against its unit. Returns (kept?, row with provenance)."""
    quote = (event.get("quote") or "").strip().strip('"').strip()
    row = dict(event) | {
        "quote": quote,
        "unit_id": unit.id,
        "product": unit.product,
        "channel": unit.channel,
    }
    if not quote:
        return False, row | {"drop": "empty quote"}
    if len(quote) > MAX_QUOTE:
        return False, row | {"drop": f"quote longer than {MAX_QUOTE} chars"}
    idx = event.get("msg")
    order = list(range(len(unit.messages)))
    if isinstance(idx, int) and 0 <= idx < len(unit.messages):
        order.remove(idx)
        order.insert(0, idx)
    for i in order:
        msg = unit.messages[i]
        if normalize(quote) in normalize(msg.text):
            span = offsets(quote, msg.text)
            return True, row | {
                "msg": i,
                "moved": i != idx,
                "artifact_id": msg.id,
                "ts_ms": msg.ts_ms,
                "ts_iso": msg.ts_iso,
                "speaker": msg.author_eid,
                "start": span[0] if span else None,
                "end": span[1] if span else None,
            }
    return False, row | {"drop": "quote not found in the unit"}
