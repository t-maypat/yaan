"""Slack connector (17.3, D073-D074). Socket Mode, so nothing needs a public URL.

In: the messages of the channels `connectors.yaml` maps (e.g. #vizforce-bugs plays VizForce's
bug-davidgarcia channel) become live messages in that channel: S1-S8 + the Sentinel.
Out: notification drafts go to the alerts channel; corrections go into the thread of the
Slack message they correct. The bot (`SlackBot`) answers `@lore <question>` in the thread with
Lore's checked citations, and posts each new alert as a card with Approve and Dismiss buttons
that record the verdict as a decision (sentinel/approve.respond), as the Inbox does.

Who may read what (H1, D109): in org mode each round also reads which mapped channels are
private and who is in them, so Yaan shows a private channel only to its members. An answer
posted in a channel uses only what everyone there may read; a direct message to the bot gets
an answer from everything the asker may read.
"""

import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from app import db, limits, org
from app.connectors import base, render
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
        self._private: set[str] = set()  # names of the private channels among _channels
        self._team_url: str | None = None
        self._names: dict[str, str | None] = {}  # org mode: Slack user id -> display name
        self._bots: set[str] = set()  # accounts users.info says are bots

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
                self._private |= {c["name"] for c in r["channels"] if c.get("is_private")}
                cursor = (r.get("response_metadata") or {}).get("next_cursor")
                if not cursor:
                    break
            self._channels = out
        return self._channels

    def fetch(self, since_ms: int) -> list[Item]:
        if not base.is_demo():
            self._channels = None  # re-read: a channel may have turned private, or been added
            self.sync_access()
        items = []
        for slack_name, herb_id in org.channel_map(self.conf).items():
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

    def sync_access(self) -> dict[str, int]:
        """Org mode: each mapped channel's privacy, and a private one's members, from Slack
        into the graph (D109). A private Slack channel must feed a Yaan channel of its own:
        merged with another, its messages would be shown to the other channel's readers."""
        from app import org

        by_target: dict[str, list[str]] = {}
        for slack_name, target in org.channel_map(self.conf).items():
            by_target.setdefault(target, []).append(slack_name)
        ids = self.channel_ids()
        if unseen := sorted(n for names in by_target.values() for n in names if n not in ids):
            # never guess "public" for a channel we can't see: it may be private
            raise ConnectorError(f"the bot can't see #{unseen[0]}: /invite @lore there")
        rows, members = [], {}
        for target, names in sorted(by_target.items()):
            private = [n for n in names if n in self._private]
            if private and len(names) > 1:
                raise ConnectorError(
                    f"connectors.yaml maps #{', #'.join(names)} to {target!r}, and "
                    f"#{private[0]} is private: give a private channel a Yaan channel of its own"
                )
            if not private:
                rows.append({"id": target, "visibility": "public"})
                continue
            rows.append({"id": target, "visibility": "private"})
            members[target] = set(self.members(ids[private[0]]))
        org.ensure_people(self.graph, [e for m in members.values() for e in m])
        return org.set_channel_access(self.graph, rows, members)

    def members(self, cid: str) -> list[str]:
        """The people in a Slack channel (bots left out), as person ids."""
        out, cursor = [], None
        while True:
            r = self._call("conversations_members", channel=cid, limit=200, cursor=cursor)
            out += [self.who(u) for u in r["members"] if not self.is_bot(u)]
            cursor = (r.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                return out

    def is_bot(self, user: str) -> bool:
        self.user_name(user)  # one users.info call per account, which also says "bot"
        return user in self._bots

    def plain(self, text: str) -> str:
        """Slack markup to the plain text HERB messages have: <url|label> -> label (url),
        <@U123> -> @eid_… of the person that account plays."""
        text = LINK.sub(lambda m: f"{m[2]} ({m[1]})" if m[2] else m[1], text or "")
        return MENTION.sub(lambda m: "@" + self.who(m[1]), text)

    def who(self, user: str | None, text: str = "") -> str:
        """The person a Slack account is; in org mode with its profile name (one users.info
        call per account, cached), so a person met here is registered under a real name."""
        name = self.user_name(user) if user and not base.is_demo() else None
        return self.maps.person("slack", user, text, name=name)

    def user_name(self, user: str) -> str | None:
        if user not in self._names:
            try:
                profile = self._call("users_info", user=user)["user"]
                self._names[user] = profile.get("real_name") or profile.get("name")
                if profile.get("is_bot") or profile.get("is_app_user"):
                    self._bots.add(user)
            except ConnectorError:  # no users:read scope, or a deleted user: keep the id
                self._names[user] = None
        return self._names[user]

    def normalize(self, m: dict[str, Any], cid: str, herb_id: str, herb_name: str) -> Item | None:
        text = m.get("text") or ""
        by_bot = bool(m.get("bot_id")) or m.get("subtype") == "bot_message"
        if not text or m.get("subtype") in SKIP_SUBTYPES - {"bot_message"}:
            return None  # channel events aren't company knowledge
        if by_bot and not (base.is_demo() and text.startswith(SEEDED)):
            return None  # Lore's own answers, cards and notifications: never read back
        text = self.plain(m["text"])
        author = self.who(m.get("user"), text)
        created = ts_ms(m["ts"])
        edited = (m.get("edited") or {}).get("ts")
        a = Artifact(
            id=base.synthetic_id("msg", "slack", f"{cid}-{m['ts']}"),
            kind="message",
            product_hint=self.maps.product_of("slack", herb_id),
            channel_id=herb_id,
            ts_ms=created,
            ts_iso=iso(created),
            author_eid=author,
            text=text,
            raw_ref=f"slack:{cid}:{m['ts']}",
            meta={
                "channel_name": herb_name,
                "sender": author,
                "synthetic": base.is_demo(),
                "source": "slack",
                "slack_channel": cid,
                "slack_ts": m["ts"],
            },
        )
        return Item(a, updated_ms=ts_ms(edited) if edited else created)

    # --- writing ---------------------------------------------------------------------------
    def post(self, action: dict[str, Any], resolve: render.Resolve | None = None) -> str | None:
        """Notifications go to the alerts channel; a correction of a Slack message goes into
        its thread. Anything else belongs to another tool. Rendered as Block Kit (D083)."""
        target = action.get("target_id") or ""
        resolve = resolve or self.link
        # the action's id rides along as message metadata, so a retry finds it (D110)
        meta = {"event_type": "yaan_action", "event_payload": {"action_id": action["id"]}}
        if action["kind"] == "notify_owner":
            channel = self.alerts_channel()
            if done := self.marked(action, channel, oldest_ms=action.get("decided_ms")):
                return done
            text, blocks = render.slack_notify(action, resolve)
            r = self._call(
                "chat_postMessage",
                channel=channel,
                text=text,
                blocks=blocks,
                unfurl_links=False,
                metadata=meta,
            )
            return self.permalink(channel, r["ts"])
        prefix = base.id_prefix("msg", "slack")
        if target.startswith(prefix):
            cid, _, ts = target.removeprefix(prefix).partition("-")
            if done := self.marked(action, cid, thread=ts):
                return done
            r = self._call(
                "chat_postMessage",
                channel=cid,
                thread_ts=ts,
                text=f"Correction from Lore: {action.get('target_name') or 'this message'}",
                blocks=render.slack_correction(action, resolve),
                unfurl_links=False,
                metadata=meta,
            )
            return self.permalink(cid, r["ts"])
        return None

    def marked(
        self,
        action: dict[str, Any],
        channel: str,
        oldest_ms: int | None = None,
        thread: str | None = None,
    ) -> str | None:
        """The message an earlier try of `action` already posted (by its metadata), if any."""
        if thread:
            r = self._call(
                "conversations_replies", channel=channel, ts=thread, include_all_metadata=True
            )
        else:
            oldest = f"{oldest_ms / 1000:.6f}" if oldest_ms else None
            r = self._call(
                "conversations_history",
                channel=channel,
                oldest=oldest,
                include_all_metadata=True,
                limit=200,
            )
        for m in r.get("messages") or []:
            meta = m.get("metadata") or {}
            payload = meta.get("event_payload") or {}
            if meta.get("event_type") == "yaan_action" and payload.get("action_id") == action["id"]:
                return self.permalink(channel, m["ts"])
        return None

    def link(self, artifact_id: str) -> render.Link | None:
        """A Slack message's label and permalink, built without an API call per message."""
        prefix = base.id_prefix("msg", "slack")
        if not artifact_id.startswith(prefix):
            return None
        cid, _, ts = artifact_id.removeprefix(prefix).partition("-")
        if self._team_url is None:
            try:
                self._team_url = self._call("auth_test").get("url") or ""
            except ConnectorError:
                self._team_url = ""
        names = {v: k for k, v in (self._channels or {}).items()}
        label = f"Message in #{names[cid]}" if cid in names else "Slack message"
        url = f"{self._team_url}archives/{cid}/p{ts.replace('.', '')}" if self._team_url else None
        return label, url

    def yaan_channel(self, slack_channel_id: str) -> str | None:
        """The Yaan channel a Slack channel feeds, by its Slack id (None: not mapped)."""
        names = {v: k for k, v in self.channel_ids().items()}
        return org.channel_map(self.conf).get(names.get(slack_channel_id, ""))

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
IN_A_DM = (
    "\n_Not everyone in this channel can see those sources, so this answer leaves them out. "
    "Send me the question as a direct message for an answer from everything you can see._"
)


def answer_text(out: dict[str, Any], permalink: Callable[[str], str | None]) -> str:
    """Lore's answer in Slack mrkdwn: the text, then each citation with its verbatim quote and,
    for a Slack message, a link back to it."""
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", out["answer_markdown"])
    if out.get("hidden_sources"):
        text += f"\n_{out['hidden_sources']} sources hidden: you can't see them in Lore._"
    if out["abstain"] or not out["citations"]:
        return text
    lines = [text, "", "_Sources (each quote checked word for word):_"]
    for c in out["citations"]:
        link = permalink(c["artifact_id"])
        where = f"<{link}|{c['artifact_id']}>" if link else f"`{c['artifact_id']}`"
        lines.append(f"[{c['n']}] {where}: “{c['quote']}”")
    return "\n".join(lines)


def alert_blocks(
    alert: dict[str, Any], decided: str | None = None, resolve: render.Resolve | None = None
) -> list[dict[str, Any]]:
    """An alert as a Slack card (render.alert_blocks): what changed, the evidence, who it
    reaches, Approve / Dismiss, or the verdict once given."""
    return render.alert_blocks(alert, decided, resolve)


class SlackBot:
    """The Socket Mode app: `@lore` questions, alert cards, button clicks, and a nudge to the
    runner when a mapped channel gets a message (so it syncs now, not at the next poll)."""

    def __init__(self, bot_token: str, app_token: str, graph: str, on_message: Callable[[], None]):
        from slack_bolt import App

        self.app = App(token=bot_token)
        self.app_token, self.graph, self.on_message = app_token, graph, on_message
        self.connector = SlackConnector(self.app.client, graph)
        # each Slack user's questions, as /ask counts a signed-in person's (D124)
        self.per_minute, self.per_day = limits.person_limits()
        # Links to sources in other tools; the runner sets one over every connector.
        self.resolve: render.Resolve = self.connector.link
        self.app.event("app_mention")(self._mention)
        self.app.event("message")(self._message)
        self.app.action("lore_approve")(self._verdict("approved"))
        self.app.action("lore_dismiss")(self._verdict("dismissed"))

    def start(self) -> None:
        from slack_bolt.adapter.socket_mode import SocketModeHandler

        self.handler = SocketModeHandler(self.app, self.app_token)
        self.handler.connect()  # returns; the socket runs on its own threads

    def _permalink(self, artifact_id: str) -> str | None:
        prefix = base.id_prefix("msg", "slack")
        if not artifact_id.startswith(prefix):
            return None
        cid, _, ts = artifact_id.removeprefix(prefix).partition("-")
        try:
            return self.connector.permalink(cid, ts)
        except ConnectorError:
            return None

    def limited(self, user: str | None) -> str | None:
        """Why this Slack user may not ask now, or None. Counted per account, like /ask."""
        key = user or "unknown"
        if not self.per_minute.allow(key):
            n = self.per_minute.per_minute
            return f"You've asked {n} questions in a minute: try again shortly."
        if not self.per_day.allow(key):
            return f"You've asked {self.per_day.per_minute} questions today: the daily limit."
        return None

    def payer(self, user: str | None) -> str | None:
        """Whose daily budget an answer counts against: the asker, in org mode (D124)."""
        return self.connector.who(user) if user and not base.is_demo() else None

    def _mention(self, event: dict[str, Any], say: Callable[..., Any]) -> None:
        from app.agent import access, loop

        question = MENTION.sub("", event.get("text") or "").strip()
        thread = event.get("thread_ts") or event["ts"]
        if len(question) < 3:
            hint = "Ask me about the company, e.g. `@lore who knows the export module?`"
            say(text=hint, thread_ts=thread)
            return
        if refusal := self.limited(event.get("user")):
            say(text=refusal, thread_ts=thread)
            return
        try:
            # Posted in the channel, so it may use only what everyone there can read, not
            # what the asker alone may (D109); a DM to the bot answers from all of that.
            channel = self.connector.yaan_channel(event.get("channel") or "")
            scope = access.Access.audience(channel, self.graph)
            product = self.connector.maps.product_of("slack", channel)
            out = loop.answer(
                question,
                product,
                graph=self.graph,
                audience=scope,
                payer=self.payer(event.get("user")),
            )
        except Exception as e:  # shown in the thread, never a made-up answer
            say(text=f"I couldn't answer: {type(e).__name__}: {e}", thread_ts=thread)
            return
        text = answer_text({**out, "hidden_sources": 0}, self._permalink)
        if out.get("hidden_sources"):
            text += IN_A_DM
        say(text=text, thread_ts=thread, unfurl_links=False)

    def _direct(self, event: dict[str, Any], say: Callable[..., Any]) -> None:
        """A direct message to the bot: answered from everything the asker may read (W7)."""
        from app.agent import loop

        question = MENTION.sub("", event.get("text") or "").strip()
        if len(question) < 3:
            return
        if refusal := self.limited(event.get("user")):
            say(text=refusal)
            return
        try:
            viewer = self.connector.who(event.get("user"))
            out = loop.answer(
                question,
                self.connector.maps.product,
                graph=self.graph,
                viewer_id=viewer,
                payer=self.payer(event.get("user")),
            )
        except Exception as e:  # shown to the asker, never a made-up answer
            say(text=f"I couldn't answer: {type(e).__name__}: {e}")
            return
        say(text=answer_text(out, self._permalink), unfurl_links=False)

    def _message(self, event: dict[str, Any], say: Callable[..., Any] | None = None) -> None:
        if event.get("bot_id") or event.get("subtype"):
            return
        if event.get("channel_type") == "im" and say is not None:
            self._direct(event, say)
            return
        self.on_message()

    def _verdict(self, verdict: str) -> Callable[..., None]:
        def handle(ack: Callable[[], None], body: dict[str, Any], client: WebClient) -> None:
            from app.sentinel import approve, live

            ack()
            alert_id = body["actions"][0]["value"]
            user = body["user"]["id"]
            person = self.connector.who(user)
            channel, ts = body["channel"]["id"], body["message"]["ts"]
            try:
                graph = live.demo_graph(self.graph)
                if not base.is_demo():  # the approver may not have posted anything yet
                    from app import org

                    org.ensure_people(graph, [person])
                out = approve.respond(alert_id, verdict, person, graph=graph)
                statement = out["decision"]["statement"]
                icon = ":white_check_mark:" if verdict == "approved" else ":no_entry_sign:"
                note = f"{icon} *{verdict.capitalize()}* by <@{user}> · {statement}"
            except Exception as e:
                note = f"Couldn't record it: {e}"
            alert = next((a for a in _alerts(self.graph) if a["id"] == alert_id), {"id": alert_id})
            alert = base.public_alert(alert, self.graph)  # the card stays as open as it was
            blocks = (
                alert_blocks(alert, decided=note, resolve=self.resolve)
                if "summary" in alert
                else None
            )
            client.chat_update(channel=channel, ts=ts, text=note, blocks=blocks)
            self.on_message()  # deliver the approved drafts now

        return handle

    def post_card(self, alert: dict[str, Any]) -> str:
        channel = self.connector.alerts_channel()
        r = self.connector._call(
            "chat_postMessage",
            channel=channel,
            text=alert["summary"],
            blocks=alert_blocks(alert, resolve=self.resolve),
            unfurl_links=False,
        )
        return r["ts"]


def _alerts(graph: str) -> list[dict[str, Any]]:
    from app.sentinel import engine

    return engine.list_alerts(graph=graph)


def now_ms() -> int:
    return int(time.time() * 1000)
