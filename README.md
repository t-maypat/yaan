<div align="center">

<img src="site/assets/favicon.svg" width="72" height="72" alt="Yaan">

# Yaan

**The company brain that knows what's still true.**

Yaan turns your team's chats, meetings, pull requests, tickets and docs into a time-aware graph of people, work and decisions.
Ask what's true now, what was true then, why, and who knows, with a word-for-word source for every sentence.

[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Self-hosted](https://img.shields.io/badge/self--hosted-Docker%20Compose-2496ED?logo=docker&logoColor=white)](#quickstart)
[![Python 3.11](https://img.shields.io/badge/python-3.11-3776AB?logo=python&logoColor=white)](backend/pyproject.toml)
[![Graph: FalkorDB](https://img.shields.io/badge/graph-FalkorDB%206.0-1d6a86)](https://github.com/FalkorDB/FalkorDB)
[![MCP server](https://img.shields.io/badge/MCP-server-111111)](#use-it-from-your-ai-client)
[![GitHub stars](https://img.shields.io/github/stars/t-maypat/yaan?style=flat&logo=github)](https://github.com/t-maypat/yaan/stargazers)

[Website](https://yaan-brain.vercel.app) · [Self-hosting guide](https://yaan-brain.vercel.app/docs) · [Demo video](DEMO_VIDEO.md) · [Evaluation report](eval/report.md) · [Contributing](CONTRIBUTING.md)

</div>

---

## Why Yaan

Search finds the message that said "fixed". It doesn't know the bug came back. Yaan keeps every fact with the time it was true, who decided it and the quote that proves it, in a [FalkorDB](https://github.com/FalkorDB/FalkorDB) graph.

- **True now, and true then.** Each status is a decision with a start and an end. "Is it fixed?" and "was it fixed on 7 January?" are one lookup. Nothing is deleted: a new fact closes the old one's window.
- **Answers that live in the links.** "Which company has the most unresolved bugs?" is a count over customers, companies and issues, not a guess from ten text chunks.
- **Who really knows.** Authorship, PageRank, betweenness and communities, computed natively in FalkorDB, find the real expert behind the listed owner.
- **A quote or nothing.** Every sentence cites a word-for-word quote from its source, checked in code before you see it. With no quote, Yaan says "not found". The check proves the words are in the source and on topic; it does not prove the sentence follows from them.
- **What it tracks today.** The decisions Yaan folds are engineering ones: the status of each issue (reported, resolved, dismissed, reopened) and what the team said to do with each pull request (merge, close, hold, revert), plus who knows what. Other kinds of decisions are on the roadmap.
- **Runs on your servers.** Your data stays in your network; the only outside call is to the AI model you choose, with your own key and a hard daily spend cap.

## Quickstart

You need Docker with Compose. Browsing works without an API key; asking new questions needs an [Anthropic key](https://console.anthropic.com).

```sh
git clone https://github.com/t-maypat/yaan && cd yaan
cp .env.example .env          # set the three FALKORDB_*_PASSWORD values; ANTHROPIC_API_KEY is optional
docker compose --profile app up -d --build
```

**Optional: load the demo graph.** A ready-made graph of a synthetic company from Salesforce AI Research's [HERB](https://huggingface.co/datasets/Salesforce/HERB) benchmark (CC BY-NC 4.0), the data Yaan is tested and evaluated on. It needs no API key and is the fastest way to see Yaan answer.

```sh
docker compose --profile app run --rm api yaan seed --from https://github.com/t-maypat/yaan/releases/latest/download/lore-snapshot.tar.gz
```

Open **http://127.0.0.1:8000**: Ask, Inbox, Timeline, Graph health. Yaan and FalkorDB listen on 127.0.0.1 only; put an HTTPS proxy in front to share it with your team ([guide](https://yaan-brain.vercel.app/docs#secure)).

> Yaan was called Lore while it was built: settings (`LORE_…`), container names and some files still use that name, and `lore` works as the command too.

## Use your own data

Org mode points Yaan at your team's tools instead of the demo. Your data goes into its own graph, and nothing from the demo is mixed in.

1. In `.env`, set `LORE_DATA_MODE=org` and each tool's token.
2. `cp connectors.org.example.yaml connectors.yaml`, then name your product and the channels Yaan reads. A channel's name starts with `bug-`, `develop-` or `planning-`, which tells Yaan what to read from it. Say who may read each non-Slack source (`visibility: everyone`, or `members`); Slack's private channels and their members are read from Slack. A channel that holds both bugs and pull-request talk can say so (`{channel: eng, kind: mixed}`), and a tool or channel can belong to another product (`product:`).
3. List the people who will use Yaan in [`people.yaml`](people.yaml), with their email, role and account in each tool. Yaan registers everyone else the first time they appear in a tool, but only listed people can sign in.
4. Create the graph and start the connectors:

```sh
docker compose --profile app up -d --force-recreate api
docker compose --profile app exec api yaan init                 # your graph, product, channels and people
docker compose --profile app --profile connectors up -d          # the connector worker: Slack over Socket Mode, the rest polled every 30 s
```

5. Sign people in. In org mode every API, web UI and `/mcp` request answers as a signed-in person, who sees only the sources they may see, and approvals are recorded as theirs:

```sh
docker compose --profile app exec api yaan token issue "Priya Shah"   # prints her token once; paste it into the web UI
docker compose --profile app exec api yaan token revoke "Priya Shah"
```

   Behind a single-sign-on proxy (oauth2-proxy, Cloudflare Access), set `LORE_AUTH=header` instead: the proxy sends the user's email in `X-Forwarded-Email` and `LORE_AUTH_PROXY_SECRET` in `X-Lore-Proxy-Secret`. In a Slack channel, `@yaan` answers from what everyone in that channel may read; a direct message to the bot answers from everything the asker may read.

| Tool | In | Out, once you approve |
|---|---|---|
| Slack | messages, `@yaan` questions | cited answers, alerts, Approve / Dismiss |
| GitHub | pull requests, reviews, issues | comments on the PR or issue |
| Jira | tickets, status changes, comments | comments on the ticket |
| Notion | pages, page comments | comments on the page |

Linear, GitLab, Confluence, Google Drive, Gmail, Google Meet, Zoom and Discord have experimental connectors. Step-by-step setup: [connectors guide](backend/app/connectors/README.md).

## Use it from your AI client

Yaan is an MCP server: Claude Code, Codex, Cursor or any MCP client gets its 16 read-only graph tools plus `ask`.

```sh
claude mcp add --transport http yaan http://127.0.0.1:8000/mcp
```

## How it works

1. **Ingest.** Every source goes through one pipeline: normalize, build the structural graph (people, teams, channels, PR chains), chunk and embed locally, link ids and names, then extract events with an LLM. Every extracted quote must match its message exactly.
2. **Fold decisions.** Verified events are kept in the graph and never deleted; claims, decisions and issues are folded from them, product by product, rewriting only what changed. A policy decides who may change what (assignees, QA, leads). Refused claims stay visible next to the decision they tried to change, and `yaan belief <issue or PR> --at <date>` replays what Yaan knew at an earlier moment.
3. **Derive.** Expertise, collaboration, communities, PageRank, betweenness, truck factor and precedents.
4. **Answer.** A plain tool loop over 17 read-only tools, each one `.cypher` file, with a citation validator and viewer-scoped redaction.
5. **Watch.** The Change Sentinel flags regressions, reversals, unauthorized changes and key-person risks, drafts follow-ups from the blast radius, and records your Approve as a decision.

Diagrams: [architecture](docs/diagrams/lore-overview.svg) · [graph data model](docs/diagrams/data-model.svg).

### Graph data model

| | Nodes | Key relationships |
|---|---|---|
| What is true, since when | `Event` (what was said, `observed_ms`, `retracted_ms`), `Decision` (value, `valid_from_ms`, `valid_to_ms`), `Claim`, `Issue`, `Area`, `Alert`, `Action` | `SUPERSEDES`, `DECIDED_BY`, `EVIDENCED_BY {quote}`, `AFFECTS`, `CONFLICTS_WITH`, `FLAGS` |
| Who | `Person`, `Team`, `Customer`, `Company`, `Community` | `AUTHORED`, `REVIEWED`, `KNOWS {score}`, `COLLAB {w}`, `WORKS_AT`, `REPORTED_BY`, `ASSIGNED_TO` |
| What was said | `Message`, `Meeting`, `Doc`, `PR`, `Link` (all `:Artifact`), `Chunk` (384-d vector), `Channel`, `Product` | `IN_PRODUCT`, `IN_CHANNEL`, `MENTIONS`, `FIXES`, `HAS_CHUNK` |

Every property: [docs/schema_card.md](docs/schema_card.md). The whole graph exports to plain JSON lines (`yaan export --out graph.jsonl.gz`, one node or edge a line) and loads back with `yaan import`.

### Cypher queries and algorithms

FalkorDB features Yaan depends on: Cypher over a property graph, a **full-text index** (stemmed) and an **HNSW vector index** fused in one search, native **`algo.pageRank`**, **`algo.betweenness`**, **`algo.labelPropagation`** and **`shortestPath`**, `GRAPH.RO_QUERY` under a read-only ACL user for generated Cypher, per-query timeouts, and `GRAPH.COPY` for a resettable demo graph.

| Query | What it answers |
|---|---|
| [`states_at`](backend/queries/states_at.cypher) | the decisions in force at one moment: the as-of view |
| [`decision_chain`](backend/queries/decision_chain.cypher) | every state of a topic, who decided it, the evidence, refused claims |
| [`rank_companies`](backend/queries/rank_companies.cypher) | companies by the issues their customers reported, with evidence |
| [`impact_of`](backend/queries/impact_of.cypher) | the blast radius of one decision |
| [`area_experts`](backend/queries/area_experts.cypher) | who knows an area: authorship, recency, PageRank |
| [`person_pagerank`](backend/queries/person_pagerank.cypher) · [`person_communities`](backend/queries/person_communities.cypher) | influence and real working groups over who works with whom |
| [`connection_path`](backend/queries/connection_path.cypher) | the shortest chain of collaborators between two people |
| [`find_missing`](backend/queries/find_missing.cypher) | what isn't there: unfixed issues, unreviewed PRs, single-author areas |

All 241 queries, one parameterized statement per file, each with a fixture test: **[docs/queries.md](docs/queries.md)**.

## Results

On 7 [HERB](https://arxiv.org/abs/2506.23139) products never looked at while building (399 questions, run once):

| System | Score (held-out) | Unanswerable declined | Quotes verified |
|---|---|---|---|
| Plain RAG baseline | 9.6 | 80.0% | 98.5% |
| Yaan without graph tools | 24.2 | 81.9% | 100% |
| **Yaan** | **63.6** | 76.7% | **100%** |
| *HERB paper's best agent (GPT-4o), for context* | *43.4* | *29.3%* | |

Removing the graph tools drops the same agent from 63.6 to 24.2: a paired difference of 39.4 points, 95% interval 32.6–46.2. Graph lookups take 3–28 ms each; an answer costs about $0.003. Method, ablations, the stale-answer test and error analysis: [eval/report.md](eval/report.md).

## Built on FalkorDB

[![FalkorDB](https://img.shields.io/github/stars/FalkorDB/FalkorDB?style=social&label=FalkorDB)](https://github.com/FalkorDB/FalkorDB)

Yaan's graph runs on [FalkorDB](https://www.falkordb.com), the low-latency graph database built on sparse matrices. Every answer, alert and ranking above is a FalkorDB query; thanks to the FalkorDB team for the engine and for Graph Hacks.

## Contributing

Issues and pull requests are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md): setup, tests, the Cypher rules FalkorDB needs, and how to add a query.

## License and data

Code: [Apache-2.0](LICENSE). Data: [HERB](https://huggingface.co/datasets/Salesforce/HERB) by Salesforce AI Research, a public, **synthetic** enterprise dataset under **CC BY-NC 4.0** ([paper](https://arxiv.org/abs/2506.23139)); the demo snapshot redistributes HERB-derived data under the same license, with attribution. The demo uses no real company's or person's data, and its items are labelled `[synthetic demo …]`.

<details>
<summary><b>Graph Hacks submission notes</b></summary>

Built for **Graph Hacks: Context for AI Agents** (WeMakeDevs × FalkorDB): **Track 03 Company Brain** (primary) and **Track 01** (agents acting on connected data).

**FalkorDB's five context-graph challenges**
- **Data quality:** no LLM output enters the graph unverified (487/488 extracted quotes matched on held-out); a policy decides who may change a status; a health report counts isolated nodes, duplicate ids and unresolved mentions.
- **Scale and latency:** product-scoped, index-first queries; bounded paths, `LIMIT` on every read, 2 s timeouts; 5–27 ms p50 per tool on 38.5k nodes and 96k edges.
- **Explainability:** a verbatim, code-checked quote per sentence; the UI shows the tool walk, each step's `.cypher`, the evidence subgraph and the decision timeline.
- **Privacy and multi-tenancy:** viewer-scoped answers redact sources before the model reads them, and derived facts (an issue, a decision) are visible only if one of their sources is; with your own data every request answers as a signed-in person; generated Cypher runs as a read-only ACL user.
- **Freshness:** a new message, PR, ticket or page is in the graph and judged by the Sentinel in 1.5–12 s.

**Track 01:** the Sentinel drafts follow-ups from the blast radius and sends them to Jira, GitHub, Notion and Slack once approved; `rank`, `who_knows`, `find_missing`, `impact_of` and `connection_path` are graph algorithms exposed as agent tools, also over MCP.

</details>

<sub>Built with Claude Code as a coding assistant; every query, schema decision and evaluation was reviewed and tested by the author. Commits made with it carry a `Co-Authored-By` trailer.</sub>
