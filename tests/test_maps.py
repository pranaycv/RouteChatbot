"""WP4 provider-response fixtures and routing integration without live credentials."""

import httpx
import pytest

from routechat.agents import maps_node as maps
from routechat.maps.client import MapClient, MapError
from routechat.maps.graphhopper import build_request, candidate_from_path
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
        self.calls = []

    def request(self, api, method, url, params, body=None):
        self.calls.append((api, method, url, params, body))
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

    fixtures.calls.clear()
    revised = maps.maps_node(state | result | {"revision_count": 1})
    assert revised["candidates"][0].error is None
    assert [call[0] for call in fixtures.calls] == ["graphhopper", "directions"]
    rules = fixtures.calls[0][-1]["custom_model"]["priority"]
    assert {"if": "average_slope > 4", "multiply_by": "0.2"} in rules


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
    payload = build_request(constraints, places, "bike", revise=False)
    assert payload["algorithm"] == "round_trip"
    assert payload["round_trip.distance"] == 5000
    assert len(payload["points"]) == 1


def test_city_qualified_station_uses_locality_bias_and_exact_match():
    fixtures = FixtureMaps()
    origin = maps._origin_matches(fixtures, "King Station, Toronto")[0]
    assert origin.name == "King Station"
    assert (origin.lon, origin.lat) == tuple(POINTS[0])
    assert origin.query == "King Station, Toronto"
    assert fixtures.calls[0][0] == "geocode"
    assert fixtures.calls[0][3]["types"] == "place,locality"
    assert fixtures.calls[1][3]["q"] == "King Station"
    assert fixtures.calls[1][3]["proximity"] == "-79.4,43.65"


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
