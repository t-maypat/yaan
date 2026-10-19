"""Authority policy, Lore's "Bailiff" (Phase 8.3, R1-3 / D039).

A pure function: given a claim's kind, its speaker and the facts about the thing it changes,
decide whether the claim may change the decision chain. The LLM never decides authority.

Three outcomes:
- `authorized`: the speaker holds a role the table allows; the claim applies.
- `unverified`: a fact needed to decide is missing (e.g. the speaker isn't a resolved
  person). The claim still applies, at lower confidence, with the reason stored.
- `unauthorized`: the speaker is known and the table says no. The claim does not change
  state; the fold records CONFLICTS_WITH and raises a conflict Alert.

Spec: docs/CONTEXT.md §9.4 (table), PLAN.md R1-3.
"""

from dataclasses import dataclass, field
from typing import Literal

Outcome = Literal["authorized", "unverified", "unauthorized"]

LEADS = {"Engineering Lead", "VP of Engineering", "Product Manager", "Chief Product Officer"}
ISSUE_ROLES = LEADS | {"QA Specialist"}
UNVERIFIED_CONFIDENCE = 0.6


@dataclass(frozen=True)
class Facts:
    """What `fold` knows when it applies a claim. Ids are `eid_…`; sets may be empty."""

    speaker: str | None
    speaker_role: str | None = None
    speaker_in_product: bool = False  # authored anything in the product's channels or PRs
    assignee: str | None = None  # issue assignee at the claim's time
    pr_author: str | None = None
    pr_reviewers: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class Verdict:
    outcome: Outcome
    rule: str
    reason: str


def is_lead(role: str | None) -> bool:
    """A role that may act for the team beyond its own work (respond to any alert, D100)."""
    return role in LEADS


def decide(kind: Literal["issue_status", "pr_action"], facts: Facts) -> Verdict:
    """The §9.4 authority table as code."""
    if not facts.speaker:
        return Verdict("unverified", "speaker", "the speaker is not a resolved person")
    if facts.speaker_role is None:
        return Verdict("unverified", "role", f"{facts.speaker} has no role in employee metadata")
    who = f"{facts.speaker} ({facts.speaker_role})"
    if kind == "issue_status":
        if facts.speaker == facts.assignee:
            return Verdict("authorized", "assignee", f"{who} is the issue's assignee")
        if facts.speaker_role in ISSUE_ROLES and facts.speaker_in_product:
            return Verdict("authorized", "role", f"{who} works on the product")
        if facts.speaker_in_product and facts.assignee is None:
            return Verdict("unverified", "no_assignee", f"no assignee recorded; {who} is on it")
        return Verdict("unauthorized", "table", f"{who} is not the assignee, QA or a lead")
    if kind == "pr_action":
        if facts.speaker == facts.pr_author:
            return Verdict("authorized", "author", f"{who} authored the PR")
        if facts.speaker in facts.pr_reviewers:
            return Verdict("authorized", "reviewer", f"{who} reviewed the PR")
        if facts.speaker_role in LEADS and facts.speaker_in_product:
            return Verdict("authorized", "role", f"{who} leads on the product")
        if facts.speaker_in_product:
            # HERB's develop channels decide by consensus among the product's engineers.
            return Verdict("authorized", "team", f"{who} works on the product")
        return Verdict("unauthorized", "table", f"{who} has no part in the PR or the product")
    raise ValueError(f"unknown claim kind {kind!r}")
