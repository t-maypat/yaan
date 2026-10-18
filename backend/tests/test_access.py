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
    assert ann.allowed(ids) == {"msg:m1", "mtg:wd_1", "pr:w1", "msg:not-in-graph"}
    assert bo.allowed(ids) == ids  # posted in bug-Widget, attended the meeting
    assert cy.allowed(ids) == {"msg:r1", "msg:m1", "pr:w1", "msg:not-in-graph"}


def test_redact_removes_hidden_rows_ids_and_whole_results(company):
    ann = access.Access("eid_a1", company, RULES)
    out, hidden = ann.redact(tools.list_issues("Widget", graph=company))
    assert hidden == {"msg:r1", "msg:r2"}  # the report and resolution messages in bug-Widget
    [issue] = out["issues"]
    assert all(h.get("artifact_id") not in hidden for h in issue["history"])
    assert issue["title"] == "Slow export"  # the issue itself stays: only sources are hidden
    whole, gone = ann.redact(tools.get_artifact("msg:r1", company))
    assert whole == {"hidden_by_permissions": True} and gone == {"msg:r1"}
    same, none = access.Access("eid_b2", company, RULES).redact({"ids": ["msg:r1"]})
    assert same == {"ids": ["msg:r1"]} and none == set()
