"""One sync round for every connected tool (17.2, D074): new items in, approved actions out.

`sync` asks each connector for what changed since its cursor, feeds every new or newer item
to the live path (S1-S8 + Sentinel on the demo graph), then hands the approved, unsent Actions
to the connectors until one delivers each. The cursor and the version seen per item live in
data/live/<graph>/connectors.json, next to the live artifacts, so `lore demo reset` clears both.

In org mode (D097) the round writes to the org graph, first creates the product, the declared
channels and the people.yaml roster there (org.prepare), and takes items without the demo label.

A failed item is never dropped (H1, D107). What went wrong decides what happens:
- the item itself can't go in (`LiveError`, a model refusal): a `:DeadLetter` node keeps the
  item and the reason, listed by `yaan connectors dead` and Graph health, replayed by
  `yaan connectors retry`;
- the system isn't ready (the spend cap, no API key, FalkorDB unreachable, the writer lock
  busy): the item waits for a later round, and these failures aren't counted against it;
- anything else: retried, and dead-lettered after POISON_AFTER failures, so one bad item
  can't stall its connector.
A connector's cursor never passes an item that will be retried, and a round that fails as a
whole is reported and retried after a growing pause: the loop doesn't die.
"""

import json
import os
import time
from typing import Any

from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app import db, llm, org
from app.connectors import base, render
from app.connectors.base import Connector, ConnectorError, Item, labeled
from app.ingest.normalize import Artifact
from app.sentinel import live

STATE = "connectors.json"
# the sources whose items carry `:synthetic-<source>-` ids; the last eight are experimental (D090)
SOURCES = (
    "slack",
    "github",
    "jira",
    "notion",
    "linear",
    "gitlab",
    "confluence",
    "gdrive",
    "gmail",
    "gmeet",
    "zoom",
    "discord",
)
PERMANENT = (live.LiveError, llm.Refused)  # the item itself is the problem: a dead letter now
WAITING = (  # the system isn't ready: retry later, the item isn't at fault
    llm.SpendCapReached,
    llm.MissingCredentials,
    llm.CacheOnly,
    db.LockBusy,
    RedisConnectionError,
    RedisTimeoutError,
)
POISON_AFTER = 5  # other failures of one item version before it becomes a dead letter
KEEP_SEEN_MS = 86_400_000  # versions remembered a day behind the cursor: more than any re-read
MAX_PAUSE_S = 900  # the longest wait between failing rounds


def load_state(graph: str) -> dict[str, Any]:
    path = live.live_dir(graph) / STATE
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def save_state(graph: str, state: dict[str, Any]) -> None:
    """Atomic: a crash mid-write leaves the previous state, never a torn file."""
    folder = live.live_dir(graph)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / STATE
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def why(e: BaseException) -> str:
    return f"{type(e).__name__}: {e}"


def sync(connectors: list[Connector], graph: str | None = None) -> dict[str, Any]:
    """One round: ingest what changed, then deliver approved actions. Fails loudly per item:
    an item the live path refuses becomes a dead letter; one that may still go in waits."""
    graph = live.demo_graph(graph)
    if org.active():
        prepare(graph, connectors)
    state = load_state(graph)
    report: dict[str, Any] = {
        "ingested": [],
        "refused": [],
        "dead": [],
        "waiting": [],
        "sent": [],
        "unsent": [],
    }
    for c in connectors:
        mine = state.setdefault(c.name, {"since_ms": 0, "seen": {}})
        failures = mine.setdefault("failures", {})  # "<id>@<version>" -> failures so far
        try:
            items = c.fetch(mine["since_ms"])
        except Exception as e:  # the tool is down or refused us: this one waits, the rest go on
            report["waiting"].append({"id": None, "source": c.name, "why": why(e)})
            continue
        items.sort(key=lambda i: (i.artifact.ts_ms or 0, i.artifact.id))
        present = live.present_ids(graph, [i.artifact.id for i in items])
        hold: int | None = None  # the cursor may not pass a version that will be retried
        for item in items:
            a = item.artifact
            if mine["seen"].get(a.id, -1) >= item.updated_ms:  # done in an earlier round
                mine["since_ms"] = max(mine["since_ms"], item.updated_ms)
                continue
            if base.is_demo() and not labeled(a.text):  # rule 5: labeled synthetic items only
                report["refused"].append({"id": a.id, "why": "not labeled [synthetic demo]"})
                mine["seen"][a.id] = item.updated_ms
                mine["since_ms"] = max(mine["since_ms"], item.updated_ms)
                continue
            key = f"{a.id}@{item.updated_ms}"
            try:
                again = a.id in mine["seen"] or a.id in present
                out = live.ingest_artifact(a, graph, events=item.events, replace=again)
            except Exception as e:
                tries = failures.get(key, 0) + (0 if isinstance(e, WAITING) else 1)
                if isinstance(e, PERMANENT) or tries >= POISON_AFTER:
                    record_dead(graph, c.name, item, e, tries)
                    report["dead"].append({"id": a.id, "source": c.name, "why": why(e)})
                    failures.pop(key, None)
                else:
                    failures[key] = tries
                    report["waiting"].append(
                        {"id": a.id, "source": c.name, "why": why(e), "failures": tries}
                    )
                    hold = item.updated_ms - 1 if hold is None else min(hold, item.updated_ms - 1)
                    continue
            else:
                failures.pop(key, None)
                present.add(a.id)
                report["ingested"].append(
                    {
                        "id": a.id,
                        "source": c.name,
                        "alerts": [x["id"] for x in out["alerts"]],
                        "freshness_lag_ms": out["freshness_lag_ms"],
                    }
                )
            mine["seen"][a.id] = item.updated_ms
            mine["since_ms"] = max(mine["since_ms"], item.updated_ms)
        if hold is not None:
            mine["since_ms"] = min(mine["since_ms"], hold)
        keep = mine["since_ms"] - KEEP_SEEN_MS
        mine["seen"] = {i: v for i, v in mine["seen"].items() if v >= keep}
        save_state(graph, state)
    report["sent"], report["unsent"] = deliver(connectors, graph)
    return report


def record_dead(graph: str, source: str, item: Item, error: BaseException, attempts: int) -> None:
    """Keep an item the live path couldn't take, with why and the item itself (for retry)."""
    a = item.artifact
    row = {
        "id": f"dead:{source}:{a.id}",
        "source": source,
        "artifact_id": a.id,
        "updated_ms": item.updated_ms,
        "reason": "refused" if isinstance(error, PERMANENT) else "kept failing",
        "error": why(error)[:1000],
        "attempts": max(attempts, 1),
        "now_ms": int(time.time() * 1000),
        "payload": a.model_dump_json(),
        "events": None if item.events is None else json.dumps(item.events),
    }
    db.run("upsert_dead_letters", {"rows": [row]}, graph=graph, readonly=False)


def dead_letters(graph: str, dead_id: str | None = None, limit: int = 200) -> list[dict]:
    """The dead letters, newest failure first, without their stored items."""
    rows = db.run("list_dead_letters", {"id": dead_id, "limit": limit}, graph=graph).rows
    return [{k: v for k, v in r.items() if k not in ("payload", "events")} for r in rows]


def retry(graph: str, dead_id: str | None = None) -> dict[str, list[dict[str, Any]]]:
    """Replay dead letters through the live path (all, or one id): an ingested item's dead
    letter goes; one that fails again stays, with the new reason."""
    rows = db.run("list_dead_letters", {"id": dead_id, "limit": 200}, graph=graph).rows
    out: dict[str, list[dict[str, Any]]] = {"ingested": [], "failed": []}
    for r in rows:
        a = Artifact.model_validate_json(r["payload"])
        item = Item(a, r["updated_ms"], json.loads(r["events"]) if r["events"] else None)
        try:
            again = bool(live.present_ids(graph, [a.id]))
            live.ingest_artifact(a, graph, events=item.events, replace=again)
        except Exception as e:
            record_dead(graph, r["source"], item, e, (r["attempts"] or 0) + 1)
            out["failed"].append({"id": r["id"], "why": why(e)})
        else:
            db.run("delete_dead_letters", {"rows": [{"id": r["id"]}]}, graph=graph, readonly=False)
            out["ingested"].append({"id": r["id"], "artifact_id": a.id})
    return out


def prepare(graph: str, connectors: list[Connector] | None = None) -> dict[str, int]:
    """Org mode: the product, the channels connectors.yaml declares and the people.yaml roster,
    created in `graph` (idempotent). Raises ConnectorError on a channel name it can't use."""
    maps = next((c.maps for c in connectors or [] if hasattr(c, "maps")), None) or base.maps()
    try:
        return org.prepare(graph, maps.product, maps.sources)
    except org.OrgError as e:
        raise ConnectorError(str(e)) from e


EVERYWHERE = {"ticket_comment"}  # the same comment belongs on the ticket and on its fix PR


def deliver(connectors: list[Connector], graph: str) -> tuple[list[dict], list[dict]]:
    """Each approved, unsent Action to the first connector that takes it (a ticket comment to
    every one that does); record where it went, so it is sent once."""
    sent, unsent = [], []
    pending = db.run("actions_to_send", {"limit": 100}, graph=graph).rows
    resolve = render.resolver(connectors)  # each source cited as a link in its own tool
    for action in pending:
        errors, refs, via = [], [], []
        for c in connectors:
            try:
                ref = c.post(action, resolve)
            except ConnectorError as e:  # reported, and the next connector may still take it
                errors.append(f"{c.name}: {e}")
                continue
            if ref:
                refs.append(ref)
                via.append(c.name)
                if action["kind"] not in EVERYWHERE:
                    break
        if refs:
            row = {
                "id": action["id"],
                "ref": " ".join(refs),
                "ms": int(time.time() * 1000),
                "via": ",".join(via),
            }
            db.run("mark_action_sent", {"rows": [row]}, graph=graph, readonly=False)
            sent.append(row)
        else:
            why = "; ".join(errors) or "no connected tool holds its target"
            unsent.append({"id": action["id"], "kind": action["kind"], "why": why})
    return sent, unsent


def configured(graph: str) -> list[Connector]:
    """The connectors whose settings are in .env, in the order they deliver actions."""
    from app.config import get_settings

    st = get_settings()
    out: list[Connector] = []
    if st.slack_bot_token:
        from slack_sdk import WebClient

        from app.connectors.slack import SlackConnector

        out.append(SlackConnector(WebClient(token=st.slack_bot_token), graph))
    if st.jira_base_url and st.jira_email and st.jira_api_token and st.jira_project:
        from app.connectors.jira import JiraConnector

        out.append(
            JiraConnector(
                st.jira_base_url, st.jira_email, st.jira_api_token, st.jira_project, graph
            )
        )
    if st.github_token and st.github_repo:
        from app.connectors.github import GitHubConnector

        out.append(GitHubConnector(st.github_token, st.github_repo, graph))
    if st.notion_token and st.notion_page_id:
        from app.connectors.notion import NotionConnector

        out.append(NotionConnector(st.notion_token, st.notion_page_id, graph))
    out.extend(experimental(graph, st))
    return out


def experimental(graph: str, st: Any) -> list[Connector]:
    """The experimental connectors (D090, not run against live APIs): each only when ALL its
    required settings are set, so an unchanged .env adds nothing."""
    out: list[Connector] = []
    if st.linear_api_key and st.linear_team:
        from app.connectors.linear import LinearConnector

        out.append(LinearConnector(st.linear_api_key, st.linear_team, graph))
    if st.gitlab_token and st.gitlab_project:
        from app.connectors.gitlab import GitLabConnector

        out.append(
            GitLabConnector(
                st.gitlab_token, st.gitlab_project, graph, base_url=st.gitlab_base_url or None
            )
        )
    if (
        st.confluence_base_url
        and st.confluence_email
        and st.confluence_api_token
        and st.confluence_space_id
    ):
        from app.connectors.confluence import ConfluenceConnector

        out.append(
            ConfluenceConnector(
                st.confluence_base_url,
                st.confluence_email,
                st.confluence_api_token,
                st.confluence_space_id,
                graph,
            )
        )
    if st.google_drive_access_token and st.google_drive_folder_id:
        from app.connectors.gdrive import GoogleDriveConnector

        out.append(
            GoogleDriveConnector(st.google_drive_access_token, st.google_drive_folder_id, graph)
        )
    if st.gmail_access_token and st.gmail_label:
        from app.connectors.gmail import GmailConnector

        out.append(GmailConnector(st.gmail_access_token, st.gmail_label, graph))
    if st.google_meet_access_token and st.google_meet_space:
        from app.connectors.gmeet import GoogleMeetConnector

        out.append(GoogleMeetConnector(st.google_meet_access_token, st.google_meet_space, graph))
    if st.zoom_access_token:
        from app.connectors.zoom import ZoomConnector

        out.append(ZoomConnector(st.zoom_access_token, graph, user_id=st.zoom_user_id or None))
    if st.discord_bot_token and st.discord_channel_id:
        from app.connectors.discord import DiscordConnector

        out.append(DiscordConnector(st.discord_bot_token, st.discord_channel_id, graph))
    return out


def from_connector(alert: dict[str, Any]) -> bool:
    """An alert raised by an item a connector brought in (not by HERB's batch data): its id is
    `<kind>:synthetic-<source>-…` in the demo, `<kind>:<source>-…` in org mode."""
    aid = alert.get("artifact_id") or ""
    kind_free = aid.partition(":")[2]
    shape = "synthetic-{}-" if base.is_demo() else "{}-"
    return any(kind_free.startswith(shape.format(s)) for s in SOURCES)


def run_forever(graph: str | None = None, every_s: float = 30, say=print) -> None:
    """`lore connectors run`: sync every `every_s` seconds, or at once when Slack reports a
    message; post each new alert a connector caused as a Slack card."""
    import threading

    from app.config import get_settings

    graph = live.demo_graph(graph)
    connectors = configured(graph)
    if connectors and org.active():
        prepare(graph, connectors)  # before Slack's bot looks up its channels
    if not connectors:
        raise ConnectorError("no connector is configured: see docs/connectors.md")
    wake = threading.Event()
    st = get_settings()
    bot = None
    if st.slack_bot_token and st.slack_app_token:
        from app.connectors.slack import SlackBot

        bot = SlackBot(st.slack_bot_token, st.slack_app_token, graph, on_message=wake.set)
        bot.resolve = render.resolver(connectors)
        bot.start()
        say("slack: socket connected (@lore answers, alert cards)")
    say(f"connectors: {', '.join(c.name for c in connectors)} -> {graph}, every {every_s:g} s")
    pause = every_s
    while True:
        try:
            healthy = one_round(connectors, graph, bot, say)
        except Exception as e:  # anything at all: reported and retried, never the end of the loop
            say(f"round failed: {why(e)}")
            healthy = False
        pause = every_s if healthy else min(pause * 2, MAX_PAUSE_S)
        beat(graph, healthy, pause)
        if not healthy:
            say(f"next round in {pause:g} s")
        wake.wait(pause)
        wake.clear()


def one_round(connectors: list[Connector], graph: str, bot: Any, say=print) -> bool:
    """One sync round, said out loud, then the Slack cards of new alerts. True when nothing
    had to wait (the pause between rounds grows while rounds keep waiting)."""
    from app.sentinel import engine

    report = sync(connectors, graph)
    for r in report["ingested"]:
        say(f"in  {r['id']} ({r['freshness_lag_ms']:.0f} ms) alerts={r['alerts']}")
    for r in report["refused"]:
        say(f"refused {r['id']}: {r['why']}")
    for r in report["dead"]:
        say(f"dead letter {r['id']}: {r['why']} (yaan connectors dead | retry)")
    for r in report["waiting"]:
        say(f"waiting {r['id'] or r['source']}: {r['why']}")
    for r in report["sent"]:
        say(f"out {r['id']} via {r['via']}: {r['ref']}")
    for r in report["unsent"]:
        say(f"unsent {r['id']}: {r['why']}")
    if bot:
        state = load_state(graph)
        carded = state.setdefault("slack_cards", {})
        for alert in engine.list_alerts("open", graph=graph):
            if from_connector(alert) and alert["id"] not in carded:
                carded[alert["id"]] = bot.post_card(alert)
                say(f"card {alert['id']}")
        save_state(graph, state)
    return not report["waiting"]


HEARTBEAT = "heartbeat.json"
HEARTBEAT_GRACE_MS = 120_000  # a round may run this long past its announced time


def beat(graph: str, healthy: bool, pause_s: float) -> None:
    """After every round: when it ended, whether it was clean, and when the next one is due.
    `yaan connectors health` (the worker's healthcheck) and the API's /health read it."""
    now = int(time.time() * 1000)
    folder = live.live_dir(graph)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / HEARTBEAT
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    body = {"at_ms": now, "healthy": healthy, "next_by_ms": now + int(pause_s * 1000)}
    tmp.write_text(json.dumps(body), encoding="utf-8")
    os.replace(tmp, path)


def worker_health(graph: str, now_ms: int | None = None) -> dict[str, Any]:
    """`absent` (no connector loop has run here), `ok`, `waiting` (rounds run, something
    waits) or `stale` (the next round is overdue: the loop is stuck or gone)."""
    path = live.live_dir(graph) / HEARTBEAT
    if not path.is_file():
        return {"status": "absent"}
    beat_ = json.loads(path.read_text(encoding="utf-8"))
    now_ms = now_ms or int(time.time() * 1000)
    if now_ms > beat_["next_by_ms"] + HEARTBEAT_GRACE_MS:
        status = "stale"
    else:
        status = "ok" if beat_["healthy"] else "waiting"
    return {"status": status, "last_round_ms": beat_["at_ms"], "next_by_ms": beat_["next_by_ms"]}
