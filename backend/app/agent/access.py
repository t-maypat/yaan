"""Viewer-scoped answers (R2 W7, D077; CONTEXT §9.11 permission personas).

One choke point: every tool result passes through `Access.redact` before the model sees it or
the loop records it, so a hidden source can't be read, quoted or cited. The answer reports how
many sources were hidden. With no viewer nothing is filtered (today's behavior; cached answers
stay valid).

The policy is `permissions.yaml` (repo root). HERB has no access rules, so it is synthetic and
labeled as such: messages in channels whose names match `private_channels` are visible to the
channel's members (people who posted there), and meeting transcripts to the people who attended
or spoke in them. Everything else is visible to everyone in the company.

Known limit: counts and rankings the graph computes (`rank`, `who_knows`) still count every
artifact; the redaction removes the hidden items they list, not their contribution to a number.
"""

import re
from dataclasses import dataclass, field
from fnmatch import fnmatch
from functools import lru_cache
from typing import Any

import yaml

from app import db
from app.config import REPO_ROOT

POLICY = REPO_ROOT / "permissions.yaml"
ARTIFACT_ID = re.compile(r"^(msg|mtg|chat|doc|pr|url):")
_DROP = object()


@dataclass(frozen=True)
class Policy:
    private_channels: tuple[str, ...] = ()
    meetings: str = "attendees"


@lru_cache
def policy() -> Policy:
    raw = yaml.safe_load(POLICY.read_text(encoding="utf-8")) if POLICY.is_file() else {}
    return Policy(tuple(raw.get("private_channels") or ()), raw.get("meetings") or "attendees")


@dataclass
class Access:
    viewer: str
    graph: str | None = None
    rules: Policy = field(default_factory=policy)
    _known: dict[str, bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        def ids(name: str) -> set[str]:
            rows = db.run(name, {"viewer": self.viewer, "limit": 5000}, graph=self.graph).rows
            return {r["id"] for r in rows}

        self.channels = ids("viewer_channels")
        self.meetings = ids("viewer_meetings")

    def private(self, channel: str | None) -> bool:
        return bool(channel) and any(fnmatch(channel, p) for p in self.rules.private_channels)

    def allowed(self, artifact_ids: set[str]) -> set[str]:
        """The ids this viewer may see (looked up once per id)."""
        unknown = sorted(a for a in artifact_ids if a not in self._known)
        if unknown:
            rows = db.run(
                "artifact_access", {"ids": unknown, "limit": len(unknown)}, graph=self.graph
            ).rows
            for r in rows:
                if r["kind"] == "meeting" and self.rules.meetings == "attendees":
                    ok = r["id"] in self.meetings
                elif self.private(r["channel"]):
                    ok = r["channel_id"] in self.channels
                else:
                    ok = True
                self._known[r["id"]] = ok
            for a in unknown:  # not in the graph: nothing to hide
                self._known.setdefault(a, True)
        return {a for a in artifact_ids if self._known.get(a, True)}

    def redact(self, value: Any) -> tuple[Any, set[str]]:
        """The tool result without what the viewer can't see, and the ids removed. A row that
        is a hidden artifact (or points at one) goes; so does a hidden id in a list."""
        found: set[str] = set()
        _ids(value, found)
        hidden = found - self.allowed(found)
        if not hidden:
            return value, set()

        def walk(v: Any) -> Any:
            if isinstance(v, dict):
                aid = v.get("artifact_id") or v.get("id")
                if isinstance(aid, str) and aid in hidden:
                    return _DROP
                return {k: w for k, x in v.items() if (w := walk(x)) is not _DROP}
            if isinstance(v, list):
                return [w for x in v if (w := walk(x)) is not _DROP]
            if isinstance(v, str) and v in hidden:
                return _DROP
            return v

        out = walk(value)
        if out is _DROP:  # e.g. get_artifact on a hidden source
            out = {"hidden_by_permissions": True}
        return out, hidden


def _ids(value: Any, found: set[str]) -> None:
    if isinstance(value, dict):
        for v in value.values():
            _ids(v, found)
    elif isinstance(value, list):
        for v in value:
            _ids(v, found)
    elif isinstance(value, str) and ARTIFACT_ID.match(value):
        found.add(value)
