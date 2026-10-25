"""The Google Drive connector (D090, experimental) against httpx's mock transport: no network, no
token. Field names follow Google's Discovery document for Drive v3 (File, Comment, FileList);
the vendor reference pages give no full response example."""

import json

import httpx
import pytest

from app import db
from app.connectors import gdrive
from app.connectors.base import Maps
from app.connectors.experimental import to_ms

MAPS = Maps(
    product="VizForce",
    default_person="eid_bd791efc",
    sources={
        "gdrive": {
            "channel": "ch-1",
            "people": {"hannah@example.com": "eid_9e9883de", "Fiona Davis": "eid_f10a0001"},
        }
    },
)
FILE_ID = "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms"
FILE = {
    "id": FILE_ID,
    "name": "Export spec",
    "createdTime": "2027-03-10T10:00:00.000Z",
    "modifiedTime": "2027-03-11T10:00:00.000Z",
    "webViewLink": f"https://docs.google.com/document/d/{FILE_ID}/edit?usp=drivesdk",
    "owners": [{"emailAddress": "hannah@example.com"}],
}
COMMENT = {
    "id": "AAAAB3",
    "content": "[synthetic demo] Add a retry section.",
    "createdTime": "2027-03-12T09:00:00.000Z",
    "modifiedTime": "2027-03-12T09:00:00.000Z",
    "author": {"displayName": "Fiona Davis"},
}


@pytest.fixture
def conn(monkeypatch):
    monkeypatch.setattr(
        db,
        "run",
        lambda name, params, **k: type("R", (), {"rows": [{"id": "ch-1", "name": "planning"}]}),
    )
    seen = {"lists": [], "posted": []}

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path == "/drive/v3/files":
            seen["lists"].append(dict(request.url.params))
            return httpx.Response(200, json={"kind": "drive#fileList", "files": [FILE]})
        if request.method == "GET" and path == f"/drive/v3/files/{FILE_ID}/comments":
            return httpx.Response(200, json={"kind": "drive#commentList", "comments": [COMMENT]})
        if request.method == "GET" and path == f"/drive/v3/files/{FILE_ID}/export":
            assert request.url.params["mimeType"] == "text/plain"
            return httpx.Response(200, text="[synthetic demo] Exports stream in chunks.\r\n")
        if request.method == "POST" and path == f"/drive/v3/files/{FILE_ID}/comments":
            assert request.url.params["fields"] == "id"  # Drive requires `fields` on create
            seen["posted"].append(json.loads(request.content)["content"])
            return httpx.Response(200, json={"id": "AAAAC4"})
        return httpx.Response(404, json={"error": {"code": 404}})

    client = httpx.Client(base_url=gdrive.API, transport=httpx.MockTransport(handle))
    c = gdrive.GoogleDriveConnector("ya29.x", "FOLDER", "lore_demo", MAPS, client)
    c.seen = seen
    return c


def test_a_google_doc_comes_in_with_its_share_message_and_comments(conn):
    doc, share, comment = [i.artifact for i in conn.fetch(0)]
    assert doc.id == f"doc:synthetic-gdrive-{FILE_ID}" and doc.kind == "doc"
    assert doc.text == "[synthetic demo] Exports stream in chunks."
    assert doc.author_eid == "eid_9e9883de" and doc.url.startswith("https://docs.google.com/")
    assert share.id.endswith("-shared") and comment.id.endswith("-comment-AAAAB3")
    assert comment.author_eid == "eid_f10a0001" and comment.channel_id == "ch-1"
    q = conn.seen["lists"][0]["q"]
    assert q.startswith("'FOLDER' in parents") and "application/vnd.google-apps.document" in q
    assert conn.fetch(to_ms("2027-03-13T00:00:00Z")) == []  # nothing newer than the cursor


def test_a_correction_becomes_a_plain_text_comment_on_the_document(conn):
    ref = conn.post(
        {
            "id": "a1",
            "kind": "doc_fix",
            "target_id": f"msg:synthetic-gdrive-{FILE_ID}-comment-AAAAB3",
            "draft": 'The spec says "30 s" (doc:synthetic-gdrive-x).',
        }
    )
    assert ref == f"https://docs.google.com/document/d/{FILE_ID}/edit"
    sent = conn.seen["posted"][0]
    assert (
        sent.startswith("Yaan Sentinel · Correction") and "###" not in sent and "<sub>" not in sent
    )
    assert (
        conn.post({"id": "a2", "kind": "doc_fix", "target_id": "doc:herb_x", "draft": "x"}) is None
    )


def test_it_sends_the_access_token_it_was_given():
    c = gdrive.GoogleDriveConnector("ya29.x", "FOLDER", "lore_demo", MAPS)
    assert c.http.headers["authorization"] == "Bearer ya29.x"
