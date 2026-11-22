"""T-5-16: optional sponsor selection on accepted leisure routes."""

import json

from routechat.schemas import LineString, RouteCandidate, VerificationReport
from routechat.stubs import running_constraints
from routechat.verify.sponsor import select_sponsor


def test_t_5_16_sponsored_suggestion(tmp_path):
    path = tmp_path / "sponsors.json"
    path.write_text(
        json.dumps([
            {"name": "Low Rating", "category": "cafe", "rating": 3.9, "lat": 0, "lon": 0},
            {"name": "Too Far", "category": "cafe", "rating": 4.9, "lat": 0.005, "lon": 0},
            {"name": "Nearby Cafe", "category": "cafe", "rating": 4.0, "lat": 0.001, "lon": 0},
            {"name": "Farther Cafe", "category": "cafe", "rating": 4.8, "lat": 0.002, "lon": 0},
        ]),
        encoding="utf-8",
    )
    route = RouteCandidate(
        id="leisure-route",
        mode="run",
        provider="test",
        geometry=LineString(type="LineString", coordinates=[[-0.02, 0], [0.02, 0]]),
    )
    report = VerificationReport(
        decision="accept",
        chosen_route=route.id,
        checks=[],
        must_mention=[],
        sponsored=None,
        handoff_reason=None,
    )
    constraints = running_constraints().model_copy(
        update={"prefer": [], "check_closures": False, "required_accessibility": False}
    )
    options = {
        "report": report,
        "route": route,
        "constraints": constraints,
        "route_action": "plan",
        "is_leisure": True,
        "is_evening": False,
        "is_safety_trip": False,
        "data_path": path,
    }

    suggestion = select_sponsor(**options)
    assert suggestion is not None
    assert suggestion.name == "Nearby Cafe"
    assert 110 < suggestion.distance_m < 112
    assert route.stops_in_order == []

    for override in (
        {"report": report.model_copy(update={"decision": "revise"})},
        {"route": route.model_copy(update={"geometry": None})},
        {"constraints": constraints.model_copy(update={"required_accessibility": True})},
        {"route_action": "compare_modes"},
        {"is_leisure": False},
        {"is_evening": True},
        {"is_safety_trip": True},
        {"data_path": tmp_path / "missing.json"},
    ):
        assert select_sponsor(**(options | override)) is None
