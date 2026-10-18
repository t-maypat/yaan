"""S2 structural load (Phase 3) and health v0, on the invented fixtures.

fixtures/herb_product.json (product "Widget") and fixtures/herb_metadata/ are written by
hand in HERB's verified shape; no HERB content is copied. Pure row-building tests run
without FalkorDB; the `db` tests load the fixture company into a throwaway graph.
"""

import shutil
from pathlib import Path

import pytest

from app import db
from app.ingest import health, structural
from app.ingest.herb_loader import load_product
from app.ingest.normalize import normalize_product

FIXTURES = Path(__file__).parent / "fixtures"
QUERIES = Path(__file__).resolve().parents[1] / "queries"
OBSERVED = 1_800_000_000_000
PR_OPEN = "pr:github_com_salesforce_Widget_pull_1"
PR_EXT = "pr:github_com_apache_kafka_pull_77"


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


@pytest.fixture(scope="module")
def rows(meta, widget):
    return structural.build_rows(meta, {"Widget": widget}, observed_ms=OBSERVED)


def by_id(artifacts):
    return {a.id: a for a in artifacts}


# --- rows (no database) --------------------------------------------------------------------
def test_teams_are_vp_groups_and_reports_follow_the_hierarchy(rows):
    assert rows["upsert_teams"] == [
        {
            "id": "team:eid_aaaa0000",
            "name": "Vera Park's team (tableau)",
            "org": "tableau",
            "lead_id": "eid_aaaa0000",
        }
    ]
    members = {r["person_id"] for r in rows["link_member_of"]}
    assert members == {f"eid_aaaa000{i}" for i in range(5)}
    reports = {(r["person_id"], r["manager_id"]) for r in rows["link_reports_to"]}
    assert reports == {
        ("eid_aaaa0001", "eid_aaaa0000"),
        ("eid_aaaa0002", "eid_aaaa0001"),
        ("eid_aaaa0003", "eid_aaaa0001"),
        ("eid_aaaa0004", "eid_aaaa0000"),
    }
    assert rows["link_part_of"] == [{"team_id": "team:eid_aaaa0000", "org_id": "org:tableau"}]


def test_customers_work_at_slugged_companies(rows):
    assert rows["upsert_companies"] == [{"id": "co:bluewave", "name": "Blue Wave"}]
    assert {r["company_id"] for r in rows["link_works_at"]} == {"co:bluewave"}


def test_channels_get_stage_original_name_and_codenames(rows):
    channels = {c["id"]: c for c in rows["upsert_channels"]}
    planning = channels["ch-wdai-aaa111"]
    assert planning["original_name"] == "planning-wdAI" and planning["stage"] == "planning"
    assert channels["ch-force-bbb222"]["stage"] == "dev"
    assert rows["upsert_products"] == [{"id": "Widget", "name": "Widget", "aliases": ["wdAI"]}]


def test_pr_chain_clamps_early_reviews_and_never_reuses_a_time(widget):
    chain = structural.pr_status_chain(by_id(widget)[PR_OPEN])
    assert [d["value"] for d in chain] == ["open", "changes_requested"]
    opened, changed = chain
    assert changed["valid_from_ms"] == opened["valid_from_ms"] + 1  # review predates the PR
    assert changed["ts_inferred"] and not opened["ts_inferred"]
    assert opened["valid_to_ms"] == changed["valid_from_ms"] and opened["status"] == "superseded"
    assert changed["valid_to_ms"] is None and changed["status"] == "active"
    assert changed["_who"] == "eid_aaaa0003" and changed["_quote"] == "Needs tests."
    assert (
        chain[0]["id"]
        == f"dec:pr:github_com_salesforce_Widget_pull_1/status:{opened['valid_from_ms']}"
    )


def test_pr_chain_ends_merged_or_closed_with_an_inferred_time(widget):
    chain = structural.pr_status_chain(by_id(widget)[PR_EXT])
    assert [d["value"] for d in chain] == ["open", "approved", "merged"]
    assert chain[-1]["ts_inferred"] and chain[-1]["_who"] is None
    assert chain[-1]["_field"] == "merged" and chain[-1]["statement"].endswith("is merged")


def test_repeated_status_adds_no_decision(widget):
    pr = by_id(widget)[PR_EXT].model_copy(deep=True)
    review = dict(pr.meta["reviews"][0])
    pr.meta["reviews"] = [review, {**review, "ts_ms": review["ts_ms"] + 60_000}]
    assert [d["value"] for d in structural.pr_status_chain(pr)] == ["open", "approved", "merged"]


def test_only_known_people_get_edges(rows):
    authors = {r["person_id"] for r in rows["link_authored"]}
    assert authors <= {f"eid_aaaa000{i}" for i in range(5)}  # no EMP_ login, no bot
    assert all(r["person_id"].startswith("eid_") for r in rows["link_reviewed"])
    prs = {r["id"]: r for r in rows["upsert_prs"]}
    assert prs[PR_OPEN]["current_status"] == "changes_requested"
    assert prs[PR_EXT]["current_status"] == "merged" and prs[PR_EXT]["external"]


def test_a_link_shared_by_two_products_is_one_node(meta, widget):
    links = [a for a in widget if a.kind == "link"]
    gadget = [a.model_copy(update={"product_hint": "Gadget"}) for a in links]
    both = structural.build_rows(meta, {"Widget": widget, "Gadget": gadget}, OBSERVED)
    assert len(both["upsert_links"]) == len(links)
    edges = [r for r in both["link_in_product"] if r["artifact_id"] == links[0].id]
    assert {r["product_id"] for r in edges} == {"Widget", "Gadget"}
    assert all(r["product_id"] is None for r in both["upsert_links"])


def test_every_load_query_has_a_file():
    for name in structural.LOAD_ORDER:
        assert (QUERIES / f"{name}.cypher").is_file(), name


# --- graph ---------------------------------------------------------------------------------
def total(report, key):
    return sum(r[key] for r in report.values())


@pytest.mark.db
def test_load_is_idempotent(empty_graph, rows):
    first = structural.load(rows, empty_graph)
    before = health.report(empty_graph)
    second = structural.load(rows, empty_graph)
    assert total(first, "nodes_created") > 0 and total(first, "relationships_created") > 0
    assert total(second, "nodes_created") == 0 and total(second, "relationships_created") == 0
    assert health.report(empty_graph) == before
    assert before["duplicate_ids"] == [] and before["isolated_pct"] == 0.0


@pytest.mark.db
def test_loaded_graph_answers_structural_questions(empty_graph, rows):
    structural.load(rows, empty_graph)

    def one(text, **params):
        return db.run_text(text, params, label="test", graph=empty_graph).rows

    chain = one(
        "MATCH (n:Decision)-[:SUPERSEDES]->(o:Decision)-[:AFFECTS]->(p:PR {id: $pr}) "
        "RETURN o.value AS old, n.value AS new, p.current_status AS now LIMIT 5",
        pr=PR_OPEN,
    )
    assert chain == [{"old": "open", "new": "changes_requested", "now": "changes_requested"}]
    reviewed = one(
        "MATCH (p:Person)-[e:REVIEWED]->(:PR {id: $pr}) RETURN p.id AS who, e.state AS s LIMIT 5",
        pr=PR_OPEN,
    )
    assert reviewed == [{"who": "eid_aaaa0003", "s": "CHANGES_REQUESTED"}]
    attended = one("MATCH (p:Person)-[:ATTENDED]->(:Meeting) RETURN p.id AS id ORDER BY id LIMIT 5")
    assert [r["id"] for r in attended] == ["eid_aaaa0002", "eid_aaaa0003"]
    evidence = one(
        "MATCH (d:Decision {value: 'changes_requested'})-[e:EVIDENCED_BY]->(a:PR) "
        "RETURN e.quote AS quote, e.field AS field, a.id AS pr LIMIT 5"
    )
    assert evidence == [{"quote": "Needs tests.", "field": "reviews[0].comment", "pr": PR_OPEN}]


@pytest.mark.db
def test_health_report_counts_the_fixture(empty_graph, rows):
    structural.load(rows, empty_graph)
    r = health.report(empty_graph)
    nodes = {row["labels"]: row["n"] for row in r["nodes"]}
    assert nodes["Person:Entity"] == 5 and nodes["Artifact:PR"] == 2
    assert nodes["Decision"] == 5  # open → changes_requested; open → approved → merged
    chains = {row["external"]: (row["with_chain"], row["prs"]) for row in r["pr_chains"]}
    assert chains == {False: (1, 1), True: (1, 1)}
    assert "isolated nodes: 0.0%" in health.render(r)


@pytest.mark.db
@pytest.mark.parametrize("name", structural.LOAD_ORDER)
def test_load_queries_find_nodes_by_index(empty_graph, name):
    plan = " / ".join(db.explain(name, {"rows": [{}]}, graph=empty_graph))
    assert "Node By Index Scan" in plan
    assert "Label Scan" not in plan and "All Node Scan" not in plan
