"""Device location: when a trip waits for it, how it is used, and what happens without it."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from routechat.agents import maps_node as maps
from routechat.device import NAME, QUERY, device_ends, device_place, fill
from routechat.graph import invoke, question_key, resume, wants_device
from routechat.schemas import ConstraintSet, GuardResult, ResolvedPlace, RouteClassification
from routechat.stubs import running_classification, running_constraints, stub_nodes

_APP = Path(__file__).resolve().parents[1] / "routechat" / "app.py"
FIX = {"lon": -79.391234567, "lat": 43.651234567, "accuracy": 20.0}


def _trip(**changes) -> ConstraintSet:
    data = {**running_constraints().model_dump(), "stops": [], "ambiguities": [], **changes}
    if "missing_field" not in changes:
        end = "origin" if data["origin"] is None else "destination" if data["destination"] is None else None
        data["missing_field"] = end
    return ConstraintSet.model_validate(data)


@pytest.mark.parametrize(
    ("text", "changes", "ends"),
    [
        ("find a route to Union Station Toronto", {"origin": None}, ["origin"]),
        ("a 5 km run loop", {"origin": None, "destination": None, "loop": True}, ["origin"]),
        ("from my current location to the ROM", {"origin": "my current location"}, ["origin"]),
        ("walk from here to the ROM", {"origin": None, "ambiguities": ["here: no saved places"]}, ["origin"]),
        ("route from the ROM to my location", {"destination": "my location"}, ["destination"]),
        ("route from where I am to the ROM", {"origin": "where I am"}, ["origin"]),
        # A start the user named, even one the pilot cannot look up, is never swapped for the device.
        ("from my hotel to the ROM", {"origin": None}, []),
        ("leaving home for the ROM", {"origin": None}, []),
        ("my office to the ROM", {"origin": None, "ambiguities": ["my office: no saved places"]}, []),
        ("King Station to the ROM", {}, []),
        ("from Here Cafe to the ROM", {"origin": "Here Cafe"}, []),
        # The start is somewhere else and was not given.
        ("get a friend to my location", {"origin": None, "destination": "my location"}, ["destination"]),
        # Nothing to plan yet, so the browser is not asked.
        ("hi", {"origin": None, "destination": None}, []),
        ("??", {"origin": None, "missing_field": "unparseable"}, []),
    ],
)
def test_which_ends_use_the_device(text, changes, ends):
    assert device_ends(_trip(**changes), text) == ends


def test_an_unknown_destination_does_not_stop_the_device_start():
    trip = _trip(origin=None, destination=None, ambiguities=["my office: no saved places"])
    assert device_ends(trip, "from here to my office") == ["origin"]
    assert fill(trip, ["origin"], True).missing_field == "destination"


@pytest.mark.parametrize(
    "fix",
    [
        None, "x", {}, {"error": "denied"}, {**FIX, "accuracy": 25_000}, {**FIX, "lat": 91},
        {**FIX, "lon": float("nan")}, {**FIX, "lat": "43.6"}, {**FIX, "lat": True}, {**FIX, "accuracy": None},
    ],
)
def test_a_missing_bad_or_rough_fix_is_no_location(fix):
    assert device_place(fix) is None


def _nodes(constraints: ConstraintSet, action="plan", clarify="none"):
    """Stub stages that record their visits, around one extraction."""
    visits, nodes = [], stub_nodes()
    nodes["guard_in"] = lambda _state: {
        "guard": GuardResult(decision="allow", clean_text="find a route to Union Station", masked=[], reason=None)
    }
    nodes["classify"] = lambda _state: {
        "classification": RouteClassification(
            route_action=action, primary_mode="bike", needs_live_data=False,
            clarify_field=clarify, refusal_reason="none",
        )
    }
    nodes["extract"] = lambda _state: {"constraints": constraints}
    for name, stub in list(nodes.items()):
        nodes[name] = lambda state, name=name, stub=stub: visits.append(name) or stub(state)
    return nodes, visits


PAYLOAD = {
    "request_id": "r_dev", "utterance": "find a route to Union Station", "turn_index": 0, "known": None,
    "clarify_count": 0, "asked": [], "revision_count": 0, "locatable": True,
}


@pytest.fixture
def lines(monkeypatch):
    from routechat import graph

    logged = []
    monkeypatch.setattr(graph, "log_request", lambda state, ms: logged.append((state["request_id"], ms)))
    return logged


@pytest.mark.parametrize(("action", "clarify"), [("plan", "none"), ("clarify", "origin")])
def test_a_trip_without_a_start_waits_then_goes_on_without_reading_again(lines, action, clarify):
    nodes, visits = _nodes(_trip(origin=None), action, clarify)
    waiting = invoke(PAYLOAD, nodes)
    assert wants_device(waiting) and waiting.get("final") is None
    assert visits == ["guard_in", "classify", "extract"] and lines == []

    place = device_place(FIX)
    state = resume(waiting, place, nodes)
    assert visits == ["guard_in", "classify", "extract", "maps", "verify", "write", "guard_out"]
    assert state["classification"].route_action == "plan"
    assert state["constraints"].origin == QUERY and state["constraints"].missing_field is None
    assert state["device"] == place and state["final"] is not None
    assert [request_id for request_id, _ms in lines] == ["r_dev"]


@pytest.mark.parametrize(("action", "clarify"), [("plan", "none"), ("clarify", "origin")])
def test_without_a_location_the_start_is_asked_for(lines, action, clarify):
    nodes, visits = _nodes(_trip(origin="my location"), action, clarify)
    state = resume(invoke(PAYLOAD, nodes), None, nodes)
    assert visits == ["guard_in", "classify", "extract", "write", "guard_out"]
    assert state["constraints"].origin is None
    assert question_key(state) == "missing:origin"
    assert len(lines) == 1


def test_a_blocked_location_still_counts_as_a_repeated_question(lines):
    nodes, _ = _nodes(_trip(origin=None), "clarify", "origin")
    state = resume(invoke({**PAYLOAD, "asked": ["missing:origin"], "clarify_count": 1}, nodes), None, nodes)
    assert state["classification"].route_action == "escalate"


@pytest.mark.parametrize("payload", [{}, {"locatable": False}])
def test_a_caller_with_no_browser_keeps_the_question(lines, payload):
    nodes, visits = _nodes(_trip(origin=None), "clarify", "origin")
    base = {key: value for key, value in PAYLOAD.items() if key != "locatable"}
    state = invoke({**base, **payload}, nodes)
    assert visits[-1] == "guard_out" and question_key(state) == "missing:origin"


def test_a_trip_still_missing_its_destination_asks_for_that_first(lines):
    trip = _trip(origin="my current location", destination=None, missing_field="destination")
    nodes, visits = _nodes(trip, "clarify", "origin")
    state = invoke(PAYLOAD, nodes)
    assert not wants_device(state) and "guard_out" in visits
    assert question_key(state) == "missing:destination"


def test_a_typed_start_never_waits(lines):
    nodes, visits = _nodes(_trip())
    assert not wants_device(invoke(PAYLOAD, nodes)) and "maps" in visits


POINTS = [[-79.391234567, 43.651234567], [-79.38, 43.65]]


class _Maps:
    def __init__(self):
        self.sent = []

    def request(self, api, method, url, params, body=None):
        self.sent.append((api, params, body))
        assert params.get("q") != QUERY, "the words are not a place name"
        return {"features": [{"properties": {"name": "Union Station"}, "geometry": {"coordinates": POINTS[1]}}]}


def test_the_map_tools_start_at_the_device_and_search_only_the_destination():
    client = _Maps()
    trip = _trip(origin=QUERY, destination="Union Station")
    places = maps._resolve(client, trip, (), device_place(FIX))
    assert [(place.role, place.name, place.source) for place in places] == [
        ("origin", NAME, "device"), ("destination", "Union Station", "searchbox"),
    ]
    assert (places[0].lon, places[0].lat) == (FIX["lon"], FIX["lat"])
    assert [params["q"] for _api, params, _body in client.sent] == ["Union Station"]


def test_the_device_can_be_the_destination():
    trip = _trip(origin="Union Station", destination=QUERY)
    places = maps._resolve(_Maps(), trip, (), device_place(FIX))
    assert [(place.role, place.source) for place in places] == [("origin", "searchbox"), ("destination", "device")]


def test_coordinates_stay_out_of_the_log_the_ticket_and_the_trip_fields(tmp_path, monkeypatch):
    """P-06: the device point is used only to plan the trip."""
    log = tmp_path / "events.jsonl"
    monkeypatch.setenv("ROUTECHAT_LOG", str(log))
    nodes, _ = _nodes(_trip(origin=None))
    state = resume(invoke(PAYLOAD, nodes), device_place(FIX), nodes)
    assert "43.65" not in log.read_text() and "79.39" not in log.read_text()
    assert "43.65" not in state["constraints"].model_dump_json()


def _app(monkeypatch, fix):
    """The app with a browser that replies fix, and a graph that waits once."""
    from routechat import graph, locate
    from routechat.schemas import FinalAnswer

    resumed, sent, asked = [], [], []

    def done(state, text):
        return {**state, "final": FinalAnswer(
            approved=True, text=text, route_geojson=None, stops=[], used_fallback_template=False)}

    def fake_invoke(payload, **_):
        sent.append(payload)
        base = {**payload, "classification": running_classification(), "constraints": _trip(origin=QUERY)}
        if "from" in payload["latest"]:
            return done({**base, "constraints": running_constraints()}, "typed")
        return {**base, "device_ends": ["origin"], "locatable": True, "spent_ms": 1.0}

    def fake_resume(state, device, **_):
        resumed.append(device)
        return done({**state, "locatable": False}, "routed" if device else "Where should the route start?")

    monkeypatch.setattr(graph, "invoke", fake_invoke)
    monkeypatch.setattr(graph, "resume", fake_resume)
    monkeypatch.setattr(locate, "browser_fix", lambda request_id: asked.append(request_id) or fix)
    monkeypatch.setenv("STUBS", "1")
    app = AppTest.from_file(str(_APP), default_timeout=30)
    app.run()
    assert asked == []  # opening the app asks for nothing
    app.chat_input[0].set_value("find a route to Union Station Toronto").run()
    assert not app.exception
    return app, resumed, sent, asked


def _texts(app):
    return [message.markdown[0].value for message in app.chat_message]


def test_the_app_finishes_the_trip_with_the_location(monkeypatch):
    app, resumed, _sent, _asked = _app(monkeypatch, FIX)
    assert [place.source for place in resumed] == ["device"]
    assert _texts(app) == ["find a route to Union Station Toronto", "routed"]
    assert app.session_state.waiting is None and not app.caption


def test_a_blocked_location_asks_and_says_why(monkeypatch):
    app, resumed, _sent, _asked = _app(monkeypatch, {"error": "denied"})
    assert resumed == [None]
    assert _texts(app)[-1] == "Where should the route start?"
    assert "Allow it in the browser" in app.caption[0].value


def test_a_message_sent_while_waiting_ends_the_wait(monkeypatch):
    app, resumed, sent, _asked = _app(monkeypatch, None)
    assert "Finding your location" in _texts(app)[-1]
    app.chat_input[0].set_value("from King Station").run()
    assert resumed == [None] and len(sent) == 2
    assert _texts(app)[-2:] == ["from King Station", "typed"]
    assert app.session_state.waiting is None


def test_new_chat_drops_a_waiting_trip(monkeypatch):
    app, resumed, _sent, _asked = _app(monkeypatch, None)
    next(button for button in app.button if button.label == "New chat").click().run()
    assert app.session_state.waiting is None and resumed == [] and app.chat_message == []
