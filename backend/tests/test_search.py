"""S3 chunking and embedding, and agent tool 1 search_artifacts (Phase 4).

The `db` tests load the invented Widget fixture and replace the embedding model with a
bag-of-words stand-in (same words → same vector), so they're fast and deterministic. One
`model` test runs the real local model to check its behaviour on Windows and Linux.
"""

import math
import re
import shutil
import zlib
from pathlib import Path

import pytest

from app.agent import tools
from app.ingest import chunk_embed, structural
from app.ingest.chunk_embed import MAX_CHARS, chunk_artifact, line_groups, spans
from app.ingest.herb_loader import load_product
from app.ingest.normalize import normalize_product

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def widget(tmp_path_factory):
    folder = tmp_path_factory.mktemp("herb") / "products"
    folder.mkdir()
    path = shutil.copy(FIXTURES / "herb_product.json", folder / "Widget.json")
    return normalize_product(load_product(Path(path)))[0]


def field_text(artifact, field):
    if field == "text":
        return artifact.text
    if field == "title":
        return artifact.title
    if field == "feedback":
        return artifact.meta["feedback"]
    i = int(re.search(r"\d+", field).group())
    return artifact.meta["reviews"][i]["comment"]


# --- chunking ----------------------------------------------------------------------------
def test_spans_cut_long_text_at_sentence_ends():
    text = " ".join(f"Sentence number {i} is here." for i in range(100))
    pieces = list(spans(text))
    assert len(pieces) > 1 and all(e - s <= MAX_CHARS for s, e in pieces)
    assert all(text[s:e].endswith(".") for s, e in pieces)
    assert " ".join(text[s:e] for s, e in pieces) == text  # nothing lost


def test_line_groups_pack_lines_up_to_the_limit():
    lines = [f"Speaker {i}: " + "x" * 300 for i in range(5)]
    text = "\n".join(lines)
    groups = list(line_groups(text, 0))
    assert [text[s:e].count("Speaker") for s, e in groups] == [2, 2, 1]


def test_chunks_are_verbatim_spans_of_their_field(widget):
    for a in widget:
        for c in chunk_artifact(a):
            assert field_text(a, c.field)[c.start_char : c.end_char] == c.text
            assert c.text.strip() == c.text and c.text


def test_what_gets_chunked(widget):
    by_id = {a.id: chunk_artifact(a) for a in widget}
    assert by_id["msg:20260501-0-aaaaa"] == []  # bot notice: not searchable
    meeting = by_id["mtg:wdAI_planning_1"]
    assert all("Attendees" not in c.text for c in meeting)  # the body only
    assert meeting[0].text.startswith("Ann Lee: Let's review")
    pr = by_id["pr:github_com_salesforce_Widget_pull_1"]
    assert [c.field for c in pr] == ["title", "text", "reviews[0].comment"]
    assert pr[0].context.startswith("PR #1 salesforce/Widget: Add export")
    doc = by_id["doc:final_wdai_vision_document"]
    assert [c.field for c in doc] == ["text", "feedback"]
    link = next(c for a in widget if a.kind == "link" for c in by_id[a.id])
    assert link.product_id is None  # links are shared across products (D023)


def test_chunk_ids_are_stable(widget):
    a = widget[3]
    assert [c.id for c in chunk_artifact(a)] == [c.id for c in chunk_artifact(a)]


# --- query building and fusion -------------------------------------------------------------
def test_keywords_drop_stopwords_and_any_query_syntax():
    assert tools.keywords('What is the status of "PR" -5 | %x%?') == "status | pr"
    assert tools.keywords("Who reverted the login PR? who reverted") == "reverted | login | pr"
    assert tools.keywords("what is the") == ""


def test_rrf_rewards_agreement_and_keeps_one_chunk_per_artifact():
    def row(chunk, artifact):
        return {
            "chunk_id": chunk,
            "artifact_id": artifact,
            "kind": "message",
            "title": None,
            "ts_iso": None,
            "product_id": "P",
            "field": "text",
            "text": chunk,
        }

    lists = {
        "fulltext": [row("c1", "a1"), row("c2", "a2"), row("c3", "a1")],
        "vector": [row("c2", "a2"), row("c4", "a3")],
    }
    out = tools.fuse(lists, k=5)
    assert [r["artifact_id"] for r in out] == ["a2", "a1", "a3"]  # a2 is in both lists
    assert out[0]["fulltext_rank"] == 2 and out[0]["vector_rank"] == 1
    assert tools.fuse(lists, k=1)[0]["artifact_id"] == "a2"


# --- graph ---------------------------------------------------------------------------------
def bag_of_words(texts, kind):
    vectors = []
    for t in texts:
        v = [0.0] * 384
        for w in re.findall(r"\w+", t.lower().split("\n")[-1]):  # ignore the context header
            v[zlib.crc32(w.encode()) % 384] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        vectors.append([x / norm for x in v])
    return vectors


@pytest.fixture
def searchable(empty_graph, widget, monkeypatch):
    monkeypatch.setattr(chunk_embed, "_embed", bag_of_words)
    meta = structural.load_metadata(FIXTURES / "herb_metadata")
    structural.load(structural.build_rows(meta, {"Widget": widget}, 1), empty_graph)
    chunks = chunk_embed.chunk_products({"Widget": widget})
    stats = chunk_embed.load(chunks, empty_graph)
    assert stats["nodes_created"] == len(chunks)
    return empty_graph


@pytest.mark.db
def test_fulltext_finds_stemmed_keywords(searchable):
    out = tools.search_artifacts("what was put on hold?", mode="fulltext", graph=searchable)
    assert out["keywords"] == "put | hold"
    assert set(out["evidence_ids"][:2]) == {"msg:20260501-1-ccccc", "msg:20260501-2-ddddd"}
    assert out["cypher"] == ["search_fulltext.cypher"]


@pytest.mark.db
def test_vector_puts_the_identical_text_first(searchable):
    out = tools.search_artifacts("Needs tests.", mode="vector", graph=searchable)
    top = out["results"][0]
    assert top["artifact_id"] == "pr:github_com_salesforce_Widget_pull_1"
    assert top["field"] == "reviews[0].comment" and top["vector_rank"] == 1


@pytest.mark.db
def test_filters_narrow_the_results(searchable):
    out = tools.search_artifacts("export", kinds=["pr"], graph=searchable)
    assert out["evidence_ids"] and all(i.startswith("pr:") for i in out["evidence_ids"])
    none = tools.search_artifacts("export", product_id="Gadget", graph=searchable)
    assert none["results"] == []
    early = tools.search_artifacts("export", until_ms=1_735_689_600_000, graph=searchable)
    assert early["evidence_ids"] == ["pr:github_com_apache_kafka_pull_77"]  # the 2024 PR


@pytest.mark.db
def test_hybrid_returns_both_lists_fused(searchable):
    out = tools.search_artifacts("hold the export", k=3, graph=searchable)
    assert out["cypher"] == ["search_fulltext.cypher", "search_vector.cypher"]
    assert len(out["results"]) == 3 and len(set(out["evidence_ids"])) == 3
    assert set(out["timings"]) == {"fulltext_ms", "embed_ms", "vector_ms"}


@pytest.mark.db
def test_people_adds_authors_and_participants(searchable):
    out = tools.search_artifacts("put on hold", mode="fulltext", people=True, graph=searchable)
    by_id = {r["artifact_id"]: r for r in out["results"]}
    assert by_id["msg:20260501-1-ccccc"]["authors"] == ["eid_aaaa0001"]
    assert out["cypher"][-1] == "artifact_people.cypher" and "people_ms" in out["timings"]
    meeting = tools.search_artifacts(
        "review the doc", kinds=["meeting"], people=True, graph=searchable
    )["results"][0]
    assert meeting["participants"] == ["eid_aaaa0002", "eid_aaaa0003"]
    plain = tools.search_artifacts("put on hold", mode="fulltext", graph=searchable)
    assert "authors" not in plain["results"][0]


@pytest.mark.db
def test_reloading_chunks_creates_nothing(searchable, widget):
    stats = chunk_embed.load(chunk_embed.chunk_products({"Widget": widget}), searchable)
    assert stats["nodes_created"] == 0 and stats["relationships_created"] == 0


@pytest.mark.db
def test_chunks_without_their_artifact_fail_loudly(empty_graph, widget, monkeypatch):
    monkeypatch.setattr(chunk_embed, "_embed", bag_of_words)
    with pytest.raises(RuntimeError, match="structural stage first"):
        chunk_embed.load(chunk_embed.chunk_products({"Widget": widget}), empty_graph)


@pytest.mark.db
@pytest.mark.parametrize("name", ["upsert_chunks", "count_linked_chunks"])
def test_chunk_queries_use_indexes(empty_graph, name):
    from app import db

    params = {"rows": [{}]} if name == "upsert_chunks" else {"ids": ["x"]}
    plan = " / ".join(db.explain(name, params, graph=empty_graph))
    assert "Node By Index Scan" in plan and "Label Scan" not in plan


@pytest.mark.model
def test_real_model_ranks_paraphrase_above_unrelated():
    query = chunk_embed.embed_query("Who rolled back the login change?")
    passages = chunk_embed.embed_passages(
        ["We reverted the authentication PR yesterday.", "The quarterly marketing report."]
    )
    assert len(query) == 384 and abs(sum(x * x for x in query) - 1.0) < 1e-3
    near, far = (sum(q * p for q, p in zip(query, v, strict=True)) for v in passages)
    assert near > far
