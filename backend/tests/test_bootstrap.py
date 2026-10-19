"""D103: confidence intervals from judged runs, recomputing the headline exactly. No LLM, no DB."""

import json

from app.eval import bootstrap


def write(folder, rows):
    folder.mkdir(parents=True)
    with (folder / "judged.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def runs(tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap, "get_settings", lambda: type("S", (), {"eval_dir": tmp_path}))
    good = [{"id": f"c{i}", "bucket": "content", "score": 80} for i in range(10)]
    good += [{"id": f"p{i}", "bucket": "person", "score": 1.0} for i in range(10)]
    good += [{"id": f"u{i}", "unanswered": i < 8, "reason": "never read"} for i in range(10)]
    weak = [{**r, "score": 20} if r.get("bucket") == "content" else r for r in good]
    weak = [{**r, "score": 0.25} if r.get("bucket") == "person" else r for r in weak]
    write(tmp_path / "runs" / "a", good)
    write(tmp_path / "runs" / "b", weak)
    return {"lore": bootstrap.load("a"), "lore-search": bootstrap.load("b")}


def test_the_headline_is_recomputed_as_the_report_does(tmp_path, monkeypatch):
    r = runs(tmp_path, monkeypatch)
    assert bootstrap.headline(r["lore"], sorted(r["lore"])) == (90.0, 80.0)  # mean(80, 100)
    assert "reason" not in r["lore"]["u0"]  # only the declined flag is read


def test_a_paired_difference_has_an_interval_around_it(tmp_path, monkeypatch):
    out = bootstrap.summary(runs(tmp_path, monkeypatch), n=500)
    assert out["systems"]["lore"]["avg"] == 90.0 and out["systems"]["lore-search"]["avg"] == 22.5
    lo, hi = out["paired_vs_lore"]["lore-search"]["avg_diff_95"]
    assert lo <= 67.5 <= hi  # identical scores per category: the difference doesn't move
    lo, hi = out["systems"]["lore"]["unanswerable_95"]
    assert lo < 80.0 < hi


def test_the_metadata_baseline_adds_a_directory_of_who_and_where(monkeypatch):
    """D103: plain RAG gets the customer -> company and person -> role joins as a document."""
    from app import db
    from app.eval import baseline_rag

    asked = []

    def run(name, params, graph=None, **_):
        asked.append(params["ids"])
        rows = [
            {"id": "CUST-0102", "name": "Helen Smith", "company": "BlueWave", "role": None},
            {
                "id": "eid_1330d187",
                "name": "Fiona Miller",
                "company": None,
                "role": "Engineering Lead",
            },
        ]
        return type("R", (), {"rows": rows})

    monkeypatch.setattr(db, "run", run)
    results = [
        {"artifact_id": "msg:1", "text": "Bug for CUST-0102, see eid_1330d187.", "title": None},
        {"artifact_id": "msg:2", "text": "no ids", "title": "t", "authors": ["eid_1330d187"]},
    ]
    out = baseline_rag.with_directory(results)
    assert asked == [["CUST-0102", "eid_1330d187"]]  # each id once
    assert out[:2] == results and out[2]["artifact_id"] == "meta:directory"
    assert out[2]["text"].splitlines() == [
        "CUST-0102: Helen Smith, customer at BlueWave",
        "eid_1330d187: Fiona Miller, Engineering Lead",
    ]
    assert baseline_rag.with_directory([{"text": "none", "title": None}]) == [
        {"text": "none", "title": None}
    ]
