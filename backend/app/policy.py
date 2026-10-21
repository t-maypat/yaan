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
# What the table reads (D102): a person's authority, not their job title. people.yaml may set
# it (`authority: lead | qa | member`), since a team's titles ("Tech Lead", "Staff Engineer")
# aren't HERB's; without it, HERB's titles map: the LEADS are leads, a QA Specialist is qa,
# any other title is a member, and no title is unknown (unverified).
AUTHORITIES = ("lead", "qa", "member")


def authority_of(role: str | None, authority: str | None = None) -> str | None:
    if authority:
        if authority not in AUTHORITIES:
            raise ValueError(f"authority must be one of {AUTHORITIES}, not {authority!r}")
        return authority
    if role is None:
        return None
    if role in LEADS:
        return "lead"
    return "qa" if role == "QA Specialist" else "member"


@dataclass(frozen=True)
class Facts:
    """What `fold` knows when it applies a claim. Ids are `eid_…`; sets may be empty."""

    speaker: str | None
    speaker_role: str | None = None
    speaker_in_product: bool = False  # authored anything in the product's channels or PRs
    speaker_authority: str | None = None  # people.yaml's, if set; else from the role
    assignee: str | None = None  # issue assignee at the claim's time
    pr_author: str | None = None
    pr_reviewers: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class Verdict:
    outcome: Outcome
    rule: str
    reason: str


def is_lead(role: str | None, authority: str | None = None) -> bool:
    """May act for the team beyond their own work (respond to any alert, D100)."""
    return authority_of(role, authority) == "lead"


def decide(
    kind: Literal["issue_status", "pr_action"], facts: Facts, unknown: str = "unverified"
) -> Verdict:
    """The §9.4 authority table as code. `unknown` (people.yaml's `unknown_speakers`, D117):
    how a speaker with no role or authority counts: `unverified` applies the claim marked so;
    `conflict` lets it change only their own work."""
    if not facts.speaker:
        return Verdict("unverified", "speaker", "the speaker is not a resolved person")
    level = authority_of(facts.speaker_role, facts.speaker_authority)
    if level is None:
        if unknown != "conflict":
            return Verdict(
                "unverified", "role", f"{facts.speaker} has no role in employee metadata"
            )
        if kind == "issue_status" and facts.speaker == facts.assignee:
            return Verdict("authorized", "assignee", f"{facts.speaker} is the issue's assignee")
        if kind == "pr_action" and facts.speaker == facts.pr_author:
            return Verdict("authorized", "author", f"{facts.speaker} authored the PR")
        if kind == "pr_action" and facts.speaker in facts.pr_reviewers:
            return Verdict("authorized", "reviewer", f"{facts.speaker} reviewed the PR")
        return Verdict(
            "unauthorized",
            "unknown_speaker",
            f"{facts.speaker} has no role or authority in people.yaml, and unknown speakers "
            "may change only their own work (unknown_speakers: conflict)",
        )
    who = f"{facts.speaker} ({facts.speaker_role or level})"
    if kind == "issue_status":
        if facts.speaker == facts.assignee:
            return Verdict("authorized", "assignee", f"{who} is the issue's assignee")
        if level in ("lead", "qa") and facts.speaker_in_product:
            return Verdict("authorized", "role", f"{who} works on the product")
        if facts.speaker_in_product and facts.assignee is None:
            return Verdict("unverified", "no_assignee", f"no assignee recorded; {who} is on it")
        return Verdict("unauthorized", "table", f"{who} is not the assignee, QA or a lead")
    if kind == "pr_action":
        if facts.speaker == facts.pr_author:
            return Verdict("authorized", "author", f"{who} authored the PR")
        if facts.speaker in facts.pr_reviewers:
            return Verdict("authorized", "reviewer", f"{who} reviewed the PR")
        if level == "lead" and facts.speaker_in_product:
            return Verdict("authorized", "role", f"{who} leads on the product")
        if facts.speaker_in_product:
            # HERB's develop channels decide by consensus among the product's engineers.
            return Verdict("authorized", "team", f"{who} works on the product")
        return Verdict("unauthorized", "table", f"{who} has no part in the PR or the product")
    raise ValueError(f"unknown claim kind {kind!r}")
