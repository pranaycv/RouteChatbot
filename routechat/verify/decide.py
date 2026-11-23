"""Evaluate route candidates and return the verifier's graph state update."""

import re
from datetime import datetime, timezone
from math import inf, isfinite
from uuid import uuid4

from routechat.config import get_settings
from routechat.schemas import (
    Check,
    ConstraintSet,
    GuardResult,
    HandoffTicket,
    ResolvedPlace,
    RouteCandidate,
    RouteClassification,
    VerificationReport,
)
from routechat.verify.checks import check_bike_lanes, check_flatness, check_highways
from routechat.verify.sponsor import select_sponsor

_REVISABLE = {"avoid:highways", "prefer:bike_lanes", "flat"}


def _measurement(value):
    return value if value is not None and isfinite(value) and value >= 0 else None


def _duration(route):
    traffic = _measurement(route.duration_traffic_s)
    return traffic if traffic is not None else _measurement(route.duration_s)


def _minutes(value):
    if value and re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value):
        hours, minutes = map(int, value.split(":"))
        return hours * 60 + minutes
    return None


def _range_check(check_id, value, minimum, maximum, unit):
    if value is None:
        return Check(
            id=check_id, hard=True, status="unverifiable",
            evidence=f"No usable measurement for {check_id}.",
        )
    return Check(
        id=check_id, hard=True,
        status="met" if minimum <= value <= maximum else "unmet",
        evidence=f"{value:g} {unit}; allowed range {minimum:g} to {maximum:g} {unit}.",
    )


def _stop_checks(constraints, places, route):
    resolved_names = {
        place.query.casefold(): place.name.casefold()
        for place in places if place.role == "stop"
    }
    route_stops = [name.casefold() for name in route.stops_in_order]
    ordered = sorted(
        (stop for stop in constraints.stops if stop.required and stop.order is not None),
        key=lambda stop: stop.order,
    )
    confirmed_order = set()
    cursor = 0
    for stop in ordered:
        query = stop.query.casefold()
        aliases = {query, resolved_names.get(query, query)}
        for index in range(cursor, len(route_stops)):
            if route_stops[index] in aliases:
                confirmed_order.add(stop.order)
                cursor = index + 1
                break

    for stop in constraints.stops:
        query = stop.query.casefold()
        slug = re.sub(r"[^\w]+", "_", query).strip("_")
        if stop.required:
            present = (
                stop.order in confirmed_order if stop.order is not None
                else any(name in route_stops for name in (query, resolved_names.get(query)))
            )
            yield Check(
                id=f"stop:{slug}", hard=True, status="met" if present else "unmet",
                evidence="Required stop confirmed." if present else "Required stop or its order is missing.",
            )
        yield Check(
            id=f"hours:{slug}", hard=False, status="unverifiable",
            evidence="The stop's opening hours at arrival could not be confirmed.",
        )


def _checks(constraints, places, route, classification):
    settings = get_settings().verifier
    yield from _stop_checks(constraints, places, route)
    if "highways" in constraints.avoid:
        yield check_highways(route)
    for feature, distance in (("tolls", route.toll_m), ("ferries", route.ferry_m)):
        if feature in constraints.avoid:
            yield _range_check(f"avoid:{feature}", _measurement(distance), 0, 0, "m")
    if "unpaved" in constraints.avoid:
        yield Check(
            id="avoid:unpaved", hard=True, status="unverifiable",
            evidence="The provider gives no road-surface measurement.",
        )
    if "bike_lanes" in constraints.prefer:
        yield check_bike_lanes(route)
    if constraints.minimize_elevation:
        yield check_flatness(route)
    distance = _measurement(route.distance_m)
    duration = _duration(route)
    if constraints.max_distance_km is not None:
        maximum = constraints.max_distance_km * 1000 * settings.distance_tolerance
        yield _range_check("max_distance", distance, 0, maximum, "m")
    if constraints.max_duration_min is not None:
        maximum = constraints.max_duration_min * 60 * settings.duration_tolerance
        yield _range_check("max_duration", duration, 0, maximum, "s")
    if constraints.loop and constraints.target_distance_km is not None:
        target = constraints.target_distance_km * 1000
        tolerance = target * settings.loop_tolerance
        yield _range_check("loop_distance", distance, target - tolerance, target + tolerance, "m")
    if constraints.arrive_by is not None:
        departure = _minutes(constraints.depart_at)
        deadline = _minutes(constraints.arrive_by)
        if departure is None or deadline is None or duration is None:
            yield Check(
                id="arrive_by", hard=True, status="unverifiable",
                evidence="A valid departure time, deadline and route duration are needed.",
            )
        else:
            available = deadline - departure
            yield _range_check(
                "arrive_by", duration, 0, (available - settings.arrive_by_buffer_min) * 60, "s",
            )
    if constraints.check_closures or classification.needs_live_data:
        closures = route.closures
        yield Check(
            id="closures", hard=True,
            status="unverifiable" if closures is None else "unmet" if closures else "met",
            evidence="Closure data is unavailable." if closures is None
            else f"The provider returned {len(closures)} closures.",
        )
    if constraints.required_accessibility or "step_free" in constraints.prefer:
        yield Check(
            id="step_free", hard=constraints.required_accessibility, status="unverifiable",
            evidence="The provider cannot confirm a step-free route.",
        )
    for preference in ("scenic", "quiet_roads", "lit_streets"):
        if preference in constraints.prefer:
            yield Check(
                id=f"prefer:{preference}", hard=False, status="unverifiable",
                evidence=f"The provider cannot confirm {preference.replace('_', ' ')}.",
            )


def _blocked(check, constraints, route):
    return check.hard and (
        check.status == "unmet"
        or (
            check.status == "unverifiable"
            and (
                check.id == "step_free"
                or (check.id == "closures" and constraints.check_closures and route.mode == "car")
            )
        )
    )


def _duration_key(candidate):
    duration = _duration(candidate[0])
    return inf if duration is None else duration


def _choose(passing, constraints, classification):
    fastest = min(passing, key=_duration_key)
    if classification.route_action != "compare_modes" or constraints.compare_threshold_min is None:
        return fastest
    preferred_mode = constraints.mode
    if preferred_mode == "unspecified" and constraints.modes_to_compare:
        preferred_mode = constraints.modes_to_compare[0]
    duration_limit = _duration_key(fastest) + constraints.compare_threshold_min * 60
    preferred = [
        candidate for candidate in passing
        if candidate[0].mode == preferred_mode
        and _duration(candidate[0]) is not None
        and _duration_key(candidate) <= duration_limit
    ]
    return min(preferred, key=_duration_key, default=fastest)


def _report(decision, route=None, checks=(), reason=None):
    return VerificationReport(
        decision=decision, chosen_route=route.id if route is not None else None,
        checks=list(checks), must_mention=[check.id for check in checks if check.status != "met"],
        sponsored=None, handoff_reason=reason,
    )


def _handoff(state, constraints, reason, route=None, checks=()):
    report = _report("handoff", route, checks, reason)
    guard = state.get("guard")
    ticket = HandoffTicket(
        ticket_id=str(uuid4()), reason=reason,
        redacted_request=GuardResult.model_validate(guard).clean_text if guard is not None else "",
        constraints=constraints, report=report, created_at=datetime.now(timezone.utc),
    )
    return {"report": report, "handoff": ticket}


def verify_node(state):
    """Check every viable route and accept, request one retry, or hand off."""
    constraints = ConstraintSet.model_validate(state["constraints"])
    classification = RouteClassification.model_validate(state["classification"])
    places = [ResolvedPlace.model_validate(place) for place in state.get("places", [])]
    routes = [RouteCandidate.model_validate(route) for route in state.get("candidates", [])]
    evaluated = [
        (route, list(_checks(constraints, places, route, classification)))
        for route in routes if not route.error
    ]
    if not evaluated:
        reason = next((route.error for route in routes if route.error), "no_route")
        return _handoff(state, constraints, reason)

    passing = [
        (route, checks) for route, checks in evaluated
        if not any(_blocked(check, constraints, route) for check in checks)
    ]
    route, checks = _choose(passing or evaluated, constraints, classification)
    failures = {check.id for check in checks if check.status == "unmet"}
    if not passing and state.get("revision_count", 0) == 0:
        revisable = [
            (option, results) for option, results in evaluated
            if option.provider != "graphhopper"
            and any(result.status == "unmet" for result in results)
            and all(
                result.id in _REVISABLE
                for result in results
                if result.status == "unmet" or _blocked(result, constraints, option)
            )
        ]
        if revisable:
            route, checks = _choose(revisable, constraints, classification)
            return {"report": _report("revise", route, checks), "revision_count": 1}

    if not passing:
        return _handoff(state, constraints, "requirements_not_met", route, checks)
    if (
        route.provider != "graphhopper"
        and failures
        and failures <= _REVISABLE
        and state.get("revision_count", 0) == 0
    ):
        return {"report": _report("revise", route, checks), "revision_count": 1}

    report = _report("accept", route, checks)
    guard = state.get("guard")
    text = GuardResult.model_validate(guard).clean_text if guard is not None else ""
    departure = _minutes(constraints.depart_at)
    arrival = _minutes(constraints.arrive_by)
    evening = any(time is not None and time > 18 * 60 for time in (departure, arrival))
    evening = evening or bool(re.search(r"\b(evening|night|tonight)\b", text, re.I))
    report.sponsored = select_sponsor(
        report, route, constraints, route_action=classification.route_action,
        is_evening=evening,
        is_safety_trip=classification.needs_live_data,
    )
    return {"report": report, "handoff": None}
