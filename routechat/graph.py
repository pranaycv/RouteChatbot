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

from routechat.agents.templates import SYSTEM_ERROR_TEXT
from routechat.config import get_settings
from routechat.logging import install_stage_log, log_request
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

# SYSTEM_ERROR_TEXT is the WP6 sentence. The graph uses it only when a stage
# crashes or the budget runs out, which is before the writer can speak.

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
    """Shared state for one request.

    total=False because each stage returns only the fields it owns, and
    LangGraph merges that partial dict into what is already there. The
    comment on each field names its only writer. A new request must not
    carry `final` over from the previous turn: a set `final` is how the
    routers know a stage has already stopped the run.
    """

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
    """Read one contract field from a Pydantic model or a plain dict.

    Stages should return models. A dict is accepted so a package can land
    before every return value has been switched over, and the routers still
    agree on the field name.
    """
    if isinstance(value, dict):
        return value[name]
    return getattr(value, name)


def _finished(state: TripState) -> bool:
    """True once the error wrapper has written the reply the user will see.

    Routers check this before they read stage output. A crash does not fill
    `guard` or `report`, and the next edge would raise if it assumed they exist.
    """
    return state.get("final") is not None


def after_guard(state: TripState) -> str:
    """allow continues. clarify, refuse, and fixed_reply skip straight to the writer."""
    if _finished(state):
        return END
    decision = _field(state["guard"], "decision")
    if decision == "allow":
        return "classify"
    if decision in {"clarify", "refuse", "fixed_reply"}:
        return "write"
    raise ValueError(f"unknown guard decision: {decision}")


def after_classify(state: TripState) -> str:
    """plan, replan, compare_modes, and lookup_only go to the extractor.

    clarify, escalate, and refuse go to the writer. A clarify that is still
    unresolved after one follow-up also goes to the writer; the write wrapper
    relabels that case as escalate before the writer runs.
    """
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
    """A complete trip goes to the map tools. A missing field or too many stops goes to the writer."""
    if _finished(state):
        return END
    constraints = state["constraints"]
    if _field(constraints, "missing_field") is None and not _field(constraints, "too_many_stops"):
        return "maps"
    return "write"


def after_maps(state: TripState) -> str:
    """Map tools continue to the verifier, unless that stage was aborted.

    The handbook draws maps to verify as a fixed edge. It is conditional
    only so a timeout or a crash can stop the graph instead of calling the
    verifier with no route.
    """
    if _finished(state):
        return END
    return "verify"


def after_verify(state: TripState) -> str:
    """accept and handoff go to the writer. The first revise goes back to maps.

    revision_count == 1 means the verifier is asking for that one loop. A
    second revise leaves the count at 2 or more and falls through to the
    writer, so a verifier bug cannot loop forever.
    """
    if _finished(state):
        return END
    decision = _field(state["report"], "decision")
    if decision == "revise" and state.get("revision_count", 0) == 1:
        return "maps"
    return "write"


def after_write(state: TripState) -> str:
    """The writer continues to the output guard, unless the writer itself failed."""
    if _finished(state):
        return END
    return "guard_out"


def _escalate_stale_clarify(state: TripState) -> tuple[TripState, dict]:
    """Turn a second clarify into escalate before the writer sees the state.

    The app keeps clarify_count between turns. The routing table calls this
    case "write, as escalate". The writer only picks a template from
    route_action, so the label change lives here, in the graph, rather than
    inside the writer's package.
    """
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
    """Allow one revise, even when the verifier asks for a second.

    The first revise sets revision_count to 1, and after_verify sends the
    request back to the map tools. If verify returns revise again, the count
    becomes 2 and the next edge goes to the writer. The verifier is supposed
    to return handoff on that second pass. This limit still holds when it does not.
    """
    report = update.get("report")
    if report is None or _field(report, "decision") != "revise":
        return update
    prior = state.get("revision_count", 0)
    capped = dict(update)
    capped["revision_count"] = 1 if prior < 1 else prior + 1
    return capped


def _log_failure(request_id: str, stage: str, decision: str, error_type: str | None, latency_ms: float) -> None:
    """Write one JSON log line with the exception type and not its message.

    The message and the traceback can contain the user's text. WP7 owns the
    full per-stage logger; this line exists so a crash is still recorded
    before that package is merged.
    """
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
    """Stop the request with the system-error template and an empty redacted request.

    The raw utterance stays out of the ticket because this path can run
    before the input guard has masked it. `final` is the signal the routers
    use to stop. A normal state update is used, rather than a LangGraph
    Command, because a Command does not cancel the edges already drawn on
    these nodes.
    """
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
    return {"handoff": ticket, "final": final}


def _call_node(stage: str, fn: NodeFn, state: TripState) -> dict:
    """Run one stage, then apply the two rules that belong to the graph.

    deadline is a time.monotonic() timestamp. The first stage fills it in
    when the caller did not. A stage that raises, returns a non-dict, or
    starts after the deadline becomes a handoff instead of a crash. The
    exception object itself is not logged.
    """
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
    """Adapt WP2's classify(turn) when predict.py does not export classify_node.

    The utterance is the guard's clean_text, so the classifier does not see
    personal data the input guard has already masked.
    """
    classify = _load("routechat.classifier.predict", ("classify",))
    guard = state.get("guard")
    utterance = _field(guard, "clean_text") if guard is not None else state["utterance"]
    turn = UserTurn(utterance=utterance, turn_index=state["turn_index"], known=state.get("known"))  # type: ignore[arg-type]
    return {"classification": classify(turn)}


def _import_stage_nodes() -> dict[str, NodeFn]:
    """Import each stage from the module its owner ships.

    classify_node is preferred over classify when both exist, so WP2 can own
    its node wrapper without this file also calling classify. A missing
    module raises ImportError. This function does not create those files.
    """
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
    """Compile the seven-stage graph.

    Pass nodes to inject fakes in tests. Production code omits that argument
    and gets resolve_nodes(), which is the stubs or the real stage packages.
    """
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
    # Handbook edge is maps -> verify. Conditional so an abort can end the run.
    graph.add_conditional_edges("maps", after_maps, {"verify": "verify", **stop})
    graph.add_conditional_edges("verify", after_verify, {"maps": "maps", "write": "write", **stop})
    graph.add_conditional_edges("write", after_write, {"guard_out": "guard_out", **stop})
    graph.add_edge("guard_out", END)
    return graph.compile()


def invoke(payload: dict, nodes: Mapping[str, NodeFn] | None = None) -> dict:
    """Run one request. The app reads state['final'] and state.get('handoff').

    WP7's logger copies the stages' JSON lines into the log file and gets one
    request line here, after guard_out, with the end-to-end latency.
    """
    install_stage_log()
    started = time.perf_counter()
    state = build_graph(nodes).invoke(payload)
    log_request(state, (time.perf_counter() - started) * 1000)
    return state
