"""Measure `who_knows` (10.8, D059): does the expertise layer find the people HERB names?

VizForce's dev set asks "engineers who resolved issues with <component>" five times, with the
engineers' ids as ground truth. For each, `who_knows(<component>)` ranks people; we report
hit@1 (the top person is in the answer) and recall@3 / @5 (how much of the answer the top 3 /
5 cover), against a naive baseline: the product's most active posters in bug channels, the
same list for every question. Dev questions only (rule 3); no LLM, so it costs nothing.
"""

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app import db
from app.agent import tools
from app.config import get_settings
from app.eval.split import load_questions, read_split

PATTERN = re.compile(r"engineers? who resolved issues with the (.+?) (?:module )?in (\w+)", re.I)


def cases() -> list[dict[str, Any]]:
    out = []
    for product in read_split()["dev"]:
        for q in load_questions(product)["answerable_questions"]:
            m = PATTERN.search(q["question"])
            if m and m.group(2) == product:
                out.append(
                    {"product": product, "component": m.group(1), "truth": q["ground_truth"]}
                )
    return out


def baseline(product: str, graph: str | None) -> list[str]:
    rows = db.run_text(
        "MATCH (p:Person)-[:AUTHORED]->(m:Message)-[:IN_CHANNEL]->(c:Channel {stage: 'support'}) "
        "WHERE m.product_id = $product WITH p, count(m) AS n "
        "RETURN p.id AS id, n ORDER BY n DESC, id LIMIT 10",
        {"product": product},
        label="experts_baseline",
        graph=graph,
    ).rows
    return [r["id"] for r in rows]


def score(ranked: list[str], truth: list[str]) -> dict[str, float]:
    t = set(truth)

    def recall(k: int) -> float:
        return round(len(set(ranked[:k]) & t) / len(t), 3) if t else 0.0

    return {
        "hit@1": float(bool(ranked[:1]) and ranked[0] in t),
        "recall@3": recall(3),
        "recall@5": recall(5),
    }


def bench(graph: str | None = None) -> dict[str, Any]:
    rows = []
    for c in cases():
        out = tools.who_knows(c["component"], c["product"], graph=graph)
        ranked: list[str] = []
        for a in out["areas"][:1]:  # the best-matching area
            ranked = [e["id"] for e in a["experts"]]
        base = baseline(c["product"], graph)
        rows.append(
            {
                **c,
                "area": out["areas"][0]["name"] if out["areas"] else None,
                "who_knows": ranked[:5],
                "baseline": base[:5],
                "yaan": score(ranked, c["truth"]),
                "naive": score(base, c["truth"]),
            }
        )
    mean = {
        system: {
            m: round(sum(r[system][m] for r in rows) / len(rows), 3) if rows else None
            for m in ("hit@1", "recall@3", "recall@5")
        }
        for system in ("yaan", "naive")
    }
    return {
        "graph": graph or get_settings().yaan_graph,
        "questions": len(rows),
        "mean": mean,
        "rows": rows,
    }


def write(result: dict[str, Any], path: Path | None = None) -> Path:
    path = path or get_settings().eval_dir / "results" / "experts_dev.json"
    stamped = {"run_at": datetime.now(UTC).isoformat(timespec="seconds"), **result}
    path.write_text(json.dumps(stamped, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
