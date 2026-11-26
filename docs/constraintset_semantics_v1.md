# ConstraintSet field meanings, v1

Owner: WP3 (constraint extractor). Agreed with WP4 map tools and WP5 verifier (Preeti) on 18 Nov 2026. Status: v1 frozen.
Source: schemas v1 in `routechat/schemas.py`, the handbook's WP3 extraction rules, and "Decisions other packages depend on" in `docs/architecture.md`.

The ConstraintSet is the form the extractor fills in from the user's message. Map tools and the verifier read it. This page states what every field means so the three packages never disagree. The types below are the frozen schema's.

## Three general rules

1. **Units.** Distances in kilometres, durations in whole minutes, clock times as 24-hour `HH:MM` in Toronto local time, today assumed. "2 hours" becomes 120. "5k" becomes 5.0. "before 5" becomes `17:00`. If `arrive_by` is earlier than `depart_at`, the extractor adds an ambiguity asking whether the user means tomorrow; it never guesses a date. Trips on a future day would need a date and are outside the pilot.
2. **Null means the user did not say.** A null origin or destination is missing and triggers a clarifying question. Lists are never null; an empty list means the user asked for nothing of that kind. (Same rule as the README: null is not zero and not an empty list.)
3. **Nothing is invented.** Place names are copied as the user wrote them. The extractor never produces coordinates, opening hours or facts. Map tools resolve places. Anything unclear goes into `ambiguities` as a sentence, not into a guessed value.

## Field by field

| Field | Type (schemas v1) | Meaning | Null or empty means | Example |
|---|---|---|---|---|
| origin | str or null | Start place, as written. City added only if the user said it. | Missing or personal ("home"). Code sets `missing_field = origin`. | "King Station, Toronto" |
| destination | str or null | End place, as written. | Missing or personal, unless `loop` is true. Code sets `missing_field = destination`. | "University of Toronto" |
| stops | list of Stop | Places to visit on the way. See Stop below. | No stops requested. | one stop, Canadian Tire |
| mode | walk, bike, hike, run, transit, car, multimodal, unspecified | How the user travels. | `unspecified`: code copies the classifier's primary_mode. | "bike" |
| modes_to_compare | list of modes, at most 2 | Only for compare requests. Transit never appears: a comparison against transit is refused by the classifier (schemas v1.1, out_of_scope_mode). | Not a comparison. | ["walk", "bike"] |
| compare_threshold_min | int or null, minutes | The cutoff between the two modes. | No threshold given. | 30 for "walk if under 30 min" |
| avoid | list from highways, tolls, ferries, unpaved. No duplicates. | Things the route must not use. | Nothing to avoid. | ["highways"] |
| prefer | list from bike_lanes, quiet_roads, scenic, lit_streets, step_free. No duplicates. | Things the route should favour. | No preference. | ["bike_lanes"] |
| minimize_elevation | bool | "Flat", "avoid hills". | Not mentioned. | true |
| check_closures | bool | User asked about closures. | Not mentioned. | true |
| max_duration_min | int or null, minutes | Upper limit on trip time. | No limit. | 120 |
| max_distance_km | float or null, km | Upper limit on distance. | No limit. | 5.0 |
| loop | bool | "Loop", "and back", round trip. Needs only an origin. | One-way trip. | true for "a 10 km run loop" |
| target_distance_km | float or null, km | Wanted length of a loop. | Not given. | 10.0 |
| depart_at | `HH:MM` or null | When the user leaves. Time only, today assumed. | Leave now, or not stated. | "08:30" |
| arrive_by | `HH:MM` or null | Deadline to arrive. Time only, today assumed. | No deadline. | "17:00" |
| required_accessibility | bool | Wheelchair, stroller or step-free request. Also adds `step_free` to prefer. | Not mentioned. | true |
| ambiguities | list of str | Things the extractor could not pin down, written as sentences. | Nothing unclear. | ["my university: which one?"] |
| missing_field | null, origin, destination or unparseable | Set by code after extraction, never by the model. `unparseable` means the model failed twice. | Form is complete. | null |
| too_many_stops | bool | Set by code when more than 3 required stops (`trip.max_required_stops`). | 3 or fewer required stops. | false |

## Stop

| Field | Type | Meaning | Example |
|---|---|---|---|
| query | str | What the user wrote, or the category word for a generic stop. | "Canadian Tire", "coffee" |
| category | str or null | Set for a type of place. Null for a named place. `ev_charging_station` is the only EV category in the pilot; a charging stop is found by category and included like any other stop. No `fuel_or_charging` category. | "coffee", "ev_charging_station" |
| required | bool | False only if the user said the stop is optional. | true |
| order | int or null | 1-based position if the user gave an order. Null means the map tools pick the best order. | null |

## Special cases, from the handbook

- Personal places ("home", "my office", "my university") become null plus an ambiguity. The pilot stores no saved places.
- A generic stop ("a coffee shop") becomes query = "coffee", category = "coffee". The type words, agreed with WP4 and WP8 on 25 Nov so D2, the extractor and the map layer share one vocabulary: coffee, lunch, grocery, pharmacy, bank, playground, viewpoint, ev_charging_station. The map layer translates them to Mapbox's IDs where they differ (lunch to restaurant, ev_charging_station to charging_station). A kind of place outside the list keeps the user's word.
- Scenic, countryside or quiet words add `scenic` and `quiet_roads` to prefer.
- Two trips in one message: extract the first only, add an ambiguity naming the second.
- Replan (turn 2): return the full merged trip. Start from the earlier ConstraintSet, change only what the user changed. "Skip the Canadian Tire" removes that stop and keeps everything else.
- Transit requests never reach the extractor: the classifier refuses them with `out_of_scope_mode` (team decision 23 Nov, schemas v1.1). A hike still reaches it and is filled in normally; the map tools and the writer turn it away.
- A loop needs `target_distance_km`. Map tools cannot build or verify a loop without a length, so when the user gives none the extractor adds an ambiguity ("loop length not given") rather than inventing one.

## Agreed with Preeti (WP4, WP5), 18 Nov 2026

1. **Times.** Clock times with today assumed, Toronto local time throughout. An `arrive_by` earlier than `depart_at` becomes an ambiguity ("do you mean tomorrow?"). Future-day planning would need a date and is out of the pilot.
2. **Stop order.** 1-based. Map tools preserve the user's order when given, else choose the shortest. A required stop is a hard check in the verifier.
3. **Empty lists.** An empty `avoid` or `prefer` adds no rules from that list. Explicit fields still apply on their own: `minimize_elevation = true` adds a slope preference even when both lists are empty. The verifier checks a preference only when it was requested.
4. **Loops.** A loop with no destination starts and finishes at the resolved origin. A valid origin and a `target_distance_km` are both needed. (Device coordinates for a "my location" origin are not a ConstraintSet field. The graph writes "current location" and passes the point to the map stage as `device`; see README, "A missing start is the device location".)
5. **EV.** `category = ev_charging_station` on a stop is enough for the pilot. Range and connector checks would need extra user inputs and provider data, so they stay out unless the team explicitly includes them. No `ev_range_km`, `charger_type` or `fuel_or_charging`; adding any of them would be a schema-change pull request with WP0.
