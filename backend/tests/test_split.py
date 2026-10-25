"""Dev/test split (2.3) and the held-out guard (2.4).

Fixtures are invented here, in HERB's verified shape; no HERB content is copied.
"""

import json

import pytest

from app.config import get_settings
from app.eval import split

NAMES = [f"P{i:02d}" for i in range(12)]
DEV = ["P03", "P07", "P11"]


def product(name):
    return {
        "answerable_questions": [{"question": f"Q of {name}", "ground_truth": ["x"]}],
        "unanswerable_questions": [f"U of {name}"],
        "team": ["eid_secret"],
        "customers": ["CUST-secret"],
        "slack": [],
    }


@pytest.fixture
def world(tmp_path):
    """A fake HERB folder with 12 products, plus an eval folder holding the dev ranking."""
    herb, evals = tmp_path / "herb", tmp_path / "eval"
    (herb / "products").mkdir(parents=True)
    evals.mkdir()
    for name in NAMES:
        (herb / "products" / f"{name}.json").write_text(json.dumps(product(name)), "utf-8")
    (evals / "dev_ranking.json").write_text(json.dumps({"dev": DEV}), "utf-8")
    split.make_split(evals / "split.json", herb)
    return herb, evals


def test_draw_is_repeatable_and_partitions_the_products():
    first = split.draw_split(NAMES, DEV)
    assert split.draw_split(list(reversed(NAMES)), DEV) == first  # input order doesn't matter
    assert first["dev"] == DEV and len(first["test"]) == split.TEST_COUNT
    groups = [set(first[k]) for k in ("dev", "test", "rest")]
    assert set.union(*groups) == set(NAMES) and sum(map(len, groups)) == len(NAMES)
    assert first["seed"] == split.SEED == int(split.HERB_REVISION[:8], 16)


def test_draw_rejects_an_unknown_dev_product():
    with pytest.raises(ValueError, match="Nope"):
        split.draw_split(NAMES, ["Nope"])


def test_split_is_written_once_then_only_checked(world):
    herb, evals = world
    path = evals / "split.json"
    written = json.loads(path.read_text("utf-8"))
    again, new = split.make_split(path, herb)
    assert not new and again == written
    path.write_text(json.dumps({**written, "test": NAMES[:7]}), "utf-8")  # tampered
    with pytest.raises(RuntimeError, match="fixed"):
        split.make_split(path, herb)


def test_dev_questions_load(world):
    herb, evals = world
    questions = split.load_questions("P03", herb, evals / "split.json")
    assert set(questions) == {"answerable_questions", "unanswerable_questions"}
    assert questions["unanswerable_questions"] == ["U of P03"]


def test_test_questions_refuse_outside_the_heldout_run(world):
    herb, evals = world
    test_product = split.read_split(evals / "split.json")["test"][0]
    with pytest.raises(split.HeldOutError, match="held-out"):
        split.load_questions(test_product, herb, evals / "split.json")


def test_rest_questions_always_refuse(world):
    herb, evals = world
    record = split.read_split(evals / "split.json")
    with split.heldout_run("test", log=evals / "heldout_runs.log"):
        with pytest.raises(split.HeldOutError, match="neither"):
            split.load_questions(record["rest"][0], herb, evals / "split.json")


def test_heldout_run_logs_first_then_opens_only_while_active(world):
    herb, evals = world
    log = evals / "heldout_runs.log"
    test_product = split.read_split(evals / "split.json")["test"][0]
    with split.heldout_run("yaan", note="final", log=log):
        assert "system=yaan\tfinal" in log.read_text("utf-8")  # logged before any read
        questions = split.load_questions(test_product, herb, evals / "split.json")
        assert questions["unanswerable_questions"] == [f"U of {test_product}"]
    with pytest.raises(split.HeldOutError):  # closed again afterwards
        split.load_questions(test_product, herb, evals / "split.json")
    assert len(log.read_text("utf-8").splitlines()) == 1


def test_committed_split_matches_the_rule():
    """eval/split.json is fixed: dev from the D017 ranking, 7 test products, the revision seed."""
    evals = get_settings().eval_dir
    record = json.loads((evals / "split.json").read_text("utf-8"))
    ranking = json.loads((evals / "dev_ranking.json").read_text("utf-8"))
    assert record["dev"] == ranking["dev"] and record["seed"] == split.SEED
    assert record["herb_revision"] == split.HERB_REVISION
    assert len(record["test"]) == split.TEST_COUNT
    assert len(record["dev"]) + len(record["test"]) + len(record["rest"]) == 30
    expected = split.draw_split(record["dev"] + record["test"] + record["rest"], record["dev"])
    assert record["test"] == expected["test"]
