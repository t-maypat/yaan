"""Dev/test product split and the held-out guard. Built in Phase 2.

Writes eval/split.json (seed, dev, test, rest). Loading questions for a test product raises
unless the logged final held-out run is active. Spec: docs/CONTEXT.md §6.5.

Dev products are picked by a fixed rule (D017, task H6): rank all 30 products on change
signals counted from PRs and Slack only, and take the top 3. `rank_products` reads files
through herb_loader.read_artifacts, so no question is ever loaded for the ranking.
"""

import json
import random
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.ingest.herb_fetch import HERB_REVISION, herb_dir, product_files
from app.ingest.herb_loader import QUESTION_KEYS, read_artifacts

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


# --- 2.3 The split (D025) -------------------------------------------------------------
TEST_COUNT = 7
# Taken from the pinned dataset commit, so nobody picks the seed and it can't be re-rolled
# for a friendlier test set.
SEED = int(HERB_REVISION[:8], 16)


def eval_path(name: str) -> Path:
    return get_settings().eval_dir / name


def draw_split(products: list[str], dev: list[str], seed: int = SEED) -> dict[str, Any]:
    """Dev as given; TEST_COUNT test products drawn with `seed` from the rest (sorted first)."""
    if not set(dev) <= set(products):
        raise ValueError(f"dev products {sorted(set(dev) - set(products))} are not HERB products")
    others = sorted(set(products) - set(dev))
    test = sorted(random.Random(seed).sample(others, TEST_COUNT))
    return {
        "seed": seed,
        "dev": list(dev),
        "test": test,
        "rest": [p for p in others if p not in test],
        "herb_revision": HERB_REVISION,
    }


def make_split(path: Path | None = None, herb: Path | None = None) -> tuple[dict[str, Any], bool]:
    """Write split.json once; later calls only check it. Returns (split, newly_written)."""
    path = path or eval_path("split.json")
    ranking = json.loads((path.parent / "dev_ranking.json").read_text(encoding="utf-8"))
    drawn = draw_split([f.stem for f in product_files(herb or herb_dir())], ranking["dev"])
    if path.exists():
        fixed = json.loads(path.read_text(encoding="utf-8"))
        if {k: fixed.get(k) for k in drawn} != drawn:
            raise RuntimeError(
                f"{path} differs from the draw for seed {SEED}: the split is fixed (D025). "
                "Find out what changed instead of rewriting it."
            )
        return fixed, False
    record = {**drawn, "created": datetime.now(UTC).isoformat(timespec="seconds")}
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n")
    return record, True


def read_split(path: Path | None = None) -> dict[str, Any]:
    path = path or eval_path("split.json")
    if not path.exists():
        raise FileNotFoundError(f"{path} is missing: run `lore split` first")
    return json.loads(path.read_text(encoding="utf-8"))


# --- 2.4 The held-out guard ---------------------------------------------------------------
class HeldOutError(PermissionError):
    """Questions of a product that isn't open to us right now."""


_HELDOUT_ACTIVE: ContextVar[bool] = ContextVar("heldout_active", default=False)


@contextmanager
def heldout_run(system: str, note: str = "", log: Path | None = None) -> Iterator[None]:
    """The final held-out run: the only place test products' questions load.

    Appends a line to eval/heldout_runs.log *before* anything is read, so every opening of
    the test set leaves a committed trace (CONTEXT §6.5; Phase 13).
    """
    log = log or eval_path("heldout_runs.log")
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    with log.open("a", encoding="utf-8", newline="\n") as f:
        f.write(f"{stamp}\tsystem={system}\t{note}\n")
    token = _HELDOUT_ACTIVE.set(True)
    try:
        yield
    finally:
        _HELDOUT_ACTIVE.reset(token)


def load_questions(
    product: str, herb: Path | None = None, split_path: Path | None = None
) -> dict[str, list[Any]]:
    """A product's answerable and unanswerable questions, if the split allows reading them.

    Dev: always. Test: only inside `heldout_run`. Rest (neither): never.
    """
    split = read_split(split_path)
    if product in split["test"]:
        if not _HELDOUT_ACTIVE.get():
            raise HeldOutError(
                f"{product} is a held-out test product: its questions load only inside "
                "heldout_run(), the logged final run (Phase 13)."
            )
    elif product not in split["dev"]:
        raise HeldOutError(f"{product} is neither a dev nor a test product in the split")
    path = (herb or herb_dir()) / "products" / f"{product}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {key: raw[key] for key in QUESTION_KEYS}
