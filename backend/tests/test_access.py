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
