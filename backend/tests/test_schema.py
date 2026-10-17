"""app/schema.py without a server: parsing 00_schema.cypher, the one tolerated error, verification.

The same flow against FalkorDB is checked by `lore schema` on a scratch graph and, from 1.6, by
the db tests.
"""

import pytest

from app import db, schema
from app.config import Settings, get_settings


def test_the_schema_file_declares_the_expected_indexes():
    specs = schema.declared()
    kinds = [s.kind for s in specs]
    assert (
        kinds.count("RANGE") == 19 and kinds.count("FULLTEXT") == 2 and kinds.count("VECTOR") == 1
    )
    assert len({(s.label, s.prop, s.kind) for s in specs}) == len(specs), "duplicate index lines"


def test_vector_index_matches_the_embedding_model():
    (vector,) = [s for s in schema.declared() if s.kind == "VECTOR"]
    assert (vector.label, vector.prop) == ("Chunk", "embedding")
    assert vector.options == {
        "dimension": get_settings().lore_embed_dim,
        "similarityFunction": "cosine",
    }


def test_every_merge_key_used_by_the_design_has_a_range_index():
    ranged = {(s.label, s.prop) for s in schema.declared() if s.kind == "RANGE"}
    for label in (
        "Person",
        "Team",
        "Product",
        "Customer",
        "Company",
        "Org",
        "Channel",
        "Artifact",
        "Chunk",
        "Feature",
        "Issue",
        "Decision",
        "Claim",
        "Alert",
        "Action",
        "Area",
    ):
        assert (label, "id") in ranged, f"{label}.id needs a range index before bulk MERGE"


def test_a_line_that_is_not_an_index_statement_is_refused(monkeypatch, tmp_path):
    (tmp_path / "00_schema.cypher").write_text(
        "// 00_schema.cypher — t\nMATCH (n) DETACH DELETE n\n", encoding="utf-8"
    )
    monkeypatch.setattr(schema, "get_settings", lambda: Settings(queries_dir=tmp_path))
    with pytest.raises(ValueError, match="not an index statement"):
        schema.declared()


def failing_run_text(message):
    def run_text(text, params=None, *, label, graph=None, readonly=True, **kw):
        assert readonly is False, "schema lines must run as the writer"
        if "Person" in text:
            raise db.QueryError(label, graph, message)

    return run_text


def test_apply_tolerates_only_already_indexed(monkeypatch):
    monkeypatch.setattr(db, "run_text", failing_run_text("Attribute 'id' is already indexed"))
    outcome = dict((str(spec), result) for spec, result in schema.apply("g"))
    assert outcome["RANGE Person.id"] == "exists"
    assert outcome["RANGE Team.id"] == "created"


@pytest.mark.parametrize(
    "message",
    ["Invalid input 'INDX'", "Attribute 'id' is already indexed, and more", "Query timed out"],
)
def test_apply_raises_on_any_other_error(monkeypatch, message):
    monkeypatch.setattr(db, "run_text", failing_run_text(message))
    with pytest.raises(db.QueryError):
        schema.apply("g")


VECTOR = schema.IndexSpec(
    "Chunk", "embedding", "VECTOR", {"dimension": 384, "similarityFunction": "cosine"}
)
RANGE = schema.IndexSpec("Chunk", "id", "RANGE")


def row(types, options=None, status="OPERATIONAL", label="Chunk", entitytype="NODE"):
    return {
        "label": label,
        "types": types,
        "options": options or {},
        "status": status,
        "entitytype": entitytype,
    }


def test_problems_is_empty_when_everything_matches():
    rows = [
        row(
            {"id": ["RANGE"], "embedding": ["VECTOR"]},
            {"embedding": {"dimension": 384, "similarityFunction": "cosine", "M": 16}},
        )
    ]
    assert schema.problems([RANGE, VECTOR], rows) == []


def test_problems_reports_missing_wrong_kind_and_edge_indexes():
    rows = [row({"id": ["FULLTEXT"]}), row({"embedding": ["VECTOR"]}, entitytype="RELATIONSHIP")]
    assert schema.problems([RANGE, VECTOR], rows) == [
        "missing: RANGE Chunk.id",
        "missing: VECTOR Chunk.embedding dimension=384 similarityFunction=cosine",
    ]


def test_problems_catches_a_vector_index_kept_with_old_options():
    rows = [
        row(
            {"embedding": ["VECTOR"]},
            {"embedding": {"dimension": 768, "similarityFunction": "cosine"}},
        )
    ]
    (problem,) = schema.problems([VECTOR], rows)
    assert problem.startswith("mismatch:") and "dimension=768" in problem


def test_verify_waits_for_indexes_still_building(monkeypatch):
    statuses = iter(["UNDER CONSTRUCTION", "UNDER CONSTRUCTION", "OPERATIONAL"])
    monkeypatch.setattr(schema, "declared", lambda: [RANGE])
    monkeypatch.setattr(
        db,
        "run",
        lambda name, graph: db.Result([], [row({"id": ["RANGE"]}, status=next(statuses))], {}, 0.0),
    )
    monkeypatch.setattr(schema.time, "sleep", lambda s: None)
    assert schema.verify("g") == []


def test_verify_gives_up_after_the_deadline(monkeypatch):
    monkeypatch.setattr(schema, "declared", lambda: [RANGE])
    monkeypatch.setattr(
        db,
        "run",
        lambda name, graph: db.Result(
            [], [row({"id": ["RANGE"]}, status="UNDER CONSTRUCTION")], {}, 0.0
        ),
    )
    monkeypatch.setattr(schema.time, "sleep", lambda s: None)
    assert schema.verify("g", wait_s=0) == ["not ready: RANGE Chunk.id is UNDER CONSTRUCTION"]
