"""The Zoom connector (D090, experimental) against httpx's mock transport: no network, no token.
Field names follow Zoom's published OpenAPI file (recordingsList: meetings[] with uuid, id, topic,
start_time, recording_files[] with file_type TRANSCRIPT, status, download_url, recording_end;
the transcript is WebVTT)."""

import time

import httpx
import pytest

from app.connectors import zoom
from app.connectors.base import Maps
from app.connectors.experimental import to_ms

MAPS = Maps(
    product="VizForce",
    default_person="eid_bd791efc",
    sources={"zoom": {"people": {"Hannah Garcia": "eid_9e9883de"}}},
)
VTT = """WEBVTT

1
00:00:01.000 --> 00:00:04.000
Hannah Garcia: The CSV export times out again.

2
00:00:05.000 --> 00:00:09.000
Ian Davis: [synthetic demo] Roll back the export change.
And tell CUST-0003.
"""
MEETING = {
    "uuid": "aDYlohsHRtCd4ii1uC2+hA==",
    "id": 6840331990,
    "topic": "Export triage",
    "start_time": "2027-03-14T15:00:00Z",
    "recording_files": [
        {"file_type": "MP4", "status": "completed", "download_url": "https://zoom.us/rec/x.mp4"},
        {
            "file_type": "TRANSCRIPT",
            "file_extension": "VTT",
            "recording_type": "audio_transcript",
            "status": "completed",
            "recording_end": "2027-03-14T15:31:00Z",
            "download_url": "https://zoom.us/rec/download/t.vtt",
        },
    ],
}


@pytest.fixture
def conn():
    seen = {"params": [], "downloads": []}

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/users/me/recordings":
            seen["params"].append(dict(request.url.params))
            return httpx.Response(200, json={"meetings": [MEETING], "next_page_token": ""})
        if request.url.host == "zoom.us":
            seen["downloads"].append(str(request.url))
            return httpx.Response(200, text=VTT)
        return httpx.Response(404, json={"message": "not found"})

    client = httpx.Client(base_url=zoom.API, transport=httpx.MockTransport(handle))
    c = zoom.ZoomConnector("tok", "lore_demo", MAPS, client)
    c.seen = seen
    return c


def test_vtt_lines_drop_the_header_numbers_and_timings():
    assert zoom.vtt_lines(VTT) == [
        "Hannah Garcia: The CSV export times out again.",
        "Ian Davis: [synthetic demo] Roll back the export change.",
        "And tell CUST-0003.",
    ]


def test_a_recording_with_a_transcript_becomes_a_meeting(conn):
    (item,) = conn.fetch(0)
    a = item.artifact
    assert a.id == "mtg:synthetic-zoom-aDYlohsHRtCd4ii1uC2-hA" and a.kind == "meeting"
    assert a.title == "Export triage" and a.ts_ms == to_ms("2027-03-14T15:00:00Z")
    assert a.participants == ["eid_9e9883de", "eid_bd791efc"]  # Ian is unmapped: the default
    assert a.text.splitlines()[0] == "Hannah Garcia: The CSV export times out again."
    assert item.updated_ms == to_ms("2027-03-14T15:31:00Z")
    assert conn.seen["downloads"] == ["https://zoom.us/rec/download/t.vtt"]
    p = conn.seen["params"][0]
    assert p["page_size"] == "300" and p["to"] == time.strftime("%Y-%m-%d", time.gmtime())


def test_the_window_never_exceeds_a_month(conn):
    conn.fetch(0)
    start = time.mktime(time.strptime(conn.seen["params"][0]["from"], "%Y-%m-%d"))
    assert time.time() - start <= (zoom.MAX_DAYS + 1) * 86400 + 86400


def test_nothing_older_than_the_cursor_and_nothing_without_a_transcript(conn):
    assert conn.fetch(to_ms("2027-03-15T00:00:00Z")) == []
    assert conn.normalize(dict(MEETING, recording_files=[MEETING["recording_files"][0]])) is None


def test_zoom_takes_nothing_back(conn):
    assert conn.post({"id": "a1", "kind": "doc_fix", "target_id": "mtg:x", "draft": "x"}) is None
    assert conn.user_id == "me"
