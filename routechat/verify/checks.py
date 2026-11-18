"""Rules for checking route requirements."""

from math import isfinite

from routechat.config import get_settings
from routechat.schemas import Check, RouteCandidate


def check_bike_lanes(
    route: RouteCandidate,
    *,
    minimum_share: float | None = None,
) -> Check:
    """Check bike-network coverage when bike lanes were requested."""
    if minimum_share is None:
        minimum_share = get_settings().verifier.bike_network_min_share

    if not 0 <= minimum_share <= 1:
        raise ValueError("minimum_share must be between 0 and 1")

    share = route.bike_network_share

    if share is None:
        return Check(
            id="prefer:bike_lanes",
            status="unverifiable",
            hard=False,
            evidence="The provider did not return bike-network coverage.",
        )

    if not 0 <= share <= 1:
        return Check(
            id="prefer:bike_lanes",
            status="unverifiable",
            hard=False,
            evidence="The provider returned invalid bike-network coverage.",
        )

    return Check(
        id="prefer:bike_lanes",
        status="met" if share >= minimum_share else "unmet",
        hard=False,
        evidence=(
            f"{share:.1%} on the bike network "
            f"(minimum {minimum_share:.1%})."
        ),
    )


def check_highways(
    route: RouteCandidate,
    *,
    maximum_m: float | None = None,
) -> Check:
    """Check highway distance when avoiding highways was requested."""
    if maximum_m is None:
        maximum_m = get_settings().verifier.highway_max_m

    if not isfinite(maximum_m) or maximum_m < 0:
        raise ValueError("maximum_m must be finite and non-negative")

    highway_m = route.highway_m

    if highway_m is None:
        return Check(
            id="avoid:highways",
            status="unverifiable",
            hard=True,
            evidence="The provider did not return highway distance.",
        )

    if not isfinite(highway_m) or highway_m < 0:
        return Check(
            id="avoid:highways",
            status="unverifiable",
            hard=True,
            evidence="The provider returned invalid highway distance.",
        )

    return Check(
        id="avoid:highways",
        status="met" if highway_m <= maximum_m else "unmet",
        hard=True,
        evidence=(
            f"{highway_m:g} m on motorway or trunk roads "
            f"(maximum {maximum_m:g} m)."
        ),
    )
