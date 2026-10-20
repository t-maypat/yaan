# Changelog

What changed between versions, newest first. Yaan is 0.x: settings and the graph schema may still change between minor versions, and each such change is listed here with what to do.

## Unreleased

Hardening after the post-event review (docs/hardening-plan.md).

### Changed
- The site and README state counts that a test now checks against the code (queries, agent tools, MCP tools), mark the eight connectors not yet run against the live tools as experimental, and say what the citation check does and doesn't prove. The HERB paper's agent is shown as context, not as a head-to-head.
- The README says which decisions Yaan tracks today: issue status and pull-request intent.

### Upgrading
- Connectors now run in their own container: `docker compose --profile app --profile connectors up -d` replaces `docker compose exec -d api yaan connectors run`.
- The containers no longer run as root. On Linux, `./data` must be writable by uid 10001 (`sudo chown -R 10001:10001 data`), or set `LORE_UID` and `LORE_GID` in `.env` to its owner. The container says so at start if it isn't. Docker Desktop needs nothing.

### Added
- A `worker` service for the connector loop, restarted by Docker and healthchecked; `/health` reports whether it is running.
- The containers run unprivileged, on a read-only filesystem apart from `./data` and `/tmp`, with no Linux capabilities.
- CI: lint, tests without a database, the full suite on FalkorDB, and the web build.
- SECURITY.md: how to report a vulnerability privately.

### Fixed
- The connector loop no longer ends on an error: an item that can't go in is kept as a dead letter (`yaan connectors dead`, `yaan connectors retry`), an item that may still go in (spend cap, FalkorDB restarting) waits without being dropped, and the cursor never skips past it.
- Connector state is written atomically, and remembers versions for a day instead of forever.
- A test that needs FalkorDB ran under `pytest -m "not db"`: every test using a database fixture is now marked `db` automatically.

## 0.1.0 · 2026-10-18

The Graph Hacks submission.
