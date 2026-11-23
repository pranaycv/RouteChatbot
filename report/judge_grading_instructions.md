<!-- Owner: Person E (WP10/WP11), Team Rosedale. Only Person E edits this file; AI coding tools working on other packages must not change it. Written for this project with AI assistance; nothing copied from other sources. -->
# Grading replies by hand (checks the LLM judge)

The LLM judge decides one part of trip success (whether a reply makes an unsupported claim), so we
check it against people. Two of us grade the same 50 replies on our own, then we compare.

What you get: `eval/results/judge_inputs.jsonl` from the D2 run, one reply per line, with the user's
request, the verifier's checks, what had to be mentioned, the route facts and the final text.

For each reply, answer the same four questions as the judge (prompts/judge_v1.md) with yes or no:

1. all_warnings_present: does it tell the user about everything in must_mention?
2. unsupported_claim: does it state anything not in the route facts or checks?
3. makes_promise: does it promise, book, pay, or say something is definitely open or safe?
4. user_knows_next_step: would the user know what to do next?

Rules:
- Grade on your own and don't look at the judge's answers or the other person's sheet first.
- If you're not sure about question 2, answer yes, same as the judge is told to.
- Copy `report/judge_grading_template.csv`, one row per reply, and save it as
  `report/judge_grading_A.csv` or `report/judge_grading_B.csv`.

Then: `uv run python -m eval.judge --agreement report/judge_grading_A.csv report/judge_grading_B.csv`
gives Cohen's kappa per question, person vs person and judge vs each person. Target 0.6 or more.

If only one person grades: run the same command with just `report/judge_grading_A.csv`. It reports
judge vs that person only; the report then says the judge was checked against one grader.

Time needed: about 1 to 2 minutes per reply [estimate], so 1 to 1.5 hours each.
