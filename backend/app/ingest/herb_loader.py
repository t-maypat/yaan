"""Raw HERB files (data/herb) -> typed records. Built in Phase 2.

Handles both Slack shapes (CONTEXT §6.2). Drops the product files' team / customers
fields, which are oracle-only (§6.3). Verified field names: docs/herb_format.md.

read_artifacts (2.1, used by the dev ranking) is the only way product files are read for
artifacts: it drops the question lists and the oracle-only fields the moment a file is
parsed, so nothing downstream can see them. Questions are loaded only through
app.eval.split, which guards held-out products (2.4).
"""

import json
from pathlib import Path
from typing import Any

ORACLE_KEYS = ("team", "customers")  # CONTEXT §6.3: never used to build or answer
QUESTION_KEYS = ("answerable_questions", "unanswerable_questions")
ARTIFACT_KEYS = ("slack", "documents", "meeting_transcripts", "meeting_chats", "urls", "prs")


def read_artifacts(path: Path) -> dict[str, list[dict[str, Any]]]:
    """A product file's artifact lists only; questions and oracle-only fields are dropped."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    for key in ORACLE_KEYS + QUESTION_KEYS:
        raw.pop(key, None)
    unexpected = set(raw) - set(ARTIFACT_KEYS)
    if unexpected:
        raise ValueError(f"{path.name}: unexpected top-level keys {sorted(unexpected)}")
    return {key: raw.get(key, []) for key in ARTIFACT_KEYS}
