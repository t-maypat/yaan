"""Raw HERB files (data/herb) -> typed records. Built in Phase 2.

Handles both Slack shapes (CONTEXT §6.2). Drops the product files' team / customers
fields, which are oracle-only (§6.3). Verified field names: docs/herb_format.md.

read_artifacts (2.1, used by the dev ranking) is the only way product files are read for
artifacts: it drops the question lists and the oracle-only fields the moment a file is
parsed, so nothing downstream can see them. Questions are loaded only through
app.eval.split, which guards held-out products (2.4).

load_product (2.5) validates those artifact lists into the models below. Every model
forbids unknown keys, so a change in HERB's shape fails loudly instead of losing data.
"""

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

ORACLE_KEYS = ("team", "customers")  # CONTEXT §6.3: never used to build or answer
QUESTION_KEYS = ("answerable_questions", "unanswerable_questions")
ARTIFACT_KEYS = ("slack", "documents", "meeting_transcripts", "meeting_chats", "urls", "prs")


def read_artifacts(path: Path) -> dict[str, list[dict[str, Any]]]:
    """A product file's artifact lists only; questions and oracle-only fields are dropped."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    for key in ORACLE_KEYS + QUESTION_KEYS:
        raw.pop(key, None)
    unexpected = set(raw) - set(ARTIFACT_KEYS)
    if unexpected:
        raise ValueError(f"{path.name}: unexpected top-level keys {sorted(unexpected)}")
    return {key: raw.get(key, []) for key in ARTIFACT_KEYS}


class Raw(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SlackChannel(Raw):
    name: str
    channelID: str


class SlackUser(Raw):
    userId: str
    timestamp: str
    text: str
    utterranceID: str


class SlackBody(Raw):
    User: SlackUser
    Reactions: list[Any] = []


class SlackMessage(Raw):
    Channel: SlackChannel
    Message: SlackBody
    ThreadReplies: list[Any] = []
    id: str

    @model_validator(mode="before")
    @classmethod
    def from_flat(cls, data: Any) -> Any:
        """The dataset card's flat variant {sender, message, timestamp, id, channel?}.

        Not seen at the pinned revision (herb_format.md), handled per CONTEXT §6.2.
        """
        if isinstance(data, dict) and "sender" in data:
            channel = data.get("channel") or {"name": "", "channelID": ""}
            if isinstance(channel, str):
                channel = {"name": channel, "channelID": channel}
            user = {
                "userId": data["sender"],
                "timestamp": data["timestamp"],
                "text": data["message"],
                "utterranceID": data["id"],
            }
            return {"Channel": channel, "Message": {"User": user}, "id": data["id"]}
        return data


class Meeting(Raw):
    transcript: str
    date: str
    document_type: str
    participants: list[str]
    id: str


class MeetingChat(Raw):
    text: str
    id: str


class Document(Raw):
    content: str
    date: str
    author: str
    document_link: str
    type: str
    id: str
    feedback: str | None = None


class Login(Raw):
    login: str


class Review(Raw):
    state: str
    user: Login
    comment: str | None = None
    submitted_at: str


class PullRequest(Raw):
    id: str
    title: str
    summary: str
    link: str
    mergeable: str
    merged: str
    number: str
    state: str
    user: Login
    created_at: str
    reviews: list[Review]


class Url(Raw):
    id: str
    link: str
    description: str


class Product(Raw):
    name: str
    slack: list[SlackMessage]
    documents: list[Document]
    meeting_transcripts: list[Meeting]
    meeting_chats: list[MeetingChat]
    urls: list[Url]
    prs: list[PullRequest]


def load_product(path: Path) -> Product:
    """One product's artifacts as typed records (no questions, no oracle-only fields)."""
    return Product.model_validate({"name": path.stem, **read_artifacts(path)})
