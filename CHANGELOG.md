# Changelog

What changed between versions, newest first. Yaan is 0.x: settings and the graph schema may still change between minor versions, and each such change is listed here with what to do.

## Unreleased

Hardening after the post-event review (docs/hardening-plan.md).

### Changed
- The site and README state counts that a test now checks against the code (193 queries, 17 agent tools, 16 MCP tools), mark the eight connectors not yet run against the live tools as experimental, and say what the citation check does and doesn't prove. The HERB paper's agent is shown as context, not as a head-to-head.
- The README says which decisions Yaan tracks today: issue status and pull-request intent.

### Added
- CI: lint, tests without a database, the full suite on FalkorDB, and the web build.
- SECURITY.md: how to report a vulnerability privately.

### Fixed
- A test that needs FalkorDB ran under `pytest -m "not db"`: every test using a database fixture is now marked `db` automatically.

## 0.1.0 · 2026-10-18

The Graph Hacks submission.
