"""The Confluence connector (D090, experimental) against httpx's mock transport: no network, no
token. Field names follow the published OpenAPI file (PageBulk, FooterCommentModel,
CreateFooterCommentModel); the vendor pages give no response example."""

import json

import httpx
import pytest

from app import db
from app.connectors import confluence
from app.connectors.base import Maps
from app.connectors.experimental import to_ms

MAPS = Maps(
    product="VizForce",
    default_person="eid_bd791efc",
    sources={"confluence": {"channel": "ch-1", "people": {"acc-hannah": "eid_9e9883de"}}},
)
PAGE = {
    "id": "98765",
    "status": "current",
    "title": "Export spec",
    "spaceId": "123",
    "authorId": "acc-hannah",
    "createdAt": "2027-03-10T10:00:00.000Z",
    "version": {"number": 2, "createdAt": "2027-03-11T10:00:00.000Z", "authorId": "acc-hannah"},
    "body": {
        "storage": {
            "representation": "storage",
            "value": "<p>[synthetic demo] Exports stream in <strong>chunks</strong>.</p>"
            "<p>Owner: Hannah</p>",
        }
    },
}
COMMENT = {
    "id": "555",
    "pageId": "98765",
    "version": {"number": 1, "createdAt": "2027-03-12T09:00:00.000Z", "authorId": "acc-other"},
    "body": {
        "storage": {
            "representation": "storage",
            "value": "<p>[synthetic demo] Add a retry section &amp; a test.</p>",
        }
    },
}
PAGE_URL = "https://acme.atlassian.net/wiki/pages/viewpage.action?pageId=98765"


@pytest.fixture
def conn(monkeypatch):
    monkeypatch.setattr(
        db,
        "run",
        lambda name, params, **k: type("R", (), {"rows": [{"id": "ch-1", "name": "planning"}]}),
    )
    seen = {"gets": [], "posted": []}

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path == "/wiki/api/v2/pages":
            seen["gets"].append(dict(request.url.params))
            if request.url.params.get("cursor"):
                return httpx.Response(200, json={"results": [], "_links": {}})
            return httpx.Response(
                200,
                json={
                    "results": [PAGE],
                    "_links": {"next": "/wiki/api/v2/pages?cursor=abc&limit=50"},
                },
            )
        if request.method == "GET" and path == "/wiki/api/v2/pages/98765/footer-comments":
            return httpx.Response(200, json={"results": [COMMENT], "_links": {}})
        if request.method == "POST" and path == "/wiki/api/v2/footer-comments":
            seen["posted"].append(json.loads(request.content))
            return httpx.Response(201, json={"id": "600", "pageId": "98765"})
        return httpx.Response(404, text="nope")

    client = httpx.Client(
        base_url="https://acme.atlassian.net", transport=httpx.MockTransport(handle)
    )
    c = confluence.ConfluenceConnector(
        "https://acme.atlassian.net", "e", "t", "123", "lore_demo", MAPS, client
    )
    c.seen = seen
    return c


def test_a_page_comes_in_as_a_doc_its_share_message_and_its_comments(conn):
    doc, share, comment = [i.artifact for i in conn.fetch(0)]
    assert doc.id == "doc:synthetic-confluence-98765" and doc.kind == "doc"
    assert doc.text == "[synthetic demo] Exports stream in chunks.\nOwner: Hannah"
    assert doc.author_eid == "eid_9e9883de" and doc.url == PAGE_URL
    assert share.id == "msg:synthetic-confluence-98765-shared"
    assert "shared the Export spec" in share.text
    assert comment.id == "msg:synthetic-confluence-98765-comment-555"
    assert "Add a retry section & a test." in comment.text and comment.channel_id == "ch-1"
    g = conn.seen["gets"][0]
    assert (g["space-id"], g["sort"], g["body-format"]) == ("123", "-modified-date", "storage")
    assert conn.seen["gets"][1]["cursor"] == "abc"  # followed _links.next


def test_a_page_with_nothing_newer_than_the_cursor_is_skipped(conn):
    assert conn.fetch(to_ms("2027-03-13T00:00:00Z")) == []


def test_a_correction_becomes_a_footer_comment_in_storage_format(conn):
    draft = 'Doc says "30 s" but a PR changed it: "limit is 60 s" (doc:synthetic-confluence-98765).'
    target = "msg:synthetic-confluence-98765-comment-555"
    ref = conn.post({"id": "a1", "kind": "doc_fix", "target_id": target, "draft": draft})
    assert ref == f"{PAGE_URL}&focusedCommentId=600"
    sent = conn.seen["posted"][0]
    assert sent["pageId"] == "98765" and sent["body"]["representation"] == "storage"
    assert "<strong>Yaan Sentinel · Correction</strong>" in sent["body"]["value"]
    assert "<blockquote><p>limit is 60 s</p></blockquote>" in sent["body"]["value"]
    assert (
        conn.post({"id": "a2", "kind": "doc_fix", "target_id": "doc:herb_x", "draft": "x"}) is None
    )


def test_the_default_client_uses_basic_auth():
    c = confluence.ConfluenceConnector(
        "https://acme.atlassian.net/", "me@x.io", "tok", "123", "lore_demo", MAPS
    )
    assert c.http.auth is not None and str(c.http.base_url) == "https://acme.atlassian.net"
