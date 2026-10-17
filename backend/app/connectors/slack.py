"""Slack connector (17.3, D073-D074). Socket Mode, so nothing needs a public URL.

In: the messages of the channels `connectors.yaml` maps (e.g. #vizforce-bugs plays VizForce's
bug-davidgarcia channel) become live messages in that channel: S1-S8 + the Sentinel.
Out: notification drafts go to the alerts channel; corrections go into the thread of the
Slack message they correct. The bot (`SlackBot`) answers `@lore <question>` in the thread with
Lore's checked citations, and posts each new alert as a card with Approve and Dismiss buttons
that record the verdict as a decision (sentinel/approve.respond), as the Inbox does.
"""

import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from app import db
from app.connectors import base
from app.connectors.base import ConnectorError, Item, Maps
from app.ingest.normalize import Artifact

SKIP_SUBTYPES = {"channel_join", "channel_leave", "bot_message", "channel_topic", "channel_purpose"}
LINK = re.compile(r"<(https?://[^|>]+)(?:\|([^>]+))?>")
MENTION = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]+)?>")
SEEDED = "[synthetic demo · as "  # a bot post counts only when it opens with a persona label


def ts_ms(ts: str) -> int:
    return int(float(ts) * 1000)


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class SlackConnector:
    name = "slack"

    def __init__(self, client: WebClient, graph: str, maps: Maps | None = None):
        self.client, self.graph = client, graph
        self.maps = maps or base.maps()
        self.conf = self.maps.source("slack")
        self._channels: dict[str, str] | None = None

    # --- reading ---------------------------------------------------------------------------
    def channel_ids(self) -> dict[str, str]:
        """Slack channel name -> id, for the channels the bot can see."""
        if self._channels is None:
            out, cursor = {}, None
            while True:
                r = self._call(
                    "conversations_list",
                    types="public_channel,private_channel",
                    exclude_archived=True,
                    limit=200,
                    cursor=cursor,
                )
                out.update({c["name"]: c["id"] for c in r["channels"]})
                cursor = (r.get("response_metadata") or {}).get("next_cursor")
                if not cursor:
                    break
            self._channels = out
        return self._channels

    def fetch(self, since_ms: int) -> list[Item]:
        items = []
        for slack_name, herb_id in (self.conf.get("channels") or {}).items():
            cid = self.channel_ids().get(slack_name)
            if not cid:
                raise ConnectorError(f"the bot can't see #{slack_name}: /invite @lore there")
            rows = db.run("get_channel", {"id": herb_id}, graph=self.graph).rows
            if not rows:
                raise ConnectorError(f"{herb_id!r} (mapped from #{slack_name}) isn't in the graph")
            cursor = None
            while True:
                r = self._call(
                    "conversations_history",
                    channel=cid,
                    oldest=f"{since_ms / 1000:.6f}" if since_ms else "0",
                    limit=200,
                    cursor=cursor,
                )
                for m in r["messages"]:
                    if item := self.normalize(m, cid, herb_id, rows[0]["name"]):
                        items.append(item)
                cursor = (r.get("response_metadata") or {}).get("next_cursor")
                if not cursor:
                    break
        return items

    def plain(self, text: str) -> str:
        """Slack markup to the plain text HERB messages have: <url|label> -> label (url),
        <@U123> -> @eid_… of the person that account plays."""
        text = LINK.sub(lambda m: f"{m[2]} ({m[1]})" if m[2] else m[1], text or "")
        return MENTION.sub(lambda m: "@" + self.maps.person("slack", m[1]), text)

    def normalize(self, m: dict[str, Any], cid: str, herb_id: str, herb_name: str) -> Item | None:
        text = m.get("text") or ""
        by_bot = bool(m.get("bot_id")) or m.get("subtype") == "bot_message"
        if not text or m.get("subtype") in SKIP_SUBTYPES - {"bot_message"}:
            return None  # channel events aren't company knowledge
        if by_bot and not text.startswith(SEEDED):
            return None  # Lore's own answers, cards and notifications: never read back
        text = self.plain(m["text"])
        author = self.maps.person("slack", m.get("user"), text)
        created = ts_ms(m["ts"])
        edited = (m.get("edited") or {}).get("ts")
        a = Artifact(
            id=base.synthetic_id("msg", "slack", f"{cid}-{m['ts']}"),
            kind="message",
            product_hint=self.maps.product,
            channel_id=herb_id,
            ts_ms=created,
            ts_iso=iso(created),
            author_eid=author,
            text=text,
            raw_ref=f"slack:{cid}:{m['ts']}",
            meta={
                "channel_name": herb_name,
                "sender": author,
                "synthetic": True,
                "source": "slack",
                "slack_channel": cid,
                "slack_ts": m["ts"],
            },
        )
        return Item(a, updated_ms=ts_ms(edited) if edited else created)

    # --- writing ---------------------------------------------------------------------------
    def post(self, action: dict[str, Any]) -> str | None:
        """Notifications go to the alerts channel; a correction of a Slack message goes into
        its thread. Anything else belongs to another tool."""
        target = action.get("target_id") or ""
        if action["kind"] == "notify_owner":
            channel = self.alerts_channel()
            text = f"*To {action.get('target_name') or target}* ({target}):\n{action['draft']}"
            r = self._call("chat_postMessage", channel=channel, text=text, unfurl_links=False)
            return self.permalink(channel, r["ts"])
        prefix = "msg:synthetic-slack-"
        if target.startswith(prefix):
            cid, _, ts = target.removeprefix(prefix).partition("-")
            r = self._call(
                "chat_postMessage",
                channel=cid,
                thread_ts=ts,
                text=action["draft"],
                unfurl_links=False,
            )
            return self.permalink(cid, r["ts"])
        return None

    def alerts_channel(self) -> str:
        name = self.conf.get("alerts_channel") or "lore-alerts"
        cid = self.channel_ids().get(name)
        if not cid:
            raise ConnectorError(f"the bot can't see #{name}: /invite @lore there")
        return cid

    def permalink(self, channel: str, ts: str) -> str:
        return self._call("chat_getPermalink", channel=channel, message_ts=ts)["permalink"]

    def _call(self, method: str, **kwargs: Any) -> Any:
        kwargs = {k: v for k, v in kwargs.items() if v is not None}
        try:
            return getattr(self.client, method)(**kwargs)
        except SlackApiError as e:  # fail loudly, with Slack's own reason
            raise ConnectorError(f"Slack {method}: {e.response.get('error', e)}") from e


# --- the bot: @lore questions and alert cards ----------------------------------------------
def answer_text(out: dict[str, Any], permalink: Callable[[str], str | None]) -> str:
    """Lore's answer in Slack mrkdwn: the text, then each citation with its verbatim quote and,
    for a Slack message, a link back to it."""
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", out["answer_markdown"])
    if out["abstain"] or not out["citations"]:
        return text
    lines = [text, "", "_Sources (each quote checked word for word):_"]
    for c in out["citations"]:
        link = permalink(c["artifact_id"])
        where = f"<{link}|{c['artifact_id']}>" if link else f"`{c['artifact_id']}`"
        lines.append(f"[{c['n']}] {where}: “{c['quote']}”")
    return "\n".join(lines)


def alert_blocks(alert: dict[str, Any], decided: str | None = None) -> list[dict[str, Any]]:
    """An alert as a Slack card: summary, the triggering quote, Approve / Dismiss (or the
    verdict once given)."""
    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{alert['summary']}*"}},
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"{alert['kind']} · {alert['severity']} · {alert['product_id']} · "
                    f"{alert['radius_size']} affected · `{alert['id']}`",
                }
            ],
        },
    ]
    if alert.get("quote"):
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": f"> {alert['quote']}"}}
        )
    if decided:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": decided}]})
    else:
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "style": "primary",
                        "text": {"type": "plain_text", "text": "Approve and send"},
                        "action_id": "lore_approve",
                        "value": alert["id"],
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Dismiss"},
                        "action_id": "lore_dismiss",
                        "value": alert["id"],
                    },
                ],
            }
        )
    return blocks


class SlackBot:
    """The Socket Mode app: `@lore` questions, alert cards, button clicks, and a nudge to the
    runner when a mapped channel gets a message (so it syncs now, not at the next poll)."""

    def __init__(self, bot_token: str, app_token: str, graph: str, on_message: Callable[[], None]):
        from slack_bolt import App

        self.app = App(token=bot_token)
        self.app_token, self.graph, self.on_message = app_token, graph, on_message
        self.connector = SlackConnector(self.app.client, graph)
        self.app.event("app_mention")(self._mention)
        self.app.event("message")(self._message)
        self.app.action("lore_approve")(self._verdict("approved"))
        self.app.action("lore_dismiss")(self._verdict("dismissed"))

    def start(self) -> None:
        from slack_bolt.adapter.socket_mode import SocketModeHandler

        self.handler = SocketModeHandler(self.app, self.app_token)
        self.handler.connect()  # returns; the socket runs on its own threads

    def _permalink(self, artifact_id: str) -> str | None:
        prefix = "msg:synthetic-slack-"
        if not artifact_id.startswith(prefix):
            return None
        cid, _, ts = artifact_id.removeprefix(prefix).partition("-")
        try:
            return self.connector.permalink(cid, ts)
        except ConnectorError:
            return None

    def _mention(self, event: dict[str, Any], say: Callable[..., Any]) -> None:
        from app.agent import loop

        question = MENTION.sub("", event.get("text") or "").strip()
        thread = event.get("thread_ts") or event["ts"]
        if len(question) < 3:
            hint = "Ask me about the company, e.g. `@lore who knows the export module?`"
            say(text=hint, thread_ts=thread)
            return
        try:
            out = loop.answer(question, self.connector.maps.product, graph=self.graph)
        except Exception as e:  # shown in the thread, never a made-up answer
            say(text=f"I couldn't answer: {type(e).__name__}: {e}", thread_ts=thread)
            return
        say(text=answer_text(out, self._permalink), thread_ts=thread, unfurl_links=False)

    def _message(self, event: dict[str, Any]) -> None:
        if event.get("bot_id") or event.get("subtype"):
            return
        self.on_message()

    def _verdict(self, verdict: str) -> Callable[..., None]:
        def handle(ack: Callable[[], None], body: dict[str, Any], client: WebClient) -> None:
            from app.sentinel import approve, live

            ack()
            alert_id = body["actions"][0]["value"]
            user = body["user"]["id"]
            person = self.connector.maps.person("slack", user)
            channel, ts = body["channel"]["id"], body["message"]["ts"]
            try:
                out = approve.respond(alert_id, verdict, person, graph=live.demo_graph(self.graph))
                statement = out["decision"]["statement"]
                note = f"{verdict.capitalize()} by <@{user}> (as {person}): {statement}"
            except Exception as e:
                note = f"Couldn't record it: {e}"
            alert = next((a for a in _alerts(self.graph) if a["id"] == alert_id), {"id": alert_id})
            blocks = alert_blocks(alert, decided=note) if "summary" in alert else None
            client.chat_update(channel=channel, ts=ts, text=note, blocks=blocks)
            self.on_message()  # deliver the approved drafts now

        return handle

    def post_card(self, alert: dict[str, Any]) -> str:
        channel = self.connector.alerts_channel()
        r = self.connector._call(
            "chat_postMessage",
            channel=channel,
            text=alert["summary"],
            blocks=alert_blocks(alert),
            unfurl_links=False,
        )
        return r["ts"]


def _alerts(graph: str) -> list[dict[str, Any]]:
    from app.sentinel import engine

    return engine.list_alerts(graph=graph)


def now_ms() -> int:
    return int(time.time() * 1000)
