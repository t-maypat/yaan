"""Dev-product ranking (D017) and the artifact reader it relies on.

Fixtures are invented here, in HERB's verified shape; no HERB content is copied.
"""

import json

import pytest

from app.eval import split
from app.ingest.herb_loader import ARTIFACT_KEYS, read_artifacts


def pr(state="closed", merged="True", title="Add feature", summary="", reviews=("APPROVED",)):
    return {
        "title": title,
        "summary": summary,
        "state": state,
        "merged": merged,
        "reviews": [{"state": s} for s in reviews],
    }


def slack(text):
    return {"Message": {"User": {"text": text}}, "ThreadReplies": []}


def product(prs=(), messages=()):
    return {
        "team": ["eid_secret"],
        "customers": ["CUST-secret"],
        "answerable_questions": [{"question": "SECRET QUESTION", "ground_truth": ["x"]}],
        "unanswerable_questions": ["SECRET UNANSWERABLE"],
        "slack": [slack(t) for t in messages],
        "documents": [],
        "meeting_transcripts": [],
        "meeting_chats": [],
        "urls": [],
        "prs": list(prs),
    }


def write(tmp_path, name, data):
    folder = tmp_path / "products"
    folder.mkdir(exist_ok=True)
    path = folder / f"{name}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_read_artifacts_drops_questions_and_oracle_fields(tmp_path):
    artifacts = read_artifacts(write(tmp_path, "P", product(messages=["hi"])))
    assert set(artifacts) == set(ARTIFACT_KEYS)
    assert "SECRET" not in json.dumps(artifacts) and "eid_secret" not in json.dumps(artifacts)


def test_read_artifacts_fails_loudly_on_an_unknown_key(tmp_path):
    data = product()
    data["surprise"] = []
    with pytest.raises(ValueError, match="surprise"):
        read_artifacts(write(tmp_path, "P", data))


def test_product_signals_counts_each_rule():
    data = product(
        prs=[
            pr(state="closed", merged="False"),  # closed unmerged
            pr(state="open", merged="False"),  # open: not counted
            pr(reviews=("CHANGES_REQUESTED", "APPROVED", "CHANGES_REQUESTED")),
            pr(title="Revert login change"),
            pr(summary="This reverts #12"),
        ],
        messages=[
            "Let's put this ON HOLD",
            "we rolled back the release",
            "bug reopened",
            "Shipping today",
            "Delayed to Q3, and we may revert",  # one message, counted once
        ],
    )
    counts = split.product_signals({k: data[k] for k in ARTIFACT_KEYS})
    assert counts == {
        "pr_closed_unmerged": 1,
        "pr_changes_requested": 2,
        "pr_revert": 2,
        "slack_change": 4,
    }


def test_rank_products_scales_each_signal_and_breaks_ties_by_name(tmp_path):
    write(tmp_path, "Alpha", product(prs=[pr(state="closed", merged="False")] * 2))
    write(tmp_path, "Beta", product(messages=["on hold"] * 4))
    write(tmp_path, "Gamma", product(prs=[pr(state="closed", merged="False")], messages=["x"]))
    write(tmp_path, "Delta", product(messages=["on hold"] * 4))
    rows = split.rank_products(tmp_path)
    by_name = {r["product"]: r for r in rows}
    assert by_name["Alpha"]["score"] == 1.0  # 2 / max 2
    assert by_name["Gamma"]["score"] == 0.5  # 1 / max 2
    assert by_name["Beta"]["score"] == 1.0  # 4 / max 4
    assert [r["product"] for r in rows] == ["Alpha", "Beta", "Delta", "Gamma"]
    assert [r["rank"] for r in rows] == [1, 2, 3, 4]


def test_write_ranking_records_the_rule_revision_and_top_three(tmp_path):
    for name in ("A", "B", "C", "D"):
        write(tmp_path, name, product(messages=["on hold"] * (ord(name) - 64)))
    rows = split.rank_products(tmp_path)
    record = json.loads(split.write_ranking(rows, tmp_path / "r.json").read_text("utf-8"))
    assert record["dev"] == ["D", "C", "B"]
    assert record["herb_revision"] == split.HERB_REVISION
    assert record["rule"].startswith("D017")
    assert "SECRET" not in json.dumps(record)
