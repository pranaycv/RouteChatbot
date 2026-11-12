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
| `eval/cost_assumptions.yaml` | WP11's own assumptions: volumes, Mapbox volume discounts, GraphHopper plans above Basic, EUR to USD, fixed and error costs, revenue. |
| `prompts/judge_v1.md` | LLM judge prompt with the four WP10 yes/no questions. |
| `policy/operating_policy.md` | Operating policy v1, P-01 to P-09. |
| `report/status_quo_study.csv`, `report/status_quo_study_instructions.md` | Template and instructions for the manual baseline (WP11 Part B). |
| `tests/test_eval.py` | T-10-01 to T-10-07 and T-11-01 to T-11-03, offline. |

```bash
FIXTURES=replay uv run pytest                     # all tests, WP0's and these
FIXTURES=replay uv run python -m eval.run_eval --all
uv run python -m eval.cost_model --logs logs/<run_id>.jsonl --success eval/results/end_to_end.csv
```

## Decisions, and why

- **No change to `config.yaml`.** `Settings` in `routechat/config.py` forbids unknown keys, so a new
  section there would break every stage. WP11's assumptions live in `eval/cost_assumptions.yaml` and
  the unit prices are read from `prices:`.
- **Graduated Mapbox pricing.** `config.yaml` has the free tier and the first paid price; the cost
  model adds Mapbox's published volume discounts so the 1,000,000-a-month row is not overstated.
- **GraphHopper is a monthly plan.** The model picks the cheapest paid plan whose daily credits cover
  the volume. The free plan is never chosen because `free_plan_commercial: false`.
- **Null stays NaN.** An assumption nobody has set shows up as NaN and in `missing_inputs`, never as 0.
- **`error_type`, not `error`.** The optional error field uses the name `graph._log_failure` already writes.

## Still to agree

- WP7: request-line fields (see the Evaluation Spec, Table 3), where the ConstraintSet for each request
  is logged, and whether stage lines carry `ts` (the graph's failure line does not yet).
- WP7: the names RUN_ID, EVAL_DATASET and EVAL_ITEM_ID used to stamp evaluation runs.
- WP8: file names for D1 test, D2, D3 and the 50-reply judge sample under `eval/datasets/`.
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
