"""Org mode (D097): Yaan on a team's own tools instead of the HERB demo company.

`LORE_DATA_MODE=org` switches the live path and the connectors from the demo to a team's data:
- writes go to `LORE_ORG_GRAPH` (default `yaan_org`) and never to a HERB graph;
- items need no "[synthetic demo]" label and keep their own ids (`msg:slack-…`, not
  `msg:synthetic-slack-…`);
- people come from `people.yaml` (one entry per person, with their account in each tool and
  their role for the authority policy), or are registered the first time they appear, under
  an id derived from their account;
- the product and the channels `connectors.yaml` declares are created in the graph by
  `prepare` (`lore init`, and every connector sync).

A channel's kind says what Yaan reads from it: `bug` (support), `develop` (dev), `planning`, or
`mixed` (a channel where bugs and pull requests are both discussed: every event type is read).
By default the name's prefix says (`bug-support`, HERB's convention); a mapping may instead
name its channel freely and give the kind: `backend: {channel: backend, kind: mixed}` (D116).

Nothing here runs in demo mode, so the demo and the evaluation behave exactly as before.
"""

import hashlib
import json
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app import db
from app.config import REPO_ROOT, get_settings

PEOPLE_FILE = REPO_ROOT / "people.yaml"
STAGES = {"planning": "planning", "develop": "dev", "bug": "support"}  # = structural.STAGES
KINDS = {**STAGES, "mixed": "mixed"}  # a declared kind -> the channel's stage (D116)
EID = re.compile(r"^eid_[0-9a-f]{8}$")  # the linker's mention pattern (@eid_…) needs this shape
SOURCE_NAMES = {
    "slack": "Slack",
    "github": "GitHub",
    "jira": "Jira",
    "notion": "Notion",
    "linear": "Linear",
    "gitlab": "GitLab",
    "confluence": "Confluence",
    "gdrive": "Google Drive",
    "gmail": "Gmail",
    "gmeet": "Google Meet",
    "zoom": "Zoom",
    "discord": "Discord",
}


class OrgError(ValueError):
    """Org mode can't go on: a bad people.yaml or connectors.yaml entry. Says what to fix."""


def active() -> bool:
    return get_settings().lore_data_mode == "org"


def eid_for(key: str) -> str:
    """A stable person id from an email, a name or `source:account`: `eid_` + 8 hex digits."""
    return "eid_" + hashlib.sha1(key.strip().lower().encode("utf-8")).hexdigest()[:8]


# --- people.yaml: the roster -----------------------------------------------------------------
@lru_cache
def roster() -> dict[str, dict[str, Any]]:
    """people.yaml as {eid: person}, read once (restart to pick up an edit)."""
    return parse_roster(PEOPLE_FILE)


def parse_roster(path: Path) -> dict[str, dict[str, Any]]:
    """{eid: person} from a people.yaml. Each entry: name (required), email, role, location,
    org, id (optional, `eid_` + 8 hex digits), accounts ({tool: account id, or a list})."""
    if not path.is_file():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = raw.get("people") or []
    if not isinstance(entries, list):
        raise OrgError(f"{path.name}: `people` must be a list")
    rules = authority_rules(path, raw)
    out: dict[str, dict[str, Any]] = {}
    for n, e in enumerate(entries, 1):
        if not isinstance(e, dict) or not e.get("name"):
            raise OrgError(f"{path.name}: person {n} needs a name")
        eid = e.get("id") or eid_for(e.get("email") or e["name"])
        if not EID.match(eid):
            raise OrgError(f"{path.name}: {e['name']}'s id {eid!r} must look like eid_1a2b3c4d")
        if eid in out:
            raise OrgError(f"{path.name}: {e['name']} and {out[eid]['name']} share the id {eid}")
        accounts = {}
        for tool, ids in (e.get("accounts") or {}).items():
            if tool not in SOURCE_NAMES:
                raise OrgError(f"{path.name}: {e['name']} has an account in unknown tool {tool!r}")
            accounts[tool] = [str(i) for i in (ids if isinstance(ids, list) else [ids])]
        out[eid] = {
            "id": eid,
            "name": e["name"],
            "role": e.get("role"),  # None: unknown, which the policy treats as unverified
            "authority": authority(path, e) or by_title(rules, e.get("role")),
            "email": e.get("email") or "",  # sign-in by proxy header (auth.py); not in the graph
            "location": e.get("location") or "",
            "org": e.get("org") or "",
            "accounts": accounts,
        }
    return out


def authority_rules(path: Path, raw: dict[str, Any]) -> list[tuple[str, str]]:
    """people.yaml's `authority_rules` (D117): {lead | qa | member: [title patterns]}, so a
    team's own titles map once ("*Lead*", "Head of *"), not person by person. Patterns are
    globs, case-insensitive; the first authority listed that matches wins."""
    from app import policy

    rules = raw.get("authority_rules") or {}
    if not isinstance(rules, dict):
        raise OrgError(f"{path.name}: `authority_rules` maps lead / qa / member to title patterns")
    out = []
    for level, patterns in rules.items():
        if level not in policy.AUTHORITIES:
            raise OrgError(f"{path.name}: authority_rules has {level!r}: use {policy.AUTHORITIES}")
        out += [(level, str(p)) for p in (patterns if isinstance(patterns, list) else [patterns])]
    return out


def by_title(rules: list[tuple[str, str]], role: str | None) -> str | None:
    """The authority a role's title gets from the rules, or None (then the policy reads the
    title itself: HERB's titles)."""
    if not role:
        return None
    from fnmatch import fnmatch

    return next((level for level, p in rules if fnmatch(role.casefold(), p.casefold())), None)


UNKNOWN = ("unverified", "conflict")


@lru_cache
def unknown_speakers() -> str:
    """people.yaml's `unknown_speakers` (D117): how a status change by someone with no role or
    authority counts. `unverified` (the default): it applies, marked unverified. `conflict`:
    only on their own work (an issue they were assigned, a PR they wrote or reviewed);
    anything else is refused and raises a conflict alert, as for a member."""
    if not PEOPLE_FILE.is_file():
        return "unverified"
    raw = yaml.safe_load(PEOPLE_FILE.read_text(encoding="utf-8")) or {}
    value = raw.get("unknown_speakers", "unverified")
    if value not in UNKNOWN:
        raise OrgError(f"{PEOPLE_FILE.name}: unknown_speakers must be one of {UNKNOWN}")
    return value


def authority(path: Path, e: dict[str, Any]) -> str | None:
    """`authority: lead | qa | member` (D102): what the policy reads, so a team's own titles
    work. Without it the role's HERB title decides (policy.authority_of)."""
    from app import policy

    value = e.get("authority")
    if value is not None and value not in policy.AUTHORITIES:
        raise OrgError(
            f"{path.name}: {e['name']}'s authority {value!r} must be one of {policy.AUTHORITIES}"
        )
    return value


@lru_cache
def accounts() -> dict[tuple[str, str], str]:
    """(tool, account id) -> eid, from people.yaml; one account belongs to one person."""
    out: dict[tuple[str, str], str] = {}
    for eid, p in roster().items():
        for tool, ids in p["accounts"].items():
            for account in ids:
                if (tool, account) in out:
                    raise OrgError(f"people.yaml: the {tool} account {account!r} is listed twice")
                out[(tool, account)] = eid
    return out


# --- people met in the tools ----------------------------------------------------------------
_hints: dict[str, dict[str, Any]] = {}  # eid -> the name and account a connector saw


def person_id(source: str, account: str | None, name: str | None = None) -> str:
    """The person behind an account: the people.yaml entry that lists it, else a person
    registered under an id derived from the account (kept as a hint until ensure_people)."""
    account = str(account) if account else None
    if account and (eid := accounts().get((source, account))):
        return eid
    key = f"{source}:{account or 'unknown'}"
    eid = eid_for(key)
    if eid in roster():  # 32-bit ids: an unlisted account must not inherit a listed person
        raise OrgError(
            f"the {source} account {account!r} hashes to {eid}, which people.yaml gives to "
            f"{roster()[eid]['name']}: list the account under its person in people.yaml"
        )
    label = (
        f"{SOURCE_NAMES.get(source, source)} user {account}"
        if account
        else (f"Unknown {SOURCE_NAMES.get(source, source)} user")
    )
    hint = _hints.setdefault(eid, {"name": label, "source": source, "account": account})
    if name and hint["name"] == label:
        hint["name"] = name
    return eid


def directory_file(graph: str) -> Path:
    return get_settings().data_dir / "live" / graph / "people.json"


def directory(graph: str) -> dict[str, dict[str, Any]]:
    """Everyone org mode knows in `graph`: people registered from the tools, then people.yaml
    (which wins for anyone in both)."""
    path = directory_file(graph)
    met = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    listed = {eid: {k: v for k, v in p.items() if k != "accounts"} for eid, p in roster().items()}
    return met | listed


def ensure_people(graph: str, eids: Iterable[str | None]) -> list[str]:
    """Register the people a connector met who aren't in the graph yet: a Person node each,
    and a row in data/live/<graph>/people.json. Returns the new ids. An id with no hint (never
    met by a connector) is left alone: the live path then refuses it as an unknown author."""
    known = directory(graph)
    new = []
    for eid in dict.fromkeys(e for e in eids if e):
        if eid in known or eid not in _hints:
            continue
        hint = _hints[eid]
        new.append({"id": eid, "name": hint["name"], "role": None, "location": "", "org": ""})
    if not new:
        return []
    path = directory_file(graph)
    met = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    met.update({r["id"]: r for r in new})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(met, indent=1, sort_keys=True), encoding="utf-8", newline="\n")
    db.run("upsert_people", {"rows": new}, graph=graph, readonly=False)
    return [r["id"] for r in new]


def metadata(graph: str):
    """The live path's view of the org, in the shape HERB's metadata has (structural.Metadata):
    everyone known, no org chart, no customer list."""
    from app.ingest.structural import Metadata

    return Metadata(employees=directory(graph), hierarchy=[], customers=[])


# --- products and channels from connectors.yaml ------------------------------------------------
def channel_map(conf: dict[str, Any]) -> dict[str, str]:
    """A tool's mapped channels as {its channel: Yaan channel}. A mapping is a Yaan channel
    name, or `{channel: <name>, kind: <kind>}` (D116)."""
    return {
        str(k): str(v["channel"] if isinstance(v, dict) else v)
        for k, v in (conf.get("channels") or {}).items()
    }


def channel_products(sources: dict[str, dict[str, Any]]) -> dict[str, str]:
    """Yaan channel -> the product it belongs to, where connectors.yaml says: a mapping's own
    `product`, else its source's. Unnamed channels belong to the top-level `product` (D116)."""
    out: dict[str, str] = {}
    for name, conf in sources.items():
        if name in ("slack", "discord"):
            for v in (conf.get("channels") or {}).values():
                target = str(v["channel"] if isinstance(v, dict) else v)
                product = (v.get("product") if isinstance(v, dict) else None) or conf.get("product")
                if product:
                    out[target] = str(product)
        elif conf.get("channel") and conf.get("product"):
            out[str(conf["channel"])] = str(conf["product"])
    return out


def declared_products(product: str, sources: dict[str, dict[str, Any]]) -> list[str]:
    """Every product connectors.yaml names: the default, then any a source or channel names."""
    named = [*channel_products(sources).values(), *(c.get("product") for c in sources.values())]
    return list(dict.fromkeys([product, *(str(p) for p in named if p)]))


def declared_channels(sources: dict[str, dict[str, Any]]) -> dict[str, str | None]:
    """Every channel connectors.yaml names, with its declared kind (None: its prefix says):
    Slack's and Discord's mapped channels, and each other source's own `channel`."""
    out: dict[str, str | None] = {}
    for name, conf in sources.items():
        if name in ("slack", "discord"):
            for v in (conf.get("channels") or {}).values():
                target = str(v["channel"] if isinstance(v, dict) else v)
                out[target] = out.get(target) or (v.get("kind") if isinstance(v, dict) else None)
        elif conf.get("channel"):
            out[str(conf["channel"])] = out.get(str(conf["channel"])) or conf.get("kind")
    return out


def channel_row(channel: str, product: str, kind: str | None = None) -> dict[str, Any]:
    if kind is not None:
        if kind not in KINDS:
            raise OrgError(
                f"connectors.yaml: channel {channel!r} has kind {kind!r}: use bug, develop, "
                "planning or mixed (bugs and pull requests both discussed there)"
            )
        stage = KINDS[kind]
    else:
        kind = channel.split("-", 1)[0]
        if kind not in STAGES or channel == kind:
            raise OrgError(
                f"connectors.yaml: channel {channel!r} must start with bug-, develop- or "
                "planning- (e.g. bug-support), or say what it holds: "
                f"`{{channel: {channel}, kind: bug | develop | planning | mixed}}`"
            )
        stage = STAGES[kind]
    return {
        "id": channel,
        "name": channel,
        "original_name": channel,
        "stage": stage,
        "kind": kind,
        "product_id": product,
    }


# --- who may read what (H1, D109) ---------------------------------------------------------------
# Slack says which of its channels are private and who is in them (connectors/slack.py syncs
# it). Every other source says nothing we can read cheaply, so connectors.yaml declares its
# audience; until it does, nothing from that source is shown to anyone.
VISIBILITY = ("everyone", "members")
FROM_THE_TOOL = frozenset({"slack"})


@dataclass(frozen=True)
class Audience:
    visibility: str  # everyone | members
    members: frozenset[str] = frozenset()  # eids, for `members`


def member_id(source: str, who: Any) -> str:
    """A person connectors.yaml names as a member: their people.yaml id, name or email."""
    key = str(who).strip().casefold()
    for eid, p in roster().items():
        if key in (eid, p["name"].casefold(), (p.get("email") or "").casefold()):
            return eid
    raise OrgError(f"connectors.yaml: {source} lists member {who!r}, who isn't in people.yaml")


def audience(source: str, conf: dict[str, Any]) -> Audience:
    """A source's declared audience, or OrgError saying what to write."""
    seen_by, members = conf.get("visibility"), conf.get("members") or []
    if seen_by not in VISIBILITY:
        raise OrgError(
            f"connectors.yaml: {source} needs `visibility: everyone` (every signed-in person "
            "may read it) or `visibility: members` with `members: [names or emails from "
            "people.yaml]`. Until then nothing from it is shown to anyone."
        )
    if seen_by == "members" and not members:
        raise OrgError(f"connectors.yaml: {source} has `visibility: members` but no `members`")
    if seen_by == "everyone" and members:
        raise OrgError(f"connectors.yaml: {source}'s `members` needs `visibility: members`")
    return Audience(seen_by, frozenset(member_id(source, m) for m in members))


def audiences(sources: dict[str, dict[str, Any]]) -> dict[str, Audience]:
    """Every declared source's audience (Slack's comes from Slack)."""
    return {
        name: audience(name, conf) for name, conf in sources.items() if name not in FROM_THE_TOOL
    }


@lru_cache
def source_audiences() -> dict[str, Audience]:
    """The audiences connectors.yaml declares, read once (restart to pick up an edit)."""
    from app.connectors import base

    return audiences(base.maps().sources)


def channel_sources(sources: dict[str, dict[str, Any]]) -> dict[str, set[str]]:
    """Yaan channel -> the sources that feed it."""
    out: dict[str, set[str]] = {}
    for name, conf in sources.items():
        if name in ("slack", "discord"):
            targets = list(channel_map(conf).values())
        else:
            targets = [conf["channel"]] if conf.get("channel") else []
        for t in targets:
            out.setdefault(str(t), set()).add(name)
    return out


def channel_access(sources: dict[str, dict[str, Any]]) -> tuple[list[dict], dict[str, set]]:
    """The visibility rows and member lists the declarations give to channels. A channel fed
    by Slack gets its access from Slack, so no other source may feed it."""
    declared = audiences(sources)
    rows, members = [], {}
    for channel, feeders in sorted(channel_sources(sources).items()):
        if "slack" in feeders and len(feeders) > 1:
            others = sorted(feeders - {"slack"})
            raise OrgError(
                f"connectors.yaml: {channel!r} is fed by Slack and by {others}: Slack decides "
                "who may read a Slack channel, so give the others a channel of their own"
            )
        if "slack" in feeders:
            continue
        found = {declared[s] for s in feeders}
        if len(found) > 1:
            raise OrgError(f"connectors.yaml: {channel!r} is fed by sources with other audiences")
        (seen,) = found
        visibility = "public" if seen.visibility == "everyone" else "private"
        rows.append({"id": channel, "visibility": visibility})
        members[channel] = set(seen.members)
    return rows, members


def set_channel_access(
    graph: str, rows: list[dict[str, Any]], members: dict[str, set[str]]
) -> dict[str, int]:
    """Write channels' visibility and bring their MEMBER_OF edges to `members`, removing only
    who left and adding only who joined (readers never see a channel emptied)."""
    now = int(time.time() * 1000)
    if rows:
        db.run(
            "set_channel_visibility",
            {"rows": [r | {"now_ms": now} for r in rows]},
            graph=graph,
            readonly=False,
        )
    have: dict[str, set[str]] = {c: set() for c in members}
    if members:
        found = db.run_all("channel_members", {"ids": sorted(members)}, cap=100_000, graph=graph)
        for r in found:
            have[r["channel_id"]].add(r["person_id"])
    gone = [
        {"channel_id": c, "person_id": p}
        for c in sorted(members)
        for p in sorted(have[c] - members[c])
    ]
    new = [
        {"channel_id": c, "person_id": p, "now_ms": now}
        for c in sorted(members)
        for p in sorted(members[c] - have[c])
    ]
    if gone:
        db.run("unlink_channel_members", {"rows": gone}, graph=graph, readonly=False)
    if new:
        db.run("link_channel_members", {"rows": new}, graph=graph, readonly=False)
    return {"channels": len(rows), "joined": len(new), "left": len(gone)}


def check(product: str, sources: dict[str, dict[str, Any]]) -> tuple[list[dict], list[dict]]:
    """The channel and people rows connectors.yaml and people.yaml give, or OrgError naming
    what to fix. Touches no graph."""
    belongs = channel_products(sources)
    channels = [
        channel_row(c, belongs.get(c, product), k) for c, k in declared_channels(sources).items()
    ]
    people = [{k: v for k, v in p.items() if k != "accounts"} for p in roster().values()]
    accounts()  # an account listed twice is an error too
    channel_access(sources)  # every source's audience is declared, and no channel is fed twice
    return channels, people


def prepare(graph: str, product: str, sources: dict[str, dict[str, Any]]) -> dict[str, int]:
    """Create (or refresh) the product, the declared channels, their declared access and the
    people.yaml roster in `graph`. Idempotent: every row is a MERGE on its id."""
    channels, people = check(product, sources)
    products = declared_products(product, sources)
    db.run(
        "upsert_products",
        {"rows": [{"id": p, "name": p, "aliases": []} for p in products]},
        graph=graph,
        readonly=False,
    )
    if channels:
        db.run("upsert_channels", {"rows": channels}, graph=graph, readonly=False)
    if people:
        db.run("upsert_people", {"rows": people}, graph=graph, readonly=False)
    access_rows, members = channel_access(sources)
    set_channel_access(graph, access_rows, members)
    return {"products": len(products), "channels": len(channels), "people": len(people)}


def people_in(artifact) -> list[str]:
    """The people one artifact involves: its author, reviewers, participants and @mentions."""
    from app.ingest.linker import EID as MENTION

    meta = artifact.meta or {}
    found = [artifact.author_eid, *artifact.participants]
    found += [r.get("reviewer_eid") for r in meta.get("reviews") or []]
    found += MENTION.findall(artifact.text or "")
    return [e for e in dict.fromkeys(found) if e]
