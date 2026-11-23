<!-- Owner: Person E (WP10/WP11), Team Rosedale. Only Person E edits this file; AI coding tools working on other packages must not change it. Written for this project with AI assistance; nothing copied from other sources. -->
# Inspecting 50 routes by hand (WP5 false-met rate, WP10 trip success)

The verifier marks each requirement on a route as met, unmet or unverifiable. We check 50 accepted
routes ourselves, because a check marked met that is really not met is the most dangerous error.

Get the sheet (after the D2 run): `uv run python -m eval.route_review --reviewers Preeti Arvin`
writes `report/route_review_sheet.csv`. The 50 routes are picked with a fixed seed and split evenly;
your rows have your name in the first column. One row is one check on one route.

For each of your rows:
1. Read the request (`utterance`) and the check (`check_id`), for example `avoid:highways` or `stop:canadian_tire`.
2. Look at the route yourself on a map (the app, Mapbox or Google Maps) and decide if that requirement holds.
3. Write `met`, `unmet` or `unverifiable` in `reviewer_status`, and one line in `reviewer_evidence`
   saying what you looked at, for example "route uses Hwy 401 for 2 km".

Rules:
- Decide before reading `verifier_status` if you can; the point is an independent check.
- Use `unverifiable` when you can't tell from a map either (live closures, opening hours).
- Count a false met only when you can show the requirement failed. If unsure, write `unverifiable`.

Then: `uv run python -m eval.route_review --score report/route_review_sheet.csv` prints the false-met
rate and writes `eval/datasets/d2_truth.csv`. Rerun `uv run python -m eval.run_eval --e2e --logs logs/<run_id>_D2.jsonl`
and trip success uses the reviewers' answers for those 50 routes; the other accepted routes keep the
verifier's own statuses and are marked `verifier_only`.

Time needed: about 2 to 3 minutes per route [estimate], so about an hour for 25 routes.
