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

## label_guide_v3.md

- **2026-11-23, v3.** The classifier prompt is restated from the WP8 guide the D1 labels were adjudicated and relabelled with (`data_gen/prompts/label_guide_v1.md`, Nov 22, plus the decision order in `relabel_v3.md`). What changed against v2: escalate triggers (asks for a person or distrusts the assistant, demands a guarantee, broken promise, stranded or hurt, several people from different places, contradictory or infeasible constraints, more than 3 required stops); replan is any message that says something specific is blocked, closed, flooded or under construction, or asks for an alternative route, at turn 0 or 1; lookup_only is any factual question with no route; priority refuse > escalate > replan > clarify > compare_modes > lookup_only > plan with plan as the last choice; compare_modes takes the first mode named; vague places and "from here" count as missing; transit removed from the allowed primary_mode values. 1,951 tokens (v2: 1,430), so a training record is up to 2,210 tokens (train with --max-length 2304).
- **Why:** on the frozen test set with v2, gpt-4.1 put 32 of 60 gold escalates, 26 of 60 gold lookup_onlys and 25 of 60 gold clarifies under plan, while refusal_reason and primary_mode were 0.91 to 0.97 accurate: the errors were the labelling conventions, not the model.
- **Metric before -> after, validation (199 records, gpt-4.1-mini, FIXTURES=record):** joint exact match 0.457 -> 0.583; policy-critical error rate 0.090 -> 0.015; route_action accuracy 0.59 -> 0.74; clarify_field 0.89 -> 0.93; primary_mode, needs_live_data and refusal_reason unchanged (0.85, 0.93/0.94, 0.97). Test-set numbers for both prompted backends and the v3 adapter follow in eval/results/classifier_v3.csv.
- **Serving rule:** an adapter is served with the guide it was trained on (`prompt_version.txt` next to its weights); prompted backends take `CLASSIFIER_PROMPT_VERSION`, default still v2 until the v3 adapter is measured.
