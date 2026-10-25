# eval/

Evaluation artifacts (the code lives in `backend/app/eval/`). Method and results: [report.md](report.md).

- `split.json`: dev/test products and the seed (Phase 2, D025), written once by `yaan split`. **Never open test products' questions.** `app.eval.split.load_questions` refuses them outside `heldout_run()`, the final held-out run.
- `heldout_runs.log`: one line per held-out run. It should end up holding exactly one final run (Phase 13).
- `results/`: scored runs, one JSON per (date, system, split), written by `yaan eval`. `results/table.md` is generated from them by `yaan eval-report` (the README table). Partial runs (`--limit`, `--products`) never land here.
- `drift_set.json`: the HERB-Drift temporal subset (Phase 13).
- `report.md`: the final evaluation write-up (Phase 13).
- `runs/<run_id>/` (gitignored): `answers.jsonl` (answer, citations, retrieved ids, latency, tokens per question), `judged.jsonl` (the judge's verdicts), `meta.json`, and `herb-ans.json` / `herb-unans.json` in the input format of HERB's own `evaluate.py`, so the answers can be re-scored with HERB's gpt-4o judge unchanged (D035).
