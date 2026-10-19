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
        "jira": {"channel": "bug-tickets"},
        "notion": {"channel": "planning-specs"},
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
    for cached in (get_settings, org.roster, org.accounts):
        cached.cache_clear()
    org._hints.clear()
    yield tmp_path
    for cached in (get_settings, org.roster, org.accounts):
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
def test_declared_channels_need_a_kind_prefix():
    assert org.declared_channels(MAPS.sources) == ["bug-support", "bug-tickets", "planning-specs"]
    assert org.channel_row("develop-backend", "Acme")["stage"] == "dev"
    for bad in ("support", "bug", "eng-bugs"):
        with pytest.raises(org.OrgError, match="must start with bug-, develop- or planning-"):
            org.channel_row(bad, "Acme")


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
    client = TestClient(main.app)
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
