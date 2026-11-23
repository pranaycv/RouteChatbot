"""Optional sponsored suggestions for eligible accepted routes."""

import json
from math import cos, hypot, radians
from pathlib import Path

from pydantic import BaseModel, Field, TypeAdapter

from routechat.config import ROOT, get_settings
from routechat.schemas import (
    ConstraintSet,
    RouteAction,
    RouteCandidate,
    SponsoredSuggestion,
    VerificationReport,
)

_METRES_PER_RADIAN = 6_371_008.8


class _Business(BaseModel):
    name: str
    category: str | None
    rating: float = Field(ge=0, le=5)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


_BUSINESSES = TypeAdapter(list[_Business])


def _distance_to_line(business: _Business, coordinates: list[list[float]]) -> float:
    """Measure point-to-segment distance in a local projection, in metres."""
    longitude_scale = _METRES_PER_RADIAN * cos(radians(business.lat))
    distances = []
    for start, end in zip(coordinates, coordinates[1:]):
        # Keep each segment continuous where it crosses the antimeridian.
        lon_delta = (end[0] - start[0] + 180) % 360 - 180
        midpoint = start[0] + lon_delta / 2
        midpoint_offset = (midpoint - business.lon + 180) % 360 - 180
        x = radians(midpoint_offset - lon_delta / 2) * longitude_scale
        y = radians(start[1] - business.lat) * _METRES_PER_RADIAN
        dx = radians(lon_delta) * longitude_scale
        dy = radians(end[1] - start[1]) * _METRES_PER_RADIAN

        length_squared = dx * dx + dy * dy
        fraction = -(x * dx + y * dy) / length_squared if length_squared else 0
        fraction = min(1, max(0, fraction))
        distances.append(hypot(x + fraction * dx, y + fraction * dy))
    return min(distances)


def select_sponsor(
    report: VerificationReport,
    route: RouteCandidate,
    constraints: ConstraintSet,
    *,
    route_action: RouteAction,
    is_evening: bool,
    is_safety_trip: bool,
    data_path: Path = ROOT / "data" / "sponsors.json",
) -> SponsoredSuggestion | None:
    """Return the card for report.sponsored after the route decision is made."""
    if (
        report.decision != "accept"
        or report.chosen_route != route.id
        or is_evening
        or is_safety_trip
        or constraints.required_accessibility
        or "step_free" in constraints.prefer
        or "lit_streets" in constraints.prefer
        or constraints.check_closures
        or route_action == "compare_modes"
        or constraints.modes_to_compare
        or route.geometry is None
    ):
        return None

    coordinates = route.geometry.coordinates
    if len(coordinates) < 2 or any(
        not (-180 <= lon <= 180 and -90 <= lat <= 90)
        for lon, lat, *_ in coordinates
    ):
        return None

    try:
        businesses = _BUSINESSES.validate_python(json.loads(data_path.read_bytes()))
    except (OSError, ValueError):
        return None

    settings = get_settings().verifier
    suggestions = []
    for business in businesses:
        if business.rating < settings.sponsor_min_rating:
            continue
        distance_m = _distance_to_line(business, coordinates)
        if distance_m <= settings.sponsor_max_distance_m:
            suggestions.append(
                SponsoredSuggestion(**business.model_dump(), distance_m=distance_m)
            )

    return min(
        suggestions,
        key=lambda suggestion: (suggestion.distance_m, -suggestion.rating, suggestion.name),
        default=None,
    )
