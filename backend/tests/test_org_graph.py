"""Org mode's collaboration graph (F4, D127): conversations are threads or sessions, not whole
channels; COLLAB comes only from what everyone may read; people.yaml's teams are the org chart;
the hourly job builds COLLAB, PageRank, communities and WORKS_ON. The last tests run on a real
graph (events are given, so no LLM)."""

import pytest

from app import db, org
from app.config import get_settings
from app.connectors import base
from app.connectors.base import Maps
from app.ingest import derive
from app.ingest.normalize import Artifact
from app.sentinel import live

H = 3_600_000


def row(n: int, who: str, ts: int, channel: str = "bug-support", thread: str | None = None):
    return {"message_id": f"msg:slack-C1-{n}", "channel_id": channel, "person_id": who,
            "ts_ms": ts, "thread_id": thread}  # fmt: skip


def test_a_conversation_is_a_thread_or_a_session_never_a_whole_channel():
    root = "msg:slack-C1-1"
    said = [
        row(1, "ann", 0),  # a thread's root ...
        row(2, "bob", 10 * 60_000, thread=root),  # ... and its replies
        row(3, "cy", 5 * H, thread=root),  # a late reply is still the thread
        row(4, "dee", 20 * 60_000),  # top-level, 20 min after the root: a session with it?
        row(5, "eve", 30 * 60_000),  # no: the root is a thread; 4 and 5 are one session
        row(6, "fay", 2 * H),  # 90 min later: a new session
        row(7, "gus", 2 * H + 60_000, channel="bug-other"),  # another channel, another session
    ]
    grouped = derive.conversations(said)
    keys = {r["message_id"].rsplit("-", 1)[1]: r["conversation"] for r in grouped}
    assert keys["1"] == keys["2"] == keys["3"] == ("thread", root)
    assert keys["4"] == keys["5"] != keys["6"]
    assert keys["7"] != keys["6"]
    # a busy channel never pauses for 30 minutes: a conversation still ends after 3 hours
    busy = [row(n, f"p{n % 9}", n * 10 * 60_000) for n in range(40)]
    assert len({r["conversation"] for r in derive.conversations(busy)}) > 1

    pairs = derive.collab_weights([], derive.conversations(said), [], [])
    assert pairs[("ann", "bob")]["chats"] == 1 and ("ann", "dee") not in pairs
    assert pairs[("dee", "eve")]["w"] == 1.0  # two people: a full tie


# --- on a real graph ------------------------------------------------------------------------
PEOPLE = """
people:
  - name: Priya Shah
    email: priya@acme.example
    role: Engineering Lead
    team: Platform
    accounts: {slack: U1}
  - name: Ken Ito
    id: eid_0000beef
    role: QA Specialist
    team: Platform
    accounts: {slack: U3}
  - name: Ana Ruiz
    email: ana@acme.example
    team: Mobile
    accounts: {slack: U4}
"""
MAPS = Maps(
    product="Acme",
    default_person=None,
    sources={"slack": {"channels": {"support": "bug-support", "leads": "bug-leads"}}},
)


@pytest.fixture
def team(tmp_path, monkeypatch, empty_graph):
    people = tmp_path / "people.yaml"
    people.write_text(PEOPLE, encoding="utf-8")
    monkeypatch.setenv("YAAN_DATA_MODE", "org")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(org, "PEOPLE_FILE", people)
    caches = (get_settings, org.roster, org.accounts, org.source_audiences, org.unknown_speakers)
    for cached in caches:
        cached.cache_clear()
    org._hints.clear()
    org.prepare(empty_graph, MAPS.product, MAPS.sources)
    ids = {n: MAPS.person("slack", u) for n, u in (("priya", "U1"), ("ken", "U3"), ("ana", "U4"))}
    ids["sam"] = MAPS.person("slack", "U2", name="Sam Lee")  # met in Slack, no team
    # Slack's sync says #support is public and #leads private (Priya and Sam read it)
    org.set_channel_access(
        empty_graph,
        [
            {"id": "bug-support", "visibility": "public"},
            {"id": "bug-leads", "visibility": "private"},
        ],
        {"bug-leads": {ids["priya"], ids["sam"]}},
    )
    yield empty_graph, ids, people
    for cached in caches:
        cached.cache_clear()
    org._hints.clear()


def say(graph: str, n: int, who: str, text: str, channel: str = "bug-support", root=None):
    a = Artifact(
        id=base.synthetic_id("msg", "slack", f"C1-18045864{n:02d}.0001"),
        kind="message",
        product_hint="Acme",
        channel_id=channel,
        ts_ms=1804586400000 + n * 60_000,
        ts_iso="2027-03-09T10:00:00Z",
        author_eid=who,
        text=text,
        raw_ref=f"slack:C1:{n}",
        meta={"channel_name": channel, "sender": who, "synthetic": False, "source": "slack"}
        | ({"thread_id": root} if root else {}),
    )
    live.ingest_artifact(a, graph, events=[])
    return a.id


def ties(graph: str) -> set[tuple[str, str]]:
    rows = db.run_text(
        "MATCH (a:Person)-[c:COLLAB]->(b:Person) WHERE a.id < b.id RETURN a.id AS a, b.id AS b "
        "LIMIT 50",
        label="test",
        graph=graph,
    ).rows
    return {(r["a"], r["b"]) for r in rows}


def test_org_mode_builds_its_collaboration_graph_from_open_conversations(team):
    g, ids, people = team
    root = say(g, 0, ids["priya"], "Export times out on big files.")
    say(g, 2, ids["ana"], "Seeing it on mobile too.", root=root)
    say(g, 3, ids["ken"], "Repro'd on staging.", root=root)
    say(g, 4, ids["priya"], "Who owns the cache layer?", channel="bug-leads")
    say(g, 5, ids["sam"], "I do, since March.", channel="bug-leads")
    say(g, 6, ids["priya"], "One more for the record.", channel="bug-leads")

    assert live.algorithms_if_due(g) is not None  # the writes made it due
    pair = lambda a, b: tuple(sorted((ids[a], ids[b])))  # noqa: E731
    # the thread ties its three writers; the private channel ties nobody (no leak)
    assert ties(g) == {pair("priya", "ana"), pair("priya", "ken"), pair("ana", "ken")}
    ranked = db.run_text(
        "MATCH (p:Person) WHERE p.pagerank_rank IS NOT NULL RETURN p.id AS id LIMIT 10",
        label="test",
        graph=g,
    ).rows
    assert {r["id"] for r in ranked} >= {ids["priya"], ids["ana"], ids["ken"]}
    works = db.run_text(
        "MATCH (t:Team)-[w:WORKS_ON]->(:Product {id: 'Acme'}) RETURN t.id AS team LIMIT 5",
        label="test",
        graph=g,
    ).rows
    assert {r["team"] for r in works} == {"team:platform"}  # 4 messages; Mobile wrote 1
    assert live.algorithms_if_due(g) is None  # done: due again after the next write and an hour


def test_people_yaml_teams_are_the_org_chart_and_a_move_is_followed(team):
    g, ids, people = team

    def teams() -> dict[str, str]:
        rows = db.run_text(
            "MATCH (p:Person)-[:MEMBER_OF]->(t:Team) RETURN p.id AS p, t.id AS t LIMIT 20",
            label="test",
            graph=g,
        ).rows
        return {r["p"]: r["t"] for r in rows}

    assert teams() == {ids["priya"]: "team:platform", ids["ken"]: "team:platform",
                       ids["ana"]: "team:mobile"}  # fmt: skip
    people.write_text(PEOPLE.replace("    team: Platform\n    accounts: {slack: U3}",
                                     "    team: Mobile\n    accounts: {slack: U3}"),
                      encoding="utf-8")  # fmt: skip
    org.roster.cache_clear()
    org.prepare(g, MAPS.product, MAPS.sources)
    assert teams()[ids["ken"]] == "team:mobile"  # moved, not in both


def test_the_demo_never_runs_the_org_job(monkeypatch):
    called = []
    monkeypatch.setattr(db, "run", lambda *a, **k: called.append(a))
    assert live.algorithms_if_due("lore_demo") is None and called == []


def test_org_mode_expertise_comes_from_what_everyone_may_read(team):
    """D131 (F10): who knows an area is shown to everyone, so a fix seen only in a private
    channel must not make its author an expert there (collaboration already works so, D127)."""
    from app.ingest import derive

    g, ids, people = team

    def said(n, who, text, channel, kind, issue, component=None):
        a = Artifact(
            id=base.synthetic_id("msg", "slack", f"C1-18045865{n:02d}.0001"),
            kind="message",
            product_hint="Acme",
            channel_id=channel,
            ts_ms=1804586400000 + n * 60_000,
            ts_iso="2027-03-09T10:00:00Z",
            author_eid=who,
            text=text,
            raw_ref=f"slack:C1:{n}",
            meta={"channel_name": channel, "sender": who, "synthetic": False, "source": "slack"},
        )
        quote = text.rstrip(".")
        event = base.structured_event(kind, a, quote, who, issue=issue, component=component)
        live.ingest_artifact(a, g, events=[event])

    said(10, ids["priya"], "Export times out on big files.", "bug-support", "issue_reported",
         "Export times out", "Export")  # fmt: skip
    said(11, ids["ken"], "Export times out is fixed.", "bug-support", "issue_resolved",
         "Export times out")  # fmt: skip
    said(12, ids["priya"], "Cache stampede on deploy.", "bug-leads", "issue_reported",
         "Cache stampede", "Cache")  # fmt: skip
    said(13, ids["sam"], "Cache stampede is fixed.", "bug-leads", "issue_resolved",
         "Cache stampede")  # fmt: skip
    derive.expertise(g)
    knows = db.run_text(
        "MATCH (p:Person)-[:KNOWS]->(a:Area) RETURN p.id AS who, a.name AS area LIMIT 50",
        label="test",
        graph=g,
    ).rows
    assert ("Export" in {r["area"] for r in knows if r["who"] == ids["ken"]})
    assert not [r for r in knows if r["area"] == "Cache" or r["who"] == ids["sam"]]
