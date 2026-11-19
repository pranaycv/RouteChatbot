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
| `eval/judge.py` | Runs the LLM judge on every D2 reply and compares it with two people's grades (Cohen's kappa). |
| `prompts/judge_v1.md` | LLM judge prompt with the four WP10 yes/no questions. |
| `policy/operating_policy.md` | Operating policy v1, P-01 to P-09. |
| `report/status_quo_study.csv`, `report/status_quo_study_instructions.md` | Template and instructions for the manual baseline (WP11 Part B). |
| `report/judge_grading_template.csv`, `report/judge_grading_instructions.md` | Sheet and instructions for grading 50 replies by hand to check the judge. |
| `tests/test_eval.py` | T-10-01 to T-10-11 and T-11-01 to T-11-04, offline. |

```bash
FIXTURES=replay uv run pytest                     # all tests, WP0's and these
FIXTURES=replay uv run python -m eval.run_eval --all
uv run python -m eval.cost_model --logs logs/<run_id>.jsonl --success eval/results/end_to_end.csv
RUN_JUDGE=1 uv run python -m eval.run_eval --e2e   # D2 run with the LLM judge (needs an API key)
uv run python -m eval.judge --agreement report/judge_grading_A.csv report/judge_grading_B.csv
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
- **A reply the judge didn't grade is not clean.** It counts against trip success, so a judge failure can't make results look better.
- **Null stays NaN.** An assumption nobody has set shows up as NaN and in `missing_inputs`, never as 0.
- **`error_type`, not `error`.** The optional error field uses the name `graph._log_failure` already writes.

## Agreed on Nov 12

- WP7: every log line carries `ts` (Pranay: the logger can add it automatically).
- Evaluation runs are tagged with RUN_ID, EVAL_DATASET and EVAL_ITEM_ID. The item id is the id that
  the synthetic data already has [field name to confirm when D2 lands].
- Status quo: the main number is minutes saved against planning by hand (map apps, blogs, checking
  each requirement), not a comparison with Google Maps. A dollar value of time is optional.

- Cost assumptions filled from the Nov 14 proposal (no objections). Training cost is 0: about 45 min on
  Kaggle's free GPU (WP9). Classifier hosting assumes CPU [WP9 to confirm].

## Still to agree

- WP8: file names for D1 test, D2 (with `gold_constraints`), D3 and the 50-reply judge sample under `eval/datasets/`.
- WP4 and WP6: a test id for P-09 (hiking and transit stopped by the map tools and the writer template).
- Error rates for the cost of errors come from the final eval run; until then error cost and totals show NaN.

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
