# Yaan landing page

A static page (`index.html`, `styles.css`, `main.js`, `assets/`) with no build step and no backend. It never calls Yaan's API.

## Preview locally
```
python -m http.server 8765 --directory site
```
Open http://localhost:8765. Add `?theme=dark` or `?theme=light` to force a theme. Add `?motion=full` to see all motion on a machine with animations turned off (Windows: Settings > Accessibility > Visual effects > Animation effects).

## Deploy on Vercel
Live at https://yaan-brain.vercel.app/.

Import the repo, set **Root Directory** to `site`, framework preset **Other**, no build command. `vercel.json` sets clean URLs and asset caching.

## Links
The repo is https://github.com/t-maypat/yaan. The self-hosting guide (`docs.html`, step 4) loads the demo graph from the repo's latest release, so the release must carry `lore-snapshot.tar.gz` (from `snapshots/`).

## Protecting a public deployment
The site is static, so a swarm can only use bandwidth (Vercel's DDoS mitigation covers it; turn on Attack Challenge Mode in the Vercel firewall if needed). The **app's API** is what costs money. On a public URL set `LORE_LLM_CACHE_ONLY=1` (answers replay from the cache, never call the API), keep the per-IP, global and concurrency limits on (`.env.example`, D081), and set `FORWARDED_ALLOW_IPS` behind a proxy.

## What's real on the page
- The hero console replays three answers built from Lore's actual tool output (`decision_lineage`, `rank`, `who_knows`) on HERB's synthetic ExplainabilityForce product. Every quote is verbatim from the artifact named next to it. The tool times are the measured p50 per tool from `eval/report.md`.
- The "same question, any day" panel uses the real decision chain of 'Inefficient Caching Strategy' (reported 6 Feb, resolved 24 Feb, reopened 26 Feb 2027).
- The Approve button in "How it works" is a replay and sends nothing.
- Numbers come from the README and `eval/report.md`: held-out products first, dev beside them. The stale-answer figures are the dev drift set.
