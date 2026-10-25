# Yaan evaluation report

What we measured, how, and what the numbers do and don't show. Every figure here comes from a results file in [`eval/results/`](results/) (the table: [`results/table.md`](results/table.md), rebuilt by `yaan eval-report`).

> **Name:** every run here was made while Yaan was called Lore, so its run folders, results files and `eval/heldout_runs.log` say `lore` / `lore-search`; the tables read them as `yaan` / `yaan-search` (D144). The numbers are unchanged.

> **Status (18 Oct, final; intervals added 19 Oct):** the held-out run is done, once, for all three systems (§3a; `eval/heldout_runs.log` has one opening per system); **held-out is the headline**, with 95% intervals in §3b. §3 keeps the dev results the system was built against.

## 1. The benchmark
[HERB](https://huggingface.co/datasets/Salesforce/HERB) (Salesforce AI Research, CC BY-NC 4.0) is a synthetic company: 30 products, each with Slack channels, meeting transcripts, documents, pull requests and shared links, plus questions with ground truth.
- **Answerable** questions come in four kinds: Content (free text, judged on a 0–100 scale), People (employee ids), Customer (company names) and Artifact (PR or document links). People, Customer and Artifact are scored by F1 against the ground-truth list.
- **Unanswerable** questions have no answer in the data. A good system declines them.
- **Avg** is the mean of the four answerable categories, as in the HERB paper.

**Split (D017, `eval/split.json`):** 3 dev products (CollaborateForce, ExplainabilityForce, VizForce: 96 answerable + 104 unanswerable questions) and 7 held-out test products drawn with a fixed seed. Test questions can only be loaded inside `split.heldout_run()`, which logs every opening to `eval/heldout_runs.log` before anything is read. They have never been read, printed or tuned on (rule 3).

**Judge (D035):** HERB's own prompts, run with Claude Haiku 5.5 instead of HERB's GPT-4o. The judge sees the question, the ground truth and the answer, never the sources.

## 2. Systems
Each row is one change away from the next, so the gap between two rows is what that change is worth.

| System | What it is |
|---|---|
| **baseline** | Hybrid RAG on the same FalkorDB search Yaan uses (full-text + vector, fused): the top 20 chunks with their authors, then one model call |
| **yaan-search** | Yaan's agent loop, citation validator and prompt, with only `search_artifacts` and `get_artifact`: Yaan without the graph |
| **yaan** | The full agent: 17 tools that walk the graph (decision lineage with validity windows, as-of status, counts computed in Cypher, absences, expertise, blast radius, connection paths, guarded text-to-Cypher) |
| **yaan-unvalidated** | The same run as `yaan`, scoring the model's first answers as written: no citation validator, no fixed abstention line (D069, D076) |

All systems: Claude Haiku 5.5 for answers (effort medium); product-scoped retrieval; 10-product graph (3 dev + 7 held-out ingested, artifacts only).

## 3. Dev results (17 Oct, run `20261017T1729Z`)

| System | Content | People | Customer | Artifact | **Avg** | Unanswerable declined | Answerable abstained | Quotes verified | p50 / p95 answer | Tokens / answer | Cost (200 q) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline | 12.8 | 27.6 | 0.0 | 18.2 | **14.7** | 73.1% | 68.8% | 97.9% | 5.2 / 9.9 s | 4,249 | $0.14 |
| yaan-search | 18.8 | 44.3 | 0.0 | 36.8 | **25.0** | 81.7% | 41.7% | 100% | 13.2 / 21.1 s | 2,001 | $0.58 |
| **yaan** | **73.9** | **88.4** | **88.4** | **82.2** | **83.2** | **87.5%** | 4.2% | **100%** | 9.4 / 19.5 s | 1,633 | $0.58 |
| yaan-unvalidated | 72.7 | 89.1 | 88.4 | 80.4 | 82.6 | 42.3% | 3.1% | — | (same run) | | |
| *HERB paper, best product-specific agent (GPT-4o ReAct)* | | | | | *43.4* | *29.3%* | | | | | |

The HERB row is the published result for a different model and setup: context, not a head-to-head.

**What the gaps say:**
- **The graph is most of the score:** 25.0 → 83.2 between yaan-search and yaan, with everything else the same. Customer questions go from 0 to 88.4: bug reports name customer ids like `CUST-0026`, and only the graph links an id to a customer to a company.
- **The agent loop alone is worth ~10 points** (14.7 → 25.0), and its abstentions are far fewer on answerable questions.
- **The validator's job is honesty, not points** (Avg 82.6 vs 83.2). It found a problem in 33% of first answers; one repair round fixed most; 19 unsupported sentences were dropped; 21 url lines were cited automatically; 1 answer became an abstention because nothing provable was left.
- **The unanswerable gap (42.3% → 87.5%) is mostly the abstention line, not the validator (correction, D076).** The model itself chose to abstain in all 91 declines. In 47 of them its own text explained near-misses ("No Product Manager reviewed it; the feedback came from…"), which HERB's judge counts as an attempt. Yaan replaces an abstention's text with a fixed line: "Not found in Yaan's graph: no evidence answers this question. Searched with: …".

**Paired view (D043):** a single run of a system moves by a few Avg points from sampling alone (Haiku 5.5 takes no temperature). The baseline scored 16.4 and 13.8 on two runs that differed only in search. Read 25 → 83 as large, not as precise.

## 3a. Held-out results (18 Oct, runs `20261018T1301Z` yaan, `T1315Z` baseline, `T1321Z` yaan-search)
The 7 test products (189 answerable + 210 unanswerable questions), never read while building. Agent, prompts, tools and graph frozen at commit `4946721`; one run per system (D079).

| System | Content | People | Customer | Artifact | **Avg** | Unanswerable declined | Answerable abstained | Quotes verified | p50 / p95 answer | Tokens / answer | Cost (399 q) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline | 12.6 | 13.3 | 0.0 | 12.6 | **9.6** | 80.0% | 79.9% | 98.5% | 4.8 / 8.9 s | 3,931 | $0.30 |
| yaan-search | 17.7 | 37.6 | 0.0 | 41.5 | **24.2** | 81.9% | 47.6% | 100% | 13.0 / 19.8 s | 1,926 | $1.07 |
| **yaan** | **64.0** | **65.2** | **54.2** | **71.3** | **63.6** | 76.7% | 8.5% | **100%** | 9.7 / 19.9 s | 1,690 | $1.12 |
| *HERB paper, best product-specific agent (GPT-4o ReAct)* | | | | | *43.4* | *29.3%* | | | | | |

**What it says:**
- **The graph is still most of the score:** 24.2 → 63.6 between yaan-search and yaan, the same gap in kind as on dev (25.0 → 83.2). Search-only systems still score 0 on Customer questions.
- **Yaan loses 20 points from dev to held-out** (83.2 → 63.6), most in Customer (88.4 → 54.2) and People (88.4 → 65.2). The fixes of §6 were found by reading dev answers; this is the part that didn't carry over to new products. The held-out questions stay unread, so we report the gap and don't analyze it per question.
- **The systems without the graph barely move** (25.0 → 24.2, 14.7 → 9.6): new products aren't harder in general; the drop is in what Yaan learned from dev.
- **Declining unanswerables dropped** (87.5% → 76.7%), and Yaan abstained on more answerable questions (4.2% → 8.5%).
- **Validator:** a problem in 40% of first answers, repaired in one round; 33 sentences dropped, 26 url lines cited automatically, 1 answer turned into an abstention. Every one of 1,009 citations quotes its source word for word.
- **Latency:** FalkorDB p50 per tool 3–28 ms (`search_artifacts` 27 ms, `list_issues` 14 ms, `rank` 5 ms); the answer's seconds are the model's turns.
- **A fault on the way, no effect on scores:** the baseline's judging crashed when one thread read the spend ledger while another rewrote it. Its 399 saved answers were judged again with `--score-only` (no new held-out opening); the ledger now reads under the lock and writes atomically (D079).

## 3b. How sure: 95% intervals (19 Oct, D103)
Bootstrap over questions (10,000 resamples within each category, so every resample keeps the run's mix), recomputing Avg and the declined share exactly as the tables do; the point estimates below match them. Two systems answered the same questions, so their difference is resampled *paired*. From the judged runs, no new answers or judging ($0): `python -m app.eval.bootstrap`, results in [`results/bootstrap.json`](results/bootstrap.json). Only scores are read, never question text.

| Held-out (399 q) | Avg | 95% interval | Unanswerable declined | 95% interval |
|---|---|---|---|---|
| **yaan** | **63.6** | 57.8–69.4 | 76.7% | 71.0–82.4 |
| yaan-search | 24.2 | 19.6–28.8 | 81.9% | 76.7–86.7 |
| baseline | 9.6 | 6.7–12.9 | 80.0% | 74.3–85.2 |

| Paired difference (Avg) | Dev (200 q) | Held-out (399 q) |
|---|---|---|
| yaan − yaan-search: the graph tools | +58.2 (51.0–65.4) | **+39.4 (32.6–46.2)** |
| yaan − baseline | +68.5 (62.0–74.9) | +54.0 (48.0–59.9) |

**What it says:**
- **The graph tools are worth about 40 points on unseen products, and the interval is far from zero.** The dev gap (+58) overstates it: yaan-search barely moves between splits, Yaan loses 20 (§3a).
- **Declining unanswerables does not separate the systems on held-out:** the three intervals overlap. Yaan's dev lead there (87.5% vs 81.7%) didn't carry over; it isn't claimed.
- **What the intervals don't cover:** they resample questions, not the model. One run per system (§7): run-to-run spread from sampling (no temperature on Haiku 5.5) comes on top.

## 3c. Is the judge fair? (19 Oct, D103)
The judge is Haiku 5.5, the same model family as the answers, so it was checked two ways on the dev run ($0, no new judging):
- **ID and link questions, every one (53):** the judge only extracts the ids or links an answer gives; F1 is computed from those. Against a regex over the same answers, the judge **never extracted an id or link that wasn't in the answer**. In 19 answers it left some out, as HERB's prompt asks (links named in reasoning, a person the answer itself says isn't a reviewer). One clear miss: an answer giving both expected PR links plus one more scored 0 instead of 0.8. Counting every link instead would lower the url score (82.2 → 73.5): the leftovers are extra links that cost precision.
- **Content questions, every one (32):** read against the ground truth by a second reader, Claude Opus 5.5 in the coding session (a larger model, not a human), and scored 0–100 the same way. Mean: judge 73.9, second reading 79.2; within 10 points on 27; the judge was **lower** by more than 10 on 5 (for example 62 for an answer with all three expected changes) and never higher. Per answer: [`results/spotcheck_dev.json`](results/spotcheck_dev.json).

So the shared model family doesn't show up as leniency: the judge, if anything, under-scores. A human pass on a sample would still be the stronger check.

## 3d. Dev re-run with the grounding validator (19 Oct, run `20261019T1148Z`, D099)
The citation validator now also asks that a quote back its sentence (at least 3 words, a word or id in common, every id in a cited record), not only that it appear in the source. The same agent, prompts and tools, re-run on dev:

| | Content | People | Customer | Artifact | **Avg** | Unanswerable declined | Answerable abstained | Sentences dropped |
|---|---|---|---|---|---|---|---|---|
| 17 Oct | 73.9 | 88.4 | 88.4 | 82.2 | 83.2 | 87.5% | 4.2% | 19 |
| 19 Oct | 78.3 | 89.7 | 83.4 | 81.8 | **83.3** | 91.3% | 2.1% | 12 |

- **The stricter check costs no score:** the paired difference is +0.1 (95% interval −6.6 to +5.8); the category moves (Content up, Customer down) are within one run's sampling spread.
- **The validator found a problem in 35% of first answers and the repair round fixed almost all**; 12 sentences dropped, none turned into an abstention.
- **Most answers were generated afresh, not replayed:** the graph's derived layer was rewritten (D101) and query results without an `ORDER BY` came back in a different order, which changed the cached conversations' keys. Cost $0.44 answers + judging.

## 4. HERB-Drift: right about now, and about then (13.1)
24 hand-labeled dev questions (`eval/drift_set.json`): 11 about the current status of an issue or PR, 10 "as of <date>", 3 "why". Ground truth was read from the raw messages by hand, never from Yaan's graph, and every item lists its message ids. A Haiku judge classifies each answer as correct, stale (a state that held at another time), abstained or wrong.

| System | Right | Stale-answer rate | Current status (right / stale) | As of a date | Why |
|---|---|---|---|---|---|
| baseline | 54.2% | 6.7% | 54.5% / 12.5% | 60.0% | 33.3% |
| yaan-search | 70.8% | 8.7% | 54.5% / 20.0% | 80.0% | 100% |
| **yaan** | **95.8%** | **0.0%** | **100% / 0%** | **100%** | 66.7% |

Search alone finds the message that said "fixed" and believes it. Yaan reads the Decision whose validity window holds now (or the asked date).

## 5. Search, expertise and latency (no LLM)
- **Product scoping holds as the graph grows (13.6, D071):** on the 96 answerable dev questions, hybrid hit@10 is 0.854 on the 3-product graph and 0.885 on the 10-product graph; vector hit@10/20 is identical (0.865 / 0.969). Full-text alone shifts both ways, because its TF-IDF weights come from the whole index.
- **Who knows a component (10.8, D061):** on HERB's 5 "engineers who resolved issues with <component>" questions, `who_knows` puts a right engineer first every time (hit@1 1.0, recall@3 0.87), against 0.0 / 0.21 for "whoever posted most in the bug channels".
- **FalkorDB time per tool call (p50 / p95, ms, dev run):**

| Tool | Calls | p50 | p95 |
|---|---|---|---|
| search_artifacts (full-text + vector) | 88 | 26.8 | 37.2 |
| list_prs | 72 | 20.5 | 32.4 |
| who_knows | 26 | 17.0 | 21.7 |
| resolve_entity | 66 | 15.4 | 35.9 |
| list_issues | 55 | 13.7 | 23.0 |
| decision_lineage | 4 | 11.9 | 14.4 |
| doc_discussion | 81 | 11.0 | 13.5 |
| get_artifact | 59 | 7.8 | 11.3 |
| rank (counts in Cypher) | 19 | 5.3 | 7.2 |
| graph_query (guarded text-to-Cypher) | 7 | 5.3 | 20.7 |

The graph answers in milliseconds; the seconds in an answer are the model's turns.

## 6. Error analysis and fixes after the dev run (9.8, D070)
Failures grouped by question template, costed in Avg points:
- competitor questions: −4.0;
- PRs reverted / to merge / to close: −4.5;
- doc feedback: −3.3.

Three fixes, checked on 70 affected questions plus controls ($0.18):
- **Competitors:** 0.56 → 0.87, with 5 better and 0 worse.
- **Doc feedback:** judge noise. The same answer scored 90 and then 40; the targeted missing item is now present.
- **Wrongly attributed quotes:** every bad quote was a revert PR's words cited on the PR it reverted. They're now re-pointed to the one related artifact holding them.

In the check, the wrongly answered unanswerables went from 4 to 0.

**7 of the 13 unanswerable misses are HERB label conflicts:** "bugs reported by <company> that did not require fixes" is labeled unanswerable, while HERB's own answerable `CollaborateForce:ans:28` lists all 8 companies as reporting such bugs. Yaan answers with the report and the dismissal quoted; we didn't tune toward the labels. The fixes landed after the measured run, and no full re-run was made (budget): 83.2 is the score before them.

## 7. Declared deviations
| Deviation | Why | Effect |
|---|---|---|
| Judge: Claude Haiku 5.5, not GPT-4o (D035) | Cost and access | Absolute scores aren't comparable to the paper's; system-vs-system comparisons are, since every system has the same judge |
| Dev split of 3 products, product-scoped retrieval | HERB's product-specific setting | Each question searches its own product's data, as in the paper's product-specific setup |
| One run per system (D043, D069) | Budget | ±a few Avg points of sampling spread, on top of the question-sampling intervals of §3b |
| Errors counted as answered | HERB's script counts a failed answer as "unanswered" | Stricter than HERB's own script (0 errors occurred) |
| The validator ablation reuses the Yaan run | No sampling noise between the two, judge cost only | It removes the validator and the fixed abstention line together (D076) |

## 8. Limitations
- **Held-out is 20 points below dev** (63.6 vs 83.2). The drift set and the error analysis are dev-only.
- **Same question templates.** HERB's held-out products share its 41 templates with dev, so a held-out run measures new products, not new kinds of question.
- **Synthetic data.** HERB is generated and tidy: every resolved bug has a fixing PR, every issue an assignee. Several of Yaan's checks (resolved without a fix, unreviewed PRs) find nothing here.
- **The content judge is noisy** (same answer: 90, then 40).
- **The drift set is small (24) and ours.** Its ground truth is public with message ids, so it can be checked.
- **Permissions (W7) are synthetic.** Since D100 counts computed over hidden sources are left out for that viewer, not recomputed.
- **The judge is the answerer's model family** (Haiku 5.5 judges Haiku 5.5). Every system shares it, so comparisons are fair to each other; the checks of §3c found it conservative, not lenient, but they are a model's reading, not a human's.
- **Part of the graph's lead is data the graph joins, not structure alone.** Search-only systems score 0 on Customer questions because a report names `CUST-0026` and only HERB's metadata, which only the graph links, says which company that customer works at. A baseline that indexes that metadata as documents would separate the two (planned, 18.5b).

## 9. Reproduce
```powershell
uv run --project backend yaan seed --from <snapshot>        # or build: fetch-herb, normalize, ingest, extract
uv run --project backend yaan eval --split dev --system yaan --workers 6         # ~$0.6
uv run --project backend yaan eval --split dev --system yaan-search --workers 6  # ~$0.6
uv run --project backend yaan eval --split dev --system baseline --workers 6     # ~$0.15
uv run --project backend yaan eval --score-only eval/runs/<run>_yaan_dev --variant unvalidated
uv run --project backend yaan eval --split drift --system yaan
uv run --project backend yaan bench-search --k 10 ; uv run --project backend yaan bench-experts   # $0
uv run --project backend yaan eval --split test --system yaan --heldout --workers 6   # the single held-out run, per system (~$1.1)
uv run --project backend yaan eval-report                                        # rebuild results/table.md
```
Answers are cached by their exact request, so a re-run with unchanged code replays for $0.
