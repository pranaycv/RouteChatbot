# Owner: Person A (WP7 output guard), Team Rosedale, MMAI 894.
# Only Person A edits this file. Other packages may import it but should not change it;
# AI coding tools must not edit this file unless they are working on WP7.
# Written for this project with AI assistance; no code copied from other sources.
"""WP7 output guard. The last check before a sentence is shown.

Checks run in handbook order. Any failure discards the draft and replaces it
with a fixed sentence built from the route facts. The model text is never
repaired in place.
"""

from __future__ import annotations

import re
import time
from collections.abc import Sequence
from typing import Any

from routechat.agents.templates import ESCALATE, sponsor_text
from routechat.evidence import evidence_numbers
from routechat.guards.input_guard import _mask_personal_data
from routechat.logging import stage_line
from routechat.schemas import DisplayedStop, FinalAnswer, LineString

# Case-insensitive phrases from the handbook. "guarantee" also catches "guaranteed".
_PROMISES = (
    "guarantee",
    "definitely open",
    "is open now",
    "safe route",
    "perfectly safe",
    "booked",
    "reserved",
    "refund",
    "promise",
)
_NUMBER_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(kilometres|kilometers|km|minutes|minute|min|metres|meters|m)\b",
    re.IGNORECASE,
)


def _field(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value[name]
    return getattr(value, name)


def _round_km(distance_m: float | None) -> float | None:
    if distance_m is None:
        return None
    return round(distance_m / 1000, 1)


def _round_minutes(duration_s: float | None) -> int | None:
    if duration_s is None:
        return None
    return int(round(duration_s / 60))


def _round_metres(metres: float | None) -> int | None:
    if metres is None:
        return None
    return int(round(metres))


def _chosen(state: dict[str, Any]) -> Any | None:
    report = state.get("report")
    candidates = list(state.get("candidates") or [])
    chosen_id = _field(report, "chosen_route") if report is not None else None
    for candidate in candidates:
        if chosen_id and _field(candidate, "id") == chosen_id:
            return candidate
    return candidates[0] if candidates else None


def _duration_s(candidate: Any) -> float | None:
    traffic = _field(candidate, "duration_traffic_s")
    if traffic is not None:
        return traffic
    return _field(candidate, "duration_s")


def _keyword(check_id: str) -> str | None:
    """The word the handbook requires in the sentence, when it names one."""
    if check_id == "closures":
        return "closure"
    if check_id == "prefer:bike_lanes":
        return "bike"
    if check_id == "step_free":
        return "step-free"
    if check_id == "hours" or check_id.startswith("hours:"):
        return "hours"
    return None


# A check that failed. Saying it could not be confirmed would hide what the verifier found.
_UNMET = {
    "avoid:highways": "It uses highways.",
    "avoid:tolls": "It uses toll roads.",
    "avoid:ferries": "It uses a ferry.",
    "prefer:bike_lanes": "It has fewer bike lanes than you asked for.",
    "flat": "It isn't as flat as you asked.",
    "loop_distance": "It doesn't match the loop distance you asked for.",
    "max_distance": "It is longer than your distance limit.",
    "max_duration": "It takes longer than your time limit.",
    "arrive_by": "It doesn't reach the end by your deadline.",
    "closures": "There are closures on the way.",
}
_UNCONFIRMED = {
    "closures": "I couldn't check for closures.",
    "prefer:bike_lanes": "I couldn't confirm the bike lanes.",
    "step_free": "I couldn't confirm that it is step-free.",
    "flat": "I couldn't confirm that it is flat.",
    "arrive_by": "I couldn't confirm that it meets your deadline.",
}


def _mention_sentence(check_id: str, status: str = "unverifiable") -> str:
    """What to tell the user about a check that is not met, without its raw id."""
    kind, _, name = check_id.partition(":")
    name = name.replace("_", " ")
    if status == "unmet":
        if check_id in _UNMET:
            return _UNMET[check_id]
        if kind == "stop":
            return "It misses a stop you need, or takes it out of order."
        return f"It doesn't meet this request: {name or kind.replace('_', ' ')}."
    if check_id in _UNCONFIRMED:
        return _UNCONFIRMED[check_id]
    if kind == "hours":
        return "I couldn't confirm the opening hours."
    if kind == "avoid":
        return f"I couldn't confirm that it avoids {name}."
    return f"I couldn't confirm the {name or kind.replace('_', ' ')}."


def _other_routes(state: dict[str, Any], chosen: Any | None) -> list[Any]:
    """The routes a compare request also measured. A reply may give their distance and time."""
    return [
        candidate
        for candidate in state.get("candidates") or []
        if not _field(candidate, "error") and candidate is not chosen
    ]


def _allowed_numbers(
    candidate: Any | None, report: Any | None, others: Sequence[Any] = ()
) -> dict[str, set[float]]:
    """Numbers from the route, the verifier's evidence, the compared route, and the sponsor."""
    allowed: dict[str, set[float]] = {"km": set(), "min": set(), "m": set()}
    for other in others:
        km = _round_km(_field(other, "distance_m"))
        minutes = _round_minutes(_duration_s(other))
        if km is not None:
            allowed["km"].add(float(km))
        if minutes is not None:
            allowed["min"].add(float(minutes))
    if report is not None:
        for check in _field(report, "checks"):
            for unit, values in evidence_numbers(_field(check, "evidence")).items():
                allowed[unit] |= values
    if candidate is not None:
        km = _round_km(_field(candidate, "distance_m"))
        minutes = _round_minutes(_duration_s(candidate))
        climb = _round_metres(_field(candidate, "ascend_m"))
        if km is not None:
            allowed["km"].add(float(km))
        if minutes is not None:
            allowed["min"].add(float(minutes))
        if climb is not None:
            allowed["m"].add(float(climb))
    if report is not None and _field(report, "sponsored") is not None:
        distance = _round_metres(_field(_field(report, "sponsored"), "distance_m"))
        if distance is not None:
            allowed["m"].add(float(distance))
    return allowed


def _unit(token: str) -> str:
    lowered = token.lower()
    if lowered.startswith("k"):
        return "km"
    if lowered.startswith("min"):
        return "min"
    return "m"


def _hours_keyword_ok(text: str) -> bool:
    lowered = text.lower()
    return "hours" in lowered or re.search(r"\bopen\b", lowered) is not None


def failed_check(
    draft: Any, report: Any | None, candidate: Any | None, others: Sequence[Any] = ()
) -> str | None:
    """Return the first failing check name, or None when the draft may be shown."""
    message = _field(draft, "message")
    lowered = message.lower()
    must_mention = list(_field(report, "must_mention")) if report is not None else []
    mentioned = list(_field(draft, "mentioned_ids"))
    if set(mentioned) != set(must_mention):
        return "disclosure"
    for check_id in must_mention:
        keyword = _keyword(check_id)
        if keyword == "hours":
            present = _hours_keyword_ok(message)
        elif keyword is None:
            present = True
        else:
            present = keyword in lowered
        if not present:
            return "disclosure"
    sponsored = _field(report, "sponsored") if report is not None else None
    if sponsored is not None and "Sponsored" not in message:
        return "sponsorship"
    if any(phrase in lowered for phrase in _PROMISES):
        return "promises"
    allowed = _allowed_numbers(candidate, report, others)
    for match in _NUMBER_RE.finditer(message):
        value = float(match.group(1))
        unit = _unit(match.group(2))
        if not any(abs(value - item) < 1e-9 for item in allowed[unit]):
            return "numbers"
    _clean, masked = _mask_personal_data(message)
    if masked:
        return "personal_data"
    return None


def fallback_text(candidate: Any | None, report: Any | None) -> str:
    """A fixed sentence. It uses only numbers and warnings already on the route."""
    parts: list[str] = []
    handoff = report is not None and _field(report, "decision") == "handoff"
    if handoff:
        parts.append(ESCALATE)
    if candidate is not None and _field(candidate, "distance_m") is not None:
        km = _round_km(_field(candidate, "distance_m"))
        minutes = _round_minutes(_duration_s(candidate))
        climb = _round_metres(_field(candidate, "ascend_m"))
        sentence = f"The closest route I found is {km} km" if handoff else f"Here's a {km} km route"
        if minutes is not None:
            sentence += f", about {minutes} minutes"
        if climb is not None:
            sentence += f", with {climb} m of climbing"
        parts.append(sentence + ".")
        names = list(_field(candidate, "stops_in_order") or [])
        middle = names[1:-1] if len(names) > 2 else []
        if middle:
            parts.append("It stops at " + ", ".join(middle) + ".")
    else:
        parts.append("I couldn't finish a route for that trip.")
    if report is not None:
        status = {_field(check, "id"): _field(check, "status") for check in _field(report, "checks")}
        for check_id in _field(report, "must_mention"):
            sentence = _mention_sentence(check_id, status.get(check_id, "unverifiable"))
            if sentence not in parts:
                parts.append(sentence)
        sponsored = _field(report, "sponsored")
        if sponsored is not None:
            parts.append(
                sponsor_text(
                    _field(sponsored, "name"), _field(sponsored, "category"), _field(sponsored, "distance_m")
                )
            )
    return " ".join(parts)


def _geometry(candidate: Any | None) -> LineString | None:
    if candidate is None:
        return None
    geometry = _field(candidate, "geometry")
    if geometry is None:
        return None
    if isinstance(geometry, LineString):
        return geometry
    return LineString.model_validate(geometry)


def _stops(state: dict[str, Any]) -> list[DisplayedStop]:
    stops: list[DisplayedStop] = []
    for place in state.get("places") or []:
        if _field(place, "role") != "stop":
            continue
        stops.append(
            DisplayedStop(
                name=_field(place, "name"),
                lon=_field(place, "lon"),
                lat=_field(place, "lat"),
            )
        )
    return stops


def output_guard(state: dict[str, Any]) -> dict[str, FinalAnswer]:
    """LangGraph node. Returns only the final answer."""
    started = time.perf_counter()
    draft = state["draft"]
    report = state.get("report")
    candidate = _chosen(state)
    failed = failed_check(draft, report, candidate, _other_routes(state, candidate))
    geometry = _geometry(candidate)
    stops = _stops(state)
    if failed is None:
        final = FinalAnswer(
            approved=True,
            text=_field(draft, "message"),
            route_geojson=geometry,
            stops=stops,
            used_fallback_template=False,
        )
        decision = "ok"
    else:
        final = FinalAnswer(
            approved=False,
            text=fallback_text(candidate, report),
            route_geojson=geometry,
            stops=stops,
            used_fallback_template=True,
        )
        decision = f"fallback:{failed}"
    # The check that failed is enough to count fallbacks; the reply text stays out of the log.
    stage_line(
        "guard_out",
        str(state.get("request_id", "")),
        decision,
        (time.perf_counter() - started) * 1000,
    )
    return {"final": final}
