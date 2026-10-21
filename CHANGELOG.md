# Changelog

What changed between versions, newest first. Yaan is 0.x: settings and the graph schema may still change between minor versions, and each such change is listed here with what to do.

## Unreleased

Hardening after the post-event review (docs/hardening-plan.md).

### Changed
- Expertise, precedents, company-use and key-person alerts are rebuilt at most every five minutes after new data, instead of on every message; regressions, reversals and conflicts are still flagged on the message itself.
- The site and README state counts that a test now checks against the code (queries, agent tools, MCP tools), mark the eight connectors not yet run against the live tools as experimental, and say what the citation check does and doesn't prove. The HERB paper's agent is shown as context, not as a head-to-head.
- The README says which decisions Yaan tracks today: issue status and pull-request intent.

### Upgrading
- Connectors now run in their own container: `docker compose --profile app --profile connectors up -d` replaces `docker compose exec -d api yaan connectors run`.
- Run `yaan schema` once on an existing graph: it adds the index the live linker now uses to find already-loaded pull requests and documents by url.
- Org mode: every source other than Slack needs `visibility: everyone` or `visibility: members` (with `members:`) in `connectors.yaml`; `yaan init` says which is missing. Slack's private channels and members are now read from Slack, so a private Slack channel needs a Yaan channel of its own.
- The web UI keeps no token: signing in trades it for an HttpOnly session cookie, so a token saved by an earlier version is exchanged once and deleted. Sign in again if asked.
- Tokens now expire (90 days unless `yaan token issue --days` says otherwise). Tokens issued before this version stay valid until revoked.
- `yaan seed` checks the snapshot's sha256 against `snapshots/SHA256SUMS`; pass `--unpinned` for a snapshot you made yourself.
- The containers no longer run as root. On Linux, `./data` must be writable by uid 10001 (`sudo chown -R 10001:10001 data`), or set `LORE_UID` and `LORE_GID` in `.env` to its owner. The container says so at start if it isn't. Docker Desktop needs nothing.

### Added
- One install reads several GitHub repositories, Jira projects or Notion pages (`repos:`, `projects:`, `pages:` in `connectors.yaml`), each with its own cursor.
- Org mode answers with a prompt that leaves out the demo dataset's conventions; the demo and the evaluation keep theirs, unchanged.
- `people.yaml` can map a team's own titles to authority (`authority_rules`) and hold people with no known role to their own work (`unknown_speakers: conflict`); Graph health counts the status changes applied without a known authority.
- Org mode: a mapped channel can declare its kind instead of carrying it as a name prefix, including `mixed` for channels where bugs and pull requests are both discussed; a tool or a channel can name its own `product`, so one graph holds several.
- A connector's first sync reads its last 30 days (`backfill_days` per tool) instead of its whole history, and `yaan connectors backfill --days N` prices a longer reach before spending anything.
- The live path no longer keeps a log of every live artifact (`data/live/<graph>/artifacts.jsonl`, which only grew): what a new item links to is looked up in the graph.
- Live ingestion in batches: a connector round's items go through one fold and one Sentinel pass. On a copy of the demo graph with twice its events, a message costs about 80 ms in a batch of ten, down from 7 s (`scripts/bench_live.py` measures it).
- `yaan token list` and `yaan token revoke <person> --id <id>` (one token, not all of a person's); tokens record when they were last used.
- A Content-Security-Policy and other security headers on every response.
- A release workflow: a version tag publishes the image to GitHub's registry and opens the release.
- A `worker` service for the connector loop, restarted by Docker and healthchecked; `/health` reports whether it is running.
- The containers run unprivileged, on a read-only filesystem apart from `./data` and `/tmp`, with no Linux capabilities.
- CI: lint, tests without a database, the full suite on FalkorDB, and the web build.
- SECURITY.md: how to report a vulnerability privately.

### Fixed
- Rebuilding expertise, precedents and company-use emptied them first, so an answer during a rebuild could find none. They are now replaced in place.
- An approval interrupted midway left an approved alert with a half-written decision. Responses are now claimed with all their writes and finished on restart; drafts are sent only once their response is complete.
- An approved follow-up could be posted twice after a timeout or a crash, or marked sent when it reached one tool but not another. Every post now carries a marker and is looked for before posting again.
- A long write could outlive the writer lock's lease; the lease is now renewed, and a writer that loses it stops.
- Org-mode approvals were limited per IP address, so a team behind one proxy shared six a minute; they are now limited per person.
- Org mode showed a private Slack channel to every signed-in person unless its name matched `permissions.yaml`, and decided membership by who had posted there. Privacy and members now come from Slack, other sources declare their readers, and anything undeclared is hidden.
- `@yaan` in a Slack channel answered from what the asker could see and posted it where everyone could read it. It now answers from what everyone in the channel may read; a direct message gets the asker's full answer.
- Alert cards and drafted comments sent to Slack, Jira, GitHub or Notion could quote a private message. They now quote only sources everyone may read.
- The connector loop no longer ends on an error: an item that can't go in is kept as a dead letter (`yaan connectors dead`, `yaan connectors retry`), an item that may still go in (spend cap, FalkorDB restarting) waits without being dropped, and the cursor never skips past it.
- Connector state is written atomically, and remembers versions for a day instead of forever.
- A test that needs FalkorDB ran under `pytest -m "not db"`: every test using a database fixture is now marked `db` automatically.

## 0.1.0 · 2026-10-18

The Graph Hacks submission.
