"""WP4 provider-response fixtures and routing integration without live credentials."""

from collections import Counter

import httpx
import pytest

from routechat.agents import maps_node as maps
from routechat.maps.client import MapClient, MapError
from routechat.maps.graphhopper import build_request, candidate_from_path
from routechat.schemas import ResolvedPlace, Stop
from routechat.stubs import running_classification, running_constraints

POINTS = [[-79.4, 43.65], [-79.39, 43.65], [-79.38, 43.65]]
PATH = {
    "points": {"type": "LineString", "coordinates": POINTS},
    "distance": 1610,
    "time": 720_000,
    "ascend": 16,
    "details": {
        "road_class": [[0, 1, "TRUNK"], [1, 2, "RESIDENTIAL"]],
        "bike_network": [[0, 1, "MISSING"], [1, 2, "LOCAL"]],
        "toll": [[0, 2, "NO"]],
    },
}


def _feature(name, point, city="Toronto"):
    return {
        "properties": {"name": name, "context": {"place": {"name": city}}},
        "geometry": {"coordinates": point},
    }


class FixtureMaps:
    def __init__(self):
        self.calls = Counter()
        self.requests = []

    def request(self, api, method, url, params, body=None):
        self.calls[api] += 1
        self.requests.append((api, method, url, params, body))
        if api == "graphhopper":
            return {"paths": [PATH]}
        if api == "directions":
            return {"routes": [{"geometry": {"coordinates": POINTS}, "legs": [{}]}]}
        features = {
            "Toronto": [_feature("Toronto", POINTS[0])],
            "King Station": [
                _feature("King Station", [-79.55022, 43.92404], "King City"),
                _feature("King", [-79.39, 43.651]),
                _feature("King Station", POINTS[0]),
            ],
            "University of Toronto": [_feature("U of T", POINTS[2])],
            "Canadian Tire": [
                _feature("Off-route store", [-79.39, 43.67]),
                _feature("On-route store", POINTS[1]),
            ],
        }
        return {"features": features.get(params["q"], [])}


@pytest.fixture(autouse=True)
def no_environment_proxy(monkeypatch):
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(key, raising=False)


def _state(**changes):
    return {
        "constraints": running_constraints().model_copy(update=changes),
        "classification": running_classification(),
    }


def test_t_4_01_running_example_and_revision(monkeypatch):
    fixtures = FixtureMaps()
    monkeypatch.setattr(maps, "MapClient", lambda *_: fixtures)
    state = _state()
    result = maps.maps_node(state)
    candidate, = result["candidates"]
    assert candidate.error is None
    assert candidate.duration_s == 720
    assert candidate.stops_in_order == ["King Station", "On-route store", "U of T"]

    standard_request = next(call[-1] for call in fixtures.requests if call[0] == "graphhopper")
    assert "custom_model" not in standard_request
    assert "ch.disable" not in standard_request

    fixtures.requests.clear()
    revised = maps.maps_node(state | result | {"revision_count": 1})
    assert revised["candidates"][0].error is None
    assert [call[0] for call in fixtures.requests] == ["graphhopper", "directions"]
    assert "custom_model" not in fixtures.requests[0][-1]
    assert "ch.disable" not in fixtures.requests[0][-1]


def test_t_4_02_picks_smallest_detour():
    places = maps._resolve(FixtureMaps(), running_constraints())
    assert places[1].name == "On-route store"
    assert places[1].detour_km == pytest.approx(0, abs=1e-5)


def test_t_4_03_detail_lengths():
    candidate = candidate_from_path(PATH, [], "bike", "test")
    assert 800 < candidate.highway_m < 810
    assert candidate.bike_network_share == pytest.approx(0.5, abs=0.005)
    assert candidate.toll_m == 0
    assert candidate.ferry_m is None


def test_t_4_04_retries_timeout_once(monkeypatch):
    monkeypatch.delenv("FIXTURES", raising=False)
    monkeypatch.setenv("GRAPHHOPPER_KEY", "fixture")
    monkeypatch.setattr("routechat.maps.client.time.sleep", lambda _: None)

    def timeout(request):
        raise httpx.ReadTimeout("fixture timeout", request=request)

    with httpx.Client(transport=httpx.MockTransport(timeout)) as http:
        client = MapClient(http)
        with pytest.raises(MapError, match="^tool_timeout$"):
            client.request("graphhopper", "POST", "https://example.test", {})
        assert client.calls["graphhopper"] == 2


def test_t_4_05_transit_short_circuits(monkeypatch):
    def unexpected(*_):
        pytest.fail("transit should not call a provider")

    monkeypatch.setattr(maps, "MapClient", unexpected)
    result = maps.maps_node(_state(mode="transit"))
    assert result["candidates"][0].error == "transit_not_supported"


def test_t_4_06_unknown_place(monkeypatch):
    monkeypatch.setattr(maps, "MapClient", lambda *_: FixtureMaps())
    result = maps.maps_node(_state(destination="Unknown location"))
    assert result["candidates"][0].error == "place_not_found"


def test_t_4_07_no_closure_coverage_is_unknown(monkeypatch):
    monkeypatch.setattr(maps, "MapClient", lambda *_: FixtureMaps())
    candidate = maps.maps_node(_state())["candidates"][0]
    assert candidate.closures is None
    assert candidate.duration_traffic_s is None


def test_round_trip_distance_uses_metres():
    constraints = running_constraints().model_copy(update={
        "destination": None, "loop": True, "target_distance_km": 5, "stops": [],
    })
    places = maps._resolve(FixtureMaps(), constraints)
    payload = build_request(constraints, places, "bike")
    assert payload["algorithm"] == "round_trip"
    assert payload["round_trip.distance"] == 5000
    assert len(payload["points"]) == 1


def test_city_qualified_station_uses_locality_bias_and_exact_match():
    fixtures = FixtureMaps()
    origin = maps._origin_matches(fixtures, "King Station, Toronto")[0]
    assert origin.name == "King Station"
    assert (origin.lon, origin.lat) == tuple(POINTS[0])
    assert origin.query == "King Station, Toronto"
    assert fixtures.requests[0][0] == "geocode"
    assert fixtures.requests[0][3]["types"] == "place,locality"
    assert fixtures.requests[1][3]["q"] == "King Station"
    assert fixtures.requests[1][3]["proximity"] == "-79.4,43.65"


def test_graphhopper_free_plan_rejection_is_distinct_from_no_route(monkeypatch):
    monkeypatch.delenv("FIXTURES", raising=False)
    monkeypatch.setenv("GRAPHHOPPER_KEY", "fixture")

    def reject(_request):
        return httpx.Response(400, json={"message": "Free packages cannot use flexible mode"})

    with httpx.Client(transport=httpx.MockTransport(reject)) as http:
        client = MapClient(http)
        with pytest.raises(MapError, match="^flexible_mode_unavailable$"):
            client.request("graphhopper", "POST", "https://example.test", {})
        assert client.calls["graphhopper"] == 1


class _Places:
    """Answers each query with fixed features."""

    def __init__(self, answers):
        self.answers = answers

    def request(self, _api, _method, _url, params, _body=None):
        return {"features": self.answers.get(params.get("q"), [])}


def _trip(origin, destination):
    return running_constraints().model_copy(update={"origin": origin, "destination": destination, "stops": []})


def test_a_destination_may_be_any_distance_away():
    client = _Places({
        "CN Tower": [_feature("CN Tower", [-79.387, 43.642])],
        "Blue Mountain": [_feature("Blue Mountain", [-80.316, 44.501], "The Blue Mountains")],
    })
    assert [place.name for place in maps._resolve(client, _trip("CN Tower", "Blue Mountain"))] == [
        "CN Tower", "Blue Mountain",
    ]


def test_a_far_trip_with_two_places_of_that_name_is_asked_about():
    edmonton = _feature("Canada Place", [-113.49, 53.54], "Edmonton")
    vancouver = _feature("Canada Place", [-123.11, 49.29], "Vancouver")
    client = _Places({
        "Canada Place": [edmonton, vancouver],
        "Stanley Park": [_feature("Stanley Park", [-123.14, 49.30], "Vancouver")],
    })
    trip = _trip("Canada Place", "Stanley Park")
    with pytest.raises(MapError, match="^place_unclear$") as asked:
        maps._resolve(client, trip)
    options = asked.value.places
    assert [place.name for place in options] == ["Canada Place, Edmonton", "Canada Place, Vancouver"]
    # The place the user picks is used as it is, so the question is not asked again.
    chosen = maps._resolve(client, _trip("Canada Place, Vancouver", "Stanley Park"), [options[1]])
    assert (chosen[0].lon, chosen[0].lat) == (-123.11, 49.29)


def test_a_name_that_does_not_fit_is_asked_about():
    client = _Places({
        "CN Tower": [_feature("CN Tower", [-79.387, 43.642])],
        "Zzyx Hall": [_feature("Hallam Street", [-79.43, 43.67])],
    })
    with pytest.raises(MapError, match="^place_unclear$") as asked:
        maps._resolve(client, _trip("CN Tower", "Zzyx Hall"))
    assert asked.value.places[0].name == "Hallam Street, Toronto"


def test_an_unknown_destination_keeps_the_origin_that_was_found():
    client = _Places({"CN Tower": [_feature("CN Tower", [-79.387, 43.642])]})
    with pytest.raises(MapError, match="^place_not_found$") as missing:
        maps._resolve(client, _trip("CN Tower", "Nowhere"))
    assert [place.role for place in missing.value.places] == ["origin"]


@pytest.mark.parametrize(("query", "name", "fits"), [
    ("the ROM", "Royal Ontario Museum", True),
    ("University of Toronto", "U of T", True),
    ("Scoatiabank Arena", "Scotiabank Arena", True),
    ("Hamilton City Hall", "Old City Hall", False),
    ("younge - eglinton crossing", "Crossing-Labrecque", False),
])
def test_name_fit(query, name, fits):
    assert (maps._score(query, name, []) >= maps._NAME_MATCH) is fits



def test_a_transit_stop_is_found_by_its_station_name():
    """Mapbox names the Toronto subway stop "King". A "King Station" elsewhere must not win over it."""
    subway = _feature("King", [-79.378, 43.649])
    subway["properties"]["poi_category_ids"] = ["railway_station", "transportation"]
    other = _feature("King Station", [-79.550, 43.924], "King")
    other["properties"]["poi_category_ids"] = ["railway_station", "transportation"]
    client = _Places({
        "King Station": [subway, other],
        "University of Toronto": [_feature("University of Toronto", [-79.395, 43.661])],
    })
    origin = maps._resolve(client, _trip("King Station", "University of Toronto"))[0]
    assert (origin.lon, origin.lat) == (-79.378, 43.649)


def test_the_kind_of_place_counts_as_part_of_its_name():
    mall = _feature("Yorkdale Place", [-79.45, 43.725], "North York")
    mall["properties"]["poi_category_ids"] = ["shopping", "shopping_mall"]
    client = _Places({"Yorkdale Mall": [mall], "Casa Loma": [_feature("Casa Loma", [-79.409, 43.678])]})
    assert maps._resolve(client, _trip("Yorkdale Mall", "Casa Loma"))[0].name == "Yorkdale Place"


class _Branches(_Places):
    """Tim Hortons every tenth of a degree, and no route, so the straight line is the way."""

    def request(self, api, method, url, params, body=None):
        if api == "graphhopper":
            raise MapError("no_route")
        if params.get("q") == "Tim Hortons":
            lon, lat = (float(value) for value in params["proximity"].split(","))
            near = sorted((round(-79 - step / 10, 1) for step in range(1, 10)), key=lambda value: abs(value - lon))
            return {"features": [_feature("Tim Hortons", [value, 43.7], f"Town {value}") for value in near[:5]]}
        return super().request(api, method, url, params, body)


def _two_stops(count=2):
    stop = Stop(query="Tim Hortons", category=None, required=True, order=None)
    return _trip("CN Tower", "Barrie Hall").model_copy(update={"stops": [stop] * count})


def test_a_stop_wanted_twice_is_two_places_spread_along_the_way():
    client = _Branches({
        "CN Tower": [_feature("CN Tower", [-79.0, 43.7])],
        "Barrie Hall": [_feature("Barrie Hall", [-80.0, 43.7], "Barrie")],
    })
    places = maps._resolve(client, _two_stops())
    assert [place.role for place in places] == ["origin", "stop", "stop", "destination"]
    assert [place.lon for place in places[1:3]] == [-79.3, -79.7]
    # Each branch is named with its town, so the two can be told apart.
    assert [place.name for place in places[1:3]] == ["Tim Hortons, Town -79.3", "Tim Hortons, Town -79.7"]


def test_too_few_branches_is_place_not_found():
    client = _Places({
        "CN Tower": [_feature("CN Tower", [-79.0, 43.7])],
        "Barrie Hall": [_feature("Barrie Hall", [-80.0, 43.7], "Barrie")],
        "Tim Hortons": [_feature("Tim Hortons", [-79.3, 43.7])],
    })
    with pytest.raises(MapError, match="^place_not_found$"):
        maps._resolve(client, _two_stops())


def _place(name, point, role="origin", query=None):
    return ResolvedPlace(role=role, query=query or name, name=name, lon=point[0], lat=point[1], source="searchbox")


def test_the_way_back_uses_the_places_of_the_trip_out():
    seen = [_place("Casa Loma", [-79.41, 43.68]), _place("High Park", [-79.46, 43.65], "destination")]
    client = _Places({"High Park": [_feature("High Park", [-82.36, 42.98], "Sarnia")]})
    back = maps._resolve(client, _trip("High Park", "Casa Loma"), seen=seen)
    assert [(place.role, place.lon) for place in back] == [("origin", -79.46), ("destination", -79.41)]


def test_a_known_place_is_used_with_one_found_near_it():
    seen = [_place("High Park", [-79.46, 43.65], "destination")]
    client = _Places({
        "High Park": [_feature("High Park", [-82.36, 42.98], "Sarnia")],
        "CN Tower": [_feature("CN Tower", [-79.387, 43.642])],
    })
    assert maps._resolve(client, _trip("High Park", "CN Tower"), seen=seen)[0].lon == -79.46
    assert maps._resolve(client, _trip("CN Tower", "High Park"), seen=seen)[-1].lon == -79.46


def test_a_known_place_does_not_pull_a_trip_from_another_city():
    seen = [_place("Union Station", [-79.38, 43.645])]
    client = _Places({
        "Union Station": [_feature("Union Station", [-97.134, 49.889], "Winnipeg")],
        "The Forks": [_feature("The Forks", [-97.131, 49.887], "Winnipeg")],
    })
    assert maps._resolve(client, _trip("Union Station", "The Forks"), seen=seen)[0].lon == -97.134


LISTING = "2 Bedrooms Available to Make Your Stay a Pleasant. Close To Tim Hortons, etc."
ENDS = {"CN Tower": [_feature("CN Tower", POINTS[0])], "Barrie Hall": [_feature("Barrie Hall", POINTS[2])]}


def _one_stop(query, origin="CN Tower", destination="Barrie Hall"):
    stop = Stop(query=query, category=None, required=True, order=None)
    return _trip(origin, destination).model_copy(update={"stops": [stop]})


def _stop(found, trip):
    return next(place for place in maps._resolve(_Places({**ENDS, **found}), trip) if place.role == "stop")


def test_a_stop_is_the_place_itself_and_not_a_listing_that_mentions_it():
    found = {"Tim Hortons": [_feature(LISTING, POINTS[1]), _feature("Tim Hortons", [-79.39, 43.651])]}
    assert _stop(found, _one_stop("Tim Hortons")).name == "Tim Hortons"


def test_a_stop_wanted_twice_skips_a_listing_that_mentions_it():
    found = {"Tim Hortons": [
        _feature(LISTING, [-79.3933, 43.65]),
        _feature("Tim Hortons", [-79.394, 43.651]),
        _feature("Tim Hortons", [-79.386, 43.651]),
    ]}
    places = maps._resolve(_Places({**ENDS, **found}), _two_stops())
    assert [place.name for place in places if place.role == "stop"] == ["Tim Hortons, Toronto"] * 2


def test_a_crossing_is_not_swapped_for_a_nearer_one():
    found = {"Markham Rd at Finch Ave East": [
        _feature("Kennedy Rd at Finch Ave East", POINTS[1]),
        _feature("Finch Ave East at Markham Rd", [-79.39, 43.651]),
    ]}
    assert _stop(found, _one_stop("Markham Rd at Finch Ave East")).name == "Finch Ave East at Markham Rd"


def test_a_longer_name_that_starts_with_the_words_is_kept():
    found = {"Ripleys": [
        _feature("Cosy condo in the heart of downtown close to Ripleys", POINTS[1]),
        _feature("Ripleys Aquarium of Canada and gift shop", [-79.39, 43.651]),
    ]}
    assert _stop(found, _one_stop("Ripleys")).name.startswith("Ripleys Aquarium")


def test_a_short_form_still_finds_its_place():
    found = {"ROM": [_feature("Druxys ROM Cafe", POINTS[1]), _feature("Royal Ontario Museum", [-79.39, 43.651])]}
    assert _stop(found, _one_stop("ROM")).name == "Royal Ontario Museum"


@pytest.mark.parametrize("end", ["origin", "destination"])
def test_a_stop_is_another_branch_and_not_the_start_or_the_end(end):
    here, other = POINTS[0] if end == "origin" else POINTS[2], [-79.39, 43.652]
    found = {"Tim Hortons": [_feature("Tim Hortons", here), _feature("Tim Hortons", other)]}
    place = _stop(found, _one_stop("Tim Hortons", **{end: "Tim Hortons"}))
    assert [place.lon, place.lat] == other


def test_a_stop_at_the_start_with_no_other_branch_is_asked_about():
    """A shop inside the mall is not a second mall, so nothing takes its place."""
    found = {"Yorkdale Mall": [
        _feature("Yorkdale Mall", POINTS[0]),
        _feature("Trade Secrets | Yorkdale Mall", POINTS[0]),
    ]}
    with pytest.raises(MapError, match="^place_not_found$"):
        _stop(found, _one_stop("Yorkdale Mall", origin="Yorkdale Mall"))


def test_a_stop_wanted_twice_is_not_the_start():
    found = {"Tim Hortons": [
        _feature("Tim Hortons", POINTS[0]),
        _feature("Tim Hortons", [-79.392, 43.652]),
        _feature("Tim Hortons", [-79.384, 43.652]),
    ]}
    trip = _two_stops().model_copy(update={"origin": "Tim Hortons"})
    stops = [place.lon for place in maps._resolve(_Places({**ENDS, **found}), trip) if place.role == "stop"]
    assert sorted(stops) == [-79.392, -79.384]


def test_a_type_of_place_at_the_start_is_still_allowed():
    """Coffee inside the station the trip starts from is a fair stop: it is not the station."""
    trip = _trip("CN Tower", "Barrie Hall").model_copy(
        update={"stops": [Stop(query="coffee", category="coffee", required=True, order=None)]}
    )
    assert _stop({None: [_feature("Union Coffee", POINTS[0])]}, trip).name == "Union Coffee"
