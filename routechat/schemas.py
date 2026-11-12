"""Shared contracts, schemas v1.

Every stage builds and reads these objects. An unknown field is an error.
A change here is a pull request reviewed by WP0 and by every package that
reads the object.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RouteAction = Literal[
    "plan",
    "replan",
    "compare_modes",
    "lookup_only",
    "clarify",
    "escalate",
    "refuse",
]
# The pilot only plans walk, bike, and car. The other values stay so a request
# for a hike, a run, transit, or several modes can be named and turned away,
# rather than quietly treated as one of the three.
PrimaryMode = Literal[
    "walk",
    "bike",
    "hike",
    "run",
    "transit",
    "car",
    "multimodal",
    "unspecified",
]
ClarifyField = Literal["none", "origin", "destination", "stops"]
# Precedence when several could apply: left to right.
RefusalReason = Literal[
    "none",
    "prompt_injection",
    "data_request",
    "unsafe_or_illegal",
    "out_of_scope_b2b",
    "booking_or_payment",
    "live_parking_or_inventory",
]
Avoid = Literal["highways", "tolls", "ferries", "unpaved"]
Prefer = Literal["bike_lanes", "quiet_roads", "scenic", "lit_streets", "step_free"]
GuardDecision = Literal["allow", "clarify", "refuse", "fixed_reply"]
GuardReason = Literal[
    "empty",
    "too_long",
    "not_english",
    "prompt_injection",
    "data_request",
]
MissingField = Literal["origin", "destination", "unparseable"]
PlaceRole = Literal["origin", "stop", "destination"]
CheckStatus = Literal["met", "unmet", "unverifiable"]
VerificationDecision = Literal["accept", "revise", "handoff"]
ReplyKind = Literal["template", "llm"]


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GuardResult(_Contract):
    """WP1. The raw text, judged, with personal data masked."""

    decision: GuardDecision
    clean_text: str
    masked: list[str]
    reason: GuardReason | None


class Stop(_Contract):
    """One stop between the origin and the destination."""

    query: str = Field(
        description="Place name or category as written, such as 'Canadian Tire' or 'coffee'."
    )
    category: str | None = Field(
        description="Set for a type of place, such as coffee or ev_charging_station. Null for a named place."
    )
    required: bool = Field(description="True when the user requires this stop.")
    order: int | None = Field(
        description="The user's order, starting at 1. Null when the user did not give an order."
    )


class ConstraintSet(_Contract):
    """WP3. The trip, in the fields routing code can check. Never invent a value."""

    origin: str | None = Field(
        description="Start place copied as written. Add the city only if the user said it. Null if it is missing or personal, such as home or my office."
    )
    destination: str | None = Field(
        description="End place copied as written. Null if it is missing or personal. A loop may leave this null."
    )
    stops: list[Stop] = Field(description="Stops along the way. Empty when there are none.")
    mode: PrimaryMode = Field(description="How the user will travel. unspecified when they did not say.")
    modes_to_compare: list[PrimaryMode] = Field(
        description="Up to two modes when the user compares them. Otherwise empty."
    )
    compare_threshold_min: int | None = Field(
        description="Minute threshold between the compared modes, such as 30. Null otherwise."
    )
    avoid: list[Avoid] = Field(description="Road features the user told us to avoid.")
    prefer: list[Prefer] = Field(description="Road features the user asked to prefer.")
    minimize_elevation: bool = Field(description="True when the user asked for a flat route.")
    check_closures: bool = Field(description="True when the user asked to check closures.")
    max_duration_min: int | None = Field(description="Duration limit in minutes. Two hours is 120. Null if none.")
    max_distance_km: float | None = Field(description="Distance limit in kilometres. Null if none.")
    loop: bool = Field(description="True for a loop or an out-and-back.")
    target_distance_km: float | None = Field(description="Target length of a loop, in kilometres. Null otherwise.")
    depart_at: str | None = Field(description="Local departure time as 24-hour HH:MM. Null if not stated.")
    arrive_by: str | None = Field(description="Local arrival deadline as 24-hour HH:MM. 'before 5' is 17:00.")
    required_accessibility: bool = Field(
        description="True for a wheelchair, a stroller, or a step-free request."
    )
    ambiguities: list[str] = Field(
        description="Points that could not be pinned down, written out. Never a guessed place, coordinate, or hour."
    )
    missing_field: MissingField | None = Field(
        description="origin or destination when that end is required and null. unparseable when extraction fails twice. Null when the request is complete."
    )
    too_many_stops: bool = Field(description="True when more than 3 stops are required.")


class UserTurn(_Contract):
    """One classifier input. The graph builds this from the trip state."""

    utterance: str
    turn_index: Literal[0, 1]
    known: ConstraintSet | None


class RouteClassification(_Contract):
    """WP2. Which path the request takes. It does not extract places."""

    route_action: RouteAction
    primary_mode: PrimaryMode
    needs_live_data: bool = Field(
        description="True for traffic, closures, incidents, disruptions, right now, or an arrive-by deadline."
    )
    clarify_field: ClarifyField
    refusal_reason: RefusalReason
    parse_error: bool = Field(
        default=False,
        description="True when the classifier output failed validation twice and the action was forced to escalate.",
    )


class LineString(_Contract):
    """GeoJSON line. Coordinates are [longitude, latitude] or [longitude, latitude, elevation]."""

    type: Literal["LineString"] = "LineString"
    coordinates: list[list[float]]

    def model_post_init(self, _context: object) -> None:
        for point in self.coordinates:
            if len(point) not in (2, 3):
                raise ValueError("each coordinate must be [lon, lat] or [lon, lat, elevation]")


class ResolvedPlace(_Contract):
    """WP4. A query resolved to a point."""

    role: PlaceRole
    query: str
    name: str
    lon: float
    lat: float
    source: str = Field(description="Resolver that produced the point, such as searchbox or geocode.")
    opening_hours: str | None = Field(
        default=None,
        description="Hours as text, or null when the provider did not return them. Null is unknown, not closed.",
    )
    detour_km: float | None = None


class RouteCandidate(_Contract):
    """WP4. One measured route. Null means the provider did not return that fact, which is not the same as zero."""

    id: str
    mode: PrimaryMode
    provider: str
    distance_m: float | None = None
    duration_s: float | None = None
    duration_traffic_s: float | None = None
    ascend_m: float | None = None
    stops_in_order: list[str] = Field(default_factory=list)
    highway_m: float | None = None
    toll_m: float | None = None
    ferry_m: float | None = None
    bike_network_share: float | None = None
    closures: list[dict[str, object]] | None = Field(
        default=None,
        description="Null means the provider gave no closure information, so the fact is unknown. An empty list means it checked and found none. WP4 must not replace null with an empty list.",
    )
    geometry: LineString | None = None
    error: str | None = None


class Check(_Contract):
    """One requirement on the chosen route."""

    id: str
    status: CheckStatus
    hard: bool
    evidence: str


class SponsoredSuggestion(_Contract):
    """At most one labelled sponsor card, attached only after an accepted leisure route."""

    name: str
    category: str | None = None
    lon: float
    lat: float
    rating: float
    distance_m: float


class VerificationReport(_Contract):
    """WP5. What the route met, and whether to accept, revise, or hand off."""

    decision: VerificationDecision
    chosen_route: str | None
    checks: list[Check]
    must_mention: list[str]
    sponsored: SponsoredSuggestion | None
    handoff_reason: str | None


class ReplyDraft(_Contract):
    """WP6. The only text proposed for the user."""

    message: str = Field(description="Plain sentences, at most 90 words, no markdown.")
    mentioned_ids: list[str] = Field(description="Check ids this message actually discloses.")
    kind: ReplyKind


class DisplayedStop(_Contract):
    """A stop the map draws. Coordinates are the resolved point."""

    name: str
    lon: float
    lat: float


class FinalAnswer(_Contract):
    """WP7. What the app shows after the output guard."""

    approved: bool
    text: str
    route_geojson: LineString | None
    stops: list[DisplayedStop]
    used_fallback_template: bool


class HandoffTicket(_Contract):
    """A trip a person needs to see.

    The graph sets reason to system_error or timeout when a stage crashes or
    the request budget is spent. The verifier uses its own reason strings,
    such as tool_timeout, no_route, place_not_found, or transit_not_supported.
    """

    ticket_id: str
    reason: str
    redacted_request: str
    constraints: ConstraintSet | None
    report: VerificationReport | None
    created_at: datetime
