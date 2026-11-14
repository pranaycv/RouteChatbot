# ConstraintSet field meanings, v1

Owner: WP3 (constraint extractor). To agree with: WP4 map tools and WP5 verifier (Preeti) and WP0 (Pranay).
Source: schemas v1 in `routechat/schemas.py`, the handbook's WP3 extraction rules, and the README's "Decisions other packages depend on".

The ConstraintSet is the form the extractor fills in from the user's message. Map tools and the verifier read it. This page states what every field means so the three packages never disagree. The types below are the frozen schema's.

## Three general rules

1. **Units.** Distances in kilometres, durations in whole minutes, clock times as 24-hour `HH:MM`. "2 hours" becomes 120. "5k" becomes 5.0. "before 5" becomes `17:00`.
2. **Null means the user did not say.** A null origin or destination is missing and triggers a clarifying question. Lists are never null; an empty list means the user asked for nothing of that kind. (Same rule as the README: null is not zero and not an empty list.)
3. **Nothing is invented.** Place names are copied as the user wrote them. The extractor never produces coordinates, opening hours or facts. Map tools resolve places. Anything unclear goes into `ambiguities` as a sentence, not into a guessed value.

## Field by field

| Field | Type (schemas v1) | Meaning | Null or empty means | Example |
|---|---|---|---|---|
| origin | str or null | Start place, as written. City added only if the user said it. | Missing or personal ("home"). Code sets `missing_field = origin`. | "King Station, Toronto" |
| destination | str or null | End place, as written. | Missing or personal, unless `loop` is true. Code sets `missing_field = destination`. | "University of Toronto" |
| stops | list of Stop | Places to visit on the way. See Stop below. | No stops requested. | one stop, Canadian Tire |
| mode | walk, bike, hike, run, transit, car, multimodal, unspecified | How the user travels. | `unspecified`: code copies the classifier's primary_mode. | "bike" |
| modes_to_compare | list of modes, at most 2 | Only for compare requests. | Not a comparison. | ["walk", "transit"] |
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
| category | str or null | Set for a type of place. Null for a named place. The schema names `ev_charging_station` as an example category. | "coffee", "ev_charging_station" |
| required | bool | False only if the user said the stop is optional. | true |
| order | int or null | 1-based position if the user gave an order. Null means the map tools pick the best order. | null |

## Special cases, from the handbook

- Personal places ("home", "my office", "my university") become null plus an ambiguity. The pilot stores no saved places.
- A generic stop ("a coffee shop") becomes query = "coffee", category = "coffee".
- Scenic, countryside or quiet words add `scenic` and `quiet_roads` to prefer.
- Two trips in one message: extract the first only, add an ambiguity naming the second.
- Replan (turn 2): return the full merged trip. Start from the earlier ConstraintSet, change only what the user changed. "Skip the Canadian Tire" removes that stop and keeps everything else.
- Modes the pilot does not route (hike, transit): the extractor still fills the form normally. Map tools return `transit_not_supported` and the writer explains. The extractor does not refuse or relabel.

## Questions for Preeti (WP4, WP5)

1. Times are clock times only, with today assumed. Do map tools or the verifier ever need a date?
2. `stops.order` is 1-based, and `required = true` is a hard check in the verifier. Agreed?
3. For an empty `avoid` or `prefer` list, map tools send no custom-model rules at all. Agreed?
4. When `loop` is true and `destination` is null, map tools use the origin as both ends. Agreed?
5. Tara asked (8 Nov comment on the handbook) for `ev_range_km`, `charger_type` and a `fuel_or_charging` stop category. Schemas v1 are frozen without them. Do map tools need them, so we raise a schema-change pull request with Pranay, or is `category = ev_charging_station` on a stop enough for the EV use cases?

If the answers are yes, this page is v1 frozen.
