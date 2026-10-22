"""Who may read what in org mode (H1, D109), and the scopes of text that leaves Yaan.

- connectors.yaml must declare every non-Slack source's audience; Slack's comes from Slack;
- a private Slack channel's members are synced, and it may not share a Yaan channel;
- an answer posted in a Slack channel uses only what everyone there may read, a DM to the bot
  everything the asker may read;
- on a real graph (marked db): public / private / not-yet-synced channels, a pull request by
  its source's declared audience, and a membership sync that changes only what changed.
These are the privacy guarantees: never skip them."""

import pytest

from app import db, org
from app.agent import access
from app.config import get_settings
from app.connectors import base, slack
from app.connectors.base import ConnectorError, Maps

PEOPLE = """
people:
  - name: Priya Shah
    email: priya@acme.example
    id: eid_0000a001
    authority: lead
    accounts: {slack: U1}
  - name: Ken Ito
    id: eid_0000a002
    accounts: {slack: U2}
"""
PRIYA, KEN = "eid_0000a001", "eid_0000a002"


@pytest.fixture
def org_mode(tmp_path, monkeypatch):
    people = tmp_path / "people.yaml"
    people.write_text(PEOPLE, encoding="utf-8")
    monkeypatch.setenv("LORE_DATA_MODE", "org")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(org, "PEOPLE_FILE", people)
    caches = (get_settings, org.roster, org.accounts, org.source_audiences, base.maps)
    for cached in caches:
        cached.cache_clear()
    org._hints.clear()
    yield tmp_path
    for cached in caches:
        cached.cache_clear()
    org._hints.clear()


# --- declarations ----------------------------------------------------------------------------
def test_every_non_slack_source_declares_who_may_read_it(org_mode):
    with pytest.raises(org.OrgError, match="github needs `visibility: everyone`"):
        org.audiences({"github": {}})
    with pytest.raises(org.OrgError, match="no `members`"):
        org.audiences({"jira": {"visibility": "members"}})
    with pytest.raises(org.OrgError, match="needs `visibility: members`"):
        org.audiences({"jira": {"visibility": "everyone", "members": ["Ken Ito"]}})
    with pytest.raises(org.OrgError, match="isn't in people.yaml"):
        org.audiences({"jira": {"visibility": "members", "members": ["Nobody"]}})
    found = org.audiences(
        {
            "slack": {"channels": {"support": "bug-support"}},  # Slack's comes from Slack
            "github": {"visibility": "everyone"},
            "notion": {"visibility": "members", "members": ["priya@acme.example", "Ken Ito"]},
        }
    )
    assert found == {
        "github": org.Audience("everyone"),
        "notion": org.Audience("members", frozenset({PRIYA, KEN})),
    }


def test_a_slack_channel_gets_its_access_from_slack_alone(org_mode):
    sources = {
        "slack": {"channels": {"support": "bug-support"}},
        "jira": {"channel": "bug-support", "visibility": "everyone"},
    }
    with pytest.raises(org.OrgError, match="fed by Slack and by"):
        org.channel_access(sources)
    sources["jira"]["channel"] = "bug-tickets"
    sources["notion"] = {"channel": "planning-specs", "visibility": "members", "members": [KEN]}
    rows, members = org.channel_access(sources)
    assert rows == [
        {"id": "bug-tickets", "visibility": "public"},
        {"id": "planning-specs", "visibility": "private"},
    ]
    assert members == {"bug-tickets": set(), "planning-specs": {KEN}}


# --- the Slack side, against a fake client ---------------------------------------------------
class FakeSlack:
    def __init__(self, channels, members):
        self.channels, self.member_lists = channels, members

    def conversations_list(self, **kw):
        return {"channels": self.channels}

    def conversations_members(self, channel, **kw):
        return {"members": self.member_lists[channel]}

    def users_info(self, user):
        return {"user": {"real_name": f"User {user}", "is_bot": user.startswith("B")}}

    def conversations_history(self, **kw):
        return {"messages": []}


def slack_maps(channels: dict[str, str]) -> Maps:
    return Maps(product="Acme", default_person=None, sources={"slack": {"channels": channels}})


@pytest.fixture
def written(monkeypatch):
    out = {}
    monkeypatch.setattr(
        org, "set_channel_access", lambda graph, rows, members: out.update(rows=rows, m=members)
    )
    monkeypatch.setattr(org, "ensure_people", lambda graph, eids: out.setdefault("people", eids))
    return out


def test_slack_says_which_channels_are_private_and_who_is_in_them(org_mode, written):
    fake = FakeSlack(
        [
            {"name": "support", "id": "C1", "is_private": False},
            {"name": "leads", "id": "C2", "is_private": True},
        ],
        {"C2": ["U1", "B9"]},  # a bot in the channel is not a reader
    )
    maps = slack_maps({"support": "bug-support", "leads": "bug-leads"})
    conn = slack.SlackConnector(fake, "yaan_org", maps)
    conn.sync_access()
    assert written["rows"] == [
        {"id": "bug-leads", "visibility": "private"},
        {"id": "bug-support", "visibility": "public"},
    ]
    assert written["m"] == {"bug-leads": {PRIYA}}


def test_a_private_slack_channel_may_not_share_a_yaan_channel(org_mode, written):
    fake = FakeSlack(
        [
            {"name": "support", "id": "C1", "is_private": False},
            {"name": "escalations", "id": "C3", "is_private": True},
        ],
        {"C3": ["U1"]},
    )
    maps = slack_maps({"support": "bug-support", "escalations": "bug-support"})
    with pytest.raises(ConnectorError, match="#escalations is private"):
        slack.SlackConnector(fake, "yaan_org", maps).sync_access()
    assert written == {}  # nothing written


def test_a_channel_the_bot_cant_see_is_never_guessed_public(org_mode, written):
    fake = FakeSlack([{"name": "support", "id": "C1", "is_private": False}], {})
    maps = slack_maps({"support": "bug-support", "leads": "bug-leads"})
    with pytest.raises(ConnectorError, match="can't see #leads"):
        slack.SlackConnector(fake, "yaan_org", maps).sync_access()
    assert written == {}


def bot(conn) -> slack.SlackBot:
    """A SlackBot without slack_bolt's App (which would call Slack at construction)."""
    from app import limits

    b = slack.SlackBot.__new__(slack.SlackBot)
    b.connector, b.graph, b.on_message = conn, "yaan_org", lambda: None
    b.per_minute, b.per_day = limits.person_limits()
    return b


def test_an_answer_in_a_channel_uses_what_everyone_there_may_read(org_mode, monkeypatch):
    from app.agent import loop

    seen = {}

    def answer(question, product, graph=None, audience=None, viewer_id=None, **kw):
        seen.update(audience=audience, viewer_id=viewer_id)
        return {"answer_markdown": "Fixed.", "abstain": False, "citations": [], "hidden_sources": 2}

    monkeypatch.setattr(loop, "answer", answer)
    fake = FakeSlack([{"name": "support", "id": "C1", "is_private": False}], {})
    conn = slack.SlackConnector(fake, "yaan_org", slack_maps({"support": "bug-support"}))
    said = []
    bot(conn)._mention(
        {"text": "<@UBOT> is export fixed?", "ts": "1.0", "channel": "C1", "user": "U1"},
        lambda **kw: said.append(kw),
    )
    scope = seen["audience"]
    assert scope.viewer is None and scope.channel == "bug-support" and seen["viewer_id"] is None
    assert "direct message" in said[0]["text"] and "you can't see them" not in said[0]["text"]
    # a channel Yaan doesn't read: only what the whole company may read
    bot(conn)._mention(
        {"text": "<@UBOT> is export fixed?", "ts": "2.0", "channel": "C7", "user": "U1"},
        lambda **kw: None,
    )
    assert seen["audience"].channel is None


def test_a_direct_message_is_answered_from_what_the_asker_may_read(org_mode, monkeypatch):
    from app.agent import loop

    seen = {}

    def answer(question, product, graph=None, audience=None, viewer_id=None, payer=None, **kw):
        seen.update(audience=audience, viewer_id=viewer_id, payer=payer)
        return {"answer_markdown": "Fixed.", "abstain": False, "citations": []}

    monkeypatch.setattr(loop, "answer", answer)
    conn = slack.SlackConnector(FakeSlack([], {}), "yaan_org", slack_maps({}))
    said = []
    bot(conn)._message(
        {"text": "is export fixed?", "channel_type": "im", "user": "U1", "ts": "1.0"},
        lambda **kw: said.append(kw),
    )
    assert seen == {"audience": None, "viewer_id": PRIYA, "payer": PRIYA}
    assert said[0]["text"] == "Fixed."


def test_slack_questions_are_limited_per_asker_and_charged_to_them(org_mode, monkeypatch):
    """D124: an @mention counts against its asker's own limits and daily budget, so one heavy
    user can't use up everyone's questions."""
    from app import limits
    from app.agent import loop

    payers = []

    def answer(question, product, graph=None, audience=None, payer=None, **kw):
        payers.append(payer)
        return {"answer_markdown": "Fixed.", "abstain": False, "citations": [], "hidden_sources": 0}

    monkeypatch.setattr(loop, "answer", answer)
    fake = FakeSlack([{"name": "support", "id": "C1", "is_private": False}], {})
    b = bot(slack.SlackConnector(fake, "yaan_org", slack_maps({"support": "bug-support"})))
    b.per_minute = limits.RateLimiter(1)
    said = []

    def ask(user: str, ts: str) -> None:
        event = {"text": "<@UBOT> is export fixed?", "ts": ts, "channel": "C1", "user": user}
        b._mention(event, lambda **kw: said.append(kw["text"]))

    ask("U1", "1.0")
    ask("U1", "2.0")  # the same person again within the minute
    ask("U2", "3.0")  # someone else: their own limit
    assert payers == [PRIYA, b.connector.who("U2")]
    assert "questions in a minute" in said[1] and said[2] == "Fixed."


# --- on the real server ------------------------------------------------------------------------
COMPANY = """
CREATE
  (:Person:Entity {id: 'eid_0000a001', name: 'Priya Shah'}),
  (:Person:Entity {id: 'eid_0000a002', name: 'Ken Ito'}),
  (pub:Channel {id: 'bug-support', name: 'bug-support', stage: 'support'}),
  (priv:Channel {id: 'bug-leads', name: 'bug-leads', stage: 'support'}),
  (new:Channel {id: 'bug-new', name: 'bug-new', stage: 'support'}),
  (m1:Artifact:Message {id: 'msg:slack-C1-1', kind: 'message', text: 'Export is slow.'}),
  (m2:Artifact:Message {id: 'msg:slack-C2-1', kind: 'message', text: 'Leads only.'}),
  (m3:Artifact:Message {id: 'msg:slack-C4-1', kind: 'message', text: 'Not synced yet.'}),
  (:Artifact:PR {id: 'pr:github-acme-app-1', kind: 'pr', title: 'Fix export'}),
  (m1)-[:IN_CHANNEL]->(pub), (m2)-[:IN_CHANNEL]->(priv), (m3)-[:IN_CHANNEL]->(new)
"""
PUB, PRIV, UNSYNCED = "msg:slack-C1-1", "msg:slack-C2-1", "msg:slack-C4-1"
PR = "pr:github-acme-app-1"


@pytest.fixture
def acme(org_mode, empty_graph, monkeypatch):
    db.run_text(COMPANY, label="acme", graph=empty_graph, readonly=False)
    org.set_channel_access(
        empty_graph,
        [
            {"id": "bug-support", "visibility": "public"},
            {"id": "bug-leads", "visibility": "private"},
        ],
        {"bug-leads": {PRIYA}},
    )
    return empty_graph


def allowed(scope: access.Access) -> set[str]:
    return scope.allowed({PUB, PRIV, UNSYNCED, PR})


@pytest.mark.db
def test_org_mode_reads_the_teams_own_access(acme, monkeypatch):
    monkeypatch.setattr(
        org, "source_audiences", lambda: {"github": org.Audience("members", frozenset({KEN}))}
    )
    assert allowed(access.Access(PRIYA, acme)) == {PUB, PRIV}  # a member; not on the PR's list
    assert allowed(access.Access(KEN, acme)) == {PUB, PR}  # never posted anywhere: no matter
    # what everyone in the company, or in one channel, may read
    assert allowed(access.Access.audience(None, acme)) == {PUB}
    assert allowed(access.Access.audience("bug-leads", acme)) == {PUB, PRIV}
    monkeypatch.setattr(org, "source_audiences", lambda: {})  # an undeclared source: hidden
    assert allowed(access.Access(KEN, acme)) == {PUB}


@pytest.mark.db
def test_a_membership_sync_changes_only_who_joined_or_left(acme, monkeypatch):
    monkeypatch.setattr(org, "source_audiences", lambda: {})
    def members() -> dict[str, int]:
        rows = db.run_text(
            "MATCH (p:Person)-[m:MEMBER_OF]->(:Channel {id: 'bug-leads'}) "
            "RETURN p.id AS id, m.since_ms AS since LIMIT 10",
            label="t",
            graph=acme,
        ).rows
        return {r["id"]: r["since"] for r in rows}

    first = members()
    assert set(first) == {PRIYA}
    out = org.set_channel_access(acme, [], {"bug-leads": {PRIYA, KEN}})
    assert out == {"channels": 0, "joined": 1, "left": 0}
    assert members()[PRIYA] == first[PRIYA]  # untouched, not removed and added again
    out = org.set_channel_access(acme, [], {"bug-leads": {KEN}})
    assert out == {"channels": 0, "joined": 0, "left": 1}
    assert allowed(access.Access(PRIYA, acme)) == {PUB}  # she left: she stops seeing it


@pytest.mark.db
def test_text_that_leaves_yaan_quotes_only_what_everyone_may_read(acme, monkeypatch):
    monkeypatch.setattr(org, "source_audiences", lambda: {})
    draft = (
        f'Reported: "Export is slow." ({PUB})\n'
        f'Escalated: "Leads only." ({PRIV}), see the thread'
    )
    out = base.public_quotes(draft, acme)
    assert f'"Export is slow." ({PUB})' in out
    assert "Leads only" not in out and f'"{base.WITHHELD}" ({PRIV}), see the thread' in out
    card = {"id": "alert:1", "quote": "Leads only.", "artifact_id": PRIV, "summary": "Back"}
    assert base.public_alert(card, acme)["quote"] == base.WITHHELD
    open_card = {**card, "quote": "Export is slow.", "artifact_id": PUB}
    assert base.public_alert(open_card, acme) == open_card
