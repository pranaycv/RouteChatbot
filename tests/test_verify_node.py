"""WP5 decision and sponsor integration through the graph's verifier callable."""

import pytest

from routechat.graph import invoke
from routechat.schemas import GuardResult, ResolvedPlace, RouteCandidate, Stop
from routechat.stubs import running_classification, running_constraints, stub_nodes
from routechat.verify.decide import verify_node


def _state(**constraint_updates):
    constraints = running_constraints().model_copy(update={
        "stops": [],
        "avoid": [],
        "prefer": [],
        "minimize_elevation": False,
        "check_closures": False,
        "max_duration_min": None,
        "max_distance_km": None,
        "loop": False,
        "arrive_by": None,
        "depart_at": "12:00",
        **constraint_updates,
    })
    return {
        "constraints": constraints,
        "classification": running_classification().model_copy(update={"needs_live_data": False}),
        "candidates": [RouteCandidate(
            id="route", mode="run", provider="fixture", highway_m=500, duration_s=600,
            geometry={"type": "LineString", "coordinates": [
                [-79.37455, 43.63821], [-79.37255, 43.63821],
            ]},
        )],
        "guard": GuardResult(
            decision="allow", clean_text="Plan a recreational run at noon", masked=[], reason=None,
        ),
    }


def test_t_5_14_revises_once_then_hands_off():
    state = _state(avoid=["highways"])
    first = verify_node(state)
    assert first["report"].decision == "revise"
    assert first["revision_count"] == 1

    second = verify_node(state | {"revision_count": first["revision_count"]})
    assert second["report"].decision == "handoff"
    assert second["handoff"].report == second["report"]


def test_t_5_15_accessibility_handoff():
    update = verify_node(_state(required_accessibility=True))
    assert update["report"].decision == "handoff"
    assert update["report"].must_mention == ["step_free"]
    assert update["handoff"].redacted_request == "Plan a recreational run at noon"


def test_t_5_16_sponsor_connected_after_acceptance():
    state = _state()
    accepted = verify_node(state)["report"]
    assert accepted.decision == "accept"
    assert accepted.sponsored is not None
    assert state["candidates"][0].stops_in_order == []

    evening = verify_node(_state(depart_at="18:30"))["report"]
    assert evening.decision == "accept"
    assert evening.sponsored is None


def test_t_5_17_missing_closure_data_is_unverifiable():
    state = _state(check_closures=True)
    running = verify_node(state)["report"]
    assert running.decision == "accept"
    assert running.checks[0].status == "unverifiable"
    assert running.must_mention == ["closures"]

    state["candidates"][0] = state["candidates"][0].model_copy(update={"mode": "car"})
    driving = verify_node(state)["report"]
    assert driving.decision == "handoff"
    assert driving.checks[0].status == "unverifiable"


@pytest.mark.parametrize(
    ("constraints", "measurements", "check_id", "status"),
    [
        ({"avoid": ["tolls"]}, {"toll_m": 0}, "avoid:tolls", "met"),
        ({"avoid": ["tolls"]}, {"toll_m": 50}, "avoid:tolls", "unmet"),
        ({"avoid": ["ferries"]}, {}, "avoid:ferries", "unverifiable"),
        ({"avoid": ["ferries"]}, {"ferry_m": 0}, "avoid:ferries", "met"),
        ({"avoid": ["ferries"]}, {"ferry_m": 50}, "avoid:ferries", "unmet"),
        ({"max_distance_km": 10}, {"distance_m": 10500}, "max_distance", "met"),
        ({"max_distance_km": 10}, {"distance_m": 10501}, "max_distance", "unmet"),
        ({"max_distance_km": 10}, {}, "max_distance", "unverifiable"),
        ({"max_duration_min": 10}, {"duration_traffic_s": 660}, "max_duration", "met"),
        ({"max_duration_min": 10}, {"duration_traffic_s": 661}, "max_duration", "unmet"),
        ({"max_duration_min": 10}, {"duration_s": None}, "max_duration", "unverifiable"),
        ({"loop": True, "target_distance_km": 10}, {"distance_m": 9000}, "loop_distance", "met"),
        ({"loop": True, "target_distance_km": 10}, {"distance_m": 8999}, "loop_distance", "unmet"),
        ({"loop": True, "target_distance_km": 10}, {}, "loop_distance", "unverifiable"),
        ({"arrive_by": "12:15"}, {}, "arrive_by", "met"),
        ({"arrive_by": "12:14"}, {}, "arrive_by", "unmet"),
        ({"arrive_by": "12:15", "depart_at": None}, {}, "arrive_by", "unverifiable"),
        ({"check_closures": True}, {"closures": []}, "closures", "met"),
        ({"check_closures": True}, {"closures": [{"name": "Road closure"}]}, "closures", "unmet"),
    ],
)
def test_route_checks(constraints, measurements, check_id, status):
    state = _state(**constraints)
    state["candidates"][0] = state["candidates"][0].model_copy(update=measurements)
    report = verify_node(state)["report"]
    assert [(check.id, check.status, check.hard) for check in report.checks] == [
        (check_id, status, True),
    ]
    assert report.must_mention == ([] if status == "met" else [check_id])
    assert report.decision == ("handoff" if status == "unmet" else "accept")


def test_required_stop_order_and_unverifiable_hours():
    stops = [
        Stop(query="Coffee", category="coffee", required=True, order=1),
        Stop(query="Museum", category=None, required=True, order=2),
    ]
    state = _state(stops=stops)
    state["places"] = [
        ResolvedPlace(role="stop", query="Coffee", name="Park Cafe", lon=0, lat=0, source="test"),
    ]
    route = state["candidates"][0]
    state["candidates"] = [route.model_copy(update={"stops_in_order": ["Park Cafe", "Museum"]})]
    report = verify_node(state)["report"]
    assert report.decision == "accept"
    assert report.must_mention == ["hours:coffee", "hours:museum"]

    state["candidates"] = [route.model_copy(update={"stops_in_order": ["Museum", "Park Cafe"]})]
    report = verify_node(state)["report"]
    assert report.decision == "handoff"
    assert "stop:museum" in report.must_mention


def test_compare_prefers_requested_mode_within_threshold():
    state = _state(mode="bike", modes_to_compare=["bike", "car"], compare_threshold_min=2)
    state["classification"] = state["classification"].model_copy(
        update={"route_action": "compare_modes"}
    )
    route = state["candidates"][0]
    car = route.model_copy(update={"id": "car", "mode": "car"})
    bike = route.model_copy(update={"id": "bike", "mode": "bike", "duration_s": 720})
    state["candidates"] = [car, bike]
    report = verify_node(state)["report"]
    assert report.chosen_route == "bike"
    assert report.sponsored is None

    state["candidates"][1] = bike.model_copy(update={"duration_s": 721})
    assert verify_node(state)["report"].chosen_route == "car"


def test_verify_node_requests_one_retry_through_graph():
    state = _state(avoid=["highways"])
    visits = []
    nodes = stub_nodes()
    nodes["extract"] = lambda _: {"constraints": state["constraints"]}
    nodes["verify"] = verify_node

    def maps(request):
        visits.append(request.get("revision_count", 0))
        route = state["candidates"][0]
        if request.get("revision_count"):
            route = route.model_copy(update={"highway_m": 0})
        return {"candidates": [route], "places": []}

    nodes["maps"] = maps
    result = invoke({"request_id": "verify-retry", "utterance": "Avoid highways"}, nodes)
    assert visits == [0, 1]
    assert result["report"].decision == "accept"
    assert result["report"].checks[0].status == "met"
    assert result["handoff"] is None


def test_missing_stop_hours_and_preference_disclosures():
    state = _state(prefer=["scenic", "quiet_roads", "lit_streets"], stops=[
        Stop(query="Gallery", category=None, required=True, order=None),
    ])
    report = verify_node(state)["report"]
    assert report.decision == "handoff"
    assert [(check.id, check.status) for check in report.checks] == [
        ("stop:gallery", "unmet"), ("hours:gallery", "unverifiable"),
        ("prefer:scenic", "unverifiable"), ("prefer:quiet_roads", "unverifiable"),
        ("prefer:lit_streets", "unverifiable"),
    ]
    assert report.must_mention == [check.id for check in report.checks]


def test_provider_error_flows_into_handoff():
    state = _state()
    route = state["candidates"][0]
    state["candidates"] = [route.model_copy(update={"error": "tool_timeout"})]
    update = verify_node(state)
    assert update["report"].handoff_reason == "tool_timeout"
    assert update["handoff"].reason == "tool_timeout"
    assert update["report"].chosen_route is None


def test_soft_flatness_failure_is_disclosed_after_retry():
    state = _state(minimize_elevation=True)
    route = state["candidates"][0]
    state["candidates"] = [route.model_copy(update={"ascend_m": 30, "distance_m": 1000})]
    assert verify_node(state)["report"].decision == "revise"
    report = verify_node(state | {"revision_count": 1})["report"]
    assert report.decision == "accept"
    assert report.must_mention == ["flat"]


@pytest.mark.parametrize(
    ("departure", "text", "eligible"),
    [
        (None, "Plan a run downtown", True),
        ("18:00", "Plan a run downtown", True),
        ("18:01", "Plan a run downtown", False),
        (None, "Plan a run tonight", False),
        (None, "Plan a run at night", False),
    ],
)
def test_sponsor_evening_exclusion(departure, text, eligible):
    state = _state(depart_at=departure)
    state["guard"] = state["guard"].model_copy(update={"clean_text": text})
    assert (verify_node(state)["report"].sponsored is not None) == eligible


def test_graphhopper_standard_does_not_retry_identical_request():
    state = _state(avoid=["highways"])
    route = state["candidates"][0]
    state["candidates"] = [route.model_copy(update={"provider": "graphhopper"})]
    report = verify_node(state)["report"]
    assert report.decision == "handoff"
    assert report.must_mention == ["avoid:highways"]

    state = _state(minimize_elevation=True)
    state["candidates"] = [route.model_copy(update={
        "provider": "graphhopper", "ascend_m": 30, "distance_m": 1000,
    })]
    report = verify_node(state)["report"]
    assert report.decision == "accept"
    assert report.must_mention == ["flat"]


@pytest.mark.parametrize(
    ("mode", "decision"),
    [("unspecified", "clarify"), ("hike", "refuse"), ("multimodal", "refuse")],
)
def test_unsupported_mode_gets_direct_reply_without_ticket(mode, decision):
    from routechat.agents.templates import mode_not_supported_text
    from routechat.agents.writer import template_message
    from routechat.logging import request_outcome

    state = _state(mode=mode)
    state["candidates"] = [RouteCandidate(
        id="unsupported", provider="graphhopper", mode=mode, error="mode_not_supported",
    )]
    state.update(verify_node(state))

    assert state["handoff"] is None
    assert state["report"].decision == decision
    assert template_message(state) == mode_not_supported_text(mode)
    assert request_outcome(state) == decision


@pytest.mark.parametrize(
    ("constraints", "decision"),
    [
        ({}, "accept"),
        ({"avoid": ["highways"]}, "revise"),
        ({"required_accessibility": True}, "handoff"),
    ],
)
def test_verify_node_logs_one_stage_line(monkeypatch, constraints, decision):
    calls = []
    monkeypatch.setattr("routechat.verify.decide.stage_line", lambda *args: calls.append(args))
    state = _state(**constraints) | {"request_id": "request-1"}
    assert verify_node(state)["report"].decision == decision

    (stage, request_id, logged_decision, duration_ms), = calls
    assert (stage, request_id, logged_decision) == ("verify", "request-1", decision)
    assert duration_ms >= 0
