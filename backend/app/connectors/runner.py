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
import time
from typing import Any

from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app import db, filestate, forget, llm, org
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
BACKFILL_DAYS = 30  # a connector's first sync reaches back this far (`backfill_days` per source)
MAX_BATCH = 200  # items per live batch; state is saved after each (D114)
EXTRACT_WORKERS = 4  # items extracted at once within a batch
KEEP_SEEN_MS = 86_400_000  # versions remembered a day behind the cursor: more than any re-read
MAX_PAUSE_S = 900  # the longest wait between failing rounds


def load_state(graph: str) -> dict[str, Any]:
    path = live.live_dir(graph) / STATE
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def save_state(graph: str, state: dict[str, Any]) -> None:
    """Atomic: a crash mid-write leaves the previous state, never a torn file."""
    filestate.write(live.live_dir(graph) / STATE, state)


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
        # one cursor per repo, project or page when a tool reads several (D118)
        mine = state.setdefault(getattr(c, "state_key", c.name), {"since_ms": 0, "seen": {}})
        failures = mine.setdefault("failures", {})  # "<id>@<version>" -> failures so far
        if not mine["since_ms"]:  # the first sync: not the whole history at once (D114)
            mine["since_ms"] = first_cursor(c)
        try:
            items = c.fetch(mine["since_ms"])
        except Exception as e:  # the tool is down or refused us: this one waits, the rest go on
            report["waiting"].append({"id": None, "source": c.name, "why": why(e)})
            continue
        items.sort(key=lambda i: (i.artifact.ts_ms or 0, i.artifact.id))
        present = live.present_ids(graph, [i.artifact.id for i in items])
        # forgotten, or by an erased person: skipped before the live path, so no dead letter
        # ever keeps their words (D125)
        stop = forget.blocked([i.artifact for i in items], graph) if items else {}
        hold: int | None = None  # the cursor may not pass a version that will be retried
        latest: dict[str, Item] = {}  # the newest version of each item: one batch (D112)
        for item in items:
            a = item.artifact
            if mine["seen"].get(a.id, -1) >= item.updated_ms:  # done in an earlier round
                mine["since_ms"] = max(mine["since_ms"], item.updated_ms)
                continue
            if a.id in stop:
                report["refused"].append({"id": a.id, "why": stop[a.id]})
                mine["seen"][a.id] = item.updated_ms
                mine["since_ms"] = max(mine["since_ms"], item.updated_ms)
                continue
            if base.is_demo() and not labeled(a.text):  # rule 5: labeled synthetic items only
                report["refused"].append({"id": a.id, "why": "not labeled [synthetic demo]"})
                mine["seen"][a.id] = item.updated_ms
                mine["since_ms"] = max(mine["since_ms"], item.updated_ms)
                continue
            if a.id not in latest or item.updated_ms > latest[a.id].updated_ms:
                latest[a.id] = item
        todo = sorted(latest.values(), key=lambda i: (i.artifact.ts_ms or 0, i.artifact.id))
        outcomes: list[Any] = []
        for lo in range(0, len(todo), MAX_BATCH):  # big rounds (a backfill) in saved chunks
            chunk = todo[lo : lo + MAX_BATCH]
            batch = [
                (i.artifact, i.events, i.artifact.id in mine["seen"] or i.artifact.id in present)
                for i in chunk
            ]
            try:
                outcomes += live.ingest_batch(batch, graph, workers=EXTRACT_WORKERS)
            except Exception as e:  # the batch as a whole: the lock stayed busy, FalkorDB left
                outcomes += [e] * len(batch)
        for item, out in zip(todo, outcomes, strict=True):
            a = item.artifact
            key = f"{a.id}@{item.updated_ms}"
            if isinstance(out, Exception):
                e = out
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
    report["derived"] = live.derive_if_due(graph)  # the derived layer, if a write made it due
    return report


def first_cursor(c: Connector, now_ms: int | None = None) -> int:
    """Where a connector's first sync starts: `backfill_days` ago (connectors.yaml, per
    source; BACKFILL_DAYS by default). Further back is `yaan connectors backfill`."""
    maps = getattr(c, "maps", None) or base.maps()
    days = float(maps.source(c.name).get("backfill_days", BACKFILL_DAYS))
    return (now_ms or int(time.time() * 1000)) - int(days * 86_400_000)


def backfill(
    connectors: list[Connector], graph: str | None, days: float, spend: bool = False
) -> dict[str, Any]:
    """Reach `days` back (D114). Without `spend`, a dry run: what each tool holds in that
    window and what extracting it would cost. With it, each connector's cursor moves back
    to there and one sync round ingests it (in saved chunks; items already in are skipped,
    and the daily spend cap still stops the model: what waits goes in on later rounds)."""
    graph = live.demo_graph(graph)
    since = int(time.time() * 1000) - int(days * 86_400_000)
    report: dict[str, Any] = {"graph": graph, "since_ms": since, "sources": {}}
    for c in connectors:
        items = c.fetch(since)
        arts = sorted((i.artifact for i in items if i.events is None), key=lambda a: a.ts_ms or 0)
        report["sources"][c.name] = {"items": len(items), **live.estimate_extraction(arts, graph)}
    totals = report["sources"].values()
    report["expected_usd"] = round(sum(s["expected_usd"] for s in totals), 4)
    report["worst_case_usd"] = round(sum(s["worst_case_usd"] for s in totals), 4)
    if not spend:
        return report
    state = load_state(graph)
    for c in connectors:
        mine = state.setdefault(c.name, {"since_ms": since, "seen": {}})
        mine["since_ms"] = min(mine["since_ms"] or since, since)
    save_state(graph, state)
    report["sync"] = sync(connectors, graph)
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
        # what leaves Yaan quotes only what the whole company may read (D109)
        action = {**action, "draft": base.public_quotes(action.get("draft"), graph)}
        errors, refs, via = [], [], []
        for c in connectors:
            try:
                ref = c.post(action, resolve)
            except Exception as e:  # a refusal or a timeout (it may have gone through: the
                errors.append(f"{c.name}: {why(e)}")  # retry looks for its marker first)
                continue
            if ref:
                refs.append(ref)
                via.append(c.name)
                if action["kind"] not in EVERYWHERE:
                    break
        if refs and not (errors and action["kind"] in EVERYWHERE):
            # a ticket comment that reached Jira but not GitHub stays unsent: the next round
            # sends it again, and Jira's copy is found by its marker, not posted twice (D110)
            row = {
                "id": action["id"],
                "ref": " ".join(refs),
                "ms": int(time.time() * 1000),
                "via": ",".join(via),
            }
            db.run("mark_action_sent", {"rows": [row]}, graph=graph, readonly=False)
            sent.append(row)
        else:
            reason = "; ".join(errors) or "no connected tool holds its target"
            unsent.append({"id": action["id"], "kind": action["kind"], "why": reason})
    return sent, unsent


def configured(graph: str) -> list[Connector]:
    """The connectors whose settings are in .env, in the order they deliver actions."""
    from app.config import get_settings

    st = get_settings()
    maps = base.maps()
    out: list[Connector] = []
    if st.slack_bot_token:
        from slack_sdk import WebClient

        from app.connectors.slack import SlackConnector

        out.append(SlackConnector(WebClient(token=st.slack_bot_token), graph))
    # several repos, projects or pages: connectors.yaml lists them (D118), one connector each
    projects = maps.containers("jira", "projects", st.jira_project)
    if st.jira_base_url and st.jira_email and st.jira_api_token and projects:
        from app.connectors.jira import JiraConnector

        for p in projects:
            c = JiraConnector(st.jira_base_url, st.jira_email, st.jira_api_token, p, graph)
            out.append(keyed(c, p, projects))
    repos = maps.containers("github", "repos", st.github_repo)
    if st.github_token and repos:
        from app.connectors.github import GitHubConnector

        out += [keyed(GitHubConnector(st.github_token, r, graph), r, repos) for r in repos]
    pages = maps.containers("notion", "pages", st.notion_page_id)
    if st.notion_token and pages:
        from app.connectors.notion import NotionConnector

        out += [keyed(NotionConnector(st.notion_token, p, graph), p, pages) for p in pages]
    out.extend(experimental(graph, st))
    return out


def keyed(c: Connector, container: str, every: list[str]) -> Connector:
    """A connector's cursor key: its tool's name when it reads one container (so an existing
    cursor carries over), `<tool>:<container>` when it is one of several."""
    if len(every) > 1:
        c.state_key = f"{c.name}:{container}"  # type: ignore[attr-defined]
    return c


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
            waiting = one_round(connectors, graph, bot, say)
        except Exception as e:  # anything at all: reported and retried, never the end of the loop
            say(f"round failed: {why(e)}")
            waiting = [f"round failed: {why(e)}"]
        healthy = not waiting
        pause = every_s if healthy else min(pause * 2, MAX_PAUSE_S)
        beat(graph, healthy, pause, waiting[0] if waiting else None)
        if not healthy:
            say(f"next round in {pause:g} s")
        wake.wait(pause)
        wake.clear()


def one_round(connectors: list[Connector], graph: str, bot: Any, say=print) -> list[str]:
    """One sync round, said out loud, then the Slack cards of new alerts. Returns why items
    or tools had to wait, empty when nothing did (the pause between rounds grows while rounds
    keep waiting, and the heartbeat says why: e.g. the spend cap pausing extraction)."""
    from app.sentinel import approve, engine

    for alert_id in approve.complete_stuck(graph):  # responses a stopped process left (D110)
        say(f"finished the response to {alert_id}")
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
                carded[alert["id"]] = bot.post_card(base.public_alert(alert, graph))
                say(f"card {alert['id']}")
        save_state(graph, state)
    return sorted({f"{r['source']}: {r['why']}" for r in report["waiting"]})


HEARTBEAT = "heartbeat.json"
HEARTBEAT_GRACE_MS = 120_000  # a round may run this long past its announced time


def beat(graph: str, healthy: bool, pause_s: float, waiting: str | None = None) -> None:
    """After every round: when it ended, whether it was clean (else why something waited),
    and when the next one is due. `yaan connectors health` (the worker's healthcheck) and the
    API's /health read it."""
    now = int(time.time() * 1000)
    body = {"at_ms": now, "healthy": healthy, "next_by_ms": now + int(pause_s * 1000)}
    if waiting:
        body["waiting"] = waiting[:300]
    filestate.write(live.live_dir(graph) / HEARTBEAT, body)


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
    out = {"status": status, "last_round_ms": beat_["at_ms"], "next_by_ms": beat_["next_by_ms"]}
    if status == "waiting" and beat_.get("waiting"):
        out["why"] = beat_["waiting"]  # e.g. "slack: SpendCapReached: ...": extraction paused
    return out
