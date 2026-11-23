# Owner: Person A (WP6 response writer), Team Rosedale, MMAI 894.
# Only Person A edits this file. Other packages may import it but should not change it;
# AI coding tools must not edit this file unless they are working on WP6.
# Written for this project with AI assistance; no code copied from other sources.
"""WP6 response writer.

Early exits return a fixed template. An accepted route, or a handoff that
still has a route, is the only path that calls the model. The model sees
rounded facts, checks, must_mention, and the sponsored place. It does not
see the raw user text, and this module has no tools.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from routechat.agents.templates import (
    CLARIFY_TEMPLATES,
    ERROR_TEMPLATES,
    ESCALATE,
    GUARD_TEMPLATES,
    REFUSAL_TEMPLATES,
    UNPARSEABLE,
    mode_not_supported_text,
    place_not_found_text,
    too_many_stops_text,
)
from routechat.config import get_settings
from routechat.llm import api_calls_for, complete
from routechat.logging import stage_line, token_cost
from routechat.schemas import ReplyDraft

_PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "writer_v1.md"
PROMPT_VERSION = _PROMPT_PATH.stem
_EARLY_GUARD = frozenset({"clarify", "refuse", "fixed_reply"})
_EARLY_ACTIONS = frozenset({"refuse", "escalate", "clarify"})


def _field(value: Any, name: str) -> Any:
    """Read one contract field from a model or a dict."""
    if isinstance(value, dict):
        return value[name]
    return getattr(value, name)


def _round_km(distance_m: float | None) -> float | None:
    """Distance to 0.1 km. None stays None so the model cannot invent it."""
    if distance_m is None:
        return None
    return round(distance_m / 1000, 1)


def _round_minutes(duration_s: float | None) -> int | None:
    """Duration to a whole minute."""
    if duration_s is None:
        return None
    return int(round(duration_s / 60))


def _round_metres(metres: float | None) -> int | None:
    """Climb or a sponsor offset to a whole metre."""
    if metres is None:
        return None
    return int(round(metres))


def _chosen_candidate(state: dict[str, Any]) -> Any | None:
    """The route the verifier chose, when that route has no tool error."""
    report = state.get("report")
    candidates = list(state.get("candidates") or [])
    if report is None:
        return None
    chosen_id = _field(report, "chosen_route")
    for candidate in candidates:
        if chosen_id and _field(candidate, "id") == chosen_id:
            return candidate if not _field(candidate, "error") else None
    for candidate in candidates:
        if not _field(candidate, "error"):
            return candidate
    return None


def _error_reason(state: dict[str, Any]) -> str | None:
    """A tool or graph error on a handoff. Accept does not consult this."""
    report = state.get("report")
    decision = _field(report, "decision") if report is not None else None
    if decision not in {None, "handoff"}:
        return None
    if report is not None:
        reason = _field(report, "handoff_reason")
        if reason in ERROR_TEMPLATES or reason == "place_not_found":
            return reason
    handoff = state.get("handoff")
    if handoff is not None:
        reason = _field(handoff, "reason")
        if reason in ERROR_TEMPLATES or reason == "place_not_found":
            return reason
    if decision == "handoff":
        for candidate in state.get("candidates") or []:
            reason = _field(candidate, "error")
            if reason in ERROR_TEMPLATES or reason == "place_not_found":
                return reason
    return None


def _unsupported_mode(state: dict[str, Any]) -> str | None:
    """The mode the map tools turned away. The trip's own mode if no route says."""
    for candidate in state.get("candidates") or []:
        if _field(candidate, "error") == "mode_not_supported":
            return _field(candidate, "mode")
    constraints = state.get("constraints")
    return _field(constraints, "mode") if constraints is not None else None


def _missing_query(state: dict[str, Any]) -> str:
    """First place the user named that the map tools did not resolve."""
    constraints = state.get("constraints")
    if constraints is None:
        return ""
    asked: list[str] = []
    origin = _field(constraints, "origin")
    if origin:
        asked.append(origin)
    for stop in _field(constraints, "stops") or []:
        query = _field(stop, "query")
        if query:
            asked.append(query)
    destination = _field(constraints, "destination")
    if destination:
        asked.append(destination)
    resolved = {_field(place, "query") for place in state.get("places") or []}
    for query in asked:
        if query not in resolved:
            return query
    return ""


def template_message(state: dict[str, Any]) -> str | None:
    """The fixed sentence for this state, or None when the model must write.

    Guard reasons win, then a classifier early exit, then an incomplete
    extraction, then a tool error. An accepted route, or a handoff that
    still has a route and no tool error, returns None.
    """
    guard = state.get("guard")
    if guard is not None and _field(guard, "decision") in _EARLY_GUARD:
        reason = _field(guard, "reason")
        return GUARD_TEMPLATES.get(reason, UNPARSEABLE)

    classification = state.get("classification")
    if classification is not None and _field(classification, "route_action") in _EARLY_ACTIONS:
        action = _field(classification, "route_action")
        if action == "refuse":
            reason = _field(classification, "refusal_reason")
            return REFUSAL_TEMPLATES.get(reason, UNPARSEABLE)
        if action == "escalate":
            return ESCALATE
        field = _field(classification, "clarify_field")
        return CLARIFY_TEMPLATES.get(field, UNPARSEABLE)

    constraints = state.get("constraints")
    if constraints is not None:
        missing = _field(constraints, "missing_field")
        if missing == "origin":
            return CLARIFY_TEMPLATES["origin"]
        if missing == "destination":
            return CLARIFY_TEMPLATES["destination"]
        if missing == "unparseable":
            return UNPARSEABLE
        if _field(constraints, "too_many_stops"):
            return too_many_stops_text(get_settings().trip.max_required_stops)

    reason = _error_reason(state)
    if reason == "place_not_found":
        return place_not_found_text(_missing_query(state))
    if reason == "mode_not_supported":
        return mode_not_supported_text(_unsupported_mode(state))
    if reason is not None:
        return ERROR_TEMPLATES[reason]

    report = state.get("report")
    if report is None:
        return UNPARSEABLE
    decision = _field(report, "decision")
    if decision in {"accept", "handoff"} and _chosen_candidate(state) is not None:
        return None
    if decision in {"handoff", "revise"}:
        return ESCALATE
    return UNPARSEABLE


def _required_stop_names(state: dict[str, Any]) -> list[str]:
    constraints = state.get("constraints")
    if constraints is None:
        return []
    names_by_query = {
        _field(place, "query"): _field(place, "name") for place in state.get("places") or []
    }
    names: list[str] = []
    for stop in _field(constraints, "stops") or []:
        if not _field(stop, "required"):
            continue
        query = _field(stop, "query")
        names.append(names_by_query.get(query, query))
    return names


def _sponsored_facts(report: Any) -> dict[str, Any] | None:
    sponsored = _field(report, "sponsored")
    if sponsored is None:
        return None
    # Coordinates stay on the map object. The model only needs the label.
    return {
        "name": _field(sponsored, "name"),
        "category": _field(sponsored, "category"),
        "rating": _field(sponsored, "rating"),
        "distance_m": _round_metres(_field(sponsored, "distance_m")),
    }


def build_facts(state: dict[str, Any]) -> dict[str, Any]:
    """Rounded facts for the model. The raw utterance is not copied in.

    Minutes use the traffic duration when the route has one, because the
    verifier's duration check does the same. Otherwise they use duration_s.
    """
    report = state["report"]
    candidate = _chosen_candidate(state)
    if candidate is None:
        raise ValueError("the model path needs a chosen route with no tool error")
    traffic_s = _field(candidate, "duration_traffic_s")
    duration_s = traffic_s if traffic_s is not None else _field(candidate, "duration_s")
    checks = [
        {
            "id": _field(check, "id"),
            "status": _field(check, "status"),
            "evidence": _field(check, "evidence"),
        }
        for check in _field(report, "checks")
    ]
    return {
        "decision": _field(report, "decision"),
        "mode": _field(candidate, "mode"),
        "distance_km": _round_km(_field(candidate, "distance_m")),
        "duration_min": _round_minutes(duration_s),
        "climb_m": _round_metres(_field(candidate, "ascend_m")),
        "required_stops": _required_stop_names(state),
        "checks": checks,
        "must_mention": list(_field(report, "must_mention")),
        "sponsored": _sponsored_facts(report),
    }


def system_prompt() -> str:
    """Prompt file plus the word cap from config, so the cap is not copied."""
    cap = get_settings().writer.max_words
    body = _PROMPT_PATH.read_text(encoding="utf-8")
    return f"Word cap: {cap} words. This number is writer.max_words in config.yaml.\n\n{body}"


def _messages(facts: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": system_prompt()},
        {"role": "user", "content": json.dumps(facts, ensure_ascii=False)},
    ]


def _live(messages: list[dict[str, str]]) -> tuple[ReplyDraft, tuple[int, int], dict[str, int]]:
    """One structured call through the shared client: OpenAI or OpenRouter, and FIXTURES record or replay."""
    settings = get_settings().writer
    result = complete(messages, settings.model, settings.temperature, schema=ReplyDraft)
    draft = ReplyDraft.model_validate_json(result.text)
    return (
        draft.model_copy(update={"kind": "llm"}),
        (result.tokens_in, result.tokens_out),
        api_calls_for(result.provider),
    )


def _as_draft(result: Any) -> ReplyDraft:
    if isinstance(result, ReplyDraft):
        return result
    if isinstance(result, dict) and "parsed" in result and "raw" in result:
        if result["parsed"] is None:
            raise ValueError("the writer model returned no valid ReplyDraft")
        return _as_draft(result["parsed"])
    return ReplyDraft.model_validate(result)


def _usage(result: Any) -> tuple[int, int]:
    """Input and output tokens from an include_raw result; (0, 0) when the call reports none."""
    raw = result.get("raw") if isinstance(result, dict) else None
    meta = getattr(raw, "usage_metadata", None) or {}
    return int(meta.get("input_tokens", 0) or 0), int(meta.get("output_tokens", 0) or 0)


def _write(state: dict[str, Any], llm: Any = None) -> tuple[ReplyDraft, tuple[int, int], dict[str, int]]:
    """The draft, its input and output tokens, and the calls it made."""
    message = template_message(state)
    if message is not None:
        return ReplyDraft(message=message, mentioned_ids=[], kind="template"), (0, 0), {}
    messages = _messages(build_facts(state))
    if llm is None:
        return _live(messages)
    result = llm.invoke(messages)
    return _as_draft(result), _usage(result), {}


def write_reply(state: dict[str, Any], llm: Any = None) -> ReplyDraft:
    """Return the ReplyDraft. Pass llm to avoid the live model in tests."""
    return _write(state, llm)[0]


def write_node(state: dict[str, Any], llm: Any = None) -> dict[str, ReplyDraft]:
    """LangGraph node. Returns only the draft field. llm is for tests.

    Logs one stage line. The model, prompt version, tokens and cost appear
    only when the model wrote the reply; a template costs nothing.
    """
    started = time.perf_counter()
    draft, (tokens_in, tokens_out), api_calls = _write(state, llm)
    model = get_settings().writer.model if draft.kind == "llm" else None
    stage_line(
        "write",
        str(state.get("request_id", "")),
        draft.kind,
        (time.perf_counter() - started) * 1000,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=token_cost(model, tokens_in, tokens_out),
        api_calls=api_calls,
        model=model,
        prompt_version=PROMPT_VERSION if model else None,
    )
    return {"draft": draft}
