"""Tests for bike-network coverage verification."""

from types import SimpleNamespace

import pytest

from routechat.schemas import RouteCandidate
from routechat.verify.checks import check_bike_lanes


@pytest.mark.parametrize(
    ("share", "expected_status"),
    [
        (0.72, "met"),
        (0.60, "met"),
        (0.59, "unmet"),
        (0.0, "unmet"),
        (None, "unverifiable"),
        (-0.1, "unverifiable"),
        (1.1, "unverifiable"),
    ],
)
def test_bike_lane_check(share, expected_status):
    route = RouteCandidate(
        id="test_bike_route",
        mode="bike",
        provider="test",
        bike_network_share=share,
    )

    result = check_bike_lanes(route, minimum_share=0.60)

    assert result.id == "prefer:bike_lanes"
    assert result.status == expected_status
    assert result.hard is False
    assert result.evidence


@pytest.mark.parametrize("minimum_share", [-0.1, 1.1])
def test_invalid_bike_lane_threshold(minimum_share):
    route = RouteCandidate(
        id="test_bike_route",
        mode="bike",
        provider="test",
        bike_network_share=0.72,
    )

    with pytest.raises(ValueError):
        check_bike_lanes(route, minimum_share=minimum_share)


def test_bike_lane_check_uses_configured_threshold(monkeypatch):
    settings = SimpleNamespace(
        verifier=SimpleNamespace(bike_network_min_share=0.75)
    )
    monkeypatch.setattr(
        "routechat.verify.checks.get_settings",
        lambda: settings,
    )

    route = RouteCandidate(
        id="test_bike_route",
        mode="bike",
        provider="test",
        bike_network_share=0.72,
    )

    result = check_bike_lanes(route)

    assert result.status == "unmet"
    assert "75.0%" in result.evidence
