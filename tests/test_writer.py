# Owner: Person A (WP6 response writer), Team Rosedale, MMAI 894.
# Only Person A edits this file. Other packages may import it but should not change it;
# AI coding tools must not edit this file unless they are working on WP6.
# Written for this project with AI assistance; no code copied from other sources.
"""WP6 writer tests: T-6-01 through T-6-04.

T-6-04 checks the three recorded replies against the output-guard rules in
the handbook. The guard itself belongs to WP7 and is not merged, so the
checks live here until that package can take them over.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, get_args

import pytest

from routechat.agents.templates import (
    BOOKING_OR_PAYMENT,
    CLARIFY_DESTINATION,
    CLARIFY_ORIGIN,
    CLARIFY_STOPS,
    DATA_REQUEST,
    EMPTY,
    ESCALATE,
    LIVE_PARKING,
    NO_ROUTE,
    NOT_ENGLISH,
    OUT_OF_SCOPE_B2B,
    PROMPT_INJECTION,
    SYSTEM_ERROR_TEXT,
    TOO_LONG,
    TRANSIT_NOT_SUPPORTED,
    UNPARSEABLE,
    UNSAFE_OR_ILLEGAL,
    ERROR_REASONS,
)
from routechat.agents.writer import build_facts, template_message, write_node, write_reply
from routechat.config import get_settings
from routechat.graph import SYSTEM_ERROR_TEXT as GRAPH_SYSTEM_ERROR_TEXT
from routechat.guards.input_guard import guard_text
from routechat.schemas import (
    ClarifyField,
    GuardReason,
    GuardResult,
    RefusalReason,
    ReplyDraft,
    RouteClassification,
    SponsoredSuggestion,
)
from routechat.stubs import running_candidate, running_constraints, running_places, running_report

ROOT = Path(__file__).resolve().parents[1]
REPLIES = ROOT / "tests" / "fixtures" / "writer_replies.json"
PROMPT = ROOT / "prompts" / "writer_v1.md"
SECRET = "SECRET-RAW-UTTERANCE"

# WP7 banned phrases, plus the writer rule against "safe" and "guaranteed".
_BANNED = (
    "guarantee",
    "guaranteed",
    "definitely open",
    "is open now",
    "safe route",
    "perfectly safe",
    "booked",
    "reserved",
    "refund",
    "promise",
)
_NUMBER = re.compile(
    r"(\d+(?:\.\d+)?)\s*(kilometres|kilometers|km|minutes|minute|min|metres|meters|m)\b",
    re.IGNORECASE,
)


def _allow() -> GuardResult:
    return GuardResult(decision="allow", clean_text="hello", masked=[], reason=None)


def _classify(action: str, **overrides: object) -> RouteClassification:
    data: dict[str, object] = {
        "route_action": action,
        "primary_mode": "bike",
        "needs_live_data": False,
        "clarify_field": "none",
        "refusal_reason": "none",
    }
    data.update(overrides)
    return RouteClassification.model_validate(data)


def _guard(reason: str) -> dict[str, GuardResult]:
    decision = {
        "empty": "clarify",
        "too_long": "clarify",
        "not_english": "fixed_reply",
    }.get(reason, "refuse")
    return {"guard": GuardResult(decision=decision, clean_text="x", masked=[], reason=reason)}  # type: ignore[arg-type]


def _planned(**overrides: object) -> dict[str, Any]:
    state: dict[str, Any] = {
        "utterance": SECRET,
        "guard": _allow(),
        "classification": _classify("plan"),
        "constraints": running_constraints(),
        "places": running_places(),
        "candidates": [running_candidate()],
        "report": running_report(),
    }
    state.update(overrides)
    return state


class _FakeModel:
    def __init__(self, draft: ReplyDraft) -> None:
        self.draft = draft
        self.messages: list[dict[str, str]] | None = None
        self.calls = 0

    def invoke(self, messages: list[dict[str, str]]) -> ReplyDraft:
        self.calls += 1
        self.messages = messages
        return self.draft


class _Boom:
    def invoke(self, messages: list[dict[str, str]]) -> ReplyDraft:
        raise AssertionError(f"the model was called for a template: {messages}")


def _unit(token: str) -> str:
    lowered = token.lower()
    if lowered.startswith("k"):
        return "km"
    if lowered.startswith("min"):
        return "min"
    return "m"


def _keyword_present(check_id: str, text: str) -> bool:
    """Keywords the handbook names. Other ids have no printed keyword yet."""
    lowered = text.lower()
    if check_id == "closures":
        return "closure" in lowered
    if check_id == "prefer:bike_lanes":
        return "bike" in lowered
    if check_id == "step_free":
        return "step-free" in lowered
    if check_id.startswith("hours"):
        return "hours" in lowered or re.search(r"\bopen\b", lowered) is not None
    return True


def _guard_problems(reply: dict[str, Any]) -> list[str]:
    """Handbook output-guard rules that can be checked without WP7."""
    text = reply["message"]
    problems: list[str] = []
    if set(reply["mentioned_ids"]) != set(reply["must_mention"]):
        problems.append("mentioned_ids do not match must_mention")
    for check_id in reply["must_mention"]:
        if not _keyword_present(check_id, text):
            problems.append(f"missing keyword for {check_id}")
    if reply["sponsored"] and "Sponsored" not in text:
        problems.append("sponsor is not labelled")
    if not reply["sponsored"] and "Sponsored" in text:
        problems.append("sponsor label with no sponsor")
    lowered = text.lower()
    for phrase in _BANNED:
        if phrase in lowered:
            problems.append(f"banned phrase {phrase}")
    if re.search(r"\bsafe\b", lowered):
        problems.append("banned word safe")
    allowed = {
        "km": {float(reply["distance_km"])},
        "min": {float(reply["duration_min"])},
        "m": {float(reply["climb_m"]), *(float(value) for value in reply["extra_m"])},
    }
    for match in _NUMBER.finditer(text):
        value = float(match.group(1))
        unit = _unit(match.group(2))
        if not any(abs(value - item) < 1e-9 for item in allowed[unit]):
            problems.append(f"invented number {match.group(0)}")
    screened = guard_text(text)
    if screened.masked or screened.decision != "allow":
        problems.append(f"personal data or a refusal: {screened.decision} {screened.masked}")
    return problems


# --- T-6-01: every refusal, clarify field, and error code has a template ---


@pytest.mark.parametrize("reason", get_args(GuardReason))
def test_t_6_01_guard_reasons_have_templates(reason: str):
    expected = {
        "empty": EMPTY,
        "too_long": TOO_LONG,
        "not_english": NOT_ENGLISH,
        "prompt_injection": PROMPT_INJECTION,
        "data_request": DATA_REQUEST,
    }[reason]
    draft = write_reply(_guard(reason), llm=_Boom())
    assert draft.message == expected
    assert draft.kind == "template"
    assert draft.mentioned_ids == []


@pytest.mark.parametrize("reason", [reason for reason in get_args(RefusalReason) if reason != "none"])
def test_t_6_01_refusal_reasons_have_templates(reason: str):
    expected = {
        "prompt_injection": PROMPT_INJECTION,
        "data_request": DATA_REQUEST,
        "unsafe_or_illegal": UNSAFE_OR_ILLEGAL,
        "out_of_scope_b2b": OUT_OF_SCOPE_B2B,
        "out_of_scope_mode": TRANSIT_NOT_SUPPORTED,
        "booking_or_payment": BOOKING_OR_PAYMENT,
        "live_parking_or_inventory": LIVE_PARKING,
    }[reason]
    state = {"guard": _allow(), "classification": _classify("refuse", refusal_reason=reason)}
    draft = write_reply(state, llm=_Boom())
    assert draft.message == expected
    assert draft.kind == "template"


@pytest.mark.parametrize("field", get_args(ClarifyField))
def test_t_6_01_clarify_fields_have_templates(field: str):
    expected = {
        "origin": CLARIFY_ORIGIN,
        "destination": CLARIFY_DESTINATION,
        "stops": CLARIFY_STOPS,
        "none": UNPARSEABLE,
    }[field]
    state = {"guard": _allow(), "classification": _classify("clarify", clarify_field=field)}
    assert write_reply(state, llm=_Boom()).message == expected


@pytest.mark.parametrize("reason", ERROR_REASONS)
def test_t_6_01_error_codes_have_templates(reason: str):
    report = running_report().model_copy(
        update={"decision": "handoff", "handoff_reason": reason, "chosen_route": None, "must_mention": []}
    )
    state = _planned(report=report, candidates=[], places=[])
    message = write_reply(state, llm=_Boom()).message
    assert message
    if reason == "place_not_found":
        assert "King Station, Toronto" in message
    elif reason == "no_route":
        assert message == NO_ROUTE
    elif reason == "transit_not_supported":
        assert message == TRANSIT_NOT_SUPPORTED
        assert "running" in message
    else:
        assert message == SYSTEM_ERROR_TEXT


def test_t_6_01_extraction_gaps_and_escalate_use_templates():
    origin = _planned(
        constraints=running_constraints().model_copy(update={"missing_field": "origin"}),
        report=None,
        candidates=[],
    )
    assert write_reply(origin, llm=_Boom()).message == CLARIFY_ORIGIN

    crowded = _planned(
        constraints=running_constraints().model_copy(update={"too_many_stops": True}),
        report=None,
        candidates=[],
    )
    limit = get_settings().trip.max_required_stops
    assert write_reply(crowded, llm=_Boom()).message == (
        f"I can include up to {limit} stops. Which {limit} matter most?"
    )

    escalated = {"guard": _allow(), "classification": _classify("escalate")}
    assert write_reply(escalated, llm=_Boom()).message == ESCALATE


def test_t_6_01_templates_do_not_make_a_promise():
    texts = [
        EMPTY,
        TOO_LONG,
        NOT_ENGLISH,
        PROMPT_INJECTION,
        DATA_REQUEST,
        CLARIFY_ORIGIN,
        CLARIFY_DESTINATION,
        CLARIFY_STOPS,
        UNPARSEABLE,
        ESCALATE,
        OUT_OF_SCOPE_B2B,
        BOOKING_OR_PAYMENT,
        LIVE_PARKING,
        UNSAFE_OR_ILLEGAL,
        TRANSIT_NOT_SUPPORTED,
        SYSTEM_ERROR_TEXT,
        NO_ROUTE,
    ]
    for text in texts:
        lowered = text.lower()
        assert "promise" not in lowered
        assert "book" not in lowered or text == BOOKING_OR_PAYMENT
        assert re.search(r"\bsafe\b", lowered) is None


def test_system_error_sentence_is_shared_with_the_graph():
    assert GRAPH_SYSTEM_ERROR_TEXT == SYSTEM_ERROR_TEXT


# --- T-6-02: facts are rounded and do not carry the raw request ---


def test_t_6_02_facts_are_rounded_and_omit_the_raw_request():
    state = _planned()
    state["guard"] = GuardResult(decision="allow", clean_text=SECRET, masked=[], reason=None)
    facts = build_facts(state)
    assert facts["distance_km"] == 4.2
    assert facts["duration_min"] == 18
    assert facts["climb_m"] == 38
    assert facts["required_stops"] == ["Canadian Tire (Yonge)"]
    assert facts["must_mention"] == ["closures"]
    assert facts["sponsored"] is None
    dumped = json.dumps(facts)
    assert SECRET not in dumped
    assert "-79." not in dumped
    for check in facts["checks"]:
        assert set(check) == {"id", "status", "evidence"}


def test_t_6_02_traffic_minutes_and_sponsor_distance_are_rounded():
    candidate = running_candidate().model_copy(update={"duration_s": 1100, "duration_traffic_s": 90})
    sponsored = SponsoredSuggestion(
        name="Dark Horse Espresso",
        category="cafe",
        lon=-79.39,
        lat=43.65,
        rating=4.6,
        distance_m=180.4,
    )
    report = running_report().model_copy(update={"sponsored": sponsored})
    facts = build_facts(_planned(candidates=[candidate], report=report))
    assert facts["duration_min"] == 2
    assert facts["sponsored"]["distance_m"] == 180
    assert "lon" not in facts["sponsored"]
    assert facts["sponsored"]["name"] == "Dark Horse Espresso"


# --- T-6-03: a fake model is returned unchanged ---


def test_t_6_03_fake_model_passes_through_and_sees_no_raw_text():
    draft = ReplyDraft(
        message="Here's a 4.2 km bike route, about 18 minutes, with 38 m of climbing.",
        mentioned_ids=["closures"],
        kind="llm",
    )
    fake = _FakeModel(draft)
    state = _planned()
    state["utterance"] = SECRET
    state["guard"] = GuardResult(decision="allow", clean_text=SECRET, masked=[], reason=None)
    assert template_message(state) is None
    returned = write_reply(state, llm=fake)
    assert returned == draft
    assert returned.mentioned_ids == ["closures"]
    assert fake.calls == 1
    assert fake.messages is not None
    blob = json.dumps(fake.messages)
    assert SECRET not in blob
    assert f"Word cap: {get_settings().writer.max_words} words." in fake.messages[0]["content"]
    assert "4.2" in fake.messages[1]["content"]


def test_t_6_03_handoff_with_a_route_uses_the_model():
    report = running_report().model_copy(
        update={"decision": "handoff", "handoff_reason": "step_free", "must_mention": ["step_free"]}
    )
    draft = ReplyDraft(message="A person has the trip.", mentioned_ids=["step_free"], kind="llm")
    fake = _FakeModel(draft)
    assert write_reply(_planned(report=report), llm=fake) == draft
    assert fake.calls == 1


def test_write_node_returns_only_the_draft():
    out = write_node(_guard("empty"), llm=_Boom())
    assert set(out) == {"draft"}
    assert out["draft"].kind == "template"


# --- T-6-04: three recorded replies satisfy the output-guard rules ---


def test_t_6_04_recorded_replies_pass_output_guard_rules():
    replies = json.loads(REPLIES.read_text(encoding="utf-8"))
    prompt = PROMPT.read_text(encoding="utf-8")
    assert [item["name"] for item in replies] == ["accept", "accept_two_warnings", "handoff"]
    for reply in replies:
        assert _guard_problems(reply) == [], reply["name"]
        assert reply["message"] in prompt
    invented = dict(replies[0])
    invented["message"] = invented["message"].replace("4.2 km", "9 km")
    assert _guard_problems(invented)
