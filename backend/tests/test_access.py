"""Viewer-scoped answers (W7, D077): the policy, the redaction choke point and its queries.
On the agent_company fixture graph: bug-Widget made private, Ann (eid_a1) never posted there,
Bo (eid_b2) did; the meeting mtg:wd_1 had Ann and Bo, not Cy (eid_c3)."""

from pathlib import Path

import pytest

from app import db
from app.agent import access, tools

pytestmark = pytest.mark.db
FIXTURE = Path(__file__).parent / "fixtures" / "agent_company.cypher"
RULES = access.Policy(private_channels=("bug-*",), meetings="attendees")


@pytest.fixture
def company(empty_graph):
    db.run_text(FIXTURE.read_text(encoding="utf-8"), label="f", graph=empty_graph, readonly=False)
    return empty_graph


def test_the_repo_policy_is_labeled_synthetic_and_parses():
    access.policy.cache_clear()
    p = access.policy()
    assert p.private_channels == ("planning-*-PM",) and p.meetings == "attendees"
    assert "SYNTHETIC" in access.POLICY.read_text(encoding="utf-8")


def test_a_private_channel_is_for_its_members_and_a_meeting_for_its_attendees(company):
    ann = access.Access("eid_a1", company, RULES)
    bo = access.Access("eid_b2", company, RULES)
    cy = access.Access("eid_c3", company, RULES)
    ids = {"msg:r1", "msg:m1", "mtg:wd_1", "pr:w1", "msg:not-in-graph"}
    assert ann.allowed(ids) == {"msg:m1", "mtg:wd_1", "pr:w1"}
    # posted in bug-Widget, attended the meeting; an id nobody can place stays hidden (D100)
    assert bo.allowed(ids) == ids - {"msg:not-in-graph"}
    assert cy.allowed(ids) == {"msg:r1", "msg:m1", "pr:w1"}


def test_redact_removes_hidden_rows_ids_and_whole_results(company):
    ann = access.Access("eid_a1", company, RULES)
    out, hidden = ann.redact(tools.list_issues("Widget", graph=company))
    # the issue was reported and resolved only in bug-Widget: with its sources, it goes too
    # (D100: it used to stay, title, status and customers included)
    assert hidden == {"msg:r1", "msg:r2", "iss:Widget:abc"}
    assert out["issues"] == [] and "counts_by_status" not in out
    assert "left out" in out["counts_hidden"]
    seen, none = access.Access("eid_b2", company, RULES).redact(
        tools.list_issues("Widget", graph=company)
    )
    assert [i["title"] for i in seen["issues"]] == ["Slow export"] and none == set()
    whole, gone = ann.redact(tools.get_artifact("msg:r1", company))
    assert whole == {"hidden_by_permissions": True} and gone == {"msg:r1"}
    same, none = access.Access("eid_b2", company, RULES).redact({"ids": ["msg:r1"]})
    assert same == {"ids": ["msg:r1"]} and none == set()


def test_a_derived_node_is_visible_iff_one_of_its_sources_is(company):
    ann = access.Access("eid_a1", company, RULES)
    # clm:3 comes from msg:r3 in the open planning channel; the issue's claims come from
    # bug-Widget; a structural PR decision's source is the PR record; a derived id with no
    # source is hidden, and so is an extracted decision with no evidence: the PR it AFFECTS
    # is its subject, not its source
    got = ann.allowed(
        {"clm:3", "dec:pr:w1/status:4100", "dec:pr:w1/action:8200", "clm:1", "iss:Widget:abc"}
    )
    assert got == {"clm:3", "dec:pr:w1/status:4100"}
    assert access.Access("eid_b2", company, RULES).allowed({"iss:Widget:abc", "clm:1"}) == {
        "iss:Widget:abc",
        "clm:1",
    }


def test_rankings_lose_their_counts_when_something_is_hidden():
    a = access.Access.__new__(access.Access)
    a._known = {"msg:1": True, "msg:2": False}
    a.allowed = lambda ids: {i for i in ids if a._known.get(i)}
    out, hidden = a.redact(
        {
            "total_ranked": 2,
            "ranking": [
                {"id": "eid_aaaaaaaa", "n": 2, "rank": 1, "items": ["msg:1", "msg:2"]},
            ],
        }
    )
    assert hidden == {"msg:2"}
    assert out["ranking"] == [{"id": "eid_aaaaaaaa", "items": ["msg:1"]}]
    assert "total_ranked" not in out and out["counts_hidden"]


def test_a_row_that_names_a_hidden_item_under_any_key_goes():
    """D131 (F10): queries return ids as `via`, `subject_id`, `trigger_id`, `thread_id` ...;
    only five keys were checked, so a row about a hidden item kept its other fields."""
    a = access.Access.__new__(access.Access)
    a._known = {"msg:open": True, "msg:private": False, "iss:W:1": True}
    a.allowed = lambda ids: {i for i in ids if a._known.get(i)}
    out, hidden = a.redact(
        {
            "experts": [
                {"person": "eid_aaaaaaaa", "via": "msg:private", "summary": "fixed the leak"},
                {"person": "eid_bbbbbbbb", "via": "msg:open", "summary": "shipped it"},
            ],
            "alerts": [{"trigger_id": "msg:private", "subject_id": "iss:W:1", "why": "reopened"}],
            "sources": ["msg:open", "msg:private"],  # a list of references: just the item goes
        }
    )
    assert hidden == {"msg:private"}
    assert [e["person"] for e in out["experts"]] == ["eid_bbbbbbbb"]
    assert out["alerts"] == [] and out["sources"] == ["msg:open"]
    top, _ = a.redact({"subject_id": "msg:private", "chain": []})  # the whole result is about it
    assert top == {"hidden_by_permissions": True}


def test_search_ranks_only_what_the_viewer_may_read_before_its_limit(company):
    """D131 (F10): search took its top k and redaction emptied it for a restricted viewer. With
    the viewer's reach in the query, the k results are ones they may read."""
    chunk = "(:Chunk {{id: 'chk:{0}', artifact_id: '{0}', idx: 0, field: 'text', text: 'zebra'}})"
    for aid in ("msg:m1", "msg:r1", "msg:r2", "msg:r3"):  # m1 open, r1-r2 private, r3 open
        db.run_text(
            f"MATCH (a:Artifact {{id: '{aid}'}}) CREATE (a)-[:HAS_CHUNK]->{chunk.format(aid)}",
            label="chunk",
            graph=company,
            readonly=False,
        )

    def top(reach=None, k=1):
        out = tools.search_artifacts("zebra", mode="fulltext", k=k, graph=company, reach=reach)
        return out["evidence_ids"]

    ann = access.Access("eid_a1", company, RULES)  # never posted in bug-Widget
    everything = top(k=4)
    readable = [a for a in everything if a in ann.allowed(set(everything))]
    assert sorted(everything) == ["msg:m1", "msg:r1", "msg:r2", "msg:r3"]
    assert sorted(readable) == ["msg:m1", "msg:r3"]
    # filtered before the limit: k readable results, whatever ranks above them
    one = top(ann.reach(), 1)
    assert len(one) == 1 and set(one) <= set(readable)
    assert sorted(top(ann.reach(), 2)) == sorted(readable)
    assert sorted(top(access.Access("eid_b2", company, RULES).reach(), 4)) == sorted(everything)
