"""`lore connectors seed` (17.7) against mocked Jira, GitHub, Notion and Slack: the calls come
in story order, and everything written into a sandbox carries the rule-5 label."""

import json

import httpx

from app.connectors import seed
from app.connectors.slack import SEEDED


def recorder(responses):
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, body))
        for (method, suffix), reply in responses.items():
            if request.method == method and request.url.path.endswith(suffix):
                return httpx.Response(200, json=reply)
        return httpx.Response(200, json={})

    return calls, handle


def client(base, handle):
    return httpx.Client(base_url=base, transport=httpx.MockTransport(handle))


def texts(value):
    """Every string written in a request body."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [t for v in value.values() for t in texts(v)]
    if isinstance(value, list):
        return [t for v in value for t in texts(v)]
    return []


def test_setup_writes_the_story_in_order_and_labeled():
    jira_calls, jira = recorder(
        {
            ("GET", "/myself"): {"accountId": "acc-me"},
            ("POST", "/rest/api/3/issue"): {"key": "VIZ-1"},
            ("GET", "/transitions"): {
                "transitions": [
                    {"id": "11", "to": {"name": "In Progress"}},
                    {"id": "31", "to": {"name": "Done"}},
                ]
            },
        }
    )
    gh_calls, gh = recorder(
        {
            ("GET", "/repos/acme/sandbox"): {"default_branch": "main"},
            ("GET", "/git/ref/heads/main"): {"object": {"sha": "abc"}},
            ("POST", "/pulls"): {"number": 1, "html_url": "https://github.com/acme/sandbox/pull/1"},
        }
    )
    notion_calls, notion = recorder({("POST", "/v1/pages"): {"id": "page-1", "url": "https://n/p"}})
    out = seed.setup(
        client("https://j", jira),
        "VIZ",
        client("https://api.github.com", gh),
        "acme/sandbox",
        client("https://api.notion.com", notion),
        "root",
        say=lambda m: None,
    )
    assert out == {
        "notion_page": "page-1",
        "jira_key": "VIZ-1",
        "pr_url": "https://github.com/acme/sandbox/pull/1",
    }
    assert [c[1] for c in gh_calls if c[0] != "GET"] == [
        "/repos/acme/sandbox/git/refs",
        "/repos/acme/sandbox/contents/export/csv_stream.py",
        "/repos/acme/sandbox/pulls",
        "/repos/acme/sandbox/pulls/1/reviews",
        "/repos/acme/sandbox/pulls/1/merge",
    ]
    issue = next(c[2] for c in jira_calls if c[1] == "/rest/api/3/issue")
    assert (
        issue["fields"]["assignee"] == {"accountId": "acc-me"}
        and issue["fields"]["summary"] == seed.TITLE
    )
    posts = [c for c in jira_calls if c[0] == "POST"]
    assert [c[1] for c in posts][-2:] == [
        "/rest/api/3/issue/VIZ-1/comment",
        "/rest/api/3/issue/VIZ-1/transitions",
    ]
    assert posts[-1][2] == {"transition": {"id": "31"}}  # to Done, not In Progress
    assert "pull/1" in json.dumps(posts[-2][2])  # the ticket names its fix PR before Done
    # rule 5: every free text the seed writes is labeled (titles and code paths aside)
    for calls in (jira_calls, notion_calls):
        for method, path, body in calls:
            if (
                method == "POST"
                and not path.endswith(("/component", "/transitions"))
                and path != "/v1/pages"
            ):
                assert any("[synthetic demo" in t for t in texts(body)), path
    review = next(c[2] for c in gh_calls if c[1].endswith("/reviews"))
    assert review["event"] == "COMMENT" and review["body"].startswith(seed.BOB)


def test_regression_posts_a_message_the_slack_connector_will_read():
    class Slack:
        def chat_postMessage(self, channel, text, **persona):
            self.sent = (channel, text, persona)
            return {"ts": "1.2"}

    s = Slack()
    assert seed.regression(s, "C1", say=lambda m: None) == "1.2"
    assert s.sent[1].startswith(SEEDED) and seed.TITLE in s.sent[1]
    assert s.sent[2]["username"].startswith("Ian Davis")  # shown as the persona (customize)


def test_a_second_take_updates_the_merged_file_on_a_new_branch():
    """After an earlier take merged export/csv_stream.py, GitHub needs the file's sha."""
    calls, gh = recorder(
        {
            ("GET", "/repos/acme/sandbox"): {"default_branch": "main"},
            ("GET", "/git/ref/heads/main"): {"object": {"sha": "abc"}},
            ("GET", "/contents/export/csv_stream.py"): {"sha": "old-file"},
            ("POST", "/pulls"): {"number": 2, "html_url": "https://github.com/acme/sandbox/pull/2"},
        }
    )
    url = seed.github_fix(client("https://api.github.com", gh), "acme/sandbox", lambda m: None)
    assert url.endswith("/pull/2")
    ref = next(b for m, p, b in calls if p.endswith("/git/refs"))
    put = next(b for m, p, b in calls if m == "PUT" and p.endswith("csv_stream.py"))
    assert ref["ref"].startswith("refs/heads/lore-demo/csv-export-chunks-")
    assert put["sha"] == "old-file" and put["branch"] == ref["ref"].removeprefix("refs/heads/")
