"""Confidence intervals for the eval's headline numbers, from runs already judged ($0, D103).

Each run's judged.jsonl has one verdict per question (its category and score, or for an
unanswerable question whether it was declined). The questions are resampled with replacement
within each category, so every resample keeps the run's mix, and the headline numbers are
recomputed exactly as report.summarize does: Avg = the mean of the four category means
(content on its 0-100 scale, the F1 categories x 100), unanswerable = the share declined.

Two systems run on the same questions are compared *paired*: one resample of question ids
scores both, so the interval is for the difference itself (what the graph tools are worth),
not two overlapping intervals.

Only ids, categories, scores and the declined flag are read; no question, answer or judge
text, so held-out runs can be summarized without being read (rule 3).

    uv run --project backend python -m app.eval.bootstrap
"""

import json
import random
import sys
from pathlib import Path
from typing import Any

from app.config import get_settings

CATEGORIES = ("content", "person", "company", "url")
RESAMPLES = 10_000
SEED = 17
# (label, dev run, held-out run): the runs the report's tables come from
SYSTEMS = (
    ("lore", "20261017T1729Z_lore_dev", "20261018T1301Z_lore_test"),
    ("lore-search", "20261017T1735Z_lore-search_dev", "20261018T1321Z_lore-search_test"),
    ("baseline", "20261017T1744Z_baseline_dev", "20261018T1315Z_baseline_test"),
)


def load(run: str) -> dict[str, dict[str, Any]]:
    """id -> {bucket, score} (answerable) or {unanswered} (unanswerable). Nothing else."""
    path = get_settings().eval_dir / "runs" / run / "judged.jsonl"
    out = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            v = json.loads(line)
            if "bucket" in v:
                out[v["id"]] = {"bucket": v["bucket"], "score": v["score"]}
            else:
                out[v["id"]] = {"unanswered": bool(v["unanswered"])}
    return out


def headline(run: dict[str, dict], ids: list[str]) -> tuple[float | None, float | None]:
    """(Avg, unanswerable %) over these question ids, as report.summarize computes them."""
    means = []
    for cat in CATEGORIES:
        scores = [run[i]["score"] for i in ids if run[i].get("bucket") == cat]
        if scores:
            means.append(sum(scores) / len(scores) * (1 if cat == "content" else 100))
    flags = [run[i]["unanswered"] for i in ids if "unanswered" in run[i]]
    avg = sum(means) / len(means) if means else None
    return avg, (100 * sum(flags) / len(flags) if flags else None)


def strata(run: dict[str, dict]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for i, v in sorted(run.items()):
        out.setdefault(v.get("bucket", "unanswerable"), []).append(i)
    return out


def resamples(groups: dict[str, list[str]], n: int, seed: int):
    rng = random.Random(seed)
    for _ in range(n):
        yield [rng.choice(ids) for ids in groups.values() for _ in ids]


def interval(values: list[float]) -> list[float]:
    values = sorted(values)
    return [
        round(values[int(0.025 * len(values))], 1),
        round(values[int(0.975 * len(values)) - 1], 1),
    ]


def summary(runs: dict[str, dict[str, dict]], n: int = RESAMPLES, seed: int = SEED) -> dict:
    """Point estimates and 95% intervals per system, and paired differences against `lore`."""
    names = list(runs)
    shared = set.intersection(*(set(r) for r in runs.values()))
    groups = strata({i: runs[names[0]][i] for i in shared})
    point = {s: headline(runs[s], sorted(shared)) for s in names}
    draws: dict[str, list[tuple]] = {s: [] for s in names}
    for ids in resamples(groups, n, seed):
        for s in names:
            draws[s].append(headline(runs[s], ids))
    out: dict[str, Any] = {"questions": len(shared), "resamples": n, "systems": {}}
    for s in names:
        out["systems"][s] = {
            "avg": round(point[s][0], 1),
            "avg_95": interval([d[0] for d in draws[s]]),
            "unanswerable": round(point[s][1], 1),
            "unanswerable_95": interval([d[1] for d in draws[s]]),
        }
    if "lore" in names:
        out["paired_vs_lore"] = {
            s: {
                "avg_diff": round(point["lore"][0] - point[s][0], 1),
                "avg_diff_95": interval(
                    [a[0] - b[0] for a, b in zip(draws["lore"], draws[s], strict=True)]
                ),
            }
            for s in names
            if s != "lore"
        }
    return out


def main() -> int:
    report = {}
    for split, column in (("dev", 1), ("held-out", 2)):
        runs = {}
        for system in SYSTEMS:
            path = get_settings().eval_dir / "runs" / system[column]
            if (path / "judged.jsonl").is_file():
                runs[system[0]] = load(system[column])
        if runs:
            report[split] = summary(runs)
    path = get_settings().eval_dir / "results" / "bootstrap.json"
    path.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(report, indent=1))
    print(f"-> {Path(path).relative_to(get_settings().eval_dir.parent)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
