"""Dev/test product split and the held-out guard. Built in Phase 2.

Writes eval/split.json (seed, dev, test). Loading questions for a test product raises
unless the logged final held-out run is active. Spec: docs/CONTEXT.md §6.5.

Dev products are picked by a fixed rule (D017, task H6): rank all 30 products on change
signals counted from PRs and Slack only, and take the top 3. `rank_products` reads files
through herb_loader.read_artifacts, so no question is ever loaded for the ranking.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.ingest.herb_fetch import HERB_REVISION, herb_dir, product_files
from app.ingest.herb_loader import read_artifacts

DEV_COUNT = 3
SIGNALS = ("pr_closed_unmerged", "pr_changes_requested", "pr_revert", "slack_change")
CHANGE_WORDS = (
    "revert",
    "roll back",
    "rollback",
    "rolled back",
    "on hold",
    "reopen",
    "re-open",
    "postpone",
    "delay",
    "dismiss",
)
RULE = (
    "D017: drop questions/team/customers on load; count pr_closed_unmerged (state=closed, "
    "merged=False), pr_changes_requested (review state CHANGES_REQUESTED), pr_revert ('revert' "
    "in PR title or summary), slack_change (message contains any CHANGE_WORDS); scale each "
    "signal by its max over all products, sum, take the top 3 (ties by name)."
)


def product_signals(artifacts: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    """Counts of the four D017 change signals in one product's artifacts."""
    prs = artifacts["prs"]
    return {
        "pr_closed_unmerged": sum(
            pr["state"] == "closed" and pr["merged"] == "False" for pr in prs
        ),
        "pr_changes_requested": sum(
            review["state"] == "CHANGES_REQUESTED" for pr in prs for review in pr["reviews"]
        ),
        "pr_revert": sum("revert" in f"{pr['title']} {pr['summary']}".lower() for pr in prs),
        "slack_change": sum(
            any(word in msg["Message"]["User"]["text"].lower() for word in CHANGE_WORDS)
            for msg in artifacts["slack"]
        ),
    }


def rank_products(herb: Path | None = None) -> list[dict[str, Any]]:
    """Every product with its signal counts and score, best first (D017)."""
    rows = [
        {"product": path.stem, **product_signals(read_artifacts(path))}
        for path in product_files(herb or herb_dir())
    ]
    peaks = {s: max(row[s] for row in rows) or 1 for s in SIGNALS}
    for row in rows:
        row["score"] = round(sum(row[s] / peaks[s] for s in SIGNALS), 4)
    rows.sort(key=lambda row: (-row["score"], row["product"]))
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
    return rows


def write_ranking(rows: list[dict[str, Any]], path: Path | None = None) -> Path:
    path = path or get_settings().eval_dir / "dev_ranking.json"
    record = {
        "herb_revision": HERB_REVISION,
        "rule": RULE,
        "change_words": list(CHANGE_WORDS),
        "dev": [row["product"] for row in rows[:DEV_COUNT]],
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "products": rows,
    }
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n")
    return path
