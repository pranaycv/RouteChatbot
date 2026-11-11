"""Week-1 stand-ins for the seven stages.

STUBS=1 installs stub_nodes() in place of the real packages. Every builder
below returns the handbook's running example, and each node returns only
the state fields its stage owns. The destination coordinate is the one
value the handbook truncates: University of Toronto, St George campus, so
the geometry is a complete line rather than an ellipsis.
"""

from routechat.schemas import (
    Check,
    ConstraintSet,
    DisplayedStop,
    FinalAnswer,
    GuardResult,
    LineString,
    ReplyDraft,
    ResolvedPlace,
    RouteCandidate,
    RouteClassification,
    Stop,
    VerificationReport,
)

RUNNING_UTTERANCE = (
    "Plan a bike route from King Station to the University of Toronto. "
    "Avoid highways, prefer bike lanes, keep it flat, check closures, "
    "and stop at a Canadian Tire."
)

# The handbook writes the destination coordinate as an ellipsis. St George
# campus completes the line. WP4 replaces this point with the resolved one.
_UofT_LON = -79.3957
_UofT_LAT = 43.6629

RUNNING_REPLY = (
    "Here's a 4.2 km bike route, about 18 minutes, with 38 m of climbing. "
    "It stops at Canadian Tire on Yonge, avoids highways, and 72% of it is on "
    "bike lanes or paths. I couldn't check for closures, so watch for signs on the way."
)


def running_guard() -> GuardResult:
    return GuardResult(
        decision="allow",
        clean_text=RUNNING_UTTERANCE,
        masked=[],
        reason=None,
    )


def running_classification() -> RouteClassification:
    return RouteClassification(
        route_action="plan",
        primary_mode="bike",
        needs_live_data=True,
        clarify_field="none",
        refusal_reason="none",
    )


def running_constraints() -> ConstraintSet:
    return ConstraintSet(
        origin="King Station, Toronto",
        destination="University of Toronto",
        stops=[Stop(query="Canadian Tire", category=None, required=True, order=None)],
        mode="bike",
        modes_to_compare=[],
        compare_threshold_min=None,
        avoid=["highways"],
        prefer=["bike_lanes"],
        minimize_elevation=True,
        check_closures=True,
        max_duration_min=None,
        max_distance_km=None,
        loop=False,
        target_distance_km=None,
        depart_at=None,
        arrive_by=None,
        required_accessibility=False,
        ambiguities=[],
        missing_field=None,
        too_many_stops=False,
    )


def running_geometry() -> LineString:
    return LineString(
        coordinates=[
            [-79.3778, 43.6490],
            [-79.3860, 43.6570],
            [_UofT_LON, _UofT_LAT],
        ]
    )


def running_places() -> list[ResolvedPlace]:
    return [
        ResolvedPlace(
            role="origin",
            query="King Station, Toronto",
            name="King Station",
            lon=-79.3778,
            lat=43.6490,
            source="searchbox",
            opening_hours=None,
            detour_km=None,
        ),
        ResolvedPlace(
            role="stop",
            query="Canadian Tire",
            name="Canadian Tire (Yonge)",
            lon=-79.3860,
            lat=43.6570,
            source="searchbox",
            opening_hours=None,
            detour_km=0.6,
        ),
        ResolvedPlace(
            role="destination",
            query="University of Toronto",
            name="University of Toronto",
            lon=_UofT_LON,
            lat=_UofT_LAT,
            source="searchbox",
            opening_hours=None,
            detour_km=None,
        ),
    ]


def running_candidate() -> RouteCandidate:
    return RouteCandidate(
        id="gh_bike_1",
        mode="bike",
        provider="graphhopper",
        distance_m=4200,
        duration_s=1100,
        duration_traffic_s=None,
        ascend_m=38,
        stops_in_order=["King Station", "Canadian Tire (Yonge)", "University of Toronto"],
        highway_m=0,
        toll_m=0,
        ferry_m=0,
        bike_network_share=0.72,
        closures=None,
        geometry=running_geometry(),
        error=None,
    )


def running_report() -> VerificationReport:
    return VerificationReport(
        decision="accept",
        chosen_route="gh_bike_1",
        checks=[
            Check(id="stop:canadian_tire", status="met", hard=True, evidence="stop 2 of 3"),
            Check(
                id="avoid:highways",
                status="met",
                hard=True,
                evidence="0 m motorway or trunk",
            ),
            Check(
                id="prefer:bike_lanes",
                status="met",
                hard=False,
                evidence="72% on bike network (min 60%)",
            ),
            Check(
                id="flat",
                status="met",
                hard=False,
                evidence="38 m climb = 9 m per km (max 10)",
            ),
            Check(
                id="closures",
                status="unverifiable",
                hard=True,
                evidence="no closure data for cycling",
            ),
        ],
        must_mention=["closures"],
        sponsored=None,
        handoff_reason=None,
    )


def running_draft() -> ReplyDraft:
    return ReplyDraft(message=RUNNING_REPLY, mentioned_ids=["closures"], kind="llm")


def running_final() -> FinalAnswer:
    return FinalAnswer(
        approved=True,
        text=RUNNING_REPLY,
        route_geojson=running_geometry(),
        stops=[DisplayedStop(name="Canadian Tire (Yonge)", lon=-79.3860, lat=43.6570)],
        used_fallback_template=False,
    )


def stub_nodes() -> dict:
    """One node per stage. Each returns only the fields that stage owns."""

    def input_guard(_state):
        return {"guard": running_guard()}

    def classify_node(_state):
        return {"classification": running_classification()}

    def extract_node(_state):
        return {"constraints": running_constraints()}

    def maps_node(_state):
        return {"places": running_places(), "candidates": [running_candidate()]}

    def verify_node(_state):
        return {"report": running_report()}

    def write_node(_state):
        return {"draft": running_draft()}

    def output_guard(_state):
        return {"final": running_final()}

    return {
        "guard_in": input_guard,
        "classify": classify_node,
        "extract": extract_node,
        "maps": maps_node,
        "verify": verify_node,
        "write": write_node,
        "guard_out": output_guard,
    }
