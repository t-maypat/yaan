# Lore landing page

A static page (`index.html`, `styles.css`, `main.js`, `assets/`) with no build step and no backend. It never calls Lore's API. Content follows [docs/landing-brief.md](../docs/landing-brief.md).

## Preview locally
```
python -m http.server 8765 --directory site
```
Open http://localhost:8765. Add `?theme=dark` or `?theme=light` to force a theme. Add `?motion=full` to see all motion on a machine with animations turned off (Windows: Settings > Accessibility > Visual effects > Animation effects).

## Deploy on Vercel
Import the repo, set **Root Directory** to `site`, framework preset **Other**, no build command. `vercel.json` sets clean URLs and asset caching.

## Before submission
Replace the placeholders in `index.html`:
- `{VIDEO_URL}`: the 3-minute demo video
- `{REPO_URL}`: the GitHub repo (also used for `#cypher-queries-and-algorithms` and `/blob/main/eval/report.md`)
- `{BLOG_URL}`: the blog post
- `{APP_URL}`: the deployed app (the API's Docker image serves the built UI). Build the app with `VITE_SITE_URL=<this site>` to get its "About Lore" link back here.

```
sed -i 's#{VIDEO_URL}#https://…#g; s#{REPO_URL}#https://github.com/…#g; s#{BLOG_URL}#https://…#g; s#{APP_URL}#https://…#g' site/index.html
```

## Protecting a public deployment
The site is static, so a swarm can only use bandwidth (Vercel's DDoS mitigation covers it; turn on Attack Challenge Mode in the Vercel firewall if needed). The **app's API** is what costs money. On a public URL set `LORE_LLM_CACHE_ONLY=1` (answers replay from the cache, never call the API), keep the per-IP, global and concurrency limits on (`.env.example`, D081), and set `FORWARDED_ALLOW_IPS` behind a proxy.

## What's real on the page
- The hero console replays three answers built from Lore's actual tool output (`decision_lineage`, `rank`, `who_knows`) on HERB's synthetic ExplainabilityForce product. Every quote is verbatim from the artifact named next to it. The tool times are the measured p50 per tool from `eval/report.md`.
- The "same question, any day" panel uses the real decision chain of 'Inefficient Caching Strategy' (reported 6 Feb, resolved 24 Feb, reopened 26 Feb 2027).
- The Approve button in "How it works" is a replay and sends nothing.
- Numbers come from the README and `eval/report.md`: held-out products first, dev beside them. The stale-answer figures are the dev drift set.
