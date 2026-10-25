"""Discord connector (D090, EXPERIMENTAL: not run against a live API): channel messages in,
replies out. REST v10, polling (no Gateway connection, so nothing is pushed).

In: the messages of one channel (`DISCORD_CHANNEL_ID`) newer than the cursor become messages in
the Yaan channel `connectors.yaml` maps (`discord.channels`: Discord channel id -> Yaan channel
id). Reading message text needs the MESSAGE_CONTENT privileged intent enabled for the bot and
the READ_MESSAGE_HISTORY permission. Edits after the cursor passed a message are not seen: the
`after` cursor only returns new messages. People are keyed by Discord user id in
`discord.people`.
Out: a draft aimed at a message that came from Discord is posted as a reply to it (Discord
Markdown). Content over Discord's 2000 characters is refused loudly, never cut: a cut quote would
no longer be verbatim.

Auth: `DISCORD_BOT_TOKEN`, sent as `Authorization: Bot <token>`; a bot token needs no OAuth flow
at run time (the bot is added to the server once, by hand).

Docs (retrieved 2026-10-18), from the published docs repo
https://github.com/discord/discord-api-docs/blob/main/developers/resources/message.mdx:
- https://discord.com/developers/docs/resources/message#get-channel-messages (after, limit 1-100)
- https://discord.com/developers/docs/resources/message#create-message (content up to 2000
  characters, message_reference)
- https://discord.com/developers/docs/resources/message#message-reference-structure
- https://discord.com/developers/docs/reference#snowflakes (id -> time: (id >> 22) + epoch
  1420070400000)
"""

from typing import Any

import httpx

from app import org
from app.connectors import base, render
from app.connectors.base import ConnectorError, Item, Maps
from app.connectors.experimental import HttpConnector, to_ms

API = "https://discord.com/api/v10"
EPOCH = 1420070400000  # Discord's epoch: 2015-01-01
LIMIT = 2000


def snowflake_ms(snowflake: str) -> int:
    return (int(snowflake) >> 22) + EPOCH


def snowflake_after(stamp_ms: int) -> str:
    return str(max(stamp_ms - EPOCH, 0) << 22)


class DiscordConnector(HttpConnector):
    name = "discord"
    label = "Discord"

    def __init__(
        self,
        bot_token: str,
        channel_id: str,
        graph: str,
        maps: Maps | None = None,
        client: httpx.Client | None = None,
    ):
        self.channel_id = channel_id
        super().__init__(
            graph,
            maps,
            client
            or httpx.Client(
                base_url=API,
                timeout=20,
                headers={"Authorization": f"Bot {bot_token}", "User-Agent": "Yaan (scaffold, 0.1)"},
            ),
        )
        self.channel = org.channel_map(self.maps.source("discord")).get(channel_id)

    def msg_ref(self, artifact_id: str) -> tuple[str, str] | None:
        """(discord channel id, message id) of a message this connector brought in."""
        prefix = base.synthetic_id("msg", "discord", "x")[:-1]
        if not artifact_id.startswith(prefix):
            return None
        channel, _, message = artifact_id.removeprefix(prefix).partition("-")
        return (channel, message) if channel.isdigit() and message.isdigit() else None

    # --- reading ---------------------------------------------------------------------------
    def fetch(self, since_ms: int) -> list[Item]:
        if not self.channel:
            raise ConnectorError(
                f"connectors.yaml: discord.channels needs {self.channel_id!r} -> a Yaan channel id"
            )
        channel_name = self.channel_name()
        items: list[Item] = []
        after = snowflake_after(since_ms) if since_ms else "0"
        while True:
            page = self._req(
                "GET",
                f"/channels/{self.channel_id}/messages",
                params={"after": after, "limit": 100},
            )
            for m in page:
                items.append(self.normalize(m, channel_name))
            if len(page) < 100:
                return items
            after = max(page, key=lambda m: int(m["id"]))["id"]

    def normalize(self, msg: dict[str, Any], channel_name: str) -> Item:
        text = msg.get("content") or ""
        stamp = to_ms(msg["timestamp"])
        who = self.maps.person("discord", (msg.get("author") or {}).get("id"), text)
        a = self.message(
            f"{self.channel_id}-{msg['id']}",
            stamp,
            who,
            text,
            channel_name,
            discord_channel=self.channel_id,
        )
        edited = msg.get("edited_timestamp")
        return Item(a, updated_ms=to_ms(edited) if edited else stamp)

    # --- writing ---------------------------------------------------------------------------
    def link(self, artifact_id: str) -> render.Link | None:
        return ("Discord message", None) if self.msg_ref(artifact_id) else None

    def post(self, action: dict[str, Any], resolve: render.Resolve | None = None) -> str | None:
        ref = self.msg_ref(action.get("target_id") or "")
        if ref is None:
            return None
        channel, message = ref
        text = (
            render.markdown(action, resolve or self.link)
            .replace("<sub>", "-# ")
            .replace("</sub>", "")
        )  # Discord has no <sub>; "-# " is its subtext
        if len(text) > LIMIT:
            raise ConnectorError(
                f"Discord: the reply is {len(text)} characters, over the {LIMIT} limit"
            )
        out = self._req(
            "POST",
            f"/channels/{channel}/messages",
            json={
                "content": text,
                "message_reference": {"message_id": message, "fail_if_not_exists": False},
                "allowed_mentions": {"parse": []},
            },
        )
        return f"discord:{channel}/{out['id']}"
