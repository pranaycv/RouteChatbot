# Owner: Person A (WP6 response writer), Team Rosedale, MMAI 894.
# Only Person A edits this file. Other packages may import it but should not change it;
# AI coding tools must not edit this file unless they are working on WP6.
# Written for this project with AI assistance; no code copied from other sources.
"""Fixed replies for every early exit. No model call.

The sentences are the handbook's, including its apostrophes. Three departures
are named here:

- transit names running as well as walking, cycling, and driving, because
  policy P-09 (12 Nov) added running to the pilot.
- no_route has a sentence because the schema names that error and the
  handbook does not print one. timeout uses the system-error sentence,
  because the graph stops the run with that reason before the writer is called.
- escalate drops "Here's what I could check: …". This reply is sent when there
  is no route, so nothing was checked. A handoff with a route is written by the
  model, which lists its checks.
"""

from __future__ import annotations

# Policy map: P-07 injection and data request, P-01 fleet refusal,
# P-04 booking, P-08 handoff, P-09 modes. P-03 and P-05 are prompt rules.

EMPTY = "What trip would you like me to plan?"
TOO_LONG = "That’s a lot to take in. Could you describe the trip in a few sentences?"
NOT_ENGLISH = "I can only help in English for now."
PROMPT_INJECTION = "I can’t change how I work, but I’m happy to plan a trip. Where are you going?"
DATA_REQUEST = "I can’t share information about other people or their searches."
CLARIFY_ORIGIN = "Where should the route start? An address, intersection or landmark works."
CLARIFY_DESTINATION = "Where are you heading?"
CLARIFY_STOPS = "Which stop do you need on the way?"
UNPARSEABLE = "I didn’t quite get that. Could you rephrase the trip?"
ESCALATE = "This needs a person, so I’ve passed your trip to our support team."
OUT_OF_SCOPE_B2B = "I can plan personal trips only. Route planning for delivery fleets or tours isn’t available."
BOOKING_OR_PAYMENT = "I can’t book or pay for anything, but I can plan how to get there."
LIVE_PARKING = "I don’t have live parking or store stock information."
UNSAFE_OR_ILLEGAL = "I can’t help with that request."
TRANSIT_NOT_SUPPORTED = (
    "Transit directions aren’t available yet. I can plan walking, running, cycling or driving."
)
HIKE_NOT_SUPPORTED = (
    "Hiking routes aren’t covered yet. I can plan walking, running, cycling or driving."
)
CHOOSE_MODE = "How would you like to travel: walking, running, cycling or driving?"
MODE_NOT_SUPPORTED = (
    "I can’t plan that way of travelling yet. I can plan walking, running, cycling or driving."
)
SYSTEM_ERROR_TEXT = "I couldn’t reach the map service just now. Please try again in a minute."
NO_ROUTE = "I couldn’t find a route between those places. Could you try a different start or end?"

GUARD_TEMPLATES: dict[str, str] = {
    "empty": EMPTY,
    "too_long": TOO_LONG,
    "not_english": NOT_ENGLISH,
    "prompt_injection": PROMPT_INJECTION,
    "data_request": DATA_REQUEST,
}

# refusal_reason "none" means the turn was not refused, so it has no sentence.
REFUSAL_TEMPLATES: dict[str, str] = {
    "prompt_injection": PROMPT_INJECTION,
    "data_request": DATA_REQUEST,
    "unsafe_or_illegal": UNSAFE_OR_ILLEGAL,
    "out_of_scope_b2b": OUT_OF_SCOPE_B2B,
    "out_of_scope_mode": TRANSIT_NOT_SUPPORTED,
    "booking_or_payment": BOOKING_OR_PAYMENT,
    "live_parking_or_inventory": LIVE_PARKING,
}

# clarify_field "none" means the classifier did not name a missing end.
CLARIFY_TEMPLATES: dict[str, str] = {
    "origin": CLARIFY_ORIGIN,
    "destination": CLARIFY_DESTINATION,
    "stops": CLARIFY_STOPS,
    "none": UNPARSEABLE,
}

# Codes the map tools and the graph can put on a handoff. The map tools use
# mode_not_supported for hike, a missing mode, and an unrouted combination.
ERROR_REASONS: tuple[str, ...] = (
    "place_not_found",
    "no_route",
    "transit_not_supported",
    "mode_not_supported",
    "tool_timeout",
    "system_error",
    "timeout",
)

ERROR_TEMPLATES: dict[str, str] = {
    "no_route": NO_ROUTE,
    "transit_not_supported": TRANSIT_NOT_SUPPORTED,
    "mode_not_supported": MODE_NOT_SUPPORTED,
    "tool_timeout": SYSTEM_ERROR_TEXT,
    "system_error": SYSTEM_ERROR_TEXT,
    "timeout": SYSTEM_ERROR_TEXT,
}


MODE_TEMPLATES: dict[str, str] = {
    "hike": HIKE_NOT_SUPPORTED,
    "unspecified": CHOOSE_MODE,
}


def mode_not_supported_text(mode: str | None) -> str:
    """The sentence for the mode the map tools turned away."""
    return MODE_TEMPLATES.get(mode, MODE_NOT_SUPPORTED)


def too_many_stops_text(limit: int) -> str:
    """Handbook sentence. The limit is trip.max_required_stops, which is 3."""
    return f"I can include up to {limit} stops. Which {limit} matter most?"


def place_not_found_text(query: str) -> str:
    """Name the place that failed. An empty query does not invent one."""
    name = query.strip() or "that place"
    return f"I couldn’t find “{name}”. Could you give an address or a nearby landmark?"
