"""Zoom connector (D090; checked against the live API, D145): cloud-recording
transcripts in, nothing out. REST v2, polling.

In: each cloud recording of a user that has a finished TRANSCRIPT file (WebVTT, produced when
audio transcription is on) becomes a `meeting` artifact in the shape HERB's transcripts have:
"Speaker: words" lines, participants mapped to people by display name under `zoom.people`.
Zoom lists recordings by date range, at most one month per call, so a round reads the days
since the cursor (never more than 30). A recording whose transcript isn't ready yet is picked
up on a later round.
Out: nothing. `post` returns None for every action. (Zoom Team Chat could take a message, but
that needs a chat scope and a channel mapping this scaffold doesn't define.)
NOTE: the live path (`sentinel/live.LIVE_KINDS`) takes message, pr and doc today, so it refuses
a `meeting` artifact loudly until that tuple is extended. That is a deliberate non-change here.

AUTH, NOT BUILT: Zoom needs OAuth 2.0 (Server-to-Server OAuth or user-level). This scaffold
takes an access token you already hold in `ZOOM_ACCESS_TOKEN` (scope `cloud_recording:read`).
Fetching the token (account credentials grant) and renewing it (they last one hour) are NOT
implemented. `ZOOM_USER_ID` is optional, default `me`.

Docs (retrieved 2026-10-18), from Zoom's published OpenAPI file
https://developers.zoom.us/api-hub/meetings/methods/endpoints.json:
- https://developers.zoom.us/docs/api/meetings/#tag/cloud-recording/GET/users/{userId}/recordings
  (operation recordingsList: from, to as yyyy-mm-dd UTC, page_size up to 300, next_page_token;
  meetings[] with uuid, id, topic, start_time, recording_files[])
- https://developers.zoom.us/docs/api/meetings/#tag/cloud-recording/GET/meetings/{meetingId}/recordings
  (a recording file with file_type TRANSCRIPT, recording_type audio_transcript, status,
  download_url; downloads take the OAuth token as a Bearer token)
- https://developers.zoom.us/docs/api/rest/using-zoom-apis/ (Bearer auth, base https://api.zoom.us/v2)
"""

import re
import time
from typing import Any

import httpx

from app.connectors import base
from app.connectors.base import Item, Maps
from app.connectors.experimental import HttpConnector, to_iso, to_ms
from app.ingest.normalize import Artifact

API = "https://api.zoom.us/v2"
MAX_DAYS = 30
CUE = re.compile(r"^\d{2}:\d{2}:\d{2}[.,]\d{3}\s+-->\s+")
SPEAKER = re.compile(r"^([^:]{1,80}):\s")


def vtt_lines(vtt: str) -> list[str]:
    """The spoken lines of a WebVTT transcript: no header, cue numbers or timings."""
    out: list[str] = []
    cue = False
    for raw in vtt.splitlines():
        line = raw.strip()
        if CUE.match(line):
            cue = True
        elif not line:
            cue = False
        elif cue:
            out.append(line)
    return out


class ZoomConnector(HttpConnector):
    name = "zoom"
    label = "Zoom"

    def __init__(
        self,
        access_token: str,
        graph: str,
        maps: Maps | None = None,
        client: httpx.Client | None = None,
        user_id: str | None = None,
    ):
        self.user_id = user_id or "me"
        super().__init__(
            graph,
            maps,
            client
            or httpx.Client(
                base_url=API,
                timeout=30,
                headers={"Authorization": f"Bearer {access_token}"},
                follow_redirects=True,
            ),
        )

    # --- reading ---------------------------------------------------------------------------
    def fetch(self, since_ms: int) -> list[Item]:
        now = time.time()
        start = max(since_ms / 1000, now - MAX_DAYS * 86400)
        window = {
            "from": time.strftime("%Y-%m-%d", time.gmtime(start)),
            "to": time.strftime("%Y-%m-%d", time.gmtime(now)),
        }
        items: list[Item] = []
        token = None
        while True:
            page = self._req(
                "GET",
                f"/users/{self.user_id}/recordings",
                params={
                    **window,
                    "page_size": 300,
                    **({"next_page_token": token} if token else {}),
                },
            )
            for meeting in page.get("meetings") or []:
                item = self.normalize(meeting)
                if item and item.updated_ms > since_ms:
                    items.append(item)
            token = page.get("next_page_token")
            if not token:
                return items

    def normalize(self, meeting: dict[str, Any]) -> Item | None:
        files = [
            f
            for f in meeting.get("recording_files") or []
            if f.get("file_type") == "TRANSCRIPT" and f.get("status", "completed") == "completed"
        ]
        if not files:
            return None
        vtt = self._req("GET", files[0]["download_url"], text=True)
        lines = vtt_lines(vtt)
        people: list[str] = []
        for line in lines:
            m = SPEAKER.match(line)
            eid = self.maps.person("zoom", m.group(1) if m else None, line)
            if eid not in people:
                people.append(eid)
        started = to_ms(meeting["start_time"])
        a = Artifact(
            id=base.synthetic_id("mtg", "zoom", meeting["uuid"]),
            kind="meeting",
            product_hint=self.maps.product_of(self.name),
            ts_ms=started,
            ts_iso=to_iso(started),
            participants=people,
            title=meeting.get("topic"),
            text="\n".join(lines),
            raw_ref=f"zoom:{meeting['uuid']}",
            meta={
                "document_type": "Zoom transcript",
                "synthetic": base.is_demo(),
                "source": "zoom",
                "meeting_id": meeting.get("id"),
            },
        )
        ended = files[0].get("recording_end")
        return Item(a, updated_ms=to_ms(ended) if ended else started)

    # --- writing ---------------------------------------------------------------------------
    def post(self, action: dict[str, Any], resolve: Any = None) -> str | None:
        return None  # nothing is sent into Zoom
