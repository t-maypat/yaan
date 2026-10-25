"""Forgetting (F2, D125): `forget` removes every copy of an item's words and keeps a tombstone;
`erase_person` does that for everything a person wrote and takes them out of the org. The
guard below fails when a write query stores words on a label `forget` doesn't cover. The rest
run the live path on a real graph (events are given, so no LLM)."""

import json
import re
import time
from pathlib import Path

import pytest
from test_org import MAPS, org_mode  # noqa: F401  (the fixture)

from app import auth, db, forget, llm, org
from app.connectors import base
from app.ingest import events as event_store
from app.ingest.normalize import Artifact
from app.sentinel import engine, live

QUERIES = Path(__file__).resolve().parents[1] / "queries"
# property names that can hold someone's words (or a name, a title, a stored item)
CONTENT = {
    "text", "title", "quote", "draft", "statement", "summary", "description", "payload",
    "issue", "reason", "aliases", "subject_title", "name", "label", "error", "events",
    "authority_reason", "actor_name", "target_name", "component", "deliveries", "url",
}  # fmt: skip


def written_content() -> dict[tuple[str, str], set[str]]:
    """(label, property) -> the write queries that set it, for content-like properties."""
    out: dict[tuple[str, str], set[str]] = {}
    for f in sorted(QUERIES.glob("*.cypher")):
        src = f.read_text(encoding="utf-8")
        if not re.search(r"\bSET\b", src):
            continue
        labels: dict[str, str] = {}
        for m in re.finditer(r"\((\w+):(\w+)", src):
            labels.setdefault(m.group(1), m.group(2))
        for m in re.finditer(r"\b(\w+)\.(\w+)\s*=(?!=)", src):
            var, prop = m.groups()
            if prop in CONTENT and var in labels:
                out.setdefault((labels[var], prop), set()).add(f.name)
    return out


def test_every_copy_of_someones_words_is_covered_by_forget():
    """A new query that stores text on a label forget doesn't know would survive erasure: it
    must be added to forget.COVERS (and handled) or to EXEMPT (with why)."""
    uncovered = {
        f"{label}.{prop} ({', '.join(sorted(files))})"
        for (label, prop), files in written_content().items()
        if label not in forget.EXEMPT
        and not {prop, "*"} & set(forget.COVERS.get(label, {}))
    }
    assert not uncovered, f"words forget can't remove: {sorted(uncovered)}"
    assert ("Artifact", "text") in written_content()  # the scan itself works


def test_org_mode_caches_expire_and_the_demo_keeps_its_replay(monkeypatch):
    from app.config import get_settings

    assert llm.cache_expiry() is None  # the demo: the eval replays from the cache
    monkeypatch.setenv("YAAN_DATA_MODE", "org")
    get_settings.cache_clear()
    try:
        assert llm.cache_expiry() == 30 * 86_400
    finally:
        monkeypatch.delenv("YAAN_DATA_MODE")
        get_settings.cache_clear()


# --- on a real graph ------------------------------------------------------------------------
def message(n: int, who: str, text: str) -> Artifact:
    return Artifact(
        id=base.synthetic_id("msg", "slack", f"C1-18045864{n:02d}.0001"),
        kind="message",
        product_hint="Acme",
        channel_id="bug-support",
        ts_ms=1804586400000 + n * 3_600_000,
        ts_iso=f"2027-03-09T{10 + n:02d}:00:00Z",
        author_eid=who,
        text=text,
        raw_ref=f"slack:C1:18045864{n:02d}.0001",
        meta={"channel_name": "bug-support", "sender": who, "synthetic": False, "source": "slack"},
    )


def say(graph: str, a: Artifact, kind: str, quote: str) -> dict:
    e = base.structured_event(kind, a, quote, a.author_eid, issue="CSV export times out")
    return live.ingest_artifact(a, graph, events=[e])


@pytest.fixture
def story(org_mode, empty_graph):  # noqa: F811
    """Sam (met in Slack) reports a bug; Priya (a lead in people.yaml) fixes it and thanks
    @Sam; Sam says it's back: a regression alert on that last message."""
    org.prepare(empty_graph, MAPS.product, MAPS.sources)
    priya = org.eid_for("priya@acme.example")
    sam = MAPS.person("slack", "U2", name="Sam Lee")
    report = message(0, sam, "The CSV export times out again since this morning's release.")
    say(empty_graph, report, "issue_reported", "The CSV export times out again")
    fix = message(1, priya, f"Fixed the CSV export timeout in today's patch, thanks @{sam}.")
    say(empty_graph, fix, "issue_resolved", "Fixed the CSV export timeout")
    back = message(2, sam, "The CSV export times out again after the patch.")
    out = say(empty_graph, back, "issue_reopened", "The CSV export times out again")
    assert [a["kind"] for a in out["alerts"]] == ["regression"]
    return {"graph": empty_graph, "sam": sam, "priya": priya, "msgs": (report, fix, back)}


def rows(graph: str, cypher: str, **params) -> list[dict]:
    return db.run_text(cypher, params, label="test", graph=graph).rows


def test_forgetting_a_message_removes_its_words_and_rebuilds_without_it(story):
    g, (_, _, back) = story["graph"], story["msgs"]
    before = int(time.time() * 1000)  # after the message was learned, before it is forgotten
    receipt = forget.forget([back.id], g, reason="deleted in slack", now_ms=before + 1_000)
    assert receipt["artifacts"] == 1 and receipt["events_closed"] == 1
    assert receipt["alerts_scrubbed"] == 1 and "Acme" in receipt["products_refolded"]
    assert json.dumps(receipt).find("times out") == -1  # a receipt holds counts, never words

    assert live.present_ids(g, [back.id]) == set()
    assert rows(g, "MATCH (c:Chunk {artifact_id: $id}) RETURN c.id LIMIT 5", id=back.id) == []
    (tomb,) = rows(g, "MATCH (t:Tombstone {id: $id}) RETURN t.kind AS kind, t.reason AS why "
                      "LIMIT 1", id=back.id)  # fmt: skip
    assert tomb == {"kind": "message", "why": "deleted in slack"}
    (event,) = rows(g, "MATCH (e:Event {artifact_id: $id}) RETURN properties(e) AS e LIMIT 1",
                    id=back.id)  # fmt: skip
    assert event["e"]["quote"] == "" and event["e"]["retracted_ms"] is not None

    # the fold no longer sees the reopening: the issue is resolved again
    (issue,) = rows(g, "MATCH (i:Issue) RETURN i.current_status AS status LIMIT 1")
    assert issue["status"] == "resolved"
    # the regression alert lost its words and was withdrawn
    (alert,) = engine.list_alerts(graph=g, artifact_id=back.id)
    assert alert["quote"] is None and alert["status"] == "withdrawn"
    # the log still knows the event was believed then, without its words
    then = event_store.read("Acme", g, known_at_ms=before)
    assert any(e["artifact_id"] == back.id and e["quote"] == "" for e in then)

    # and nothing brings it back
    with pytest.raises(live.LiveError, match="forgotten"):
        say(g, back, "issue_reopened", "The CSV export times out again")
    assert forget.blocked([back], g) == {back.id: forget.blocked([back], g)[back.id]}


def test_erasing_a_person_takes_them_and_their_words_out(story):
    g, sam, priya = story["graph"], story["sam"], story["priya"]
    report, fix, back = story["msgs"]
    token = auth.issue(sam)
    with pytest.raises(org.OrgError, match="people.yaml"):
        forget.erase_person(priya, g)  # listed: the roster would bring her back

    receipt = forget.erase_person(sam, g)
    assert receipt["artifacts"] == 2 and receipt["mentions_replaced"] == 1
    assert receipt["tokens_revoked"] == 1
    assert live.present_ids(g, [report.id, fix.id, back.id]) == {fix.id}
    assert rows(g, "MATCH (p:Person {id: $id}) RETURN p.id LIMIT 1", id=sam) == []
    assert sam not in json.loads(org.directory_file(g).read_text(encoding="utf-8"))
    with pytest.raises(auth.AuthError):
        auth.check_token(token)

    # Priya's thanks no longer names Sam, and her quote still checks against her words
    (kept,) = rows(g, "MATCH (a:Artifact {id: $id}) RETURN a.text AS text LIMIT 1", id=fix.id)
    assert f"@{sam}" not in kept["text"] and forget.PLACEHOLDER in kept["text"]
    (e,) = rows(g, "MATCH (e:Event {artifact_id: $id}) RETURN e.quote AS q LIMIT 1", id=fix.id)
    assert llm.quote_in(e["q"], kept["text"])

    # a new message from Sam's account is refused, and Sam isn't registered again
    org._hints.clear()
    again = message(5, MAPS.person("slack", "U2", name="Sam Lee"), "Is the export fixed?")
    assert forget.blocked([again], g) == {again.id: "its author was erased from Yaan"}
    with pytest.raises(live.LiveError, match="erased"):
        say(g, again, "issue_reported", "Is the export fixed?")
    assert org.ensure_people(g, [sam]) == []
    assert rows(g, "MATCH (p:Person {id: $id}) RETURN p.id LIMIT 1", id=sam) == []


def test_the_commands_are_dry_runs_until_told_and_refuse_a_listed_person(story, capsys):
    from app import cli

    g, (_, fix, _) = story["graph"], story["msgs"]
    assert cli.main(["forget", fix.id, "--graph", g]) == 0
    assert "dry run: 1 of 1" in capsys.readouterr().err
    assert live.present_ids(g, [fix.id]) == {fix.id}  # nothing happened
    assert cli.main(["erase", "Priya Shah", "--graph", g]) == 1
    assert "remove the entry first" in capsys.readouterr().err
    assert cli.main(["erase", "Sam Lee", "--graph", g]) == 0
    assert "wrote 2 items and is @-mentioned in 1" in capsys.readouterr().err
    assert cli.main(["forget", fix.id, "--graph", g, "--yes"]) == 0
    assert json.loads(capsys.readouterr().out)["artifacts"] == 1


# --- threads, edits and deletions from a source (F1, F7, D126) ------------------------------
def in_thread(a: Artifact, root: Artifact) -> Artifact:
    return a.model_copy(update={"meta": {**a.meta, "thread_id": root.id}})


def test_a_reply_is_read_with_its_thread_and_a_message_with_its_channel(story):
    g, sam, priya = story["graph"], story["sam"], story["priya"]
    report, fix, back = story["msgs"]
    first = in_thread(message(3, priya, "Looking at it now."), report)
    live.ingest_artifact(first, g, events=[])
    stored = rows(g, "MATCH (m:Message {id: $id}) RETURN m.thread_id AS t LIMIT 1", id=first.id)
    assert stored == [{"t": report.id}]

    second = in_thread(message(4, sam, "Thanks, it's the 4.2 build."), report)
    assert [m.id for m in live.conversation(second, g)] == [report.id, first.id]
    # ten days on: the root is read however old; the replies of the last week only
    later = in_thread(message(240, sam, "Still slow."), report)
    assert [m.id for m in live.conversation(later, g)] == [report.id]
    # a top-level message reads the channel's top-level messages, not the thread
    top = message(5, sam, "Another thing: the PDF export too.")
    assert [m.id for m in live.conversation(top, g)] == [report.id, fix.id, back.id]
    # a reply in the same batch, not yet in the graph, is part of its thread
    assert [m.id for m in live.conversation(later, g, earlier=[second])] == [report.id, second.id]


def test_a_round_applies_an_edit_and_a_deletion_from_the_source(story):
    from app.connectors import runner
    from app.connectors.base import Item

    g, priya = story["graph"], story["priya"]
    report, fix, back = story["msgs"]
    unsaid = fix.model_copy(update={"text": "Looking into the CSV export timeout."})

    class Source:
        name, maps = "slack", MAPS

        def fetch(self, since_ms):
            return [Item(unsaid, updated_ms=int(time.time() * 1000), events=[]),
                    Item(back, updated_ms=int(time.time() * 1000), deleted=True)]  # fmt: skip

    report_ = runner.sync([Source()], g)
    assert report_["forgotten"] == [{"source": "slack", "items": 1}]
    assert [r["id"] for r in report_["ingested"]] == [fix.id]
    assert live.present_ids(g, [back.id]) == set()
    # the edited message no longer says it's fixed: its event is closed, the issue reported
    live_events = rows(g, "MATCH (e:Event {artifact_id: $id}) WHERE e.retracted_ms IS NULL "
                          "RETURN e.id LIMIT 5", id=fix.id)  # fmt: skip
    assert live_events == []
    (issue,) = rows(g, "MATCH (i:Issue) RETURN i.current_status AS status LIMIT 1")
    assert issue["status"] == "reported"
    assert priya  # the lead's message is still there, with its new words
    (kept,) = rows(g, "MATCH (a:Artifact {id: $id}) RETURN a.text AS text LIMIT 1", id=fix.id)
    assert kept["text"] == unsaid.text
