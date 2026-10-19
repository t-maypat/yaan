"""Google Meet connector (D090, EXPERIMENTAL: not run against a live API): transcripts in,
nothing out. Meet REST API v2, polling.

In: each ended conference of one meeting space that has a finished transcript becomes a
`meeting` artifact in the shape HERB's transcripts have: "Speaker: words" lines in time order,
participants mapped to people. A speaker's key in `gmeet.people` is the signed-in user's
resource name ("users/123") or, for a guest, the display name they joined with.
Out: nothing. The Meet API has no call that posts into a meeting or its chat, so `post`
returns None for every action and the next connector may take it.
NOTE: the live path (`sentinel/live.LIVE_KINDS`) takes message, pr and doc today, so it refuses
a `meeting` artifact loudly ("live ingestion takes ...") until that tuple is extended. That is a
deliberate non-change in this scaffold branch.

AUTH, NOT BUILT: Meet needs OAuth 2.0. This scaffold takes an access token you already hold in
`GOOGLE_MEET_ACCESS_TOKEN` (scope `meetings.space.readonly`). The consent flow and the token's
refresh are NOT implemented. `GOOGLE_MEET_SPACE` is the space to read ("spaces/NAME").
Transcripts exist only if the meeting's organizer turned transcription on (a Workspace feature).

Docs (retrieved 2026-10-18), from Google's Discovery document
https://meet.googleapis.com/$discovery/rest?version=v2 and the reference:
- https://developers.google.com/workspace/meet/api/reference/rest/v2/conferenceRecords/list
  (filter: space.name, start_time; ConferenceRecord.name / startTime / endTime)
- https://developers.google.com/workspace/meet/api/reference/rest/v2/conferenceRecords.transcripts/list
  (Transcript.name / state STARTED | ENDED | FILE_GENERATED)
- https://developers.google.com/workspace/meet/api/reference/rest/v2/conferenceRecords.transcripts.entries/list
  (transcriptEntries: participant, text, startTime)
- https://developers.google.com/workspace/meet/api/reference/rest/v2/conferenceRecords.participants/get
  (signedinUser { user, displayName }, anonymousUser { displayName }, phoneUser { displayName })
"""

from typing import Any

import httpx

from app.connectors import base
from app.connectors.base import Item, Maps
from app.connectors.experimental import HttpConnector, to_iso, to_ms
from app.ingest.normalize import Artifact

API = "https://meet.googleapis.com"
DONE = {"ENDED", "FILE_GENERATED"}


class GoogleMeetConnector(HttpConnector):
    name = "gmeet"
    label = "Google Meet"

    def __init__(
        self,
        access_token: str,
        space: str,
        graph: str,
        maps: Maps | None = None,
        client: httpx.Client | None = None,
    ):
        self.space = space
        self._who: dict[str, tuple[str, str]] = {}  # participant name -> (account, display name)
        super().__init__(
            graph,
            maps,
            client
            or httpx.Client(
                base_url=API, timeout=20, headers={"Authorization": f"Bearer {access_token}"}
            ),
        )

    def _pages(self, path: str, key: str, **params: Any) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        token = None
        while True:
            page = self._req(
                "GET", path, params={**params, **({"pageToken": token} if token else {})}
            )
            out.extend(page.get(key) or [])
            token = page.get("nextPageToken")
            if not token:
                return out

    def speaker(self, participant: str) -> tuple[str, str]:
        """(account key for the people map, name shown in the transcript) of a participant."""
        if participant not in self._who:
            p = self._req("GET", f"/v2/{participant}")
            kind = next((k for k in ("signedinUser", "anonymousUser", "phoneUser") if k in p), "")
            u = p.get(kind) or {}
            shown = u.get("displayName") or participant.rsplit("/", 1)[-1]
            self._who[participant] = (u.get("user") or shown, shown)
        return self._who[participant]

    # --- reading ---------------------------------------------------------------------------
    def fetch(self, since_ms: int) -> list[Item]:
        flt = f'space.name = "{self.space}"'
        if since_ms:
            flt += f' AND start_time>="{to_iso(since_ms)}"'
        items: list[Item] = []
        for rec in self._pages("/v2/conferenceRecords", "conferenceRecords", filter=flt):
            if not rec.get("endTime"):  # still running
                continue
            for tr in self._pages(f"/v2/{rec['name']}/transcripts", "transcripts"):
                if tr.get("state") not in DONE:
                    continue
                entries = self._pages(f"/v2/{tr['name']}/entries", "transcriptEntries")
                items.append(self.normalize(rec, tr, entries))
        return items

    def normalize(
        self, rec: dict[str, Any], transcript: dict[str, Any], entries: list[dict[str, Any]]
    ) -> Item:
        lines, people = [], []
        for e in sorted(entries, key=lambda e: e["startTime"]):
            account, shown = self.speaker(e["participant"])
            lines.append(f"{shown}: {e['text']}")
            eid = self.maps.person("gmeet", account, e["text"])
            if eid not in people:
                people.append(eid)
        started = to_ms(rec["startTime"])
        key = rec["name"].rsplit("/", 1)[-1]
        a = Artifact(
            id=base.synthetic_id("mtg", "gmeet", key),
            kind="meeting",
            product_hint=self.maps.product,
            ts_ms=started,
            ts_iso=to_iso(started),
            participants=people,
            title=key,
            text="\n".join(lines),
            raw_ref=f"gmeet:{rec['name']}",
            meta={
                "document_type": "Google Meet transcript",
                "synthetic": base.is_demo(),
                "source": "gmeet",
                "transcript": transcript["name"],
            },
        )
        return Item(a, updated_ms=to_ms(transcript.get("endTime") or rec["endTime"]))

    # --- writing ---------------------------------------------------------------------------
    def post(self, action: dict[str, Any], resolve: Any = None) -> str | None:
        return None  # the Meet API can't post into a meeting
