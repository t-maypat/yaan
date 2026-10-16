# frontend/

Lore's web UI (Phase 12): Vite + React 19 + TypeScript + Tailwind 4 + Cytoscape.js. How it works: [docs/learn/12-web-ui.md](../docs/learn/12-web-ui.md); decisions: D052.

## Run it (from the repo root)
1. API: `uv run --project backend uvicorn app.main:app --reload` (port 8000; FalkorDB up).
2. Once: `npm --prefix frontend install`.
3. `npm --prefix frontend run dev` → http://localhost:5173. Vite proxies the API paths to :8000 (set `LORE_API` to change it).
4. Type check + production build: `npm --prefix frontend run build` → `frontend/dist/`.

## What's here
- `src/api.ts`: the API client; reads the `/ask` SSE stream with fetch.
- `src/App.tsx`: one run's state; the product picker and the suggested questions (`src/suggestions.ts`, HERB dev questions only).
- `src/components/`: `Walk` (tool steps + the Cypher dialog), `Answer` (`[n]` markers + hover popover), `Sources` (quote cards, read in context), `EvidenceGraph` (Cytoscape).
- `src/index.css`: the color and type tokens, light and dark.

Done: the Ask pane and the evidence graph. Still to come: the decision Timeline with the as-of slider (after tool 13), the Inbox (with the Sentinel), and the Graph Health and Eval pages. No signup and no viewer API key.
