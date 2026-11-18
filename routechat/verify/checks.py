"""Rules for checking route requirements."""

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
