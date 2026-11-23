# RouteChatbot WP4 and WP5 validation

## System design

WP4 uses Mapbox to resolve named places and GraphHopper to generate route geometry and
measurements. It ranks stop matches by straight-line detour and orders up to three stops
while preserving user-specified relative order. The pilot uses GraphHopper Standard.
WP5 checks the candidate that would be displayed and accepts or hands off. It skips
a revision for GraphHopper Standard because the request would be identical. A sponsor
is attached to the report after an eligible acceptance. The selected route and its stops
do not change.

## Offline evaluation

| Case | Decision or expected output | Evidence |
|---|---|---|
| Named stop has multiple matches | Choose the lowest-detour match | T-4-01 and T-4-02 |
| Route details contain filtered segments | Sum segment lengths and compute bike-network share | T-4-03 |
| Provider times out | Retry once, then return `tool_timeout` | T-4-04 |
| Transit requested or place cannot be resolved | Return a typed routing error | T-4-05 and T-4-06 |
| Provider omits closure coverage | Keep closure data null and disclose it as unverifiable when applicable | T-4-07 and T-5-17 |
| Required stop missing or out of order | Handoff with the relevant stop check | `test_required_stop_order_and_unverifiable_hours` |
| Distance, duration, or arrival limits | Check configured limits and report unavailable measurements | `test_route_checks` |
| Highway requirement remains unmet after retry | Handoff with the highway check | T-5-14 and cross-stage graph smoke test |
| Flatness preference remains unmet after retry | Accept with a disclosure | `test_soft_flatness_failure_is_disclosed_after_retry` |
| Accessibility cannot be verified | Handoff with a step-free disclosure | T-5-15 |
| Eligible acceptance | Attach at most one qualifying sponsor without adding a stop | T-5-16 and writer/output-guard smoke test |
| Comparable route modes | Prefer the requested mode within the time threshold | `test_compare_prefers_requested_mode_within_threshold` |

The final Windows checkout passed all 158 tests in offline replay mode. The
repository key scanner inspects ignored files as well as tracked files, so the
local .env was temporarily moved outside the project during this run and restored
afterward. Automated checks and smoke tests do not establish evaluation-set
place-resolution accuracy, latency, or API spend.

## Live smoke test

On the Windows checkout with Mapbox and GraphHopper credentials configured,
the baseline bicycle request resolved King Station (43.64900, -79.37783),
Canadian Tire (43.65587, -79.38311), and University of Toronto (43.66075,
-79.39477). GraphHopper returned 2734.779 m and 570.62 s without an error.

The initial locality-biased search chose a same-name station north of downtown
and returned a 41.3 km route. Requiring a match on Mapbox's locality or place
context corrected the selected origin.

The low-climb custom-model request returned HTTP 400 with GraphHopper's message
that free packages cannot use flexible mode. WP4 reports this rejection as
`flexible_mode_unavailable`. Standard routing works with the same credentials.
The pilot now sends Standard requests and verifies the route returned.

Recording and replay reproduced the 2734.779 m point-to-point candidate
exactly. The live round-trip request returned a closed 4186.675 m loop in
852.405 s for a 5000 m target. The measured loop is under the minimum
4500 m allowed by WP5's configured ±10% tolerance. Offline replay reproduced
the 4186.675 m candidate and WP5 returned handoff with loop_distance unmet.
The broader evaluation and 50-route manual inspection are pending.

## Limits and live validation

- Mapbox Directions may propose a different route. Transfer traffic or closure data only
  when its geometry matches the GraphHopper candidate. Leave unmatched data unknown.
- Stop hours are unverifiable without an arrival estimate at each stop and suitable
  provider hours. The candidate contract also lacks verified accessibility and lighting.
- The browser-location example requires an app-to-maps coordinate contract. The current
  map stage accepts named origins only. The country setting follows the shared config.
- Sponsors are eligible on accepted routes unless the request is accessibility-related,
  safety-related, a mode comparison, or explicitly in the evening. An omitted time
  leaves a route eligible. A provided time after 18:00 or evening/night wording
  disqualifies it.

For a live run, use approved provider access to record evaluation responses and capture
calls, latency, and errors by API. On the evaluation set, compare resolved points with
gold coordinates using the handbook's 200 m tolerance. For the 50-route manual review,
record route ID, requested check, verifier status, reviewer status, and evidence. Count
a false met only where a reviewer establishes that a requirement marked met failed.
Also record handoffs, revisions and their outcomes, and trip success. Report those
measurements after the live run and manual review.
