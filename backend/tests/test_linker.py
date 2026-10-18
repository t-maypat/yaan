"""S4 deterministic linking (Phase 6) on the invented Widget fixture and hand-made artifacts.

The fixture has two people named "Ann Lee" (eid_aaaa0001, eid_aaaa0002); only 0002 attends
the planning meeting, so the transcript's "Ann Lee" must resolve to 0002.
"""

import shutil
from pathlib import Path

import pytest

from app import db
from app.ingest import health, linker, structural
from app.ingest.herb_loader import load_product
from app.ingest.normalize import Artifact, normalize_product

FIXTURES = Path(__file__).parent / "fixtures"
PR_OPEN = "pr:github_com_salesforce_Widget_pull_1"


@pytest.fixture(scope="module")
def widget(tmp_path_factory):
    folder = tmp_path_factory.mktemp("herb") / "products"
    folder.mkdir()
    path = shutil.copy(FIXTURES / "herb_product.json", folder / "Widget.json")
    artifacts, _ = normalize_product(load_product(Path(path)))
    return artifacts


@pytest.fixture(scope="module")
def meta():
    return structural.load_metadata(FIXTURES / "herb_metadata")


def art(id_, text, kind="message", **kw):
    if kind == "message":  # normalized messages always carry their channel
        kw.setdefault("channel_id", "ch-test")
        kw.setdefault("meta", {"channel_name": "planning-Widget"})
    return Artifact(
        id=id_,
        kind=kind,
        product_hint="Widget",
        ts_ms=1,
        ts_iso="x",
        text=text,
        raw_ref="test",
        **kw,
    )


def edges(rows, name, key="entity_id"):
    return {(r["artifact_id"], r[key]) for r in rows[name]}


# --- pure rules ------------------------------------------------------------------------------
def test_slack_markup_and_punctuation_are_cut_from_urls():
    text = "see <https://github.com/o/r/pull/1|PR 1>, and https://x.example/a/."
    assert linker.urls(text) == ["https://github.com/o/r/pull/1", "https://x.example/a"]


def test_widget_links_shares_mentions_and_speakers(meta, widget):
    rows, unresolved = linker.build_rows(meta, {"Widget": widget})
    shares = edges(rows, "link_shares", "target_id")
    assert ("msg:20260430-0-bbbbb", PR_OPEN) in shares  # <url|PR 1> in a Slack message
    assert ("chat:wdAI_planning_1_chat", "doc:final_wdai_vision_document") in shares
    spoke = {(r["person_id"], r["artifact_id"]): r["method"] for r in rows["link_spoke_in"]}
    # duplicate name: two Ann Lees in the directory, one in the meeting
    assert spoke[("eid_aaaa0002", "mtg:wdAI_planning_1")] == "participants"
    assert ("eid_aaaa0001", "mtg:wdAI_planning_1") not in spoke
    assert spoke[("eid_aaaa0003", "mtg:wdAI_planning_1")] == "participants"
    # the chat's speaker is resolved against its meeting's participants
    assert spoke[("eid_aaaa0002", "chat:wdAI_planning_1_chat")] == "participants"
    assert unresolved == []
    # the channel-creation message mentions eid_aaaa0001 but is a system event
    assert not any(r["artifact_id"] == "msg:20260501-0-aaaaa" for r in rows["link_mentions_person"])


def test_a_renamed_product_is_found_by_its_codename(meta, widget):
    extra = art("msg:codename", "The wdAI rollout slips a week.", channel_id="ch-wdai-aaa111")
    rows, _ = linker.build_rows(meta, {"Widget": [*widget, extra]})
    found = {r["artifact_id"]: r for r in rows["link_mentions_product"]}
    assert found["msg:codename"]["entity_id"] == "Widget"
    assert found["msg:codename"]["surfaces"] == ["wdAI"]


def test_a_cross_product_pr_reference_is_a_share_not_delivery(meta, widget):
    gadget_pr = art(
        "pr:gadget_1", "Gadget export.", kind="pr", url="https://github.com/s/Gadget/pull/9"
    )
    msg = art("msg:xref", "Same bug as Gadget's https://github.com/s/Gadget/pull/9 and Widget.")
    rows, _ = linker.build_rows(meta, {"Widget": [*widget, msg], "Gadget": [gadget_pr]})
    assert ("msg:xref", "pr:gadget_1") in edges(rows, "link_shares", "target_id")
    products = edges(rows, "link_mentions_product")
    assert ("msg:xref", "Widget") in products and ("msg:xref", "Gadget") in products
    assert "link_implements" not in rows and "link_fixes" not in rows  # never from a mention


def test_a_revert_pr_links_to_the_pr_it_undoes_in_its_own_repo(meta, widget):
    def pr(id_, title, repo, number):
        return art(id_, "x", kind="pr", title=title, meta={"repo": repo, "number": number})

    original = pr("pr:w8", "Implement GraphQL Server", "s/Widget", 8)
    undo = pr("pr:w13", "Revert PR# 8: Implement GraphQL Server", "s/Widget", 13)
    other_repo = pr("pr:g8", "Unrelated", "s/Gadget", 8)
    dangling = pr("pr:w14", "Revert PR#99: Gone", "s/Widget", 14)
    rows, unresolved = linker.build_rows(
        meta, {"Widget": [*widget, original, undo, dangling], "Gadget": [other_repo]}
    )
    assert rows["link_reverts"] == [{"revert_id": "pr:w13", "original_id": "pr:w8"}]
    assert {"artifact_id": "pr:w14", "product": "Widget", "surface": "revert:#99"} in unresolved


def test_customers_companies_and_unknown_ids(meta, widget):
    msg = art("msg:cust", "CUST-0002 at Blue Wave reported it; CUST-9999 too. Dee Fox agrees.")
    rows, unresolved = linker.build_rows(meta, {"Widget": [*widget, msg]})
    assert edges(rows, "link_mentions_customer") >= {
        ("msg:cust", "CUST-0002"),
        ("msg:cust", "CUST-0001"),
    }  # by name
    assert ("msg:cust", "co:bluewave") in edges(rows, "link_mentions_company")
    assert {
        "artifact_id": "msg:cust",
        "product": "Widget",
        "surface": "customer:CUST-9999",
    } in unresolved
    notes = {r["artifact_id"]: r for r in rows["set_link_notes"]}["msg:cust"]
    assert notes["mentions"] == 4 and notes["unresolved"] == ["customer:CUST-9999"]


def test_speakers_outside_the_participants_use_the_directory_then_activity(meta, widget):
    d = linker.dictionaries(meta, {"Widget": widget})
    # Bo Chen is unique in the directory
    assert linker.resolve_speaker("Bo Chen", [], d, "Widget") == (
        "eid_aaaa0003",
        "directory",
        ["eid_aaaa0003"],
    )
    # two Ann Lees; both authored Widget artifacts, so no tiebreak: unresolved
    eid, method, pool = linker.resolve_speaker("Ann Lee", [], d, "Widget")
    assert (eid, method, pool) == (None, "unresolved", ["eid_aaaa0001", "eid_aaaa0002"])
    # in a product where only one of them is active, activity breaks the tie
    d2 = linker.dictionaries(meta, {"Gadget": [art("msg:g", "hi", author_eid="eid_aaaa0001")]})
    assert linker.resolve_speaker("Ann Lee", [], d2, "Gadget")[:2] == (
        "eid_aaaa0001",
        "directory+activity",
    )


def test_meeting_turns_count_people_not_headers():
    text = "Attendees\nAnn Lee, Bo Chen\nTranscript\nAnn Lee: Hi.\nNote: skip\nAnn Lee: Bye."
    assert linker.speaker_turns(text) == {"Ann Lee": 2}


def test_every_link_query_has_a_file():
    queries = Path(__file__).resolve().parents[1] / "queries"
    assert all((queries / f"{name}.cypher").is_file() for name in linker.LOAD_ORDER)


# --- graph -----------------------------------------------------------------------------------
@pytest.fixture
def linked(empty_graph, meta, widget):
    structural.load(structural.build_rows(meta, {"Widget": widget}, 1), empty_graph)
    rows, _ = linker.build_rows(meta, {"Widget": widget})
    return empty_graph, rows, linker.load(rows, empty_graph)


@pytest.mark.db
def test_link_load_is_idempotent_and_reported_in_health(linked):
    graph, rows, first = linked
    assert first["link_shares"]["relationships_created"] == len(rows["link_shares"])
    again = linker.load(rows, graph)
    assert sum(r["relationships_created"] for r in again.values()) == 0
    r = health.report(graph)
    assert (
        r["linking"]
        == {"mentions": r["linking"]["mentions"], "unresolved": 0, "unresolved_pct": 0.0}
        and r["linking"]["mentions"] > 0
    )
    assert {row["method"] for row in r["speakers"]} == {"participants"}
    assert "linking:" in health.render(r)


@pytest.mark.db
def test_the_meeting_speaker_is_the_right_ann_lee(linked):
    graph, _, _ = linked
    who = db.run_text(
        "MATCH (p:Person)-[:SPOKE_IN]->(m:Meeting {id: $m}) RETURN p.id AS id ORDER BY id LIMIT 5",
        {"m": "mtg:wdAI_planning_1"},
        label="test_read",
        graph=graph,
    ).rows
    assert [r["id"] for r in who] == ["eid_aaaa0002", "eid_aaaa0003"]


@pytest.mark.db
@pytest.mark.parametrize("name", linker.LOAD_ORDER)
def test_link_queries_find_nodes_by_index(empty_graph, name):
    plan = " / ".join(db.explain(name, {"rows": [{}]}, graph=empty_graph))
    assert "Node By Index Scan" in plan
    assert "Label Scan" not in plan and "All Node Scan" not in plan
