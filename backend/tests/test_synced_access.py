"""Who may read GitHub and Jira items, from the tools themselves (Phase 21.5, F10, D131):
`visibility: synced` reads a repository's privacy and collaborators, or a Jira project's
"Browse projects" holders; a PR is read by its repository's readers, a Jira channel by the
people who may browse every project feeding it, and anything not yet synced by nobody. No
network and no database: fake HTTP transports, and the channel write recorded."""

import httpx
import pytest
from test_org import org_mode  # noqa: F401  (the fixture)

from app import org
from app.agent import access
from app.config import get_settings
from app.connectors import github, jira
from app.connectors.base import Maps

PEOPLE = """
people:
  - name: Priya Shah
    email: priya@acme.example
    accounts: {github: priya-s}
  - name: Ken Ito
    email: ken@acme.example
"""


@pytest.fixture
def listed(org_mode):  # noqa: F811
    org.PEOPLE_FILE.write_text(PEOPLE, encoding="utf-8")
    for cached in (org.roster, org.accounts, org.emails, org.email_domains):
        cached.cache_clear()
    people = {p["name"]: eid for eid, p in org.roster().items()}
    return get_settings().lore_org_graph, people["Priya Shah"], people["Ken Ito"]


def client(base: str, routes: dict) -> httpx.Client:
    def handle(request: httpx.Request) -> httpx.Response:
        key = (request.url.path, request.url.params.get("projectKey"), request.url.params.get(
            "startAt"))  # fmt: skip
        for (path, project, start), body in routes.items():
            if key[0] == path and project in (None, key[1]) and start in (None, key[2]):
                return httpx.Response(200, json=body)
        return httpx.Response(200, json=[])

    return httpx.Client(transport=httpx.MockTransport(handle), base_url=base)


def test_a_private_repository_is_read_by_its_collaborators_a_public_one_by_everyone(listed):
    graph, priya, ken = listed
    maps = Maps(product="Acme", default_person=None, sources={"github": {"visibility": "synced"}})
    routes = {
        ("/repos/acme/app", None, None): {"visibility": "private", "private": True},
        ("/repos/acme/app/collaborators", None, None): [{"login": "priya-s"}, {"login": "x"}],
        ("/repos/acme/site", None, None): {"visibility": "public"},
    }
    api = client("https://api.github.com", routes)
    for repo in ("acme/app", "acme/site"):
        github.GitHubConnector("t", repo, graph, maps, api).sync_access()
    assert org.synced_reads(graph, "github:acme/app", priya)
    assert not org.synced_reads(graph, "github:acme/app", ken)
    assert not org.synced_reads(graph, "github:acme/app", None)  # an audience: everyone
    assert org.synced_reads(graph, "github:acme/site", None)
    assert not org.synced_reads(graph, "github:acme/web", priya)  # never synced: hidden

    def sees(viewer, repo):
        a = access.Access.__new__(access.Access)
        a.viewer, a.graph = viewer, graph
        row = {"id": f"pr:github-{repo.replace('/', '-')}-1", "channel_id": None, "repo": repo}
        return a.org_visible(row)

    org.source_audiences.cache_clear()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(org, "source_audiences", lambda: {"github": org.Audience("synced")})
        assert sees(priya, "acme/app") and not sees(ken, "acme/app") and sees(ken, "acme/site")
        assert not sees(priya, "acme/web")


def test_a_jira_channel_is_read_by_who_may_browse_every_project_feeding_it(listed, monkeypatch):
    graph, priya, ken = listed
    conf = {"channel": "bug-tickets", "visibility": "synced", "projects": ["KAN", "OPS"]}
    maps = Maps(product="Acme", default_person=None, sources={"jira": conf})
    search = "/rest/api/3/user/permission/search"
    routes = {
        (search, "KAN", "0"): [
            {"accountId": "a1", "displayName": "Priya", "emailAddress": "priya@acme.example"},
            {"accountId": "a2", "displayName": "Ken", "emailAddress": "ken@acme.example"},
            {"accountId": "bot", "accountType": "app"},  # an app is nobody
        ],
        (search, "OPS", "0"): [{"accountId": "a1", "emailAddress": "priya@acme.example"}],
    }
    written = []
    monkeypatch.setattr(org, "set_channel_access", lambda g, rows, members: written.append(
        (rows, members)))  # fmt: skip
    site = "https://acme.atlassian.net"
    api = client(site, routes)
    jira.JiraConnector(site, "e", "t", "KAN", graph, maps, api).sync_access()
    assert written == []  # OPS not read yet: the channel stays hidden
    assert org.synced(graph)["jira:KAN"]["members"] == sorted([priya, ken])
    jira.JiraConnector(site, "e", "t", "OPS", graph, maps, api).sync_access()
    assert written == [([{"id": "bug-tickets", "visibility": "private"}], {"bug-tickets": {priya}})]


def test_synced_is_for_tools_that_can_say_and_needs_a_channel_of_its_own():
    assert org.audience("github", {"visibility": "synced"}) == org.Audience("synced")
    with pytest.raises(org.OrgError, match="only .* can sync"):
        org.audience("notion", {"visibility": "synced"})
    with pytest.raises(org.OrgError, match="drop `members`"):
        org.audience("jira", {"visibility": "synced", "members": ["Priya"]})
    sources = {
        "jira": {"channel": "bug-tickets", "visibility": "synced"},
        "linear": {"channel": "bug-tickets", "visibility": "everyone"},
    }
    with pytest.raises(org.OrgError, match="syncs who may read it"):
        org.channel_access(sources)
    rows, members = org.channel_access({"jira": sources["jira"]})
    assert rows == [] and members == {}  # the connector writes it


def test_a_viewers_search_reaches_only_the_repositories_they_may_read(listed, monkeypatch):
    graph, priya, ken = listed
    org.set_synced(graph, "github:acme/app", False, [priya])
    org.set_synced(graph, "github:acme/site", True, [])
    monkeypatch.setattr(org, "source_audiences", lambda: {"github": org.Audience("synced")})
    monkeypatch.setattr(access.db, "run_all", lambda name, params, **k: [])  # no channels

    def reach(viewer):
        return access.Access(viewer, graph).reach()

    assert reach(priya)["reach_repos"] == ["acme/app", "acme/site"]
    assert reach(ken)["reach_repos"] == ["acme/site"]
    assert reach(ken)["reach_sources"] == []  # a synced source isn't open by its name
