# eval/

Evaluation artifacts (the code lives in `backend/app/eval/`). Method: `docs/CONTEXT.md` §7.

- `split.json`: dev/test products and the seed (Phase 2). **Never open test products' questions.** Code refuses to load them outside the final held-out run.
- `heldout_runs.log`: one line per held-out run. It should end up holding exactly one final run (Phase 13).
- `results/`: scored runs, one JSON per (date, system, split). The README results table is generated from these.
- `drift_set.json`: the HERB-Drift temporal subset (Phase 13).
- `report.md`: the final evaluation write-up (Phase 13).
- `runs/`: raw per-question outputs (gitignored).
