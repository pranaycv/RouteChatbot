# Prompt change log

Every prompt change gets an entry: what changed, why, and the metric before and after (handbook A2).

## extractor_v1.md

- **2026-11-18, v1.** First version. Four parts as the handbook asks: role, field rules (the WP3 extraction table plus the 18 Nov agreements with WP4/WP5 on times, loops and EV stops), prohibitions, and six worked examples (running example, loop, compare, replan, vague request, two-trip message). The code-set fields missing_field and too_many_stops are always null/false from the model; routechat/agents/extractor.py sets them. No metric yet: the 120 gold ConstraintSets (D2) are not available. Replay fixtures for the tests are synthetic (scripts/llm_fixtures.py --synthetic) until a key is in .env; re-run with --record then.

## classifier_fewshot_v1.jsonl

- **2026-11-18, v1.** Eight fixed examples for the prompted backends only: one per route_action plus one prompt-injection attack. Each validates against schemas v1. The fine-tuned and base backends get the label guide alone.

## label_guide_v1.md

- **2026-11-13, v1.** First version. Written from the handbook's WP2 label rules and the A1 controlled vocabularies, matched to schemas v1 (`routechat/schemas.py`) and operating policy P-09: hike and transit are labelled as themselves and are not refused; the map tools and the writer turn them away. Decisions the handbook left open, to confirm with WP8: both ends missing gives `clarify_field = origin`; `clarify_field = stops` only when a stop is requested but its kind is unknown; `primary_mode` and `needs_live_data` are still labelled on refuse, escalate and clarify turns; EV and car-share are `car`. No metric yet; the frozen D1 test set is not available.
