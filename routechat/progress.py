# Owner: Person A (WP7 app), Team Rosedale, MMAI 894.
# Written for this project with AI assistance; no code copied from other sources.
"""What the chat says about each step while a request runs. Plain words only, no model call."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from html import escape
from typing import Any

_DOING = {
    "guard_in": "Checking your message",
    "rewrite": "Refining your question",
    "classify": "Working out what you need",
    "extract": "Reading the trip details",
    "maps": "Finding the places and the route",
    "verify": "Checking the route",
    "write": "Writing the reply",
    "guard_out": "Checking the reply",
}
_ACTIONS = {
    "plan": "This is a trip to plan",
    "replan": "This is a change to your trip",
    "compare_modes": "This compares ways to travel",
    "lookup_only": "This asks about a route",
    "clarify": "Something may be missing",
    "escalate": "This needs a person",
    "refuse": "This is not something I can plan",
}
_DECISIONS = {
    "accept": "Route checked",
    "revise": "Looking for a better route",
    "handoff": "Route checked, a person is needed",
    "clarify": "I need to ask you something",
    "refuse": "This trip is not covered",
}
_MODES = {"walk": "walking", "run": "running", "bike": "cycling", "car": "driving", "hike": "hiking", "transit": "transit"}


def _field(value: Any, name: str) -> Any:
    if value is None:
        return None
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def doing(stage: str) -> str:
    return _DOING.get(stage, "Working on it")


def _details(constraints: Any) -> str:
    missing = _field(constraints, "missing_field")
    if missing in ("origin", "destination"):
        return f"The {'start' if missing == 'origin' else 'destination'} is missing"
    if missing:
        return "The trip is not clear"
    if _field(constraints, "too_many_stops"):
        return "There are too many stops"
    ends = [end for end in (_field(constraints, "origin"), _field(constraints, "destination")) if end]
    parts = [" to ".join(ends)]
    mode = _MODES.get(_field(constraints, "mode") or "")
    if mode:
        parts.append(mode)
    stops = len(_field(constraints, "stops") or [])
    if stops:
        parts.append(f"{stops} stop{'s' if stops > 1 else ''}")
    return "Trip read: " + ", ".join(part for part in parts if part)


def _route(update: Mapping[str, Any]) -> str:
    for candidate in update.get("candidates") or []:
        error = _field(candidate, "error")
        if error:
            return {
                "place_unclear": "A place needs your choice",
                "place_not_found": "A place was not found",
                "mode_not_supported": "The way of travelling is not set or not covered",
                "transit_not_supported": "Transit is not covered",
            }.get(error, "No route was found")
        distance = _field(candidate, "distance_m")
        seconds = _field(candidate, "duration_traffic_s") or _field(candidate, "duration_s")
        facts = []
        if distance is not None:
            facts.append(f"{distance / 1000:.1f} km")
        if seconds is not None:
            facts.append(f"{round(seconds / 60)} min")
        return "Route found" + (f": {', '.join(facts)}" if facts else "")
    return "No route was found"


def done(stage: str, update: Mapping[str, Any]) -> str | None:
    """The line for a finished step. None for a step that had nothing to do."""
    if stage == "guard_in":
        return "Message checked"
    if stage == "rewrite":
        status = _field(update.get("rewrite"), "status")
        return {
            "ready": "Question refined", "unclear": "More than one trip fits", "pass": "Question kept as you wrote it",
        }.get(status or "")
    if stage == "classify":
        return _ACTIONS.get(_field(update.get("classification"), "route_action") or "", "Request understood")
    if stage == "extract":
        constraints = update.get("constraints")
        return _details(constraints) if constraints is not None else None
    if stage == "maps":
        return _route(update)
    if stage == "verify":
        return _DECISIONS.get(_field(update.get("report"), "decision") or "", "Route checked")
    return {"write": "Reply written", "guard_out": "Reply checked"}.get(stage)


def board_html(finished: Sequence[str], current: str | None) -> str:
    """The steps so far, and the one that is running."""
    rows = [f"<li class='done'>{escape(text)}</li>" for text in finished]
    if current:
        rows.append(f"<li class='now'>{escape(current)}…</li>")
    return f"<ul class='rc-steps' role='status'>{''.join(rows)}</ul>"


def summary_html(finished: Sequence[str], seconds: float) -> str:
    """The same steps, folded under the reply."""
    return (
        f"<details class='rc-done'><summary>{len(finished)} steps, {seconds:.1f} s</summary>"
        f"{board_html(finished, None)}</details>"
    )
