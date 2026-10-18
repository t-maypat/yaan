# Lore

**The company brain that knows what's still true.** Lore turns a company's chats, meetings, PRs and docs into a time-aware [FalkorDB](https://www.falkordb.com) graph of people, work and decisions. An agent can then say what's true now, why, who knows, and what breaks when it changes, with a verbatim source for every claim.

Graph Hacks: Context for AI Agents (WeMakeDevs × FalkorDB) · Track 03 Company Brain · Track 01

> **Status:** pre-event scaffold. The build plan is in [docs/PLAN.md](docs/PLAN.md).

## Development quickstart
Requires Docker and [uv](https://docs.astral.sh/uv/).

```sh
cp .env.example .env            # then set FALKORDB_TAG to a pinned release
docker compose up -d            # FalkorDB; Browser on http://localhost:3000
uv sync --project backend
uv run --project backend pytest backend -m "not db"
```

On Linux/macOS, `make setup`, `make up`, `make test` and friends wrap the same commands.

## Data
[HERB](https://huggingface.co/datasets/Salesforce/HERB) (Salesforce AI Research) is a public, synthetic enterprise dataset licensed CC BY-NC 4.0. Lore uses no private or personal data.

---
<sub>Built with AI assistance (Claude Code). See [docs/ai-usage.md](docs/ai-usage.md).</sub>
