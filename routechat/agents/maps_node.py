"""WP4 place resolution and route candidates for the RouteChatbot graph."""

import re
import time
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
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
# Below this share of matched words, the place found may not be the one asked for.
_NAME_MATCH = 0.7
# On a trip longer than this, a second place with the same name is worth a question.
_FAR_M = 50_000
# Two branches closer than this are one stop.
_APART_M = 500
_AREAS = {"country", "region", "district", "place", "locality"}
_SMALL_WORDS = {"the", "of", "at", "and", "a", "in", "on", "le", "la", "de", "du", "des"}
# A stop Mapbox names "King" is what people call King Station.
_STATIONS = {"railway_station", "public_transportation_station", "light_rail_station", "bus_station"}
# What people call a kind of stop, as Mapbox names it. Its list is /search/searchbox/v1/list/category.
# A word that is not here goes as it is, and Mapbox finds nothing for one it does not list.
_KINDS = {
    "lunch": "restaurant", "dinner": "restaurant", "supper": "restaurant", "meal": "restaurant",
    "breakfast": "breakfast_restaurant", "brunch": "brunch_restaurant", "diner": "diner_restaurant",
    "pizza": "pizza_restaurant", "tea": "teahouse", "snack": "convenience_store",
    "ev_charging_station": "charging_station", "ev_charging": "charging_station", "ev_charger": "charging_station",
    "fast_charger": "charging_station", "charger": "charging_station", "charging": "charging_station",
    "gas": "gas_station", "fuel": "gas_station", "petrol": "gas_station", "petrol_station": "gas_station",
    "fuel_or_charging": "gas_station", "groceries": "grocery", "grocery_store": "grocery",
    "bike_shop": "bicycle_shop", "bookstore": "book_store", "bookshop": "book_store", "gym": "fitness_center",
    "parking": "parking_lot", "doctor": "doctors_office", "drugstore": "pharmacy",
    "lookout": "viewpoint", "scenic_lookout": "viewpoint", "scenic_viewpoint": "viewpoint",
    # Mapbox lists no kind for these, so they are searched by name.
    "washroom": "public_toilet", "restroom": "public_toilet", "bathroom": "public_toilet", "toilet": "public_toilet",
}
_ROUTE = "https://graphhopper.com/api/1/route"
_DIRECTIONS = {"walk": "walking", "run": "walking", "bike": "cycling", "car": "driving-traffic"}


class _Match(ResolvedPlace):
    """A place with what is needed to judge it. Never leaves this module."""

    label: str = ""
    kind: str = ""
    score: float = 1.0
    exact: bool = False


class _PlaceError(MapError):
    """A place problem, with the places the writer needs to name it."""

    def __init__(self, reason, places):
        super().__init__(reason)
        self.places = [_plain(place) for place in places]


def _plain(place):
    return ResolvedPlace(**{name: getattr(place, name) for name in ResolvedPlace.model_fields})


def _point(place):
    return place.lon, place.lat


def _words(text):
    folded = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(char for char in folded if not unicodedata.combining(char)).replace("'", "").replace("’", "")
    return re.findall(r"[a-z0-9]+", text)


def _same_word(first, second, prefix):
    short, long = sorted((first, second), key=len)
    if first == second or (len(short) >= (1 if prefix else 3) and long.startswith(short)):
        return True
    return not first.isdigit() and SequenceMatcher(None, first, second).ratio() >= 0.8


def _score(query, name, context):
    """The share of the asked words found in the place's name, city, or province."""
    asked, named = _words(query), _words(name)
    prefix = len(asked) == len(named)
    asked = [word for word in asked if word not in _SMALL_WORDS] or asked
    if len(asked) == 1 and asked[0] == "".join(word[0] for word in named):
        return 1.0
    pool = named + [word for text in context for word in _words(text)]
    if not asked:
        return 0.0
    return sum(any(_same_word(word, other, prefix) for other in pool) for word in asked) / len(asked)


def _context(properties):
    """Place names around a match, and the city or province to show with it."""
    parts = properties.get("context") or {}
    names = {
        level: str((parts.get(level) or {}).get("name") or "")
        for level in ("street", "neighborhood", "locality", "place", "district", "region", "country")
    }
    region = str((parts.get("region") or {}).get("region_code") or "")
    city = names["place"] or names["locality"] or names["region"]
    return [text for text in (*names.values(), region) if text], city


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
        name = properties.get("name") or query
        context, city = _context(properties)
        kinds = properties.get("poi_category_ids") or []
        # The kind of place counts as part of its name: Yorkdale Place, a shopping mall, is Yorkdale Mall.
        around = context + [kind.replace("_", " ") for kind in kinds]
        known_as = name
        if _STATIONS.intersection(kinds) and "station" not in _words(name):
            known_as = f"{name} Station"
        matches.append(_Match(
            role=role, query=query, name=name, lon=lon, lat=lat, source=api,
            label=f"{name}, {city}" if city and city != name else name,
            kind=str(properties.get("feature_type") or ""),
            score=1.0 if category else _score(query, known_as, around),
            exact=sorted(_words(known_as)) == sorted(_words(query)),
        ))
    if role != "stop" and not locality:
        # Any distance is allowed, so the closest name wins, then the provider's own order.
        # One word, such as ROM, is too little to prefer an exact name over that order.
        several = len([word for word in _words(query) if word not in _SMALL_WORDS]) > 1
        matches.sort(key=lambda place: (several and not place.exact, -place.score))
    return matches


def _stop_matches(client, stop, proximity):
    """Places for a stop. A kind of stop, such as lunch, is searched as Mapbox names it, then by name."""
    if not stop.category:
        return _search(client, stop.query, "stop", proximity)
    word = "_".join(_words(stop.category.replace("_", " ")))
    kind = _KINDS.get(word, word)
    found = _search(client, stop.query, "stop", proximity, kind)
    if found:
        return found
    named = _search(client, kind.replace("_", " "), "stop", proximity)
    return [place.model_copy(update={"query": stop.query}) for place in named if place.score >= _NAME_MATCH]


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


def _picked(picked, query, role):
    """The place the user already chose for this name, if any."""
    for place in picked:
        if query and _words(place.name) == _words(query):
            return [_Match(**{**place.model_dump(), "role": role, "query": query}, label=place.name)]
    return None


def _options(matches):
    """The top match, and other places far from it that fit the name equally well."""
    top = matches[0]
    if top.kind in _AREAS:
        return [top]
    chosen = [top]
    for place in matches[1:]:
        if (
            place.score == top.score and place.kind not in _AREAS and place.label not in {other.label for other in chosen}
            and all(distance_m(_point(place), _point(other)) > _FAR_M for other in chosen)
        ):
            chosen.append(place)
    return chosen[:3]


def _ask(options):
    raise _PlaceError("place_unclear", [place.model_copy(update={"name": place.label}) for place in options])


def _check_names(origins, destinations):
    """Ask, rather than guess, when a name does not fit or fits several distant places."""
    for matches in (origins, destinations):
        if matches and matches[0].score < _NAME_MATCH:
            _ask(matches[:1])
    if not destinations or distance_m(_point(origins[0]), _point(destinations[0])) <= _FAR_M:
        return
    for matches in (origins, destinations):
        options = _options(matches)
        if len(options) > 1:
            _ask(options)


def _path(client, constraints, origin, destination):
    """Points along the way from the start to the end. The straight line when no route is found."""
    ends = [origin, destination]
    if constraints.mode in PROFILES:
        try:
            payload = client.request(
                "graphhopper", "POST", _ROUTE, {},
                build_request(constraints.model_copy(update={"loop": False}), ends, constraints.mode),
            )
            return candidate_from_path(payload["paths"][0], ends, constraints.mode, "path").geometry.coordinates
        except (MapError, KeyError, IndexError, TypeError, ValueError):
            pass
    (lon1, lat1), (lon2, lat2) = _point(origin), _point(destination)
    return [[lon1 + (lon2 - lon1) * step / 50, lat1 + (lat2 - lat1) * step / 50] for step in range(51)]


def _fitting(stop, places, ends):
    """The places a named stop can mean. The nearest of them is chosen later.

    The start or the end is not a stop: another branch with its name may take its place, and nothing
    else may. Then the name that fits best wins over a nearer one, and a place named after the stop wins
    over one that only mentions it, such as a listing "close to Tim Hortons". A type of place, such as
    coffee, has no name to fit.
    """
    if stop.category:
        return places

    def is_end(place):
        return any(
            _words(place.name) == _words(end.name) and distance_m(_point(place), _point(end)) <= _APART_M
            for end in ends
        )

    names = [_words(place.name) for place in places if is_end(place)]
    if names:
        places = [place for place in places if _words(place.name) in names and not is_end(place)]
    if not places:
        return []
    exact = [place for place in places if place.exact]
    if exact:
        return exact
    best = max(place.score for place in places)
    places = [place for place in places if place.score == best]
    return [place for place in places if _named_after(stop.query, place.name)] or places


def _named_after(query, name):
    """True when a name starts with the asked words, is at least half those words, or has them as its initials."""
    asked = [word for word in _words(query) if word not in _SMALL_WORDS] or _words(query)
    named = [word for word in _words(name) if word not in _SMALL_WORDS] or _words(name)
    if not asked or not named:
        return False
    if len(asked) == 1 and asked[0] == "".join(word[0] for word in _words(name)):
        return True
    starts = len(named) >= len(asked) and all(_same_word(one, other, False) for one, other in zip(asked, named))
    said = sum(any(_same_word(word, other, False) for other in asked) for word in named)
    return starts or said * 2 >= len(named)


def _spread(client, stop, count, path, maximum_km, ends):
    """Places for a stop that is wanted count times: different ones, spaced along the way."""
    lengths = [0.0]
    for first, second in zip(path, path[1:]):
        lengths.append(lengths[-1] + distance_m(first, second))
    found = []
    for number in range(1, count + 1):
        target = lengths[-1] * number / (count + 1)
        near = path[min(range(len(path)), key=lambda index: abs(lengths[index] - target))]

        def away(place):
            return min(distance_m(_point(place), point) for point in path)

        options = [
            place for place in _stop_matches(client, stop, near)
            if all(distance_m(_point(place), _point(other)) > _APART_M for other in found)
            and 2 * away(place) / 1000 <= maximum_km
        ]
        options = _fitting(stop, options, ends)
        if not options:
            break
        # Of the places close to the way, the one nearest the even spacing.
        place = min(options, key=lambda option: 2 * away(option) + distance_m(_point(option), near))
        found.append(place.model_copy(update={"name": place.label, "detour_km": 2 * away(place) / 1000}))
    return found


def _device(device, query, role):
    """The device location for an end that is it. Its words are not a name to search for."""
    if device is None or not query or query != device.query:
        return None
    return [_Match(**{**device.model_dump(), "role": role}, label=device.name)]


def _from_chat(client, constraints, seen):
    """The ends of this trip as places this chat already routed to, when they fit together.

    Both ends known, as on the way back, or one known and the other found near it.
    Otherwise the names are searched as usual, so a trip in another city is not pulled here.
    """
    def known(query, role):
        for place in seen:
            if query and _words(query) in (_words(place.query), _words(place.name)):
                return _Match(**{**place.model_dump(), "role": role, "query": query}, label=place.name)
        return None

    origin, destination = known(constraints.origin, "origin"), known(constraints.destination, "destination")
    if origin and destination:
        return [origin], [destination]
    if constraints.loop or not (constraints.origin and constraints.destination) or not (origin or destination):
        return None
    if origin:
        found = _search(client, constraints.destination, "destination", _point(origin))
        ends = [origin], found
    else:
        found = _search(client, constraints.origin, "origin", _point(destination))
        ends = found, [destination]
    if not found or found[0].score < _NAME_MATCH or distance_m(_point(ends[0][0]), _point(ends[1][0])) > _FAR_M:
        return None
    return ends


def _resolve(client, constraints, picked=(), device=None, seen=()):
    here = {device.query} if device is not None else set()
    chat = None
    if seen and not here & {constraints.origin, constraints.destination} and not any(
        _picked(picked, query, role)
        for query, role in ((constraints.origin, "origin"), (constraints.destination, "destination"))
    ):
        chat = _from_chat(client, constraints, seen)
    origins = chat[0] if chat else _device(device, constraints.origin, "origin") or _picked(picked, constraints.origin, "origin") or _origin_matches(client, constraints.origin)
    if not origins:
        raise _PlaceError("place_not_found", [])
    origin = origins[0]
    start = _point(origin)
    destinations = []
    if constraints.loop and not constraints.destination:
        destination = origin.model_copy(update={"role": "destination"})
    else:
        destinations = chat[1] if chat else (
            _device(device, constraints.destination, "destination")
            or _picked(picked, constraints.destination, "destination")
            or (
                _search(client, constraints.destination, "destination", start)
                if constraints.destination else []
            )
        )
        if not destinations:
            raise _PlaceError("place_not_found", [origin])
        destination = destinations[0]
    _check_names(origins, destinations)
    end = _point(destination)
    settings = get_settings().maps
    direct_m = distance_m(start, end)
    maximum_km = max(settings.detour_min_km, settings.detour_fraction * direct_m / 1000)
    stops = []
    wanted = Counter((stop.query.casefold(), stop.category) for stop in constraints.stops)
    path, repeated = None, {}
    for stop in constraints.stops:
        key = (stop.query.casefold(), stop.category)
        if wanted[key] > 1:
            if key not in repeated:
                path = path or _path(client, constraints, origin, destination)
                repeated[key] = _spread(client, stop, wanted[key], path, maximum_km, (origin, destination))
            if repeated[key]:
                stops.append((repeated[key].pop(0), stop.order))
                continue
            if stop.required:
                raise _PlaceError("place_not_found", [origin, *(place for place, _ in stops), destination])
            continue
        def detour_km(place):
            return (distance_m(start, _point(place)) + distance_m(_point(place), end) - direct_m) / 1000

        near = [
            place for place in _stop_matches(client, stop, start)
            if max(0, detour_km(place)) <= maximum_km
        ]
        near = _fitting(stop, near, (origin, destination))
        if near:
            place = min(near, key=detour_km)
            stops.append((place.model_copy(update={"detour_km": max(0, detour_km(place))}), stop.order))
            continue
        if stop.required:
            raise _PlaceError("place_not_found", [origin, *(place for place, _ in stops), destination])

    def path_length(ordered):
        points = [start, *(_point(place) for place, _ in ordered), end]
        return sum(distance_m(first, second) for first, second in zip(points, points[1:]))

    ordered = min(
        (candidate for candidate in permutations(stops)
         if [order for _, order in candidate if order is not None]
         == sorted(order for _, order in candidate if order is not None)),
        key=path_length,
    )
    return [_plain(place) for place in (origin, *(place for place, _ in ordered), destination)]


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


def _candidate(client, constraints, places, mode, needs_live_data):
    try:
        payload = client.request(
            "graphhopper", "POST", _ROUTE, {},
            build_request(constraints, places, mode),
        )
        route = candidate_from_path(payload["paths"][0], places, mode, f"gh_{mode}_1")
    except (MapError, KeyError, IndexError, TypeError, ValueError) as error:
        return _failed(mode, str(error) if isinstance(error, MapError) else "no_route")
    if needs_live_data:
        _directions(client, route, places)
    return route


def _failed(mode, error):
    return RouteCandidate(id=f"gh_{mode}_1", mode=mode, provider="graphhopper", error=error)


def _seen(state):
    return [ResolvedPlace.model_validate(place) for place in state.get("seen") or ()]


def _device_place(state):
    device = state.get("device")
    return None if device is None else ResolvedPlace.model_validate(device)


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
                if revise and state.get("places")
                else _resolve(client, constraints, state.get("picked") or (), _device_place(state), _seen(state))
            )
            candidates = [
                _candidate(client, constraints, places, mode, classification.needs_live_data)
                for mode in modes
            ]
        except MapError as error:
            places = getattr(error, "places", places)
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
