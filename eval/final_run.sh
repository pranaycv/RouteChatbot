#!/usr/bin/env bash
# Owner: Person E (WP10 evaluation / WP11 economics), Team Rosedale, MMAI 894.
# Written for this project with AI assistance (see eval/README.md, "AI use").
# Final evaluation run, in order. Needs every stage merged into main, the WP8 datasets (data/d1/test.jsonl,
# eval/datasets/d2_end_to_end.jsonl, eval/datasets/d3_adversarial.jsonl), and the keys in .env:
# OPENAI_API_KEY or OPENROUTER_API_KEY (stages and the LLM judge), MAPBOX_TOKEN and GRAPHHOPPER_KEY (maps).
#   bash eval/final_run.sh
set -euo pipefail
export RUN_ID="${RUN_ID:-final_$(date +%Y%m%d_%H%M)}"
echo "run id: $RUN_ID (commit $(git rev-parse --short HEAD 2>/dev/null || echo unknown))"

echo "== 1. tests (offline, replayed calls)"
FIXTURES=replay uv run pytest -q

# The run itself needs live calls: D2 and D3 requests have no recorded replies, so with FIXTURES=replay
# every request stops with LLMFixtureMissing and the map tools log no API calls. "record" calls the
# providers once and saves the replies, so the same run can be replayed later for free.
export FIXTURES="${FIXTURES:-record}"
if [ "$FIXTURES" = "replay" ]; then echo "FIXTURES=replay: this replays an earlier recorded run"; fi

echo "== 2. classifier (D1)"
uv run python -m eval.run_eval --classifier

echo "== 3. end to end with the judge (D2)"
RUN_JUDGE=1 uv run python -m eval.run_eval --e2e

echo "== 4. adversarial (D3); its log is logs/<run_id>_D3.jsonl, so D3 never mixes into the D2 cost"
uv run python -m eval.run_eval --adversarial

echo "== 5. cost model, on the D2 log only"
if [ -f "logs/${RUN_ID}_D2.jsonl" ]; then
  uv run python -m eval.cost_model --logs "logs/${RUN_ID}_D2.jsonl" --success eval/results/end_to_end.csv
else
  echo "cost model: waiting for the D2 log (logs/${RUN_ID}_D2.jsonl); step 3 did not run the graph"
fi

echo "== 6. sheet for the 50 manual route inspections"
uv run python -m eval.route_review --reviewers ${REVIEWERS:-Preeti Arvin}

echo "== done. Results are in eval/results/. Next, by hand:"
echo "   - 50 routes: report/route_review_instructions.md, then: uv run python -m eval.route_review --score report/route_review_sheet.csv"
echo "   - 50 replies: report/judge_grading_instructions.md, then: uv run python -m eval.judge --agreement report/judge_grading_A.csv [report/judge_grading_B.csv]"
echo "   - then rerun the scores on the same log: uv run python -m eval.run_eval --e2e --logs logs/${RUN_ID}_D2.jsonl"
