"""Viewer-scoped answers (R2 W7, D077; provenance-aware since D100; CONTEXT §9.11 personas).

One choke point: every tool result passes through `Access.redact` before the model sees it or
the loop records it, and so does every API or MCP read made for an authenticated viewer, so a
hidden source can't be read, quoted or cited. The answer reports how many sources were hidden.
With no viewer nothing is filtered (the demo's default; cached answers stay valid).

The policy is `permissions.yaml` (repo root). HERB has no access rules, so it is synthetic and
labeled as such: messages in channels whose names match `private_channels` are visible to the
channel's members (people who posted there), and meeting transcripts to the people who attended
or spoke in them. Everything else is visible to everyone in the company.

Derived nodes inherit from their sources (D100): an issue, claim, decision or alert is visible
only if the viewer may see at least one artifact it was derived from (`derived_sources.cypher`).
Without that, an issue reported only in a private channel kept its title, status and customers.

Fail-closed: an id the policy can't place (not in the graph, or a derived node with no source)
is hidden. When anything is hidden, the result's counts (`counts_by_status`, `count`, a
ranking's `n` ...) are left out, since they were computed over everything; a note says so.

Org mode (H1, D109) reads a team's real access instead of the synthetic policy: a channel is
`public` or `private` (from Slack, or declared in connectors.yaml), a private one is read by
its MEMBER_OF people, an item outside any channel (a pull request) by its source's declared
audience, and anything whose access isn't known is hidden.

An audience (`Access.audience`) is a set of readers rather than one person: everyone in one
channel (a Slack answer posted there, S1), or everyone in the company (text that leaves Yaan:
alert cards and drafted comments, S3). It sees what is open to all, plus that channel's own.
"""

import re
from dataclasses import dataclass, field
from fnmatch import fnmatch
from functools import lru_cache
from typing import Any

import yaml

from app import db, org
from app.config import REPO_ROOT

POLICY = REPO_ROOT / "permissions.yaml"
ARTIFACT_ID = re.compile(r"^(msg|mtg|chat|doc|pr|url):")
DERIVED_ID = re.compile(r"^(iss|dec|clm|alert):")
ID_KEYS = ("artifact_id", "id", "issue_id", "decision_id", "report_id")  # a row's own ids
AGGREGATES = frozenset(
    {"counts_by_status", "counts_by_kind", "count", "total_ranked", "reopen_count", "size"}
)
RANKED = frozenset({"n", "rank"})  # in a ranking's rows: computed over everything
HIDDEN_NOTE = "some sources are hidden from you, so counts computed over all of them are left out"
_DROP = object()


@dataclass(frozen=True)
class Policy:
    private_channels: tuple[str, ...] = ()
    meetings: str = "attendees"


@lru_cache
def policy() -> Policy:
    raw = yaml.safe_load(POLICY.read_text(encoding="utf-8")) if POLICY.is_file() else {}
    return Policy(tuple(raw.get("private_channels") or ()), raw.get("meetings") or "attendees")


def scoped(value: str) -> bool:
    return bool(ARTIFACT_ID.match(value) or DERIVED_ID.match(value))


def source_of(artifact_id: str) -> str:
    """The tool an org-mode artifact came from: `pr:github-…` -> github."""
    return artifact_id.partition(":")[2].split("-", 1)[0]


@dataclass
class Access:
    viewer: str | None  # a person; None for an audience (see `audience`)
    graph: str | None = None
    rules: Policy = field(default_factory=policy)
    channel: str | None = None  # an audience's channel: everyone who reads it
    _known: dict[str, bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.viewer is None:  # an audience: the channel its readers share, no meetings
            self.channels = {self.channel} if self.channel else set()
            self.meetings = set()
            return

        def ids(name: str) -> set[str]:
            rows = db.run_all(name, {"viewer": self.viewer}, cap=50_000, graph=self.graph)
            return {r["id"] for r in rows}

        self.channels = ids("viewer_member_channels" if org.active() else "viewer_channels")
        self.meetings = ids("viewer_meetings")

    @classmethod
    def audience(cls, channel: str | None = None, graph: str | None = None) -> "Access":
        """What every reader of `channel` may see: what is open to the whole company, plus
        that channel's own messages. With no channel, what the whole company may see."""
        return cls(None, graph, channel=channel)

    def private(self, channel: str | None) -> bool:
        return bool(channel) and any(fnmatch(channel, p) for p in self.rules.private_channels)

    def allowed(self, ids: set[str]) -> set[str]:
        """The ids this viewer may see (each looked up once). Unknown ids are hidden."""
        unknown = sorted(i for i in ids if i not in self._known)
        derived = [i for i in unknown if DERIVED_ID.match(i)]
        sources: dict[str, set[str]] = {}
        if derived:
            for r in db.run_all(
                "derived_sources", {"ids": derived}, cap=200_000, graph=self.graph
            ):
                sources.setdefault(r["id"], set()).add(r["source"])
        artifacts = sorted(
            {i for i in unknown if ARTIFACT_ID.match(i)} | {s for v in sources.values() for s in v}
        )
        self._place(artifacts)
        for i in derived:  # visible iff one of its sources is
            self._known[i] = any(self._known.get(s, False) for s in sources.get(i, ()))
        return {i for i in ids if self._known.get(i, False)}

    def _place(self, artifact_ids: list[str]) -> None:
        todo = [a for a in artifact_ids if a not in self._known]
        if not todo:
            return
        rows = db.run("artifact_access", {"ids": todo, "limit": len(todo)}, graph=self.graph)
        for r in rows.rows:
            if r["kind"] == "meeting" and self.rules.meetings == "attendees":
                ok = r["id"] in self.meetings
            elif org.active():
                ok = self.org_visible(r)
            elif self.private(r["channel"]):
                ok = r["channel_id"] in self.channels
            else:
                ok = True
            self._known[r["id"]] = ok
        for a in todo:  # not in the graph: nothing proves the viewer may see it
            self._known.setdefault(a, False)

    def org_visible(self, r: dict[str, Any]) -> bool:
        """Org mode: a channel's synced or declared visibility, else the source's declared
        audience. Unknown is hidden: a Slack channel before its first sync, an undeclared
        source."""
        if r["channel_id"]:
            if r["visibility"] == "public":
                return True
            return r["visibility"] == "private" and r["channel_id"] in self.channels
        seen = org.source_audiences().get(source_of(r["id"]))
        if seen is None:
            return False
        return seen.visibility == "everyone" or self.viewer in seen.members

    def redact(self, value: Any) -> tuple[Any, set[str]]:
        """The result without what the viewer can't see, and the ids removed. A row that is
        (or names, by one of its own ids) a hidden artifact or derived node goes; so does a
        hidden id in a list. With anything hidden, counts over everything go too."""
        found: set[str] = set()
        _ids(value, found)
        hidden = found - self.allowed(found)
        if not hidden:
            return value, set()

        def walk(v: Any, in_ranking: bool = False) -> Any:
            if isinstance(v, dict):
                if any(isinstance(v.get(k), str) and v[k] in hidden for k in ID_KEYS):
                    return _DROP
                out = {}
                for k, x in v.items():
                    if k in AGGREGATES or (in_ranking and k in RANKED):
                        continue
                    if (w := walk(x, k == "ranking")) is not _DROP:
                        out[k] = w
                return out
            if isinstance(v, list):
                return [w for x in v if (w := walk(x, in_ranking)) is not _DROP]
            if isinstance(v, str) and v in hidden:
                return _DROP
            return v

        out = walk(value)
        if out is _DROP:  # e.g. get_artifact on a hidden source
            out = {"hidden_by_permissions": True}
        elif isinstance(out, dict):
            out["counts_hidden"] = HIDDEN_NOTE
        return out, hidden


def _ids(value: Any, found: set[str]) -> None:
    if isinstance(value, dict):
        for v in value.values():
            _ids(v, found)
    elif isinstance(value, list):
        for v in value:
            _ids(v, found)
    elif isinstance(value, str) and scoped(value):
        found.add(value)
