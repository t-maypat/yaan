"""D101: events in the graph are the derived layer's system of record; the fold reads them per
product and writes only what changed. The key property: folding events one at a time, in any
order, ends in exactly the layer one full fold gives, and a second fold changes nothing."""

import random
import time

import pytest

from app import db
from app.ingest import events as event_store
from app.ingest import fold

pytestmark = pytest.mark.db

BASE = """
CREATE (w:Product:Entity {id: 'W', name: 'W', aliases: []}),
  (ann:Person:Entity {id: 'eid_a1', name: 'Ann', role: 'Engineering Lead'}),
  (bo:Person:Entity {id: 'eid_b2', name: 'Bo', role: 'Software Engineer'}),
  (cy:Person:Entity {id: 'eid_c3', name: 'Cy', role: 'Software Engineer'}),
  (dee:Customer:Entity {id: 'CUST-0001', name: 'Dee'}),
  (ch:Channel {id: 'ch-bug', name: 'bug-W', stage: 'support', product_id: 'W'}),
  (pr:PR:Artifact {id: 'pr:w1', kind: 'pr', number: 1, repo: 'x/w', external: false,
                   ts_ms: 500, ts_iso: 't500', product_id: 'W', title: 'Faster export'}),
  (pr)-[:IN_PRODUCT]->(w), (bo)-[:AUTHORED]->(pr), (ann)-[:REVIEWED]->(pr)
WITH w, ann, bo, cy, ch
UNWIND range(1, 6) AS i
CREATE (m:Message:Artifact {id: 'msg:m' + toString(i), kind: 'message', text: 'm', ts_ms: i * 1000,
                            product_id: 'W', channel_id: 'ch-bug'})
CREATE (m)-[:IN_PRODUCT]->(w), (m)-[:IN_CHANNEL]->(ch)
WITH m, ann, bo, cy, i
FOREACH (_ IN CASE WHEN i IN [1, 3, 4] THEN [1] ELSE [] END | CREATE (ann)-[:AUTHORED]->(m))
FOREACH (_ IN CASE WHEN i IN [2, 6] THEN [1] ELSE [] END | CREATE (bo)-[:AUTHORED]->(m))
FOREACH (_ IN CASE WHEN i = 5 THEN [1] ELSE [] END | CREATE (cy)-[:AUTHORED]->(m))
"""


def ev(i, type_, speaker, quote, **kw):
    e = dict.fromkeys(event_store.FIELDS)
    e |= {
        "type": type_,
        "artifact_id": f"msg:m{i}",
        "quote": quote,
        "speaker": speaker,
        "ts_ms": i * 1000,
        "ts_iso": f"t{i}",
        "product": "W",
        "channel": "bug-W",
        "decisive": True,
        "confidence": 0.9,
        "start": 0,
        "end": len(quote),
    }
    return e | kw


EVENTS = [
    ev(
        1,
        "issue_reported",
        "eid_a1",
        "Issue 'Slow export' hurts CUST-0001, @eid_b2 take it",
        issue="Slow export",
        customer="CUST-0001",
        person="eid_b2",
    ),
    ev(
        2,
        "issue_resolved",
        "eid_b2",
        "Slow export is fixed by PR#1",
        issue="Slow export",
        pr="PR#1",
    ),
    ev(
        3,
        "issue_reported",
        "eid_a1",
        "There's a 'Slow export' issue again for CUST-0001",
        issue="Slow export",
        customer="CUST-0001",
    ),
    ev(4, "pr_to_close", "eid_a1", "Let's not merge PR#1 for now", pr="PR#1"),
    ev(5, "issue_dismissed", "eid_c3", "Slow export needs no fix", issue="Slow export"),
    ev(
        6,
        "pr_reverted",
        "eid_b2",
        "Reverting PR#1, it broke exports",
        pr="PR#1",
        reason="introduced_bugs",
    ),
]


@pytest.fixture
def base(empty_graph):
    db.run_text(BASE, label="base", graph=empty_graph, readonly=False)
    return empty_graph


@pytest.fixture
def second(falkordb):
    """Another graph with the same base, for comparing two ways of getting there."""
    import uuid

    from app import schema

    name = f"yaan_test_{uuid.uuid4().hex[:12]}"
    schema.apply(name)
    db.run_text(BASE, label="base", graph=name, readonly=False)
    yield name
    w = db.client(readonly=False)
    if name in w.list_graphs():
        w.select_graph(name).delete()


DERIVED_EDGES = (
    "FROM|ASSERTED_BY|ABOUT|SUPPORTS|CONFLICTS_WITH|AFFECTS|DECIDED_BY|EVIDENCED_BY|SUPERSEDES"
    "|IN_PRODUCT|REPORTED_BY|REPORTED_IN|ASSIGNED_TO"
)


def layer(graph, timeless=True):
    """The derived layer as comparable data. `timeless` drops when Yaan learned things
    (observed_ms, and the row hash that covers it): two orders of arrival differ there only."""
    skip = {"observed_ms", "row_hash"} if timeless else set()
    nodes = {}
    for label, where in (
        ("Claim", ""),
        ("Issue", ""),
        ("Decision", "WHERE n.origin = 'extracted'"),
    ):
        q = f"MATCH (n:{label}) {where} RETURN n.id AS id, properties(n) AS p LIMIT 1000"
        for r in db.run_text(q, label="n", graph=graph).rows:
            nodes[r["id"]] = {k: v for k, v in r["p"].items() if k not in skip}
    q = (
        "MATCH (n)-[e:" + DERIVED_EDGES + "]->(m) WHERE n:Claim OR n:Issue OR n:Decision "
        "RETURN n.id AS s, type(e) AS t, m.id AS d, properties(e) AS p LIMIT 10000"
    )
    edges = {
        (r["s"], r["t"], r["d"], tuple(sorted(r["p"].items())))
        for r in db.run_text(q, label="e", graph=graph).rows
    }
    q = "MATCH (p:PR)-[:FIXES]->(i:Issue) RETURN p.id AS s, i.id AS d LIMIT 100"
    edges |= {(r["s"], "FIXES", r["d"], ()) for r in db.run_text(q, label="f", graph=graph).rows}
    q = "MATCH (p:PR) RETURN p.id AS id, p.current_action AS a, p.action_reason AS r LIMIT 100"
    actions = {r["id"]: (r["a"], r["r"]) for r in db.run_text(q, label="a", graph=graph).rows}
    return nodes, edges, actions


# --- events ----------------------------------------------------------------------------------
def test_an_event_keeps_when_yaan_first_learned_it(base):
    event_store.load(EVENTS[:2], base, observed_ms=100)
    event_store.load(EVENTS[:3], base, observed_ms=200)  # the first two again, and one new
    got = {e["artifact_id"]: e["observed_ms"] for e in event_store.read("W", base)}
    assert got == {"msg:m1": 100, "msg:m2": 100, "msg:m3": 200}
    assert event_store.read("W", base)[0]["product"] == "W"


def test_a_retracted_event_is_closed_not_deleted_and_replayable(base):
    event_store.load(EVENTS[:3], base, observed_ms=100)
    assert event_store.retract("msg:m2", [], base, now_ms=150) == 1
    assert {e["artifact_id"] for e in event_store.read("W", base)} == {"msg:m1", "msg:m3"}
    before = {e["artifact_id"] for e in event_store.read("W", base, known_at_ms=120)}
    assert before == {"msg:m1", "msg:m2", "msg:m3"}  # what Yaan knew before the retraction
    assert event_store.read("W", base, known_at_ms=50) == []  # before it learned anything
    event_store.load([EVENTS[1]], base, observed_ms=300)  # the newer version says it again
    assert len(event_store.read("W", base)) == 3


# --- the lock --------------------------------------------------------------------------------
def test_one_writer_at_a_time_and_a_dead_holder_stops_counting(base):
    with db.writer_lock(base, ttl_ms=60_000):
        with pytest.raises(db.LockBusy):
            with db.writer_lock(base, wait_s=0.3):
                pass
    with db.writer_lock(base, wait_s=0.3):  # given back
        pass
    # a holder that died: it took the lock and stopped renewing it (no process left to)
    dead = {"name": "derived", "holder": "dead-1", "now_ms": int(time.time() * 1000), "ttl_ms": 1}
    assert db.run("acquire_lock", dead, graph=base, readonly=False).rows
    with db.writer_lock(base, wait_s=2):  # it doesn't block the next writer for long
        pass


def test_a_long_write_keeps_its_lease_and_a_lost_one_stops_writing(base):
    """D110: the lease is renewed while the holder works; if it is lost anyway (taken by
    another writer after a renewal failed), the holder's next write raises LockLost."""
    with db.writer_lock(base, ttl_ms=600):
        time.sleep(1.5)  # more than twice the lease: renewed meanwhile
        with pytest.raises(db.LockBusy):
            with db.writer_lock(base, wait_s=0.3):
                pass
    with db.writer_lock(base, ttl_ms=600):
        db.run_text(
            "MATCH (l:Lock {id: 'derived'}) SET l.holder = 'someone-else'",
            label="steal",
            graph=base,
            readonly=False,
        )
        time.sleep(0.5)  # the next renewal finds the lock gone
        with pytest.raises(db.LockLost):
            db.run_text("CREATE (:Probe {id: 'x'})", label="w", graph=base, readonly=False)
    db.run_text("CREATE (:Probe {id: 'y'})", label="w", graph=base, readonly=False)  # outside: ok


# --- the fold --------------------------------------------------------------------------------
def test_one_event_at_a_time_in_any_order_ends_where_one_full_fold_does(base, second):
    event_store.load(EVENTS, base, observed_ms=1)
    fold.fold_product("W", base)
    full = layer(base)
    assert any(
        c["value"] == "reopened" for c in full[0].values() if c.get("topic", "").endswith("/status")
    )

    order = EVENTS[:]
    random.Random(7).shuffle(order)
    for n, e in enumerate(order, 1):
        event_store.load([e], second, observed_ms=n)
        fold.fold_product("W", second)
    assert layer(second) == full


def test_a_second_fold_changes_nothing(base):
    event_store.load(EVENTS, base, observed_ms=1)
    first = fold.fold_product("W", base)["write"]
    assert first["changed"] == first["nodes"] > 0
    again = fold.fold_product("W", base)["write"]
    assert (again["changed"], again["stale"]) == (0, 0)


def test_a_retraction_ends_where_never_having_the_event_does(base, second):
    event_store.load(EVENTS, base, observed_ms=1)
    fold.fold_product("W", base)
    event_store.retract("msg:m3", [], base, now_ms=2)  # the re-report was edited away
    report = fold.fold_product("W", base)["write"]
    assert report["stale"] > 0  # the reopened state and what hung off it went
    event_store.load([e for e in EVENTS if e["artifact_id"] != "msg:m3"], second, observed_ms=1)
    fold.fold_product("W", second)
    assert layer(base) == layer(second)


def test_a_fold_reuses_the_last_passs_matches_and_ends_where_a_full_fold_does(base, second):
    """D129: each issue event's match is stored on it; the next fold matches only what is new
    or could have changed, and a retraction still ends where a full fold does."""
    event_store.load(EVENTS[:3], base, observed_ms=1)
    assert fold.fold_product("W", base)["matched"] == 1  # the fix (reports make their issue)
    assert fold.fold_product("W", base)["matched"] == 0  # nothing new: nothing matched again
    stored = {e["id"]: e["res_how"] for e in event_store.read("W", base)}
    assert sorted(h for h in stored.values() if h) == ["exact", "reported", "reported"]
    event_store.load(EVENTS[3:], base, observed_ms=2)
    later = fold.fold_product("W", base)["matched"]
    assert later == sum(1 for e in EVENTS[3:] if e["type"] not in ("issue_reported",) and
                        e["type"].startswith("issue_") and e.get("issue"))  # fmt: skip
    event_store.retract("msg:m1", [], base, now_ms=3)  # the report that made the issue
    fold.fold_product("W", base)
    assert not [e for e in event_store.retracted_resolved("W", base)]  # cleared once folded
    event_store.load([e for e in EVENTS if e["artifact_id"] != "msg:m1"], second, observed_ms=1)
    fold.fold_product("W", second)
    assert layer(base) == layer(second)


def test_forgetting_a_report_refolds_without_matching_everything_again(base, second):
    from app import forget

    event_store.load(EVENTS, base, observed_ms=1)
    fold.fold_product("W", base)
    before = fold.fold_product("W", base)["matched"]
    forget.forget(["msg:m1"], base, now_ms=5)  # its words go; the fold still knew what it made
    assert before == 0 and not [e for e in event_store.read("W", base) if not e.get("res_v")
                                and e["type"].startswith("issue_") and e.get("issue")]  # fmt: skip
    event_store.load([e for e in EVENTS if e["artifact_id"] != "msg:m1"], second, observed_ms=1)
    fold.fold_product("W", second)
    mine, theirs = layer(base), layer(second)
    assert mine[0].keys() == theirs[0].keys() and mine[2] == theirs[2]


def test_an_interrupted_write_is_finished_by_the_next_fold(base, monkeypatch):
    event_store.load(EVENTS, base, observed_ms=1)
    real = db.run

    def crash(name, *a, **k):  # dies after the nodes and edges are written, before the hashes
        if name.startswith("set_") and name.endswith("_hash"):
            raise RuntimeError("process killed")
        return real(name, *a, **k)

    monkeypatch.setattr(db, "run", crash)
    with pytest.raises(RuntimeError):
        fold.fold_product("W", base)
    monkeypatch.setattr(db, "run", real)
    redo = fold.fold_product("W", base)["write"]
    assert redo["changed"] == redo["nodes"]  # no hash was stamped: everything is redone
    assert fold.fold_product("W", base)["write"]["changed"] == 0


def test_the_belief_at_a_moment_is_the_fold_of_what_was_known_then(base, second):
    issue = "iss:W:" + fold_issue_hash()
    for n, e in enumerate(EVENTS, 1):  # learned one by one, at observed time n * 10
        event_store.load([e], base, observed_ms=n * 10)
        fold.fold_product("W", base)
    then = fold.belief(issue, "W", base, known_at_ms=25)  # after the 2nd event, before the 3rd
    assert [d["value"] for d in then["chains"]["status"]] == ["reported", "resolved"]
    now = fold.belief(issue, "W", base, known_at_ms=None)
    assert [d["value"] for d in now["chains"]["status"]][-1] == "reopened"  # Cy may not dismiss

    event_store.load(EVENTS[:2], second, observed_ms=1)  # a graph that only ever knew those two
    fold.fold_product("W", second)
    written = sorted(
        (
            d
            for d in layer(second)[0].values()
            if d.get("topic") == f"{issue}/status" and "status" in d
        ),
        key=lambda d: d["valid_from_ms"],
    )
    assert [d["value"] for d in written] == [d["value"] for d in then["chains"]["status"]]
    assert [d["status"] for d in written] == [d["status"] for d in then["chains"]["status"]]


def fold_issue_hash() -> str:
    from app.ingest.resolve import issue_id

    return issue_id("W", "Slow export").split(":")[-1]


def test_the_api_serves_the_belief_at_a_moment(base, monkeypatch):
    from fastapi.testclient import TestClient

    from app import main

    for n, e in enumerate(EVENTS, 1):
        event_store.load([e], base, observed_ms=n * 10)
    fold.fold_product("W", base)
    monkeypatch.setattr(main, "graph", lambda: base)
    issue = "iss:W:" + fold_issue_hash()
    client = TestClient(main.app)
    then = client.get(f"/decision/{issue}/belief", params={"known_at_ms": 15}).json()
    assert [d["value"] for d in then["chains"]["status"]] == ["reported"]
    assert client.get("/decision/msg:m1/belief").status_code == 404


def test_an_explicit_authority_lets_a_team_lead_by_another_title_decide(base):
    # Cy is a "Software Engineer": his dismissal is refused. Given `authority: lead`
    # (people.yaml, D102), the same claim applies.
    event_store.load(EVENTS, base, observed_ms=1)
    fold.fold_product("W", base)
    issue = "iss:W:" + fold_issue_hash()
    assert fold.belief(issue, "W", base, None)["chains"]["status"][-1]["value"] == "reopened"
    db.run_text(
        "MATCH (p:Person {id: 'eid_c3'}) SET p.authority = 'lead'",
        label="a",
        graph=base,
        readonly=False,
    )
    fold.fold_product("W", base)
    assert fold.belief(issue, "W", base, None)["chains"]["status"][-1]["value"] == "dismissed"


# --- live-vs-batch parity (D102) -------------------------------------------------------------
def test_parity_reads_each_message_in_the_window_the_live_path_would(monkeypatch):
    from app.eval import parity
    from app.ingest.normalize import Artifact

    def msg(i, channel, day):
        return Artifact(
            id=f"msg:{i}",
            kind="message",
            product_hint="W",
            channel_id=channel,
            ts_ms=day * 86_400_000 + i,
            ts_iso="t",
            author_eid="eid_a1",
            text=f"m{i}",
            raw_ref="r",
            meta={"channel_name": f"{channel}-W"},
        )

    arts = [msg(i, "bug", 10) for i in range(10)] + [msg(20, "planning", 10), msg(21, "bug", 30)]
    monkeypatch.setattr(parity, "read_jsonl", lambda path: arts)
    got = {a.id: [m.id for m in u.messages] for a, u in parity.windows("W")}
    assert "msg:20" not in got  # a planning channel isn't extracted
    assert got["msg:9"] == [f"msg:{i}" for i in range(1, 10)]  # 8 earlier messages, then it
    assert got["msg:21"] == ["msg:21"]  # 20 days later: none of the earlier ones in its week


def test_parity_compares_events_and_folded_outcomes(base):
    from app.eval import parity

    same = parity.compare("W", EVENTS, EVENTS, base)
    assert (same["precision"], same["recall"], same["final_state_agreement"]) == (1.0, 1.0, 1.0)
    fewer = parity.compare("W", EVENTS[:2], EVENTS, base)
    assert fewer["precision"] == 1.0 and fewer["recall"] < 1.0
    assert fewer["final_state_agreement"] < 1.0  # without the re-report the issue stays resolved


MORE_MESSAGES = """
MATCH (w:Product {id: 'W'}), (ch:Channel {id: 'ch-bug'}), (bo:Person {id: 'eid_b2'})
UNWIND range(7, 60) AS i
CREATE (m:Message:Artifact {id: 'msg:m' + toString(i), kind: 'message', text: 'm',
        ts_ms: i * 1000, product_id: 'W', channel_id: 'ch-bug'})
CREATE (m)-[:IN_PRODUCT]->(w), (m)-[:IN_CHANNEL]->(ch), (bo)-[:AUTHORED]->(m)
"""


def history(seed: int) -> list[dict]:
    """Reports of six issues, then fixes, reopenings, dismissals and PR intents, by people of
    different authority, in random order of arrival (the ts stays each message's own)."""
    rnd = random.Random(seed)
    titles = ["Slow export", "Login loop", "Chart blank", "Sync stalls", "Token expiry",
              "Upload fails"]  # fmt: skip
    pool, i = [], 7
    for t in titles:
        pool.append(ev(i, "issue_reported", rnd.choice(["eid_a1", "eid_b2"]),
                       f"Issue '{t}' for CUST-0001", issue=t, customer="CUST-0001",
                       person=rnd.choice(["eid_b2", "eid_c3"])))  # fmt: skip
        i += 1
    while i < 60:
        kind = rnd.choice(["issue_resolved", "issue_reopened", "issue_dismissed", "pr_to_merge",
                           "pr_on_hold", "pr_reverted"])  # fmt: skip
        speaker = rnd.choice(["eid_a1", "eid_b2", "eid_c3"])
        if kind.startswith("pr_"):
            pool.append(ev(i, kind, speaker, f"PR#1 {kind}", pr="PR#1"))
        else:
            t = rnd.choice(titles)
            pool.append(ev(i, kind, speaker, f"{t} {kind}", issue=t))
        i += 1
    return pool


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_writing_only_the_changed_topics_ends_where_a_full_write_does(base, second, seed):
    """22.7 (D142): a fold that remembers its last write compares and writes only the topics
    whose inputs moved. Over random arrivals, late events and retractions, its layer equals
    a full write's after every step, and most steps compare far fewer nodes."""
    for g in (base, second):
        db.run_text(MORE_MESSAGES, label="more", graph=g, readonly=False)
    rnd = random.Random(seed)
    pool, live, partial, step = history(seed), [], 0, 0
    rnd.shuffle(pool)
    while pool or step < 40:
        step += 1
        if pool and (not live or rnd.random() < 0.75):
            batch = [pool.pop() for _ in range(min(len(pool), rnd.randint(1, 3)))]
            for g in (base, second):
                event_store.load(batch, g, observed_ms=step)
            live += [e["artifact_id"] for e in batch]
        elif live:
            gone = live.pop(rnd.randrange(len(live)))
            for g in (base, second):
                event_store.retract(gone, [], g, now_ms=step)
        else:
            break
        mine = fold.fold_product("W", base)["write"]  # remembers its last write
        fold.forget_memo(second)
        full = fold.fold_product("W", second)["write"]  # always in full
        assert not full["partial"]
        assert layer(base) == layer(second), f"step {step}"
        if mine["partial"]:
            partial += 1
            assert mine["compared"] <= full["compared"]
    assert partial >= step // 2  # the memory was used, not bypassed


def test_a_layer_changed_behind_the_folds_back_is_written_in_full(base):
    """The count guard: another path deleted derived nodes (a migration, a reset) without
    touching events; the memo no longer matches the graph, so the next fold writes in full."""
    event_store.load(EVENTS, base, observed_ms=1)
    fold.fold_product("W", base)
    assert fold.fold_product("W", base)["write"]["partial"]
    db.run_text("MATCH (c:Claim) WITH c LIMIT 1 DETACH DELETE c", label="x", graph=base,
                readonly=False)  # fmt: skip
    again = fold.fold_product("W", base)["write"]
    assert not again["partial"] and again["changed"] >= 1
    before = layer(base)
    fold.forget_memo(base)
    assert layer(base) == before and fold.fold_product("W", base)["write"]["changed"] == 0
