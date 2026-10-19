# Contributing to Yaan

Thanks for helping. Bug reports, fixes, new connectors, queries and docs are all welcome. This page covers how to set up, the rules the code follows, and how a change gets merged.

## Ground rules

- **No real data.** Yaan is developed and tested on [HERB](https://huggingface.co/datasets/Salesforce/HERB), a public synthetic dataset (CC BY-NC 4.0), plus demo items labelled `[synthetic demo …]`. Never commit a real company's or person's data, tokens or `.env`.
- **Fail loudly.** No silent fallbacks or canned output: when something can't be done, raise or say so.
- **Every answer sentence cites a verbatim quote** from a source a tool returned. Keep the citation validator strict.
- **FalkorDB is load-bearing.** A feature that would work just as well without the graph is rarely the right next step.

## Set up

You need Python 3.11, [uv](https://docs.astral.sh/uv/), Docker with Compose, and Node 20+ for the web UI. Run every command from the repository root.

```sh
cp .env.example .env                 # set the three FALKORDB_*_PASSWORD values
uv sync --project backend            # Python environment
docker compose up -d                 # FalkorDB (+ Browser at http://localhost:3000)
uv run --project backend uvicorn app.main:app --reload    # API on :8000
npm --prefix frontend install && npm --prefix frontend run dev   # UI on :5173
```

`uv run --project backend lore --help` lists the CLI. To work against the demo graph, restore the snapshot with `lore seed --from <release URL>` (see the README's Quickstart).

## Test and lint

```sh
uv run --project backend pytest backend                 # everything (needs FalkorDB running)
uv run --project backend pytest backend -m "not db"     # without a database
uv run --project backend ruff check backend
```

Tests marked `db` need FalkorDB; tests marked `model` load the local embedding model. A pull request should keep the whole suite green.

## Writing Cypher for FalkorDB

Queries live in `backend/queries/<name>.cypher`: one parameterized statement per file, first line `// <name>.cypher — <purpose>`, plus a fixture test. `backend/tests/test_query_lint.py` enforces the mechanical rules. After adding or renaming a query, regenerate the list with `uv run --project backend python scripts/query_table.py`.

FalkorDB 6 is not Neo4j. The rules that matter most:

- Compare ids, never nodes (`a.id = b.id`, not `a = b`).
- No label alternation `(n:A|B)`: use the umbrella label (`:Entity`, `:Artifact`) or `UNION`. Relationship alternation `[:A|B]` is fine.
- No regex `=~`: use `STARTS WITH`, `ENDS WITH`, `CONTAINS`, `toLower()`.
- Times are integer epoch ms (`*_ms`, UTC); there's no temporal arithmetic.
- Bounded variable-length paths only (`[*..4]`), `LIMIT` on every read, a timeout on every read.
- `shortestPath()` only in `WITH` / `RETURN`, never in `MATCH p = …`.
- Writes ignore the per-query timeout and roll back in full after 10 s: batch them with `UNWIND $rows AS r MERGE …`, and use the same label set in every `MERGE` of a type.
- Vectors are written as `vecf32(…)`; the vector score is a cosine distance (lower is closer).
- Full-text queries are built from keywords, never a raw question.
- Indexes are declared in `00_schema.cypher` and applied and checked by `lore schema`.

`backend/tests/test_falkordb_contract.py` pins the engine behaviour these rules come from; if you find another quirk, add a contract test for it.

## Code conventions

- Python 3.11, FastAPI, Pydantic v2, the `falkordb` client, the `anthropic` SDK. No LangChain or LangGraph.
- Every LLM call goes through `backend/app/llm.py` (cached, schema-validated, quotes checked by substring match). The LLM extracts; `app/policy.py` decides who may change what.
- Agent tools are read-only. Generated Cypher runs only through the guarded `graph_query` tool (read-only user, `GRAPH.RO_QUERY`, `LIMIT` ≤ 200, 2 s timeout).
- Ids are stable strings (`msg:…`, `pr:…`, `feat:<product>:<slug>`). Facts are bi-temporal: never delete a superseded fact, close its `valid_to_ms` instead.
- Add a dependency only where it's needed: `uv add --project backend <package>`.

## Connectors

A connector lives in `backend/app/connectors/<tool>.py`: `fetch` turns the tool's changes into artifacts, `post` delivers an approved action. Test it against recorded API payloads with `httpx.MockTransport`, never a live account, and document its setup in `backend/app/connectors/README.md`.

## Pull requests

1. Open an issue first for anything larger than a fix, so we can agree on the approach.
2. Keep each pull request to one change, with tests.
3. Write the commit message as `Area: what changed`, with a body that says why.
4. Make sure `pytest` and `ruff` pass, and that `scripts/query_table.py --check` is clean if you touched queries.

## Security

Please don't open a public issue for a vulnerability. Report it privately through GitHub's [security advisories](https://github.com/t-maypat/yaan/security/advisories/new).

## License

By contributing, you agree that your contributions are licensed under the [Apache-2.0 License](LICENSE).
