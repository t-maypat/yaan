"""The Google Meet connector (D090, experimental) against httpx's mock transport: no network, no
token. Field names follow Google's Discovery document for Meet v2 (ConferenceRecord, Transcript,
TranscriptEntry, Participant); the vendor reference gives no full response example."""

import httpx
import pytest

from app.connectors import gmeet
from app.connectors.base import Maps
from app.connectors.experimental import to_ms

MAPS = Maps(
    product="VizForce",
    default_person="eid_bd791efc",
    sources={"gmeet": {"people": {"users/111": "eid_9e9883de", "Guest Gia": "eid_aaaa0001"}}},
)
REC = "conferenceRecords/abc123"
RECORD = {
    "name": REC,
    "startTime": "2027-03-14T15:00:00.000Z",
    "endTime": "2027-03-14T15:30:00.000Z",
    "space": "spaces/SPACE",
}
TRANSCRIPT = {
    "name": f"{REC}/transcripts/t1",
    "state": "FILE_GENERATED",
    "startTime": "2027-03-14T15:00:05Z",
    "endTime": "2027-03-14T15:29:00Z",
}
ENTRIES = [  # out of order on purpose
    {
        "name": f"{REC}/transcripts/t1/entries/e2",
        "participant": f"{REC}/participants/p2",
        "text": "[synthetic demo] Then roll back the export change.",
        "startTime": "2027-03-14T15:02:00Z",
    },
    {
        "name": f"{REC}/transcripts/t1/entries/e1",
        "participant": f"{REC}/participants/p1",
        "text": "The CSV export times out again.",
        "startTime": "2027-03-14T15:01:00Z",
    },
]
PARTICIPANTS = {
    f"{REC}/participants/p1": {
        "signedinUser": {"user": "users/111", "displayName": "Hannah Garcia"}
    },
    f"{REC}/participants/p2": {"anonymousUser": {"displayName": "Guest Gia"}},
}


@pytest.fixture
def conn():
    seen = {"filters": []}

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v2/conferenceRecords":
            seen["filters"].append(request.url.params["filter"])
            return httpx.Response(
                200, json={"conferenceRecords": [RECORD, {"name": "conferenceRecords/live"}]}
            )
        if path == f"/v2/{REC}/transcripts":
            return httpx.Response(200, json={"transcripts": [TRANSCRIPT]})
        if path == f"/v2/{REC}/transcripts/t1/entries":
            return httpx.Response(200, json={"transcriptEntries": ENTRIES})
        if path.removeprefix("/v2/") in PARTICIPANTS:
            return httpx.Response(200, json=PARTICIPANTS[path.removeprefix("/v2/")])
        return httpx.Response(404, json={"error": {"code": 404}})

    client = httpx.Client(base_url=gmeet.API, transport=httpx.MockTransport(handle))
    c = gmeet.GoogleMeetConnector("ya29.x", "spaces/SPACE", "lore_demo", MAPS, client)
    c.seen = seen
    return c


def test_an_ended_conference_with_a_transcript_becomes_a_meeting(conn):
    (item,) = conn.fetch(0)  # the record without endTime (still running) is skipped
    a = item.artifact
    assert a.id == "mtg:synthetic-gmeet-abc123" and a.kind == "meeting"
    assert a.text == (
        "Hannah Garcia: The CSV export times out again.\n"
        "Guest Gia: [synthetic demo] Then roll back the export change."
    )
    assert a.participants == ["eid_9e9883de", "eid_aaaa0001"]
    assert a.ts_ms == to_ms("2027-03-14T15:00:00Z")
    assert item.updated_ms == to_ms("2027-03-14T15:29:00Z")
    assert conn.seen["filters"][0] == 'space.name = "spaces/SPACE"'
    conn.fetch(to_ms("2027-03-14T00:00:00Z"))
    assert conn.seen["filters"][1].endswith('AND start_time>="2027-03-14T00:00:00Z"')


def test_a_transcript_still_being_written_is_not_read(conn):
    TRANSCRIPT_STATE = dict(TRANSCRIPT, state="STARTED")
    conn.http = httpx.Client(
        base_url=gmeet.API,
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200,
                json={"conferenceRecords": [RECORD]}
                if r.url.path == "/v2/conferenceRecords"
                else {"transcripts": [TRANSCRIPT_STATE]},
            )
        ),
    )
    assert conn.fetch(0) == []


def test_meet_has_no_api_to_post_into_a_meeting(conn):
    assert conn.post({"id": "a1", "kind": "doc_fix", "target_id": "mtg:x", "draft": "x"}) is None
