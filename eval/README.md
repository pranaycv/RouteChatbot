# WP10 and WP11 (Person E)

Evaluation (WP10) and operating policy, economics and decision memo (WP11), built on the WP0 skeleton.
This branch only adds new files. It does not change any file owned by another package.

## Files

| File | What it is |
|---|---|
| `eval/log_schema.py` | The log fields evaluation and the cost model read, a line validator, and the outcome label for a final TripState. Stage names come from `routechat.graph.NODE_ORDER`. |
| `eval/metrics.py` | Every WP10 metric as a function: trip success, classifier metrics, policy-critical errors, extractor field accuracy, adversarial pass rate, handoff precision/recall, latency, cost per successful request, judge agreement. |
| `eval/run_eval.py` | The single command named in the root README. Steps that need other packages print "waiting for ..." until that code is merged. |
| `eval/cost_model.py` | WP11 Part C cost model. Unit prices come from `config.yaml` `prices:` through `routechat.config`; nothing is copied. |
| `eval/cost_assumptions.yaml` | Business assumptions only: volumes, EUR to USD, fixed and error costs, revenue. No prices. |
| `prompts/judge_v1.md` | LLM judge prompt with the four WP10 yes/no questions. |
| `policy/operating_policy.md` | Operating policy v1, P-01 to P-09. |
| `report/status_quo_study.csv`, `report/status_quo_study_instructions.md` | Template and instructions for the manual baseline (WP11 Part B). |
| `tests/test_eval.py` | T-10-01 to T-10-09 and T-11-01 to T-11-03, offline. |

```bash
FIXTURES=replay uv run pytest                     # all tests, WP0's and these
FIXTURES=replay uv run python -m eval.run_eval --all
uv run python -m eval.cost_model --logs logs/<run_id>.jsonl --success eval/results/end_to_end.csv
```

## Decisions, and why

- **No change to `config.yaml`.** `Settings` in `routechat/config.py` forbids unknown keys, so a new
  section there would break every stage. Prices are read from `prices:`; `eval/cost_assumptions.yaml`
  holds business assumptions only.
- **Mapbox and GraphHopper use only the prices in config.** Mapbox's volume discounts above 500,000 calls
  and GraphHopper plans above Basic are not in `config.yaml`, so the 1,000,000-a-month row is an upper
  bound for Mapbox and NaN for GraphHopper. If we want that row priced, the tiers go into `prices:` (WP0).
- **The ConstraintSet stays out of the app log.** The origin can be a street address. `run_eval` saves the
  predicted ConstraintSet in the evaluation results for each item (`eval/results`, git-ignored); the gold
  copy stays in the D2 file.
- **Second clarify = handoff.** The graph rewrites it to `escalate` and creates no ticket, so the outcome
  reads `route_action`, not the ticket.
- **`revision_count` can be 0, 1 or 2.** A second revise is counted but does not loop.
- **Null stays NaN.** An assumption nobody has set shows up as NaN and in `missing_inputs`, never as 0.
- **`error_type`, not `error`.** The optional error field uses the name `graph._log_failure` already writes.

## Still to agree

- WP7: whether stage lines carry `ts` (the graph's failure line does not yet), and the names RUN_ID,
  EVAL_DATASET and EVAL_ITEM_ID used to stamp evaluation runs.
- WP8: file names for D1 test, D2 (with `gold_constraints`), D3 and the 50-reply judge sample under `eval/datasets/`.
- WP4 and WP6: a test id for P-09 (hiking and transit stopped by the map tools and the writer template).
- Team: EUR to USD rate, value of time, sponsored card price and the cost per error type.

## Prompt template for AI coding tools

Use the team's Project Development & Collaboration Guidelines (shared by Pranay) as the prompt, then add:

> My assigned work packages are WP10 (evaluation and experiments) and WP11 (operating policy,
> economics and decision memo). You may create or edit files under `eval/`, `prompts/judge_*`,
> `policy/`, `report/` and `tests/test_eval.py`. Do not edit any other file, including `config.yaml`,
> `pyproject.toml`, `routechat/` and other tests. Work on the `stage/eval` branch, never on `main`.
> Read data contracts from `routechat/schemas.py` and settings from `routechat.config`; do not copy them.
> If a change outside these paths seems needed, stop and say what you would change so the owner can decide.

Every file here starts with an owner comment, so each contribution is traceable.

## AI use

These files were written for this project with help from an AI assistant (Claude), following the
Stage Work Packages and the WP0 skeleton. Design choices, metric definitions and numbers come from the
team's documents, the providers' public pricing pages (dated in the files) and our own runs.
