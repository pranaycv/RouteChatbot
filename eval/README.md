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
| `eval/route_review.py`, `report/route_review_instructions.md` | Sheet for the 50 manual route inspections, then the false-met rate and `d2_truth.csv` for trip success. |
| `eval/final_run.sh` | The final run in order: tests, all eval steps with the judge, then the cost model. |
| `tests/test_eval.py` | T-10-01 to T-10-16 and T-11-01 to T-11-06, offline. |

```bash
FIXTURES=replay uv run pytest                     # all tests, WP0's and these
FIXTURES=replay uv run python -m eval.run_eval --all
uv run python -m eval.cost_model --logs logs/<run_id>_D2.jsonl --success eval/results/end_to_end.csv
RUN_JUDGE=1 uv run python -m eval.run_eval --e2e   # D2 run with the LLM judge (needs an API key)
uv run python -m eval.judge --agreement report/judge_grading_A.csv report/judge_grading_B.csv
uv run python -m eval.judge --agreement report/judge_grading_A.csv   # one grader only
bash eval/final_run.sh                             # the final run, once every stage is merged
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
- **One grader is enough to run the judge check.** With a second grader we also report person vs
  person; with one, that column is NaN and the report says the judge was checked against one person.
- **Hike, transit and combined modes follow P-09.** Transit is refused by the classifier
  (`out_of_scope_mode`). Hike and combined modes are refused by the verifier without a ticket, and a
  missing mode becomes clarify (PR #18). When the gold mode is hike, transit or multimodal and the system
  says it is not covered yet, the outcome counts as right; `not_covered_p09` keeps these visible.
- **A new RUN_ID for every run.** WP7's logger appends, so if a log file for that RUN_ID already exists
  `run_eval` moves it aside first; otherwise its cost would be counted twice.
- **Only inspected routes use a person's answer.** Trip success uses the reviewer's answer for the 50
  inspected routes; other accepted routes keep the verifier's statuses and are marked `verifier_only`.
- **An API with no price is never free.** If the log has an API name that `cost_assumptions.yaml` does
  not price, the Mapbox cost is NaN and the name is printed. WP4 logs `geocode`, `searchbox`,
  `directions` and `graphhopper`; all four are covered.
- **Null stays NaN.** An assumption nobody has set shows up as NaN and in `missing_inputs`, never as 0.
- **`error_type`, not `error`.** The optional error field uses the name `graph._log_failure` already writes.

## Agreed on Nov 12

- WP7: every log line carries `ts` (Pranay: the logger can add it automatically).
- Evaluation runs are tagged with RUN_ID, EVAL_DATASET and EVAL_ITEM_ID. The item id is the id that
  the synthetic data already has [field name to confirm when D2 lands].
- Status quo: the main number is minutes saved against planning by hand (map apps, blogs, checking
  each requirement), not a comparison with Google Maps. A dollar value of time is optional.

- Cost assumptions filled from the Nov 14 proposal (no objections). Training cost is $0.69: 1.97 GPU-hours on
  a Kaggle T4 at the rate in finetune/config.yaml (WP9, Nov 24). Classifier hosting assumes CPU [WP9 to confirm].

- The run log comes from WP7's logger (`routechat/logging.py`, merged Nov 23). `run_eval` points it at
  `logs/<run_id>_<dataset>.jsonl` (D2, D3) with `ROUTECHAT_LOG`, so each run and dataset has its own file; the classifier step
  writes one file per backend. The runner no longer writes log lines itself.

## Checked on Nov 22 against the other branches

- Merging `main` into this branch has no conflicts; none of the files here are touched by `main`.
- WP8's file names and fields match what `run_eval` reads (`data/d1/test.jsonl`,
  `eval/datasets/d2_end_to_end.jsonl`, `eval/datasets/d3_adversarial.jsonl`; `item_id`, `utterance`,
  `gold_constraints`, `expected_outcome`). Checked with the generator's offline mock data only.
- The final run cannot use `FIXTURES=replay`: new requests have no recorded replies, and a replayed
  run logs no map API calls, so map cost would show as 0. `eval/final_run.sh` uses `record`.

## Still to agree

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
