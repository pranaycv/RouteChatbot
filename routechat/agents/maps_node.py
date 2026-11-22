"""WP4 place resolution and route candidates for the RouteChatbot graph."""

import re
import time
from itertools import permutations
from urllib.parse import quote

import httpx

from routechat.config import get_settings
from routechat.maps.client import MapClient, MapError
from routechat.maps.graphhopper import PROFILES, build_request, candidate_from_path, distance_m
from routechat.schemas import ConstraintSet, ResolvedPlace, RouteCandidate, RouteClassification

_MAPBOX = "https://api.mapbox.com"
_SEARCHBOX = f"{_MAPBOX}/search/searchbox/v1"
_ADDRESS = re.compile(r"^\s*\d+\s+\S|\s(?:&|and)\s", re.I)
_ROUTE = "https://graphhopper.com/api/1/route"
_DIRECTIONS = {"walk": "walking", "run": "walking", "bike": "cycling", "car": "driving-traffic"}


def _point(place):
    return place.lon, place.lat


def _search(client, query, role, proximity=None, category=None, *, locality=False, required_city=None):
    params = {"limit": 5, "language": "en"}
    country = get_settings().maps.geocode_country
    if country:
        params["country"] = country
    if proximity is not None:
        params["proximity"] = f"{proximity[0]},{proximity[1]}"

    if category:
        api, url = "searchbox", f"{_SEARCHBOX}/category/{quote(category, safe='')}"
    elif locality or _ADDRESS.search(query):
        api, url = "geocode", f"{_MAPBOX}/search/geocode/v6/forward"
        params["q"] = query
        if locality:
            params["types"] = "place,locality"
    else:
        api, url = "searchbox", f"{_SEARCHBOX}/forward"
        params["q"] = query

    reply = client.request(api, "GET", url, params)
    matches = []
    for feature in reply.get("features") or []:
        properties = feature.get("properties") or {}
        if required_city is not None:
            context = properties.get("context") or {}
            city_names = {
                str((context.get(level) or {}).get("name") or "").casefold()
                for level in ("place", "locality")
            }
            if required_city.casefold() not in city_names:
                continue
        coordinates = properties.get("coordinates") or {}
        points = properties.get("routable_points") or coordinates.get("routable_points") or []
        if points:
            point = [points[0].get("longitude"), points[0].get("latitude")]
        else:
            point = (feature.get("geometry") or {}).get("coordinates") or []
        if len(point) < 2 or not all(isinstance(value, (int, float)) for value in point[:2]):
            continue
        lon, lat = point[:2]
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            continue
        if proximity is not None and distance_m(proximity, point) > 50_000:
            continue
        matches.append(ResolvedPlace(
            role=role, query=query, name=properties.get("name") or query,
            lon=lon, lat=lat, source=api,
        ))
    return matches


def _origin_matches(client, query):
    if not query:
        return []
    name, separator, locality = query.partition(",")
    if not separator or _ADDRESS.search(query) or not locality.strip():
        return _search(client, query, "origin")

    # Bias the short place name to the explicitly requested locality.
    cities = _search(client, locality.strip(), "origin", locality=True)
    if not cities:
        return []
    city = cities[0]
    matches = _search(client, name.strip(), "origin", _point(city), required_city=city.name)
    matches.sort(key=lambda place: place.name.casefold() != name.strip().casefold())
    return [place.model_copy(update={"query": query}) for place in matches]


def _resolve(client, constraints):
    origins = _origin_matches(client, constraints.origin)
    if not origins:
        raise MapError("place_not_found")
    origin = origins[0]
    start = _point(origin)
    if constraints.loop and not constraints.destination:
        destination = origin.model_copy(update={"role": "destination"})
    else:
        destinations = (
            _search(client, constraints.destination, "destination", start)
            if constraints.destination else []
        )
        if not destinations:
            raise MapError("place_not_found")
        destination = destinations[0]
    end = _point(destination)
    settings = get_settings().maps
    direct_m = distance_m(start, end)
    maximum_km = max(settings.detour_min_km, settings.detour_fraction * direct_m / 1000)
    stops = []
    for stop in constraints.stops:
        candidates = _search(client, stop.query, "stop", start, stop.category)
        if candidates:
            def detour_km(place):
                return (distance_m(start, _point(place)) + distance_m(_point(place), end) - direct_m) / 1000

            place = min(candidates, key=detour_km)
            detour = max(0, detour_km(place))
            if detour <= maximum_km:
                stops.append((place.model_copy(update={"detour_km": detour}), stop.order))
                continue
        if stop.required:
            raise MapError("place_not_found")

    def path_length(ordered):
        points = [start, *(_point(place) for place, _ in ordered), end]
        return sum(distance_m(first, second) for first, second in zip(points, points[1:]))

    ordered = min(
        (candidate for candidate in permutations(stops)
         if [order for _, order in candidate if order is not None]
         == sorted(order for _, order in candidate if order is not None)),
        key=path_length,
    )
    return [origin, *(place for place, _ in ordered), destination]


def _directions(client, candidate, places):
    if candidate.geometry is None:
        return
    points = [_point(place) for place in places]
    if points[0] == points[-1] and len(points) == 2:
        return
    mode = candidate.mode
    coordinates = ";".join(f"{lon},{lat}" for lon, lat in points)
    params = {"geometries": "geojson", "overview": "full", "annotations": "duration"}
    if mode == "car":
        params["annotations"] = "duration,congestion,closure"
    try:
        reply = client.request(
            "directions", "GET",
            f"{_MAPBOX}/directions/v5/mapbox/{_DIRECTIONS[mode]}/{coordinates}", params,
        )
        route = (reply.get("routes") or [])[0]
    except (MapError, IndexError):
        return

    # Directions may select a different route. Transfer measurements only for
    # an identical path, so we do not certify the GraphHopper route from it.
    geometry = route.get("geometry") or {}
    if geometry.get("coordinates") != [point[:2] for point in candidate.geometry.coordinates]:
        return
    if mode == "car" and isinstance(route.get("duration"), (float, int)):
        candidate.duration_traffic_s = route["duration"]
    legs = route.get("legs") or []
    if legs and all(isinstance(leg.get("closures"), list) for leg in legs):
        candidate.closures = [
            event for leg in legs for field in ("closures", "incidents")
            for event in (leg.get(field) or []) if isinstance(event, dict)
        ]


def _candidate(client, constraints, places, mode, revise, needs_live_data):
    try:
        payload = client.request(
            "graphhopper", "POST", _ROUTE, {},
            build_request(constraints, places, mode, revise=revise),
        )
        route = candidate_from_path(payload["paths"][0], places, mode, f"gh_{mode}_1")
    except (MapError, KeyError, IndexError, TypeError, ValueError) as error:
        return _failed(mode, str(error) if isinstance(error, MapError) else "no_route")
    if needs_live_data:
        _directions(client, route, places)
    return route


def _failed(mode, error):
    return RouteCandidate(id=f"gh_{mode}_1", mode=mode, provider="graphhopper", error=error)


def maps_node(state):
    """Execute map tools and return only WP4-owned state."""
    constraints = ConstraintSet.model_validate(state["constraints"])
    classification = RouteClassification.model_validate(state["classification"])
    settings = get_settings()
    requested = (
        constraints.modes_to_compare if classification.route_action == "compare_modes"
        else [constraints.mode]
    )
    modes = list(dict.fromkeys(requested))[:settings.trip.max_modes_to_compare]
    if not modes or any(mode not in PROFILES for mode in modes):
        mode = next((mode for mode in modes if mode not in PROFILES), constraints.mode)
        error = "transit_not_supported" if mode == "transit" else "mode_not_supported"
        return {"places": [], "candidates": [_failed(mode, error)]}
    if len(constraints.stops) > settings.trip.max_required_stops:
        return {"places": [], "candidates": [_failed(modes[0], "too_many_stops")]}

    revise = state.get("revision_count", 0) == 1
    started = time.monotonic()
    with httpx.Client() as http:
        client = MapClient(http, state.get("deadline"))
        places = []
        try:
            places = (
                [ResolvedPlace.model_validate(place) for place in state["places"]]
                if revise and state.get("places") else _resolve(client, constraints)
            )
            candidates = [
                _candidate(client, constraints, places, mode, revise, classification.needs_live_data)
                for mode in modes
            ]
        except MapError as error:
            candidates = [_failed(mode, str(error)) for mode in modes]

    try:
        from routechat.logging import stage_line
    except ModuleNotFoundError as error:
        if error.name != "routechat.logging":
            raise
    else:
        stage_line(
            "maps", state.get("request_id", ""), "ok" if any(not route.error for route in candidates)
            else "error", (time.monotonic() - started) * 1000, api_calls=dict(client.calls),
        )
    return {"places": places, "candidates": candidates}
