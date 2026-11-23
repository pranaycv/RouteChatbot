"""GraphHopper request and response mapping for WP4."""

from math import asin, cos, isfinite, radians, sin, sqrt

from routechat.schemas import ConstraintSet, LineString, ResolvedPlace, RouteCandidate

PROFILES = {"walk": "foot", "run": "foot", "bike": "bike", "car": "car"}
_DETAILS = ["road_class", "bike_network", "toll", "road_environment", "average_slope"]


def distance_m(first, second):
    """Great-circle distance between longitude/latitude points."""
    lon1, lat1 = map(radians, first[:2])
    lon2, lat2 = map(radians, second[:2])
    value = sin((lat2 - lat1) / 2)**2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2)**2
    return 12_742_017.6 * asin(sqrt(min(1.0, value)))


def build_request(
    constraints: ConstraintSet, places: list[ResolvedPlace], mode: str,
) -> dict:
    """Build a Standard-mode request. WP5 checks preferences on the resulting route."""
    points = [[place.lon, place.lat] for place in places]
    request = {
        "profile": PROFILES[mode], "points": points, "points_encoded": False,
        "elevation": True, "instructions": False, "details": _DETAILS,
    }
    if constraints.loop and len(points) == 2 and points[0] == points[-1]:
        request.update(algorithm="round_trip", points=points[:1])
        if constraints.target_distance_km is not None:
            request["round_trip.distance"] = constraints.target_distance_km * 1000
    return request


def _detail_length(rows, cumulative_m, matches):
    if not isinstance(rows, list) or not rows:
        return None
    cursor = 0
    length = 0.0
    for row in rows:
        if not isinstance(row, list) or len(row) != 3:
            return None
        start, end, value = row
        if type(start) is not int or type(end) is not int:
            return None
        if start != cursor or not start <= end < len(cumulative_m) or not isinstance(value, str):
            return None
        if matches(value.upper()):
            length += cumulative_m[end] - cumulative_m[start]
        cursor = end
    return length if cursor == len(cumulative_m) - 1 else None


def _number(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) and value >= 0:
        return value
    return None


def candidate_from_path(path, places, mode, route_id):
    geometry = LineString.model_validate(path["points"])
    points = geometry.coordinates
    if len(points) < 2 or any(not (-180 <= lon <= 180 and -90 <= lat <= 90) for lon, lat, *_ in points):
        raise ValueError("invalid GraphHopper geometry")
    cumulative_m = [0.0]
    for first, second in zip(points, points[1:]):
        cumulative_m.append(cumulative_m[-1] + distance_m(first, second))

    details = path.get("details") or {}
    highway = _detail_length(details.get("road_class"), cumulative_m, {"MOTORWAY", "TRUNK"}.__contains__)
    toll = _detail_length(details.get("toll"), cumulative_m, lambda value: value != "NO")
    ferry = _detail_length(details.get("road_environment"), cumulative_m, lambda value: value == "FERRY")
    distance = _number(path.get("distance"))
    duration_ms = _number(path.get("time"))
    bike_share = None
    if mode == "bike" and distance:
        network = _detail_length(details.get("bike_network"), cumulative_m, lambda value: value != "MISSING")
        if network is not None:
            bike_share = min(1.0, network / distance)

    return RouteCandidate(
        id=route_id, mode=mode, provider="graphhopper", distance_m=distance,
        duration_s=None if duration_ms is None else duration_ms / 1000,
        ascend_m=_number(path.get("ascend")), geometry=geometry, highway_m=highway,
        bike_network_share=bike_share, toll_m=toll, ferry_m=ferry,
        stops_in_order=[place.name for place in places],
    )
