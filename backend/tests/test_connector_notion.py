"""The Notion connector (17.6) against httpx's mock transport: no network, no token. Payload
shapes follow Notion's API 2022-06-28 (block children, pages, comments)."""

import json

import httpx
import pytest

from app.connectors import notion
from app.connectors.base import Maps

MAPS = Maps(
    product="VizForce",
    default_person="eid_bd791efc",
    sources={"notion": {"channel": "ch-vizai-afe877", "people": {"u-me": "eid_cb131cb3"}}},
)
ROOT = "11111111222233334444555555555555"
PAGE = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
FIONA = "[synthetic demo · as Fiona Davis (eid_131494b8)]"


def para(text):
    return {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": text}]}}


RESPONSES = {
    f"/v1/blocks/{ROOT}/children": {
        "results": [
            {"id": PAGE, "type": "child_page", "child_page": {"title": "CSV Export Spec"}},
            {"id": "x", "type": "paragraph", "paragraph": {"rich_text": []}},
        ],
        "has_more": False,
    },
    f"/v1/pages/{PAGE}": {
        "id": PAGE,
        "url": "https://www.notion.so/CSV-Export-Spec-aaaaaaaabbbbccccddddeeeeeeeeeeee",
        "created_time": "2027-03-10T09:00:00.000Z",
        "last_edited_time": "2027-03-10T09:30:00.000Z",
        "created_by": {"id": "u-me"},
    },
    f"/v1/blocks/{PAGE}/children": {
        "results": [
            para("[synthetic demo] The CSV export streams rows in chunks of 5,000."),
            {"type": "heading_2", "heading_2": {"rich_text": [{"plain_text": "Limits"}]}},
            {"type": "image", "image": {}},
        ],
        "has_more": False,
    },
    "/v1/comments": {
        "results": [
            {
                "id": "c1c1c1c1-0000-0000-0000-000000000000",
                "created_time": "2027-03-11T10:00:00.000Z",
                "created_by": {"id": "u-me"},
                "rich_text": [{"plain_text": FIONA + " Add a section on timeouts."}],
            }
        ],
        "has_more": False,
    },
}


@pytest.fixture
def conn(monkeypatch):
    rows = [{"id": "ch-vizai-afe877", "name": "planning-VizForce"}]
    monkeypatch.setattr(notion.db, "run", lambda name, params, **k: type("R", (), {"rows": rows}))
    posted = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posted.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "new"})
        if request.url.path == "/v1/comments":  # the review, then what Yaan posted
            mine = [
                {"rich_text": [{"plain_text": p["rich_text"][0]["text"]["content"]}]}
                for p in posted
            ]
            found = RESPONSES["/v1/comments"]["results"] + mine
            return httpx.Response(200, json={"results": found, "has_more": False})
        return httpx.Response(200, json=RESPONSES[request.url.path])

    client = httpx.Client(base_url=notion.API, transport=httpx.MockTransport(handle))
    c = notion.NotionConnector("t", ROOT, "lore_demo", MAPS, client)
    c.posted = posted
    return c


def test_a_page_becomes_a_doc_its_share_and_its_review(conn):
    doc, share, comment = (i.artifact for i in conn.fetch(0))
    assert doc.id == "doc:synthetic-notion-aaaaaaaabbbbccccddddeeeeeeeeeeee" and doc.kind == "doc"
    assert doc.text == "[synthetic demo] The CSV export streams rows in chunks of 5,000.\nLimits"
    assert (doc.author_eid, doc.meta["doc_type"], doc.meta["base_id"]) == (
        "eid_cb131cb3",
        "CSV Export Spec",
        doc.id,
    )
    assert share.channel_id == "ch-vizai-afe877" and doc.url in share.text  # -> SHARES
    assert share.ts_ms == doc.ts_ms + 1 and share.text.startswith("[synthetic demo]")
    assert comment.author_eid == "eid_131494b8" and "Add a section on timeouts." in comment.text
    assert conn.fetch(notion.ms("2027-03-12T00:00:00.000Z")) == []  # nothing changed since


def test_corrections_go_on_the_page(conn):
    ref = conn.post(
        {"id": "a1", "kind": "doc_fix", "target_id": conn.doc_id(PAGE), "draft": "Fix it."}
    )
    assert ref.endswith("aaaaaaaabbbbccccddddeeeeeeeeeeee")
    assert conn.posted[-1]["parent"] == {"page_id": "aaaaaaaabbbbccccddddeeeeeeeeeeee"}
    again = conn.post(
        {"id": "a1", "kind": "doc_fix", "target_id": conn.doc_id(PAGE), "draft": "Fix it."}
    )
    assert again == ref and len(conn.posted) == 1  # a retry finds its marker (D110)
    via_comment = "msg:synthetic-notion-aaaaaaaabbbbccccddddeeeeeeeeeeee-comment-c1"
    assert conn.post({"id": "a2", "kind": "doc_fix", "target_id": via_comment, "draft": "x"})
    assert (
        conn.post({"id": "a3", "kind": "notify_owner", "target_id": "eid_1", "draft": "x"}) is None
    )


def test_an_unchanged_page_costs_one_call_and_its_comments_are_read_every_ten_minutes(
    conn, monkeypatch
):
    """D132: every round re-read every page, its comments and its body."""
    paths = []
    handle = conn.http._transport.handler

    def counting(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return handle(request)

    conn.http = httpx.Client(base_url=notion.API, transport=httpx.MockTransport(counting))
    later = notion.ms("2027-03-12T00:00:00.000Z")
    clock = [1_900_000_000.0]
    monkeypatch.setattr(notion.time, "time", lambda: clock[0])
    conn.fetch(later)  # the first round reads the comments
    paths.clear()
    clock[0] += 60
    assert conn.fetch(later) == []
    assert "/v1/comments" not in paths and f"/v1/blocks/{PAGE}/children" not in paths
    clock[0] += notion.COMMENTS_EVERY_MS / 1000
    conn.fetch(later)
    assert "/v1/comments" in paths  # ten minutes on: a new comment would show
