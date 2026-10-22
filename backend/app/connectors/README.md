# Connectors and the MCP server: setup

Lore reads a company's tools and writes its approved actions back into them. This page says how to connect each one. For the demo every tool is a **sandbox holding only labeled synthetic data**: the content comes from `lore connectors seed`, every item starts with "[synthetic demo]", and it all lands in the demo graph `lore_demo`, never in the HERB graph.

Put every token in `.env` (never commit it). The variable names are in `.env.example`.

## Your own tools (org mode)
The setup steps below are the same for a team's real workspace. What changes is the data mode: set `LORE_DATA_MODE=org` in `.env`, and the connectors write to your own graph (`LORE_ORG_GRAPH`, default `yaan_org`) instead of the demo:

- **No label.** Items need no "[synthetic demo]" prefix, and their ids drop `synthetic-` (`msg:slack-…`, `pr:github-…`).
- **Your channels.** Copy `connectors.org.example.yaml` over `connectors.yaml`. Each Yaan channel's name starts with `bug-` (issues reported, fixed, reopened), `develop-` (pull requests, development decisions) or `planning-` (documents and their review), or a mapping names its channel freely and gives its kind: `backend: {channel: backend, kind: mixed}`. Extraction reads bug, develop and mixed channels.
- **Who may read what.** Slack tells Yaan which channels are private and who is in them; each round re-reads it, so someone who leaves a channel stops seeing it in Yaan. A private Slack channel needs a Yaan channel of its own. Every other source declares its readers in `connectors.yaml`: `visibility: everyone`, or `visibility: members` with `members:` (names or emails from `people.yaml`). Until a source is declared, `lore init` stops and nothing from it is shown. Text Yaan sends out (alert cards, drafted comments) quotes only sources everyone may read. In a channel, `@yaan` answers from what everyone there may read; send the bot a direct message for an answer from everything you may read.
- **Your people.** `people.yaml` lists people with their role (what the authority policy reads), their team (the org chart: who works on what, a working group's team mix) and their account in each tool, so one person's Slack, GitHub and Jira accounts are one node. Anyone not listed is registered the first time they appear, under their name in that tool, with no role: their claims apply, marked unverified.
- **Threads, edits and deletions.** Slack's thread replies, later edits and deletions arrive over the socket; an edit closes what the old words said, and a deletion removes the message from Yaan. At start-up and daily Yaan compares the last 14 days of each mapped channel with its graph (`sweep_days`), for what it missed while down; `lore connectors reconcile --days 90` checks once.
- **Forgetting.** `lore forget <id>` removes an item, and `lore erase <person>` a person, everywhere Yaan copied their words (both are dry runs without `--yes`). Cache entries expire after `LORE_CACHE_DAYS` (30).
- **Jira.** A status in Jira's "done" category resolves the issue, in any workflow or language; a resolution like Won't Do dismisses it. `customer_pattern` (a regular expression) says how a ticket names a customer, and `issue_types: [Bug, Incident]` limits which tickets are issues.
- **Collaboration.** Who works with whom (PageRank, communities, connection paths) is rebuilt hourly from threads and conversations in what everyone may read, so a private channel never shows who talks there.
- **History:** a tool's first sync reads its last 30 days (`backfill_days` under the tool in `connectors.yaml`). `lore connectors backfill --days 365` prices a longer reach first (what each tool holds in that window, and the expected and worst-case cost of extracting it) and spends only with `--yes`; the daily spend cap still applies, and what it holds back goes in on later rounds.
- **Set up once:** `lore init` creates the graph, its indexes, the product, the channels and the listed people. It checks both files first and changes nothing if one is wrong. Then start the connector worker (`docker compose --profile app --profile connectors up -d`), or `lore connectors run` as below.
- **The demo stays out.** In org mode, `lore seed`, `lore demo` and `lore connectors seed` refuse, live writes refuse the HERB graphs, and the web UI hides the demo's sample questions, viewers and injections.

`connectors.yaml` and `people.yaml` are mounted into the `api` container, so an edit applies on `docker compose --profile app restart api`. One product per install: everything the connectors read belongs to the product `connectors.yaml` names.

## MCP server (no account needed)
Any MCP client (Claude Code, Claude Desktop, Cursor, your own agent) can use Lore's read-only graph tools and, unless `LORE_MCP_ASK=0`, `ask` for a cited answer.

```powershell
# Claude Code, stdio (runs on your machine against the local graph)
claude mcp add lore -- uv run --project backend lore mcp
# or over HTTP while the API runs: http://127.0.0.1:8000/mcp
```

## Slack (about 10 minutes)
1. Create a free workspace at https://slack.com/get-started (or use a sandbox you own).
2. Go to https://api.slack.com/apps → **Create New App** → **From a manifest** → pick the workspace → paste the manifest below → **Create**.
3. **Basic Information → App-Level Tokens → Generate Token and Scopes**: name it `socket`, add the scope `connections:write`, generate. Copy the `xapp-…` token into `SLACK_APP_TOKEN`.
4. **Install App → Install to Workspace → Allow**. Copy the **Bot User OAuth Token** (`xoxb-…`) into `SLACK_BOT_TOKEN`.
5. In Slack, create the channels `#vizforce-bugs` and `#lore-alerts`, and type `/invite @lore` in each.

```yaml
display_information:
  name: Lore
  description: Company brain on FalkorDB. Answers with sources and flags changes. Demo with synthetic data.
  background_color: "#18202b"
features:
  app_home:
    messages_tab_enabled: true # direct messages to the bot: answers scoped to the asker
    messages_tab_read_only_enabled: false
  bot_user:
    display_name: lore
    always_online: true
oauth_config:
  scopes:
    bot:
      - app_mentions:read
      - channels:history
      - channels:read
      - chat:write
      - chat:write.customize
      - groups:history
      - groups:read
      - im:history
      - im:read
      - im:write
      - users:read
      - users:read.email
settings:
  event_subscriptions:
    bot_events:
      - app_mention
      - message.channels
      - message.groups
      - message.im
  interactivity:
    is_enabled: true
  org_deploy_enabled: false
  socket_mode_enabled: true
  token_rotation_enabled: false
```

Socket Mode means Slack talks to Lore over an outgoing connection, so no public URL is needed.

## GitHub (about 5 minutes)
1. Create a new **private** repository you own, e.g. `lore-demo-sandbox`, with a README so it has a `main` branch. Put `owner/name` into `GITHUB_REPO`.
2. https://github.com/settings/personal-access-tokens/new (fine-grained token): **Repository access → Only select repositories →** the sandbox repo. **Permissions → Repository:** Contents *Read and write* (the seed opens PRs from branches), Pull requests *Read and write*, Issues *Read and write* (Metadata *Read* is added automatically). Expiry: 7 days. Copy it into `GITHUB_TOKEN`.

## Jira Cloud (about 10 minutes)
1. Create a free Jira site at https://www.atlassian.com/software/jira/free. Note the address `https://<site>.atlassian.net` → `JIRA_BASE_URL`.
2. Create a project (Kanban is fine) and put its **key** into `JIRA_PROJECT`, not its name. The key is the prefix of its ticket ids: a project named "viz" whose tickets are KAN-1, KAN-2… has the key `KAN`.
3. https://id.atlassian.com/manage-profile/security/api-tokens → **Create API token** → copy it into `JIRA_API_TOKEN`; your login email goes into `JIRA_EMAIL`.

## Notion (about 5 minutes)
1. https://www.notion.so/profile/integrations → **New integration** (internal), on your workspace. Capabilities: Read content, Insert content, Read comments, Insert comments. Copy the secret into `NOTION_TOKEN`.
2. Create a page named **Lore demo (synthetic)**. Open its **•••** menu → **Connections** → add your integration.
3. Copy the page id (the 32 characters at the end of its URL) into `NOTION_PAGE_ID`.

## What each connector does
| Source | In (to Lore) | Out (approved actions) |
|---|---|---|
| Slack | channel messages, thread replies, edits and deletions → the live pipeline → the Sentinel; `@lore <question>` | answers in the thread with citations; alert cards with Approve / Dismiss; messages to the people a change affects |
| GitHub | pull requests and their reviews (not issues, comments or commits yet) | comments on the PR |
| Jira | tickets and their status changes (each change becomes a decision with who made it, no LLM needed) and comments | comments on the ticket |
| Notion | pages of the demo database as documents, page comments as feedback | comments on the page |

The sandboxes play the team of one HERB product (VizForce): `connectors.yaml` maps each channel, repo, project and page to the product, and each sandbox account to one of its people, so the Sentinel's rules and the authority policy apply unchanged.

## Run the story
With the tokens in `.env` (any subset works; the seed needs Jira and GitHub):

```powershell
uv run --project backend lore demo reset                       # lore_demo := a fresh copy of the HERB graph
uv run --project backend lore connectors status                # which connectors are configured
uv run --project backend lore connectors seed --act setup      # Notion spec, Jira ticket, GitHub fix PR, ticket -> Done
uv run --project backend lore connectors run                   # leave running: polls every 30 s, holds the Slack socket
```

The setup act can run again for another take (after `lore demo reset`): each take uses its own GitHub branch and a new Jira ticket. The loop prints only when something changes; set `PYTHONUNBUFFERED=1` to watch its log live.

Then, for the live moment, either type this in `#vizforce-bugs` or run `lore connectors seed --act regression`:

> [synthetic demo · as Ian Davis (eid_7017b913)] CUST-0003 has reopened 'CSV export times out on large dashboards': the CSV export times out on large dashboards again since this morning's release.

Within a few seconds a **regression** card appears in `#lore-alerts`. **Approve and send** records the decision; the correction and the ticket comment land on the Jira ticket, the ticket comment on the GitHub PR, and the notifications in `#lore-alerts`. Ask `@lore what changed with the CSV export bug, and who should look at it?` in any channel the bot is in.

In Jira and GitHub every item is written by your own account; `connectors.yaml` maps it (`self`) to Hannah Garcia, and the persona label in each text names who the rest is written as.

## Experimental connectors (not run against a live API)
Eight more tools have a connector scaffold. Each was written from the vendor's API reference and tested only against mock responses (`httpx.MockTransport`): **nobody has run them against the real service.** Expect small differences (a field, a scope, a rate limit) the first time you do. They are **off by default**: a connector starts only when **all** its required variables are set in `.env`, so an existing `.env` changes nothing. `lore connectors status` lists what is on.

| Tool | Env vars (required unless marked) | In (to Lore) | Out (approved actions) | Docs |
|---|---|---|---|---|
| Linear | `LINEAR_API_KEY`, `LINEAR_TEAM` (team key, e.g. `ENG`) | issues of the team as bug-channel messages: creation, a completed / canceled state (deterministic events, no LLM), comments | comment on the issue (Markdown) | [GraphQL](https://linear.app/developers/graphql) · [pagination](https://linear.app/developers/pagination) · [filtering](https://linear.app/developers/filtering) · [schema](https://github.com/linear/linear/blob/master/packages/sdk/src/schema.graphql) |
| Confluence Cloud | `CONFLUENCE_BASE_URL`, `CONFLUENCE_EMAIL`, `CONFLUENCE_API_TOKEN`, `CONFLUENCE_SPACE_ID` | pages of one space as documents, plus a share message and footer comments as review messages | footer comment on the page (storage format) | [pages](https://developer.atlassian.com/cloud/confluence/rest/v2/api-group-page/) · [comments](https://developer.atlassian.com/cloud/confluence/rest/v2/api-group-comment/) · [OpenAPI](https://dac-static.atlassian.com/cloud/confluence/openapi-v2.v3.json) |
| GitLab | `GITLAB_TOKEN`, `GITLAB_PROJECT` (id or `group/project`), `GITLAB_BASE_URL` (optional; default gitlab.com) | merge requests as PRs, their human notes as reviews (`COMMENTED`; approvals not read) | note on the merge request (Markdown) | [merge requests](https://docs.gitlab.com/api/merge_requests/) · [notes](https://docs.gitlab.com/api/notes/) |
| Google Drive | `GOOGLE_DRIVE_ACCESS_TOKEN`, `GOOGLE_DRIVE_FOLDER_ID` | Google Docs of one folder (as plain text) plus a share message and comments | comment on the document (plain text) | [files.list](https://developers.google.com/workspace/drive/api/reference/rest/v3/files/list) · [export](https://developers.google.com/workspace/drive/api/reference/rest/v3/files/export) · [comments](https://developers.google.com/workspace/drive/api/reference/rest/v3/comments) |
| Gmail | `GMAIL_ACCESS_TOKEN`, `GMAIL_LABEL` (label id) | mail carrying one label, as messages (subject + plain body) | a reply in the thread, only to a message that came from Gmail | [messages.list](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list) · [get](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/get) · [send](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/send) |
| Google Meet | `GOOGLE_MEET_ACCESS_TOKEN`, `GOOGLE_MEET_SPACE` (`spaces/NAME`) | transcripts of ended conferences as meetings | nothing (the API can't post into a meeting) | [conferenceRecords](https://developers.google.com/workspace/meet/api/reference/rest/v2/conferenceRecords/list) · [transcripts](https://developers.google.com/workspace/meet/api/reference/rest/v2/conferenceRecords.transcripts/list) · [entries](https://developers.google.com/workspace/meet/api/reference/rest/v2/conferenceRecords.transcripts.entries/list) |
| Zoom | `ZOOM_ACCESS_TOKEN`, `ZOOM_USER_ID` (optional; default `me`) | cloud-recording transcripts (WebVTT) as meetings, at most the last 30 days per round | nothing | [recordings](https://developers.zoom.us/docs/api/meetings/#tag/cloud-recording/GET/users/{userId}/recordings) · [OpenAPI](https://developers.zoom.us/api-hub/meetings/methods/endpoints.json) |
| Discord | `DISCORD_BOT_TOKEN`, `DISCORD_CHANNEL_ID` | messages of one channel | a reply to a Discord message (refused if over 2000 characters) | [messages](https://discord.com/developers/docs/resources/message) |

**Auth caveat.** Linear (personal API key), Confluence (API token), GitLab (access token) and Discord (bot token) need no sign-in flow. **Google Drive, Gmail, Google Meet and Zoom use OAuth 2.0, and the OAuth flow is not built:** you must obtain an access token yourself (Google's OAuth Playground, or Zoom's Server-to-Server OAuth app) and paste it in. These tokens last about an hour; after that every call fails loudly with the vendor's 401. Refreshing them is not implemented.

**Maps.** `connectors.yaml` is the same file as for the other tools; add the keys below for each tool you turn on. A missing `channel` stops the connector at its first fetch with a clear message.

```yaml
linear:     { channel: ch-force-d79648, people: { self: eid_9e9883de } }   # Linear user id -> eid
confluence: { channel: ch-vizai-afe877, people: {} }                      # Atlassian account id -> eid
gitlab:     { people: {} }                                                # GitLab username -> eid
gdrive:     { channel: ch-vizai-afe877, people: {} }                      # owner email / commenter display name -> eid
gmail:      { channel: ch-force-d79648, people: {} }                      # sender address (lower case) -> eid
gmeet:      { people: {} }                                                # "users/123" or guest display name -> eid
zoom:       { people: {} }                                                # speaker display name -> eid
discord:    { channels: { "<discord channel id>": ch-force-d79648 }, people: {} }   # Discord user id -> eid
```

**Limits to know.**
- Google Meet and Zoom items are `meeting` artifacts, and the live path takes only message, pr and doc today: it refuses them with a clear error until `LIVE_KINDS` is widened.
- Like the other connectors, everything read must start with "[synthetic demo]"; other items are reported as refused. Point these at sandboxes you own.
- Sending is real: Gmail replies go to the sender of the message, Discord replies and the comment-style writes post to the live service. Use throwaway accounts.
- Run the unit tests (no network) with `uv run --project backend pytest backend/tests -k "connector" -m "not db"`.

