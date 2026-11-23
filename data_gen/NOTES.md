# WP8 notes: decisions, open issues, what is still manual

## Decisions agreed with the team (2026-11-08)
1. **Transit policy is one switch** (`ROUTECHAT_TRANSIT_POLICY`, default `out_of_scope_mode`); the team decision is pending.
   - `out_of_scope_mode`: transit wording becomes `refuse / out_of_scope_mode` (rows 10, 18); `primary_mode` has no `transit`.
   - `named_mode` (repo policy P-09): `transit` stays a named mode; rows 10 and 18 become normal plan/lookup rows.
   `hike` stays a named mode (as in the repo). `multimodal` stays, but only when every leg is walk, run, bike or car (row 30).
2. Motorcycle maps to `car` (the word "motorcycle" stays in the message, row 20).
3. New `refusal_reason`: `out_of_scope_mode`, placed right after `out_of_scope_b2b` in the precedence order.
4. Row 17 (several origins) is an out-of-schema edge case: `clarify` or `escalate`.
5. Row 14 (EV charging) is kept. Proposed ConstraintSet additions: stop category `fuel_or_charging`, `ev_range_km`, `charger_type`.
6. Six `escalate` rows added (24 too many stops, 25 infeasible constraint, 26 guarantee demanded, 27 asks for a person, 28 stranded, 29 contradictory).

## Contract changes to take to Person A / Person C
- `schemas.py`: only if the team picks `out_of_scope_mode`: add it to `refusal_reason` (and drop `transit` from `primary_mode`).
  Until then `data_gen/contracts.py` accepts it as a proposed extension and validates everything else against the real schemas.
- `ConstraintSet`: `fuel_or_charging` stop category, `ev_range_km`, `charger_type` (D2 row-14 records carry these under `ev_fields` until then).
- WP2 label guide: `prompts/label_guide_draft.md` is MY draft. Person C must confirm it before the test set is frozen,
  especially the escalate vs refuse boundary for "stranded / in difficulty" (row 28), and how `needs_live_data` is set.

## Inconsistencies in the handbook that I resolved (please confirm)
- **Test size.** "400 test records, at least 60 per action" is impossible with 7 actions (7 x 60 = 420). I built a
  class-balanced test of 60 per action = 420. Train and validation follow the natural quotas. Report macro-F1 on test.
- **Hand-written test messages.** "150 (30 each)" only fits 5 classes. I split 150 across the 7 actions
  (plan 30, clarify 20, refuse 25, replan 15, compare 20, lookup 20, escalate 20) in `spec.HANDWRITTEN_PER_ACTION`.
- **D3 size.** The table says 120, the text says 6 x 20 plus 10 two-turn (= 130). I made 120 total with 10 of them two-turn.
- **D5** needs real system replies, so it can only be prepared after WP6/WP10 produce output.

## Things that need people (the pipeline cannot do these)
- Write the 150 test messages (`handwriting-sheet`) and the ~40 D2 messages (`review/d2_handwrite_TODO.csv`).
- Review the flagged D1 disagreements, then review ALL test records (`test_review.csv`).
- Double-label 100 records with two people and report kappa.
- Verify gold coordinates (`review/d2_coords_verification.csv`). `places.csv` coordinates are from memory to about 3 decimals and some (trailheads, lookouts) are rough.
- Review `review/d3_review.csv` for realism.
- Run the live generation with an OpenRouter key. Everything here was tested only with the mock generator.

## Not measured yet
- QC yield (I assumed ~70% when sizing 3,000 jobs), diversity numbers by model/temperature, relabel disagreement rate, and real cost.
  Run a 200-job pilot first and resize `--n`. Rough budget guess: ~5,500 live calls (3,000 generation, ~2,100 relabel,
  ~400 for D2/D3) at small-model prices, likely a few dollars at most. This is an estimate, not a measurement.

## Known limitations
- The mode-leak case (a message labelled `unspecified` that names a mode) is not an automatic QC check; it relies on the blind relabel catching it.
- Language check uses langdetect and only fires on messages of 6+ words with a confident non-English result.
- `needs_live_data` is assigned by rules in `sampling.build_jobs` (replan always true, traffic/closure/trail lookups true, hours false); WP2 may want a different rule.
- Place coverage is ~146 places in four cities; rare place names will repeat across 2,000+ records.
- IPF priors (`DIFF_PRIOR`, `TURN_FACTOR`) are my judgment. The solver guarantees the marginals, not that every cell looks natural.
- Sponsor-manipulation attacks (D3) have `route_action = plan` as expected label, which has no counterpart in D1; WP11 should confirm the expected behaviour.
