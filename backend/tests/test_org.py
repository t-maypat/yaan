"""Org mode (D097): Yaan on a team's own tools. The switch, the people directory, ids without the
demo label, the guards that keep HERB out of an org graph, and one end-to-end live ingest on a
real graph (marked db + model; events are given, so no LLM)."""

import json

import pytest

from app import db, org
from app.config import get_settings
from app.connectors import base, runner
from app.connectors.base import Item, Maps
from app.ingest.normalize import Artifact
from app.sentinel import live

PEOPLE = """
people:
  - name: Priya Shah
    email: priya@acme.example
    role: Engineering Lead
    accounts: {slack: U1, github: priya-s, jira: [acc-1, acc-9]}
  - name: Ken Ito
    id: eid_0000beef
    role: QA Specialist
"""
MAPS = Maps(
    product="Acme",
    default_person=None,
    sources={
        "slack": {"channels": {"support": "bug-support"}, "people": {"U7": "eid_0000beef"}},
        "jira": {"channel": "bug-tickets", "visibility": "everyone"},
        "notion": {"channel": "planning-specs", "visibility": "members", "members": ["Ken Ito"]},
    },
)


@pytest.fixture
def org_mode(tmp_path, monkeypatch):
    """LORE_DATA_MODE=org with a people.yaml and a data dir of the test's own."""
    people = tmp_path / "people.yaml"
    people.write_text(PEOPLE, encoding="utf-8")
    monkeypatch.setenv("LORE_DATA_MODE", "org")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(org, "PEOPLE_FILE", people)
    for cached in (get_settings, org.roster, org.accounts, org.source_audiences):
        cached.cache_clear()
    org._hints.clear()
    yield tmp_path
    for cached in (get_settings, org.roster, org.accounts, org.source_audiences):
        cached.cache_clear()
    org._hints.clear()


# --- the switch ------------------------------------------------------------------------------
def test_demo_is_the_default_and_keeps_its_shapes():
    assert not org.active()
    assert base.synthetic_id("msg", "slack", "C1-1") == "msg:synthetic-slack-C1-1"
    assert base.tag() == "[synthetic demo] "
    assert get_settings().api_graph() == get_settings().lore_api_graph


def test_org_mode_drops_the_demo_label_and_serves_the_org_graph(org_mode):
    assert org.active()
    assert base.synthetic_id("msg", "slack", "C1/170.5") == "msg:slack-C1-170.5"
    assert base.id_prefix("pr", "github") == "pr:github-"
    assert base.tag() == ""
    assert get_settings().api_graph() == "yaan_org"


def test_from_connector_knows_each_modes_id_shape(org_mode):
    assert runner.from_connector({"artifact_id": "msg:slack-C1-1"})
    assert not runner.from_connector({"artifact_id": "msg:synthetic-slack-C1-1"})
    assert not runner.from_connector({"artifact_id": "msg:20270309-0-abc"})


# --- people ----------------------------------------------------------------------------------
def test_people_yaml_gives_ids_roles_and_accounts(org_mode):
    priya = org.eid_for("priya@acme.example")
    roster = org.roster()
    assert roster[priya]["role"] == "Engineering Lead"
    assert roster["eid_0000beef"]["name"] == "Ken Ito"
    assert org.accounts()[("jira", "acc-9")] == priya


def test_an_account_maps_to_its_person_never_to_a_stand_in(org_mode):
    priya = org.eid_for("priya@acme.example")
    assert MAPS.person("slack", "U1") == priya  # people.yaml
    assert MAPS.person("slack", "U7") == "eid_0000beef"  # connectors.yaml's people map
    # a persona label means nothing outside the demo
    assert MAPS.person("slack", "U1", "[synthetic demo · as Ann (eid_0123abcd)] hi") == priya
    sam = MAPS.person("slack", "U2", name="Sam Lee")  # met for the first time
    assert sam == org.eid_for("slack:U2") and org.EID.match(sam)
    assert org._hints[sam]["name"] == "Sam Lee"
    nobody = MAPS.person("notion", None)
    assert org._hints[nobody]["name"] == "Unknown Notion user"


@pytest.mark.parametrize(
    "text, error",
    [
        ("people: [{role: QA Specialist}]", "needs a name"),
        ("people: [{name: A, id: ann}]", "must look like"),
        ("people: [{name: A, accounts: {teams: X}}]", "unknown tool"),
        ("people: [{name: A, email: a@x}, {name: B, email: a@x}]", "share the id"),
    ],
)
def test_a_bad_people_yaml_says_what_to_fix(tmp_path, text, error):
    path = tmp_path / "people.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(org.OrgError, match=error):
        org.parse_roster(path)


def test_one_account_can_belong_to_one_person(org_mode):
    org.PEOPLE_FILE.write_text(
        "people: [{name: A, accounts: {slack: U1}}, {name: B, accounts: {slack: U1}}]",
        encoding="utf-8",
    )
    org.roster.cache_clear()
    with pytest.raises(org.OrgError, match="listed twice"):
        org.accounts()


def test_an_unlisted_account_never_inherits_a_listed_persons_id(org_mode):
    """Ids are 32-bit hashes: a stranger whose account hashes to a listed person's id would
    otherwise take over their name and role, and so their authority (D098)."""
    taken = org.eid_for("slack:U99")
    org.PEOPLE_FILE.write_text(
        f"people: [{{name: Lead, id: {taken}, role: Engineering Lead}}]", encoding="utf-8"
    )
    org.roster.cache_clear()
    org.accounts.cache_clear()
    with pytest.raises(org.OrgError, match="hashes to .* which people.yaml gives to Lead"):
        org.person_id("slack", "U99")


def test_the_people_an_artifact_involves(org_mode):
    a = Artifact(
        id="pr:github-acme-1",
        kind="pr",
        product_hint="Acme",
        ts_ms=1,
        ts_iso="2027-03-09T10:00:00Z",
        raw_ref="test:pr",
        author_eid="eid_000000a1",
        text="Fixes the export; thanks @eid_000000b2.",
        participants=["eid_000000c3"],
        meta={"reviews": [{"reviewer_eid": "eid_000000d4"}]},
    )
    assert org.people_in(a) == ["eid_000000a1", "eid_000000c3", "eid_000000d4", "eid_000000b2"]


# --- channels --------------------------------------------------------------------------------
def test_declared_channels_need_a_kind_prefix_or_a_declared_kind():
    assert org.declared_channels(MAPS.sources) == {
        "bug-support": None,
        "bug-tickets": None,
        "planning-specs": None,
    }
    assert org.channel_row("develop-backend", "Acme")["stage"] == "dev"
    for bad in ("support", "bug", "eng-bugs"):
        with pytest.raises(org.OrgError, match="must start with bug-, develop- or planning-"):
            org.channel_row(bad, "Acme")
    # D116: a channel named freely says what it holds; `mixed` is bugs and PR talk together
    sources = {
        "slack": {"channels": {"backend": {"channel": "backend", "kind": "mixed"}}},
        "jira": {"channel": "tickets", "kind": "bug", "visibility": "everyone"},
    }
    assert org.declared_channels(sources) == {"backend": "mixed", "tickets": "bug"}
    assert org.channel_map(sources["slack"]) == {"backend": "backend"}
    row = org.channel_row("backend", "Acme", "mixed")
    assert (row["stage"], row["kind"]) == ("mixed", "mixed")
    with pytest.raises(org.OrgError, match="use bug, develop, planning or mixed"):
        org.channel_row("backend", "Acme", "chatter")


# --- guards: nothing from HERB reaches an org graph ------------------------------------------
def test_org_writes_go_to_the_org_graph_never_a_herb_graph(org_mode):
    assert live.demo_graph(None) == "yaan_org"
    for herb in ("lore_herb", "lore_demo"):
        with pytest.raises(live.LiveError, match="never the HERB graph"):
            live.demo_graph(herb)
    with pytest.raises(live.LiveError, match="demo reset is off"):
        live.reset_demo()


def test_an_api_message_keeps_its_own_id_in_org_mode(org_mode):
    a = live.artifact(
        {
            "id": "msg:helpdesk-42",
            "product": "Acme",
            "channel_id": "bug-support",
            "channel_name": "bug-support",
            "author_eid": "eid_0000beef",
            "ts_iso": "2027-03-09T10:00:00Z",
            "text": "Export times out again.",
        }
    )
    assert (a.id, a.raw_ref, a.meta["synthetic"]) == (
        "msg:helpdesk-42",
        "api:msg:helpdesk-42",
        False,
    )


def test_maps_need_no_stand_in_person_in_org_mode(org_mode, tmp_path, monkeypatch):
    conf = tmp_path / "connectors.yaml"
    conf.write_text("product: Acme\nslack: {channels: {support: bug-support}}\n", encoding="utf-8")
    monkeypatch.setattr(base, "CONFIG", conf)
    base.maps.cache_clear()
    try:
        assert base.maps().default_person is None
    finally:
        base.maps.cache_clear()


def test_an_org_sync_prepares_the_graph_and_takes_unlabeled_items(org_mode, monkeypatch):
    prepared, ingested = [], []
    monkeypatch.setattr(runner, "prepare", lambda graph, connectors=None: prepared.append(graph))
    monkeypatch.setattr(runner, "deliver", lambda connectors, graph: ([], []))
    monkeypatch.setattr(live, "present_ids", lambda graph, ids: set())
    monkeypatch.setattr(live, "derive_if_due", lambda graph: None)
    monkeypatch.setattr(
        live,
        "ingest_batch",
        lambda items, graph, workers=1: [ingest(a, graph, e, r) for a, e, r in items],
    )
    monkeypatch.setattr(runner, "first_cursor", lambda c, now_ms=None: 0)

    def ingest(a, graph, events=None, replace=False):
        ingested.append((a.id, graph))
        return {"alerts": [], "freshness_lag_ms": 3}

    monkeypatch.setattr(live, "ingest_artifact", ingest)
    a = Artifact(
        id="msg:slack-C1-1",
        kind="message",
        product_hint="Acme",
        ts_ms=1,
        ts_iso="2027-03-09T10:00:00Z",
        text="no label",
        raw_ref="test:1",
    )

    class One:
        name, maps = "slack", MAPS

        def fetch(self, since_ms):
            return [Item(a, updated_ms=5)]

    report = runner.sync([One()])
    assert prepared == ["yaan_org"]
    assert ingested == [("msg:slack-C1-1", "yaan_org")]
    assert report["refused"] == []


def test_the_demo_only_commands_refuse_in_org_mode(org_mode, capsys):
    from app import cli

    for command in (["demo", "reset"], ["seed"], ["connectors", "seed"]):
        assert cli.main(command) == 1
        assert "off in org mode" in capsys.readouterr().err


def test_init_asks_for_org_mode_first(capsys):
    from app import cli

    assert cli.main(["init"]) == 1
    assert "LORE_DATA_MODE=org" in capsys.readouterr().err


# --- a connector in org mode -----------------------------------------------------------------
def test_slack_reads_a_teams_channel_in_org_mode(org_mode, monkeypatch):
    from app.connectors import slack
    from tests.test_connector_slack import FakeClient

    class Team(FakeClient):
        def users_info(self, user):
            return {"user": {"real_name": {"U2": "Sam Lee"}.get(user, user), "name": user}}

    history = [
        {"type": "message", "user": "U2", "ts": "1805000000.000100", "text": "<@U1> export broke"},
        {
            "type": "message",
            "bot_id": "B1",
            "ts": "1805000300.000300",
            "text": "[synthetic demo · as Ian Davis (eid_7017b913)] a seeded demo post",
        },
    ]
    rows = [{"id": "bug-support", "name": "bug-support"}]
    monkeypatch.setattr(slack.db, "run", lambda name, params, **k: type("R", (), {"rows": rows}))
    maps = Maps(
        product="Acme",
        default_person=None,
        sources={
            "slack": {"channels": {"vizforce-bugs": "bug-support"}, "alerts_channel": "lore-alerts"}
        },
    )
    conn = slack.SlackConnector(Team(history), "yaan_org", maps)
    items = conn.fetch(0)
    assert len(items) == 1  # no bot post is read back, persona label or not
    a = items[0].artifact
    sam, priya = org.eid_for("slack:U2"), org.eid_for("priya@acme.example")
    assert (a.id, a.author_eid, a.text) == (
        "msg:slack-C1-1805000000.000100",
        sam,
        f"@{priya} export broke",
    )
    assert a.meta["synthetic"] is False and org._hints[sam]["name"] == "Sam Lee"
    fix = {
        "id": "a2",
        "kind": "doc_fix",
        "target_id": a.id,
        "target_name": "Sam's message",
        "draft": "It was fixed in 2.3.",
        "evidence": [],
    }
    assert conn.post(fix).startswith("https://")  # a correction still finds its thread


# --- the API: the UI learns the mode, and the demo's injections are off -----------------------
def test_the_api_serves_the_org_graph_and_turns_the_demo_off(org_mode, monkeypatch):
    from fastapi.testclient import TestClient

    from app import main

    seen = []

    def run(name, params, graph=None, **k):
        seen.append(graph)
        return type(
            "R", (), {"rows": [{"id": "Acme", "name": "Acme", "aliases": [], "artifacts": 1}]}
        )

    monkeypatch.setattr(main.db, "run", run)
    from app import auth

    anon = TestClient(main.app)
    assert anon.get("/products").status_code == 401  # a team's data needs sign-in (D100)
    assert anon.get("/health").status_code in (200, 503)  # but not the uptime check
    client = TestClient(main.app, headers={"Authorization": f"Bearer {auth.issue('eid_0000beef')}"})
    got = client.get("/products").json()
    assert (got["mode"], seen) == ("org", ["yaan_org"])
    assert client.get("/demo/injections").json()["injections"] == []
    refused = client.post("/demo/inject/01-regression")
    assert refused.status_code == 409 and "org mode" in refused.json()["detail"]


def test_the_api_says_demo_by_default(monkeypatch):
    from fastapi.testclient import TestClient

    from app import main

    rows = [{"id": "W", "name": "W", "aliases": [], "artifacts": 3}]
    monkeypatch.setattr(main.db, "run", lambda name, params, **k: type("R", (), {"rows": rows}))
    assert TestClient(main.app).get("/products").json()["mode"] == "demo"


# --- end to end on a real graph ---------------------------------------------------------------
@pytest.mark.db
@pytest.mark.model
def test_a_teams_message_lands_in_its_own_graph(org_mode, empty_graph):
    """prepare (product, channels, roster) -> a Slack message from someone new, mentioning a
    rostered person -> the live path (fold, derived layers, the Sentinel): person registered,
    artifact linked, the issue folded, and nothing from HERB in the graph."""
    made = org.prepare(empty_graph, MAPS.product, MAPS.sources)
    assert made == {"products": 1, "channels": 3, "people": 2}

    priya = org.eid_for("priya@acme.example")
    sam = MAPS.person("slack", "U2", name="Sam Lee")
    text = f"The CSV export times out again since this morning's release. @{priya} can you look?"
    a = Artifact(
        id=base.synthetic_id("msg", "slack", "C1-1804586400.0001"),
        kind="message",
        product_hint="Acme",
        channel_id="bug-support",
        ts_ms=1804586400000,
        ts_iso="2027-03-09T10:00:00Z",
        author_eid=sam,
        text=text,
        raw_ref="slack:C1:1804586400.0001",
        meta={"channel_name": "bug-support", "sender": sam, "synthetic": False, "source": "slack"},
    )
    quote = "The CSV export times out again"
    event = base.structured_event("issue_reported", a, quote, sam, issue="CSV export times out")
    out = live.ingest_artifact(a, empty_graph, events=[event])
    assert out["graph"] == empty_graph and [e["quote"] for e in out["events"]] == [quote]

    people = db.run_text(
        "MATCH (p:Person) RETURN p.id AS id, p.name AS name ORDER BY name LIMIT 10",
        label="people",
        graph=empty_graph,
    ).rows
    assert people == [
        {"id": "eid_0000beef", "name": "Ken Ito"},
        {"id": priya, "name": "Priya Shah"},
        {"id": sam, "name": "Sam Lee"},
    ]
    edges = db.run_text(
        "MATCH (m:Message {id: $id}) "
        "OPTIONAL MATCH (author:Person)-[:AUTHORED]->(m) "
        "OPTIONAL MATCH (m)-[:IN_CHANNEL]->(c:Channel) "
        "OPTIONAL MATCH (m)-[:MENTIONS]->(named:Person) "
        "RETURN author.id AS author, c.id AS channel, collect(named.id) AS mentions LIMIT 1",
        {"id": a.id},
        label="message edges",
        graph=empty_graph,
    ).rows[0]
    assert edges == {"author": sam, "channel": "bug-support", "mentions": [priya]}
    issues = db.run_text(
        "MATCH (i:Issue)-[:IN_PRODUCT]->(:Product {id: 'Acme'}) RETURN i.title AS title LIMIT 5",
        label="issues",
        graph=empty_graph,
    ).rows
    assert [r["title"] for r in issues] == ["CSV export times out"]
    products = db.run_text(
        "MATCH (p:Product) RETURN p.id AS id LIMIT 5", label="products", graph=empty_graph
    ).rows
    assert products == [{"id": "Acme"}]  # no HERB product came along

    met = json.loads(org.directory_file(empty_graph).read_text(encoding="utf-8"))
    assert list(met) == [sam]  # only the person met in the tool; the roster stays in people.yaml
    assert org.directory(empty_graph)[sam]["role"] is None  # unknown: the policy says unverified

    # Priya (Engineering Lead in people.yaml) resolves it; Sam says it's back: a regression
    def said(n: int, who: str, words: str, kind: str, quote: str) -> list[str]:
        m = a.model_copy(
            update={
                "id": base.synthetic_id("msg", "slack", f"C1-1804586400.000{n}"),
                "ts_ms": a.ts_ms + n * 3_600_000,
                "author_eid": who,
                "text": words,
                "meta": {**a.meta, "sender": who},
            }
        )
        e = base.structured_event(kind, m, quote, who, issue="CSV export times out")
        return [x["kind"] for x in live.ingest_artifact(m, empty_graph, events=[e])["alerts"]]

    assert (
        said(
            2,
            priya,
            "Fixed the CSV export timeout in today's patch.",
            "issue_resolved",
            "Fixed the CSV export timeout",
        )
        == []
    )
    assert said(
        3,
        sam,
        "The CSV export times out again after the patch.",
        "issue_reopened",
        "The CSV export times out again",
    ) == ["regression"]


def test_people_yaml_may_give_an_authority_and_says_what_is_wrong(tmp_path):
    path = tmp_path / "people.yaml"
    path.write_text(
        "people: [{name: Sam, role: Staff Engineer, authority: lead}, {name: Kim}]",
        encoding="utf-8",
    )
    got = {p["name"]: p["authority"] for p in org.parse_roster(path).values()}
    assert got == {"Sam": "lead", "Kim": None}
    path.write_text("people: [{name: Sam, authority: boss}]", encoding="utf-8")
    with pytest.raises(org.OrgError, match="Sam's authority 'boss' must be one of"):
        org.parse_roster(path)


def test_a_live_message_is_read_with_its_conversation(org_mode, empty_graph, monkeypatch):
    """D102: org mode runs only the live path, which used to read each message alone. Now the
    model sees the channel's earlier messages, as the batch extraction does, and only what the
    new message states is kept."""
    from app.ingest import extract, verify

    org.prepare(empty_graph, MAPS.product, MAPS.sources)
    sam = MAPS.person("slack", "U2", name="Sam Lee")
    priya = org.eid_for("priya@acme.example")

    def message(n: int, who: str, text: str) -> Artifact:
        ts = 1804586400000 + n * 60_000
        return Artifact(
            id=base.synthetic_id("msg", "slack", f"C1-{ts}"),
            kind="message",
            product_hint="Acme",
            channel_id="bug-support",
            ts_ms=ts,
            ts_iso=f"2027-03-09T10:0{n}:00Z",
            author_eid=who,
            text=text,
            raw_ref=f"slack:C1:{ts}",
            meta={"channel_name": "bug-support", "sender": who, "source": "slack"},
        )

    first = message(1, sam, "Issue 'CSV export times out' is back for CUST-0001.")
    live.ingest_artifact(first, empty_graph, events=[])
    reply = message(2, priya, "Fixed it: the export now streams.")
    seen = []

    def fake_run(units, workers=1):
        [unit] = units
        seen.append([m.id for m in unit.messages])
        rows = [
            {"type": "issue_reported", "quote": "CSV export times out", "msg": 0},
            {"type": "issue_resolved", "quote": "Fixed it", "msg": 1},
        ]
        kept = []
        for r in rows:
            e = {**dict.fromkeys(extract.Event.model_fields), **r, "issue": "CSV export times out"}
            e |= {"decisive": True, "confidence": 0.9}
            ok, row = verify.check(e, unit)
            assert ok
            kept.append(row)
        return kept, [], {"cost_usd": 0.0}

    monkeypatch.setattr(extract, "run", fake_run)
    out = live.ingest_artifact(reply, empty_graph)
    assert seen == [[first.id, reply.id]]  # the earlier message first, then the new one
    assert [e["type"] for e in out["events"]] == ["issue_resolved"]  # its own claim only
    assert out["events"][0]["artifact_id"] == reply.id


@pytest.mark.db
@pytest.mark.model
def test_a_batch_ends_where_one_message_at_a_time_does(org_mode, empty_graph):
    """D112: a connector round goes in as one batch (one fold, one Sentinel pass). A report, a
    fix and a reopening of one bug, batched on one graph and one by one on another, end in
    the same derived layer and the same regression alert."""
    import uuid

    from app import schema

    other = f"lore_test_{uuid.uuid4().hex[:12]}"
    schema.apply(other)
    try:
        for g in (empty_graph, other):
            org.prepare(g, MAPS.product, MAPS.sources)
        priya, ken = org.eid_for("priya@acme.example"), "eid_0000beef"
        steps = [
            (priya, "issue_reported", "The CSV export times out on big dashboards"),
            (priya, "issue_resolved", "The CSV export times out bug is fixed"),
            (ken, "issue_reopened", "The CSV export times out again"),
        ]

        def item(n, who, kind, text):
            a = Artifact(
                id=base.synthetic_id("msg", "slack", f"C1-18045864{n:02d}.0001"),
                kind="message",
                product_hint="Acme",
                channel_id="bug-support",
                ts_ms=1804586400000 + n * 60_000,
                ts_iso=f"2027-03-09T10:{n:02d}:00Z",
                author_eid=who,
                text=text,
                raw_ref=f"slack:C1:{n}",
                meta={"channel_name": "bug-support", "sender": who, "source": "slack"},
            )
            ev = base.structured_event(kind, a, text, who, issue="CSV export times out")
            return a, [ev], False

        batch = [item(n, *s) for n, s in enumerate(steps)]
        outs = live.ingest_batch(batch, empty_graph)
        assert [o["batch"] for o in outs] == [3, 3, 3]
        for a, events, replace in batch:
            live.ingest_artifact(a, other, events=events, replace=replace)

        def state(g):
            """Every derived node's labels and properties, apart from when each of its events
            reached Yaan (observed_ms, and the row hash that covers it): the two graphs
            received the same messages at different moments."""
            nodes = db.run_text(
                "MATCH (n) WHERE n:Claim OR n:Decision OR n:Issue "
                "RETURN labels(n) AS labels, properties(n) AS props LIMIT 100",
                label="derived",
                graph=g,
            ).rows
            alerts = db.run_text(
                "MATCH (a:Alert) RETURN a.id AS id, a.kind AS kind, a.status AS status LIMIT 20",
                label="alerts",
                graph=g,
            ).rows
            plain = [
                (r["labels"], sorted((k, str(v)) for k, v in r["props"].items()
                                     if k not in ("observed_ms", "row_hash")))
                for r in nodes
            ]
            return sorted(plain), sorted((r["id"], r["kind"], r["status"]) for r in alerts)

        derived, alerts = state(empty_graph)
        assert len(derived) == 7  # 3 claims, 3 decisions, the issue
        assert (derived, alerts) == state(other)
        assert [k for _, k, _ in alerts] == ["regression"]
        assert outs[2]["alerts"] and outs[2]["alerts"][0]["kind"] == "regression"
    finally:
        db.client(readonly=False).select_graph(other).delete()


@pytest.mark.db
@pytest.mark.model
def test_the_live_linker_finds_what_is_loaded_in_the_graph(org_mode, empty_graph):
    """D113: no log of every live artifact so far. A message's url finds the PR already in the
    graph and "Revert PR# 1" finds PR 1 of its repository, each by an indexed lookup for that
    artifact. (Codenames: test_linker, an org's products have none.)"""
    org.prepare(empty_graph, MAPS.product, MAPS.sources)
    priya = org.eid_for("priya@acme.example")
    repo, url = "acme/app", "https://github.com/acme/app/pull/1"

    def pr(n, title):
        return Artifact(
            id=base.synthetic_id("pr", "github", f"{repo}#{n}"),
            kind="pr",
            product_hint="Acme",
            ts_ms=1804586400000 + n,
            ts_iso="2027-03-09T10:00:00Z",
            author_eid=priya,
            title=title,
            text="",
            url=f"https://github.com/{repo}/pull/{n}",
            raw_ref=f"github:{repo}#{n}",
            meta={"repo": repo, "number": n, "state": "open", "merged": False, "reviews": []},
        )

    first, revert = pr(1, "Stream the CSV export"), pr(2, "Revert PR# 1: Stream the CSV export")
    live.ingest_artifact(first, empty_graph)
    note = Artifact(
        id=base.synthetic_id("msg", "slack", "C1-1804586500.0001"),
        kind="message",
        product_hint="Acme",
        channel_id="bug-support",
        ts_ms=1804586500000,
        ts_iso="2027-03-09T10:01:40Z",
        author_eid=priya,
        text=f"The export fix is in {url}/ now.",
        raw_ref="slack:C1:1",
        meta={"channel_name": "bug-support", "sender": priya, "source": "slack"},
    )
    live.ingest_batch([(note, [], False), (revert, None, False)], empty_graph)
    edges = db.run_text(
        "MATCH (m:Message {id: $m}) "
        "OPTIONAL MATCH (m)-[:SHARES]->(shared:Artifact) "
        "OPTIONAL MATCH (r:Artifact {id: $r})-[:REVERTS]->(o:Artifact) "
        "RETURN shared.id AS shared, o.id AS reverted LIMIT 1",
        {"m": note.id, "r": revert.id},
        label="links",
        graph=empty_graph,
    ).rows[0]
    assert edges == {"shared": first.id, "reverted": first.id}
    assert not (live.live_dir(empty_graph) / "artifacts.jsonl").exists()  # no log any more


@pytest.mark.db
def test_a_mixed_channel_is_read_for_events_and_a_planning_one_is_not(org_mode, empty_graph):
    """D116: the kind a channel declares, not its name, decides what extraction reads."""
    sources = {
        "slack": {
            "channels": {
                "backend": {"channel": "backend", "kind": "mixed"},
                "roadmap": {"channel": "roadmap", "kind": "planning"},
            }
        }
    }
    org.prepare(empty_graph, "Acme", sources)
    priya = org.eid_for("priya@acme.example")

    def message(channel):
        return Artifact(
            id=base.synthetic_id("msg", "slack", f"{channel}-1"),
            kind="message",
            product_hint="Acme",
            channel_id=channel,
            ts_ms=1804586400000,
            ts_iso="2027-03-09T10:00:00Z",
            author_eid=priya,
            text="The CSV export bug is fixed; PR 12 can merge.",
            raw_ref="x",
            meta={"channel_name": channel},
        )

    mixed = live.estimate_extraction([message("backend")], empty_graph)
    planning = live.estimate_extraction([message("roadmap")], empty_graph)
    assert (mixed["need_the_model"], planning["need_the_model"]) == (1, 0)


def test_a_source_or_a_channel_may_name_its_own_product():
    """D116: one org graph, several products. A channel's own `product` wins, then its
    source's, then the top-level one."""
    maps = Maps(
        product="Acme",
        default_person=None,
        sources={
            "slack": {
                "channels": {
                    "support": "bug-support",
                    "mobile": {"channel": "bug-mobile", "product": "Acme Mobile"},
                }
            },
            "github": {"product": "Acme Mobile", "visibility": "everyone"},
            "jira": {"channel": "bug-tickets", "visibility": "everyone"},
        },
    )
    assert maps.product_of("slack", "bug-support") == "Acme"
    assert maps.product_of("slack", "bug-mobile") == "Acme Mobile"
    assert maps.product_of("github") == "Acme Mobile"
    assert maps.product_of("jira", "bug-tickets") == "Acme"
    assert org.declared_products(maps.product, maps.sources) == ["Acme", "Acme Mobile"]


@pytest.mark.db
def test_prepare_makes_every_product_and_puts_each_channel_in_its_own(org_mode, empty_graph):
    sources = {
        "slack": {
            "channels": {
                "support": "bug-support",
                "mobile": {"channel": "bug-mobile", "product": "Acme Mobile"},
            }
        }
    }
    made = org.prepare(empty_graph, "Acme", sources)
    assert made["products"] == 2
    rows = db.run_text(
        "MATCH (c:Channel) RETURN c.id AS id, c.product_id AS product ORDER BY id LIMIT 10",
        label="channels",
        graph=empty_graph,
    ).rows
    assert rows == [
        {"id": "bug-mobile", "product": "Acme Mobile"},
        {"id": "bug-support", "product": "Acme"},
    ]
