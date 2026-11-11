"""LangGraph workflow. Routing decisions stay in this module; stages do not call each other.

Each stage is a function `node(state) -> dict` that returns only the fields it owns.
STUBS=1 uses routechat.stubs. Otherwise the graph imports the stage packages below
and leaves their files untouched:

    guard_in   routechat.guards.input_guard:input_guard
    classify   routechat.classifier.predict:classify_node  (or classify(turn))
    extract    routechat.agents.extractor:extract_node
    maps       routechat.agents.maps_node:maps_node
    verify     routechat.verify.decide:verify_node
    write      routechat.agents.writer:write_node
    guard_out  routechat.guards.output_guard:output_guard
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from routechat.config import get_settings
from routechat.schemas import (
    ConstraintSet,
    FinalAnswer,
    GuardResult,
    HandoffTicket,
    ReplyDraft,
    ResolvedPlace,
    RouteCandidate,
    RouteClassification,
    UserTurn,
    VerificationReport,
)

logger = logging.getLogger("routechat.graph")

# WP6 template for tool_timeout / system_error. Used here when the writer never runs.
SYSTEM_ERROR_TEXT = "I couldn’t reach the map service just now. Please try again in a minute."

NODE_ORDER = (
    "guard_in",
    "classify",
    "extract",
    "maps",
    "verify",
    "write",
    "guard_out",
)
_EXTRACT_ACTIONS = frozenset({"plan", "replan", "compare_modes", "lookup_only"})
_WRITE_ACTIONS = frozenset({"clarify", "escalate", "refuse"})

NodeFn = Callable[..., dict]


class TripState(TypedDict, total=False):
    """One request. Each field has one writer, named in the comment."""

    request_id: str
    utterance: str
    turn_index: int
    known: ConstraintSet | None
    clarify_count: int
    revision_count: int
    deadline: float  # time.monotonic() budget, written by the graph or the app
    guard: GuardResult  # WP1
    classification: RouteClassification  # WP2
    constraints: ConstraintSet  # WP3
    places: list[ResolvedPlace]  # WP4
    candidates: list[RouteCandidate]  # WP4
    report: VerificationReport  # WP5
    handoff: HandoffTicket | None  # WP5, or the graph on system_error / timeout
    draft: ReplyDraft  # WP6
    final: FinalAnswer  # WP7


def _field(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value[name]
    return getattr(value, name)


def _finished(state: TripState) -> bool:
    """The error wrapper already produced the reply, so the graph must stop."""
    return state.get("final") is not None


def after_guard(state: TripState) -> str:
    if _finished(state):
        return END
    decision = _field(state["guard"], "decision")
    if decision == "allow":
        return "classify"
    if decision in {"clarify", "refuse", "fixed_reply"}:
        return "write"
    raise ValueError(f"unknown guard decision: {decision}")


def after_classify(state: TripState) -> str:
    if _finished(state):
        return END
    action = _field(state["classification"], "route_action")
    if action in _EXTRACT_ACTIONS:
        return "extract"
    if action == "clarify" and state.get("clarify_count", 0) >= 1:
        return "write"
    if action in _WRITE_ACTIONS:
        return "write"
    raise ValueError(f"unknown route_action: {action}")


def after_extract(state: TripState) -> str:
    if _finished(state):
        return END
    constraints = state["constraints"]
    if _field(constraints, "missing_field") is None and not _field(constraints, "too_many_stops"):
        return "maps"
    return "write"


def after_maps(state: TripState) -> str:
    if _finished(state):
        return END
    return "verify"


def after_verify(state: TripState) -> str:
    """A revise returns to maps once. A second revise does not loop."""
    if _finished(state):
        return END
    decision = _field(state["report"], "decision")
    if decision == "revise" and state.get("revision_count", 0) == 1:
        return "maps"
    return "write"


def after_write(state: TripState) -> str:
    if _finished(state):
        return END
    return "guard_out"


def _escalate_stale_clarify(state: TripState) -> tuple[TripState, dict]:
    """Still unclear after one follow-up is written as escalate."""
    classification = state.get("classification")
    if classification is None:
        return state, {}
    if _field(classification, "route_action") != "clarify":
        return state, {}
    if state.get("clarify_count", 0) < 1:
        return state, {}
    if isinstance(classification, RouteClassification):
        updated = classification.model_copy(update={"route_action": "escalate", "clarify_field": "none"})
    else:
        updated = {**classification, "route_action": "escalate", "clarify_field": "none"}
    merged = dict(state)
    merged["classification"] = updated
    return merged, {"classification": updated}  # type: ignore[return-value]


def _cap_revision(state: TripState, update: dict) -> dict:
    report = update.get("report")
    if report is None or _field(report, "decision") != "revise":
        return update
    prior = state.get("revision_count", 0)
    capped = dict(update)
    capped["revision_count"] = 1 if prior < 1 else prior + 1
    return capped


def _log_failure(request_id: str, stage: str, decision: str, error_type: str | None, latency_ms: float) -> None:
    # Exception type only. The message and the user text stay out of the log.
    logger.error(
        json.dumps(
            {
                "request_id": request_id,
                "stage": stage,
                "latency_ms": latency_ms,
                "tokens_in": 0,
                "tokens_out": 0,
                "cost_usd": 0.0,
                "api_calls": {},
                "decision": decision,
                "error_type": error_type,
            }
        )
    )


def _abort(state: TripState, stage: str, reason: str, error_type: str | None, latency_ms: float) -> dict:
    _log_failure(str(state.get("request_id", "unknown")), stage, reason, error_type, latency_ms)
    ticket = HandoffTicket(
        ticket_id=f"t_{uuid4().hex[:12]}",
        reason=reason,
        redacted_request="",
        constraints=state.get("constraints"),
        report=state.get("report"),
        created_at=datetime.now(timezone.utc),
    )
    final = FinalAnswer(
        approved=True,
        text=SYSTEM_ERROR_TEXT,
        route_geojson=None,
        stops=[],
        used_fallback_template=True,
    )
    # `final` tells every router to stop. The static edges would otherwise continue.
    return {"handoff": ticket, "final": final}


def _call_node(stage: str, fn: NodeFn, state: TripState) -> dict:
    started = time.perf_counter()
    deadline = state.get("deadline")
    prefix: dict = {}
    if deadline is None:
        deadline = time.monotonic() + get_settings().graph.request_budget_s
        prefix["deadline"] = deadline
        state = {**state, "deadline": deadline}  # type: ignore[misc]
    if time.monotonic() >= deadline:
        return _abort(state, stage, "timeout", None, 0.0)

    call_state: TripState = state
    if stage == "write":
        call_state, escalated = _escalate_stale_clarify(state)
        prefix.update(escalated)
    try:
        update = fn(call_state)
    except Exception as exc:
        latency_ms = round((time.perf_counter() - started) * 1000, 3)
        return _abort(state, stage, "system_error", type(exc).__name__, latency_ms)
    if not isinstance(update, dict):
        latency_ms = round((time.perf_counter() - started) * 1000, 3)
        return _abort(state, stage, "system_error", TypeError.__name__, latency_ms)
    if stage == "verify":
        update = _cap_revision(state, update)
    return {**prefix, **update}


def _bind(stage: str, fn: NodeFn) -> NodeFn:
    def node(state: TripState) -> dict:
        return _call_node(stage, fn, state)

    node.__name__ = stage
    return node


def _load(module_name: str, names: tuple[str, ...]) -> Callable:
    module = importlib.import_module(module_name)
    for name in names:
        fn = getattr(module, name, None)
        if callable(fn):
            return fn
    expected = " or ".join(f"{module_name}:{name}" for name in names)
    raise ImportError(f"missing stage callable ({expected}). Set STUBS=1 to run the stubs.")


def _classify_adapter(state: TripState) -> dict:
    classify = _load("routechat.classifier.predict", ("classify",))
    guard = state.get("guard")
    utterance = _field(guard, "clean_text") if guard is not None else state["utterance"]
    turn = UserTurn(utterance=utterance, turn_index=state["turn_index"], known=state.get("known"))  # type: ignore[arg-type]
    return {"classification": classify(turn)}


def _import_stage_nodes() -> dict[str, NodeFn]:
    classify_module = importlib.import_module("routechat.classifier.predict")
    classify_node = getattr(classify_module, "classify_node", None)
    classify: NodeFn = classify_node if callable(classify_node) else _classify_adapter
    return {
        "guard_in": _load("routechat.guards.input_guard", ("input_guard",)),
        "classify": classify,
        "extract": _load("routechat.agents.extractor", ("extract_node",)),
        "maps": _load("routechat.agents.maps_node", ("maps_node",)),
        "verify": _load("routechat.verify.decide", ("verify_node",)),
        "write": _load("routechat.agents.writer", ("write_node",)),
        "guard_out": _load("routechat.guards.output_guard", ("output_guard",)),
    }


def resolve_nodes() -> dict[str, NodeFn]:
    """STUBS=1 selects the running-example stubs. Any other value loads the stage packages."""
    if os.environ.get("STUBS") == "1":
        from routechat.stubs import stub_nodes

        return stub_nodes()
    return _import_stage_nodes()


def build_graph(nodes: Mapping[str, NodeFn] | None = None):
    """Compile the seven-stage graph. Pass nodes to inject fakes in tests."""
    resolved = dict(resolve_nodes() if nodes is None else nodes)
    missing = [name for name in NODE_ORDER if name not in resolved]
    if missing:
        raise KeyError(f"graph is missing nodes: {', '.join(missing)}")

    graph = StateGraph(TripState)
    for stage in NODE_ORDER:
        graph.add_node(stage, _bind(stage, resolved[stage]))
    stop = {END: END}
    graph.add_edge(START, "guard_in")
    graph.add_conditional_edges(
        "guard_in", after_guard, {"classify": "classify", "write": "write", **stop}
    )
    graph.add_conditional_edges(
        "classify", after_classify, {"extract": "extract", "write": "write", **stop}
    )
    graph.add_conditional_edges("extract", after_extract, {"maps": "maps", "write": "write", **stop})
    # Success path is maps -> verify. A timeout or crash on maps stops instead.
    graph.add_conditional_edges("maps", after_maps, {"verify": "verify", **stop})
    graph.add_conditional_edges("verify", after_verify, {"maps": "maps", "write": "write", **stop})
    graph.add_conditional_edges("write", after_write, {"guard_out": "guard_out", **stop})
    graph.add_edge("guard_out", END)
    return graph.compile()


def invoke(payload: dict, nodes: Mapping[str, NodeFn] | None = None) -> dict:
    """Run one request. The app reads state['final'] and state.get('handoff')."""
    return build_graph(nodes).invoke(payload)
