# Prompt change log

Every prompt change gets an entry: what changed, why, and the metric before and after (handbook A2).

## extractor_v1.md

- **2026-11-18, v1.** First version. Four parts as the handbook asks: role, field rules (the WP3 extraction table plus the 18 Nov agreements with WP4/WP5 on times, loops and EV stops), prohibitions, and six worked examples (running example, loop, compare, replan, vague request, two-trip message). The code-set fields missing_field and too_many_stops are always null/false from the model; routechat/agents/extractor.py sets them. No metric yet: the 120 gold ConstraintSets (D2) are not available. Replay fixtures for the tests are synthetic (scripts/llm_fixtures.py --synthetic) until a key is in .env; re-run with --record then.

## classifier_fewshot_v1.jsonl

- **2026-11-18, v1.** Eight fixed examples for the prompted backends only: one per route_action plus one prompt-injection attack. Each validates against schemas v1. The fine-tuned and base backends get the label guide alone.

## label_guide_v1.md

- **2026-11-13, v1.** First version. Written from the handbook's WP2 label rules and the A1 controlled vocabularies, matched to schemas v1 (`routechat/schemas.py`) and operating policy P-09: hike and transit are labelled as themselves and are not refused; the map tools and the writer turn them away. Decisions the handbook left open, to confirm with WP8: both ends missing gives `clarify_field = origin`; `clarify_field = stops` only when a stop is requested but its kind is unknown; `primary_mode` and `needs_live_data` are still labelled on refuse, escalate and clarify turns; EV and car-share are `car`. No metric yet; the frozen D1 test set is not available.

## writer_v1

- What: first writer prompt. Fixed early-exit sentences stay in code. The model only describes an accepted route, or a handoff that still has a route.
- Why: WP6. The wording follows policy P-03 (say what was not confirmed), P-04 (no booking or promise), P-05 (label a sponsor), and P-08 (a handoff says a person has the trip).
- Metric before: none. This is the first version.
- Metric after: not measured. Faithfulness and disclosure completeness wait for the WP10 judge.

## label_guide_v2.md

- **2026-11-23, v2.** Transit is now refused: a request that needs public transit for any part of the trip, a transit schedule question, or a comparison against transit gets `refuse` / `out_of_scope_mode` with `primary_mode = unspecified`. This matches schemas v1.1 (PR #13) and the D1 data (58 train / 10 val / 17 test records carry out_of_scope_mode, all with primary_mode unspecified). Hike is still labelled hike and not refused. Rule 6 no longer lists "the last train tonight"; rule 5 now says two in-pilot modes. `routechat/classifier/predict.py` points at v2; v1 stays for the record. Metric before and after: not yet measured, the four-backend run on the frozen test set is next. Few-shot v1 updated in the same change: the compare example is walk vs bike, and the b2b refusal example is replaced by a transit refusal so each of the two most common refusal reasons in D1 has an example. Replay fixtures re-seeded (synthetic).
- **extractor_v1.md, same day:** the compare example and field rule use walk vs bike, since a transit comparison never reaches the extractor now. No other change.
- **2026-11-23, fixtures recorded live.** All 31 replay fixtures re-recorded through OpenRouter (gpt-4.1-mini and gpt-4.1, temperature 0) with `scripts/llm_fixtures.py --record`; none are synthetic now. Extractor v1: all six worked examples round-trip exactly on gpt-4.1-mini. Classifier on the 11 smoke turns: gpt-4.1 matches all five labels on 10/11, gpt-4.1-mini on 9/11; every disagreement is primary_mode only (route_action and refusal_reason agree on all turns). One JSON failure observed: on the prompt-injection attack gpt-4.1-mini first replied "I'm sorry, but I cannot assist with that request." instead of JSON; the parser's single retry produced the correct refuse / prompt_injection. That retry is recorded as its own fixture so the path replays offline.
