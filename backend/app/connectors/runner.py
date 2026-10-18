"""One sync round for every connected tool (17.2, D074): new items in, approved actions out.

`sync` asks each connector for what changed since its cursor, feeds every new or newer item
to the live path (S1-S8 + Sentinel on the demo graph), then hands the approved, unsent Actions
to the connectors until one delivers each. The cursor and the version seen per item live in
data/live/<graph>/connectors.json, next to the live artifacts, so `lore demo reset` clears both.
"""

import json
import time
from typing import Any

from app import db
from app.connectors import render
from app.connectors.base import Connector, ConnectorError, labeled
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


def load_state(graph: str) -> dict[str, Any]:
    path = live.live_dir(graph) / STATE
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def save_state(graph: str, state: dict[str, Any]) -> None:
    folder = live.live_dir(graph)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / STATE).write_text(json.dumps(state, indent=1), encoding="utf-8", newline="\n")


def sync(connectors: list[Connector], graph: str | None = None) -> dict[str, Any]:
    """One round: ingest what changed, then deliver approved actions. Fails loudly per item:
    an item the live path refuses is reported, never silently skipped."""
    graph = live.demo_graph(graph)
    state = load_state(graph)
    report: dict[str, Any] = {"ingested": [], "refused": [], "sent": [], "unsent": []}
    for c in connectors:
        mine = state.setdefault(c.name, {"since_ms": 0, "seen": {}})
        items = c.fetch(mine["since_ms"])
        items.sort(key=lambda i: (i.artifact.ts_ms or 0, i.artifact.id))
        for item in items:
            a = item.artifact
            if mine["seen"].get(a.id, -1) >= item.updated_ms:
                continue
            if not labeled(a.text):  # rule 5: the sandboxes hold labeled synthetic items only
                report["refused"].append({"id": a.id, "why": "not labeled [synthetic demo]"})
                mine["seen"][a.id] = item.updated_ms
                continue
            try:
                again = a.id in mine["seen"]
                out = live.ingest_artifact(a, graph, events=item.events, replace=again)
            except live.LiveError as e:
                report["refused"].append({"id": a.id, "why": str(e)})
            else:
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
        save_state(graph, state)
    report["sent"], report["unsent"] = deliver(connectors, graph)
    return report


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
    """An alert raised by an item a connector brought in (not by HERB's batch data)."""
    aid = alert.get("artifact_id") or ""
    return any(f":synthetic-{s}-" in aid for s in SOURCES)


def run_forever(graph: str | None = None, every_s: float = 30, say=print) -> None:
    """`lore connectors run`: sync every `every_s` seconds, or at once when Slack reports a
    message; post each new alert a connector caused as a Slack card."""
    import threading

    from app.config import get_settings
    from app.sentinel import engine

    graph = live.demo_graph(graph)
    connectors = configured(graph)
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
    while True:
        try:
            report = sync(connectors, graph)
        except ConnectorError as e:  # loud, then the next round tries again
            say(f"sync failed: {e}")
            report = None
        if report:
            for r in report["ingested"]:
                say(f"in  {r['id']} ({r['freshness_lag_ms']:.0f} ms) alerts={r['alerts']}")
            for r in report["refused"]:
                say(f"refused {r['id']}: {r['why']}")
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
        wake.wait(every_s)
        wake.clear()
