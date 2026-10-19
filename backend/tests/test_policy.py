"""Phase 8.3: the authority policy's three outcomes (R1-3)."""

import pytest

from app import policy
from app.policy import Facts, decide


def test_an_unresolved_speaker_is_unverified_not_rejected():
    v = decide("issue_status", Facts(speaker=None))
    assert v.outcome == "unverified" and v.rule == "speaker"
    assert decide("pr_action", Facts(speaker="eid_x")).outcome == "unverified"  # no role


def test_issue_status_by_assignee_qa_or_lead_is_authorized():
    base = {"speaker_in_product": True, "assignee": "eid_a"}
    assert decide("issue_status", Facts("eid_a", "Software Engineer", **base)).rule == "assignee"
    assert decide("issue_status", Facts("eid_q", "QA Specialist", **base)).outcome == "authorized"
    assert (
        decide("issue_status", Facts("eid_l", "Engineering Lead", **base)).outcome == "authorized"
    )


def test_issue_status_by_another_engineer_is_unauthorized():
    f = Facts("eid_b", "Software Engineer", speaker_in_product=True, assignee="eid_a")
    v = decide("issue_status", f)
    assert v.outcome == "unauthorized" and "eid_b" in v.reason


def test_issue_status_without_a_recorded_assignee_is_unverified():
    f = Facts("eid_b", "Software Engineer", speaker_in_product=True)
    assert decide("issue_status", f).outcome == "unverified"


def test_pr_action_by_author_reviewer_or_team_is_authorized_outsider_is_not():
    pr = {"pr_author": "eid_a", "pr_reviewers": frozenset({"eid_r"})}
    assert decide("pr_action", Facts("eid_a", "Software Engineer", **pr)).rule == "author"
    assert decide("pr_action", Facts("eid_r", "Software Engineer", **pr)).rule == "reviewer"
    team = Facts("eid_t", "Software Engineer", speaker_in_product=True, **pr)
    assert decide("pr_action", team).outcome == "authorized"
    outsider = Facts("eid_o", "Marketing Manager", **pr)
    assert decide("pr_action", outsider).outcome == "unauthorized"


def test_unknown_kind_fails_loudly():
    with pytest.raises(ValueError):
        decide("feature_status", Facts("eid_a", "Product Manager"))  # type: ignore[arg-type]


# --- authority, not titles (D102) ------------------------------------------------------------
def test_herb_titles_map_to_authority_and_an_explicit_one_wins():
    assert policy.authority_of("Engineering Lead") == "lead"
    assert policy.authority_of("QA Specialist") == "qa"
    assert policy.authority_of("Software Engineer") == "member"
    assert policy.authority_of(None) is None
    assert policy.authority_of("Staff Engineer", "lead") == "lead"  # a team's own title
    with pytest.raises(ValueError, match="authority must be one of"):
        policy.authority_of("Staff Engineer", "boss")


def test_a_team_lead_under_another_title_may_dismiss_and_respond():
    staff = policy.Facts(
        speaker="eid_s", speaker_role="Staff Engineer", speaker_in_product=True, assignee="eid_x"
    )
    assert policy.decide("issue_status", staff).outcome == "unauthorized"
    lead = policy.Facts(**{**staff.__dict__, "speaker_authority": "lead"})
    verdict = policy.decide("issue_status", lead)
    assert verdict.outcome == "authorized" and "Staff Engineer" in verdict.reason
    assert policy.is_lead("Staff Engineer", "lead") and not policy.is_lead("Staff Engineer")
