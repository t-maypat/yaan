# Changelog

What changed between versions, newest first. Yaan is 0.x: settings and the graph schema may still change between minor versions, and each such change is listed here with what to do.

## Unreleased

### Operating Yaan (Phase 22)

#### Added
- `yaan backup` writes one file with the graph and every state file beside it (sign-in tokens, connector cursors, the people met in your tools, synced access lists, the spend ledger), plus the config files, with a `.sha256` beside it; `--keep N` deletes older ones. `yaan restore` brings it back, refusing a damaged file, another FalkorDB version, an existing graph or a running worker unless `--force`; `--as <graph>` is a trial restore of the graph alone.
- A memory cap for FalkorDB (`FALKORDB_MAXMEMORY`, 2 GB by default, nothing evicted). `/health`, the worker's heartbeat and the Admin page warn from 80%; at the cap, writes wait (no item becomes a dead letter) while answers go on. `yaan persistence` shows memory and how the graph is saved.
- Optional append-only persistence (`FALKORDB_APPENDONLY=yes`, `FALKORDB_SAVE`): at most about a second of writes lost in a crash instead of up to a minute. `yaan persistence aof` switches a running server over without losing its data.
- Logs are JSON lines (`LORE_LOG_FORMAT=text` for plain): one per API request with a request id (also sent back as `X-Request-ID`), one per connector round with its counts. Paths only: no query string or question text is logged. Docker rotates them (5 × 10 MB per container).
- `/metrics`: Prometheus text for admins, or a scraper with `LORE_METRICS_TOKEN`.

#### Upgrading
- FalkorDB now starts with a 2 GB memory cap: if `yaan persistence` (or `INFO memory`) shows more in use, set `FALKORDB_MAXMEMORY` higher before `docker compose up -d`, or writes stop at once.
- Don't set `FALKORDB_APPENDONLY=yes` on an existing volume before running `yaan persistence aof`: started that way over a snapshot-only volume, FalkorDB comes up empty, and its next snapshot overwrites the good one.

### Running Yaan at an organisation's scale (23 Oct)

#### Added
- `yaan people` prints the people Yaan met in your tools who aren't in `people.yaml`, as entries to paste.
- `scripts/load_fold.py`: a load test of the decision fold on synthetic events (no model calls).
- An admin role: `admin: true` in `people.yaml`. Admins see a new Admin page (the connector worker and why it waits, dead letters with retry, sign-in tokens with revoke, today's spend by person, people met but not listed) and the Graph health and Evaluation pages; with sign-in on, nobody else does.
- Sign-in with Google, Okta or Entra ID: the `sso` compose profile runs oauth2-proxy in front of Yaan (`LORE_AUTH=header`).
- Optional webhooks: `GITHUB_WEBHOOK_SECRET` / `JIRA_WEBHOOK_SECRET` and `/hooks/github`, `/hooks/jira` wake the worker as soon as something changes.
- `visibility: synced` for GitHub and Jira in `connectors.yaml`: who may read a repository or a Jira project comes from the tool itself, every hour.

#### Changed
- The decision fold no longer slows down as history grows: each event's issue match is kept and only new events, or ones a change could affect, are matched again. A year of 2.5M messages (one product holding 62,000 events) folds in about 13 s per batch; before, matching was quadratic and took minutes.
- Connectors cost less and fail alone: a rate-limited or failing tool waits as long as it asks (Retry-After), or 30 s doubling, while the others go on; Slack reads channel access when Slack says it changed (else every 10 minutes) and history every 5 minutes as a safety net behind its socket; an archived or unreadable Slack channel waits alone and is hidden instead of stopping Slack; GitHub uses conditional requests and reads every review, not the first 100; Notion re-reads a page's body only when it changed and its comments every 10 minutes.
- Search ranks only what the asker may read, so someone who can't see a private channel still gets a full page of results instead of the top results minus the hidden ones.
- An answer's sources are hidden whenever a result row names a hidden item under any field, not only under five known ones.
- Org mode builds who-knows-what only from what everyone may read: work seen only in a private channel no longer makes someone an area's expert.
- People met in a tool get 64-bit ids (`eid_` + 16 hex digits), so two accounts no longer collide; a second account landing on a taken id is refused loudly. Slack and Jira emails at your domains merge a person across tools.

#### Upgrading
- With sign-in on, the Graph health and Evaluation pages are for admins only: mark yourself `admin: true` in `people.yaml`.
- Slack: add the bot events `member_joined_channel`, `member_left_channel`, `channel_archive`, `channel_unarchive`, `channel_rename`, `channel_left`, `group_archive`, `group_unarchive`, `group_rename`, `group_left` to your app (the manifest in `backend/app/connectors/README.md` lists them); without them a membership change reaches Yaan within 10 minutes instead of at once.
- Accounts already registered keep their 8-digit ids; `people.yaml` ids never change. An unlisted account whose Slack or Jira email matches a listed person now counts as that person: their earlier items stay under the old id.
- The first fold of each product after upgrading matches everything once (the stored matches don't exist yet); with years of chat-named issues this can take minutes.

### Running Yaan on a team's own data (22 Oct)

Fixes for what stopped a pilot on an organisation's own tools.

#### Added
- Slack thread replies, edits and deletions now reach Yaan: replies are read with their thread, an edit closes what the old words said, and a deleted message leaves Yaan. In org mode, a daily sweep (and one at start-up) catches up on what the socket missed while Yaan was down; `yaan connectors reconcile --days N` runs it on demand.
- `yaan forget <ids>` removes items, and `yaan erase <person>` a person, everywhere Yaan copied their words: text, search chunks and vectors, extracted events, quotes in alerts and drafts, dead letters, caches. A content-free tombstone stops any sync from bringing them back. Both are dry runs without `--yes`.
- Org mode builds who-works-with-whom, PageRank, communities and team activity every hour, from threads and conversations everyone may read, so a private channel never shows who talks in it. `people.yaml` takes an optional `team:` per person.
- Per-person budgets in org mode: answers may use at most half the daily cap (`LORE_ANSWER_SPEND_CAP_USD`), so reading new items keeps the rest, and each person's answers at most `LORE_PERSON_SPEND_CAP_USD` ($0.25) a day.
- Jira: `customer_pattern` and `issue_types` in `connectors.yaml`.

#### Changed
- Question and request limits count the signed-in person, not the address: a team behind one office address or proxy no longer shares six questions a minute. MCP and Slack questions have the same per-person limits.
- The request limit no longer counts the web UI's own files.
- Jira decides "resolved" from a status's category rather than its English name, so custom workflows and other languages work; a Won't Do resolution dismisses.
- Org mode no longer reads HERB's `CUST-0000` customer ids from tickets, and the MCP server no longer tells clients an org's graph is HERB.
- `/health` says why the connector worker waits (for example, the spend cap pausing extraction) and warns when a product's fold takes over 15 s.
- The README and site say what each connector actually reads: GitHub brings in pull requests and reviews, not issues.
- Sign-in tokens and the people directory are written under a lock: concurrent requests no longer fail or corrupt `tokens.json`, and a revoked token stays revoked.

#### Upgrading
- Behind a reverse proxy in Docker, set `FORWARDED_ALLOW_IPS=*` in `.env` so anonymous visitors are told apart (the port is published on 127.0.0.1 only).
- Org mode: cache entries now expire after `LORE_CACHE_DAYS` (30). The demo's cache never expires.

### Hardening after the post-event review (docs/hardening-plan.md)

#### Changed
- Expertise, precedents, company-use and key-person alerts are rebuilt at most every five minutes after new data, instead of on every message; regressions, reversals and conflicts are still flagged on the message itself.
- The site and README state counts that a test now checks against the code (queries, agent tools, MCP tools), mark the eight connectors not yet run against the live tools as experimental, and say what the citation check does and doesn't prove. The HERB paper's agent is shown as context, not as a head-to-head.
- The README says which decisions Yaan tracks today: issue status and pull-request intent.

#### Upgrading
- Connectors now run in their own container: `docker compose --profile app --profile connectors up -d` replaces `docker compose exec -d api yaan connectors run`.
- Run `yaan schema` once on an existing graph: it adds the index the live linker now uses to find already-loaded pull requests and documents by url.
- Org mode: every source other than Slack needs `visibility: everyone` or `visibility: members` (with `members:`) in `connectors.yaml`; `yaan init` says which is missing. Slack's private channels and members are now read from Slack, so a private Slack channel needs a Yaan channel of its own.
- The web UI keeps no token: signing in trades it for an HttpOnly session cookie, so a token saved by an earlier version is exchanged once and deleted. Sign in again if asked.
- Tokens now expire (90 days unless `yaan token issue --days` says otherwise). Tokens issued before this version stay valid until revoked.
- `yaan seed` checks the snapshot's sha256 against `snapshots/SHA256SUMS`; pass `--unpinned` for a snapshot you made yourself.
- The containers no longer run as root. On Linux, `./data` must be writable by uid 10001 (`sudo chown -R 10001:10001 data`), or set `LORE_UID` and `LORE_GID` in `.env` to its owner. The container says so at start if it isn't. Docker Desktop needs nothing.

#### Added
- `yaan export` writes a graph to plain JSON lines and `yaan import` loads one into a new graph: the graph leaves Yaan in a format any tool reads.
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

#### Fixed
- An answer could state a number none of its sources held (a count, a day). The citation check now drops such a sentence.
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
