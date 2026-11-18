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
@pytest.mark.parametrize(
    ("highway_m", "expected_status"),
    [
        (0.0, "met"),
        (150.0, "met"),
        (200.0, "met"),
        (200.1, "unmet"),
        (500.0, "unmet"),
        (None, "unverifiable"),
        (-1.0, "unverifiable"),
        (float("nan"), "unverifiable"),
        (float("inf"), "unverifiable"),
    ],
)
def test_highway_check(highway_m, expected_status):
    from routechat.verify.checks import check_highways

    route = RouteCandidate(
        id="test_route",
        mode="car",
        provider="test",
        highway_m=highway_m,
    )

    result = check_highways(route, maximum_m=200)

    assert result.id == "avoid:highways"
    assert result.status == expected_status
    assert result.hard is True
    assert result.evidence


@pytest.mark.parametrize(
    "maximum_m",
    [-1.0, float("nan"), float("inf")],
)
def test_invalid_highway_threshold(maximum_m):
    from routechat.verify.checks import check_highways

    route = RouteCandidate(
        id="test_route",
        mode="car",
        provider="test",
        highway_m=0,
    )

    with pytest.raises(ValueError):
        check_highways(route, maximum_m=maximum_m)


def test_highway_check_uses_configured_threshold(monkeypatch):
    from routechat.verify.checks import check_highways

    settings = SimpleNamespace(
        verifier=SimpleNamespace(highway_max_m=100)
    )
    monkeypatch.setattr(
        "routechat.verify.checks.get_settings",
        lambda: settings,
    )

    route = RouteCandidate(
        id="test_route",
        mode="car",
        provider="test",
        highway_m=150,
    )

    result = check_highways(route)

    assert result.status == "unmet"
    assert "maximum 100 m" in result.evidence
    result = check_bike_lanes(route)

    assert result.status == "unmet"
    assert "75.0%" in result.evidence
