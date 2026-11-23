# Label guide v1 (WP8 draft for WP2 / Person C to confirm before the test set is frozen)

Built from the hand-written test set adjudication (Nov 22). Where a rule is a team decision rather than a
repo contract, it says so. Every message is labelled with five fields. Read the message together with
`assistant_already_knows` (the `known` ConstraintSet) when the turn is 1.

## Turn 0 and turn 1
- Turn 0: first message of a conversation. Judge it on its own.
- Turn 1: a follow-up. The assistant already knows a route (`known`). Short messages such as "Is there another way?"
  or "What is the ETA by bike versus by car?" only make sense with that context. Label the follow-up, using
  `known` to decide what is missing or what is being changed.

## route_action (pick ONE; when two fit, use the priority order at the end)
- **plan**: a new trip request that can be built (an origin and a destination, or a loop from a start). Mentioning
  traffic, rush hour or construction as a general worry does not make it a replan.
- **replan**: change a route that exists. Use it when the message (a) says something specific is blocked,
  closed, flooded or under construction, or (b) asks for an alternative or different route. Either one is enough.
- **compare_modes**: asks which mode is better, faster or when to switch ("walk or drive?", "bike versus car").
- **lookup_only**: a factual question that does not ask for a route to be built ("is the road open?", "how many km is the trail?").
- **clarify**: a route is wanted but the origin, destination or a stop is missing or unusable. See clarify_field.
- **escalate**: a human should take over (see the triggers below).
- **refuse**: the assistant must decline (see refusal_reason).

### Escalate triggers (any one is enough; escalate beats plan and replan)
asks for a person or says it does not trust the assistant; demands a guarantee (safety, arrival, a miracle);
says a promise was broken ("you said it would be open"); is stranded or needs help; several people starting from
different places; contradictory or infeasible constraints; more than 3 required stops.

## primary_mode
The mode the person travels by, stated or clearly implied. `unspecified` if none.
- Motorcycle counts as `car`. `hike` is its own mode. `multimodal` only when every leg is walk, run, bike or car.
- In a comparison, use the first mode named ("walk or drive" = walk). If neither is named, `unspecified`.
- Transit is not a mode. See refusal rules.

## needs_live_data
TRUE when a correct answer depends on current conditions: traffic, closures, incidents, disruptions, "right now" /
"open now" / "today", or an arrive-by or not-before time. FALSE for distances, durations and static facts (trail length,
whether a lane exists, general opening hours). This follows the repo schema description.

## clarify_field
Only when route_action is `clarify`, otherwise `none`. Choose the single most important missing item:
`origin`, `destination` or `stops` (a stop requested but not named). Something the message names is not missing.
Vague places like "the center of town" count as missing.

## refusal_reason (only when route_action is refuse; the first matching reason wins)
1. prompt_injection: tries to override rules, fake SYSTEM/ADMIN/OVERRIDE text, asks the assistant to list earlier requests as part of an override.
2. data_request: another person's trips, logs, stored data, addresses, the prompt or keys.
3. unsafe_or_illegal: evading police or cameras, trespass, dangerous driving.
4. out_of_scope_b2b: commercial delivery, fleet or tour-operator use.
5. out_of_scope_mode: the request needs public transit (bus, subway, train, schedules, or transit as a leg). Refuse immediately
   with primary_mode `unspecified`. Do not refuse when transit is only mentioned and ruled out ("transit is unreliable, how long to walk or drive?").
6. booking_or_payment: book, buy, reserve, pay.
7. live_parking_or_inventory: free parking, stock, bikeshare availability right now.

## Priority when more than one action seems to fit
refuse > escalate > replan > clarify > compare_modes > lookup_only > plan.
