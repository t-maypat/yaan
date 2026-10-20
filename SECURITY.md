# Security policy

Yaan holds a team's private conversations, so a bug that shows one person what they may not see is a security bug, as is anything that lets a request write to the graph or spend the LLM key without permission.

## Reporting a vulnerability

Please report privately, not in a public issue: use **Report a vulnerability** on the repository's Security tab (GitHub private vulnerability reporting). Include what you did, what you saw, and the commit or image tag you ran.

Yaan has one maintainer. Expect an acknowledgement within a week and a fix or a plan within 30 days for anything that exposes data. You'll be credited in the changelog unless you'd rather not be.

## Supported versions

Yaan is 0.x: only the latest commit on `main` and the latest release get fixes.

## In scope

- Answers, tools, the web UI, Slack or MCP showing a source the viewer may not see (`backend/app/agent/access.py`).
- Bypassing sign-in (`backend/app/auth.py`) or acting as another person.
- Generated Cypher writing to the graph or escaping its guard (`backend/app/agent/graph_query.py`).
- Spending past `LORE_DAILY_SPEND_CAP_USD`, or LLM calls from an unauthenticated request when `LORE_LLM_CACHE_ONLY=1`.
- Posting to Slack, GitHub, Jira or Notion without a person's approval.

## Out of scope

- A deployment that ignores the hardening steps in the self-hosting guide (default passwords, ports opened beyond 127.0.0.1, no TLS proxy).
- The HERB demo data itself: it is public and synthetic.
