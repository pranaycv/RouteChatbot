"""Device location: which end of a trip may use it, and how it is written in.

The browser gives the point only when a trip needs it (P-06), and only after the
user allows it. The point never goes to a model, and never into ConstraintSet,
which holds just the words "current location". The map tools get it as a place
that is already chosen, so those words are never searched for.
"""

from __future__ import annotations

import re
from math import isfinite
from typing import Any, Literal

from routechat.schemas import ConstraintSet, ResolvedPlace

QUERY = "current location"
NAME = "Current location"
# A fix wider than this, such as one guessed from an IP address, is no place to start a walk.
MAX_ACCURACY_M = 1000.0

End = Literal["origin", "destination"]

_HERE = (
    r"(?:(?:my|the)\s+)?(?:current|present|exact|live)\s+(?:location|position|place|spot|address)"
    r"|my\s+(?:location|position)|(?:right\s+)?here|where\s+i\s+am(?:\s+(?:right\s+)?now)?|where\s+i['’]m\s+at"
)
_HERE_ONLY = re.compile(rf"\s*(?:{_HERE})\s*[.!?]?\s*", re.IGNORECASE)
_FROM_HERE = re.compile(
    rf"\b(?:from|leav(?:e|ing)|start(?:ing)?(?:\s+(?:at|from))?)\s+(?:{_HERE})(?!\w)", re.IGNORECASE
)
# A start the user did name, though the pilot does not know where it is: my hotel, home, work.
_FROM_PERSONAL = re.compile(
    r"\b(?:from|leav(?:e|ing)|start(?:ing)?\s+(?:at|from))\s+(?:my|our|home\b|work\b|school\b|the\s+(?:office|hotel)\b)",
    re.IGNORECASE,
)
_NO_SAVED_PLACE = re.compile(r"saved place", re.IGNORECASE)


def _is_here(text: str | None) -> bool:
    return bool(text) and _HERE_ONLY.fullmatch(text) is not None


def _names_a_start(constraints: ConstraintSet, text: str) -> bool:
    """True when the user named a personal start, so the device may not be there."""
    if _FROM_PERSONAL.search(text):
        return True
    # Both ends but the start are known, so an unknown saved place is the start.
    return constraints.destination is not None and any(
        _NO_SAVED_PLACE.search(note) for note in constraints.ambiguities
    )


def device_ends(constraints: ConstraintSet, text: str) -> list[End]:
    """The ends of this trip the device location would fill, if there is one.

    An end the user called their own location, and a start they did not mention.
    A message with no place in it at all gets the question instead: asking the
    browser for a location because someone wrote "hi" is not planning a trip.
    """
    if constraints.missing_field == "unparseable":
        return []
    ends: list[End] = []
    said_here = _FROM_HERE.search(text) is not None
    if constraints.origin is None:
        has_a_trip = constraints.destination is not None or constraints.loop
        if said_here or (has_a_trip and not _names_a_start(constraints, text)):
            ends.append("origin")
    elif _is_here(constraints.origin):
        ends.append("origin")
    if _is_here(constraints.destination):
        # A trip from here to here is a loop without a length, so only the start is filled.
        ends.append("destination")
        if "origin" in ends and constraints.origin is None and not said_here:
            # "Route to here": the start is elsewhere and was not given.
            ends.remove("origin")
    return ends


def fill(constraints: ConstraintSet, ends: list[End], available: bool) -> ConstraintSet:
    """The trip with those ends set to the device location, or left missing when there is none."""
    if not ends:
        return constraints
    changed = constraints.model_copy(update={end: QUERY if available else None for end in ends})
    missing = None
    if changed.origin is None:
        missing = "origin"
    elif changed.destination is None and not changed.loop:
        missing = "destination"
    return changed.model_copy(update={"missing_field": missing})


def device_place(fix: Any) -> ResolvedPlace | None:
    """The browser's reply as a place. None for an error, a bad point, or too rough a fix."""
    if not isinstance(fix, dict):
        return None
    values = [fix.get(name) for name in ("lon", "lat", "accuracy")]
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
        return None
    lon, lat, accuracy = (float(value) for value in values)
    if not all(isfinite(value) for value in (lon, lat, accuracy)):
        return None
    if not (-180 <= lon <= 180 and -90 <= lat <= 90 and 0 <= accuracy <= MAX_ACCURACY_M):
        return None
    return ResolvedPlace(role="origin", query=QUERY, name=NAME, lon=lon, lat=lat, source="device")
