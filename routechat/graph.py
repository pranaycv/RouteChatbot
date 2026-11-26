"""LangGraph workflow. Routing decisions stay in this module; stages do not call each other.

Each stage is a function `node(state) -> dict` that returns only the fields it owns.
STUBS=1 uses routechat.stubs. Otherwise the graph imports the stage packages below
and leaves their files untouched:

    guard_in   routechat.guards.input_guard:input_guard
    rewrite    routechat.agents.rewriter:rewrite_node
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
from routechat.device import End, device_ends, fill
from routechat.logging import install_stage_log, log_request
from routechat.schemas import (
    ConstraintSet,
    FinalAnswer,
    GuardResult,
    HandoffTicket,
    ReplyDraft,
    ResolvedPlace,
    RewrittenQuery,
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
    "rewrite",
    "classify",
    "extract",
    "maps",
    "verify",
    "write",
    "guard_out",
)
# Stages that read the request. They get the rewritten one in place of the latest message.
_READS_REQUEST = frozenset({"classify", "extract", "verify"})
# Different questions on one trip before a person takes it. The same one is never asked twice.
_MAX_QUESTIONS = 3
_EXTRACT_ACTIONS = frozenset({"plan", "replan", "compare_modes", "lookup_only"})
_WRITE_ACTIONS = frozenset({"clarify", "escalate", "refuse"})
# The ends of the trip the extractor can confirm. It has no field for a stop of unknown kind.
_PLACE_FIELDS = frozenset({"origin", "destination"})

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
    history: list[dict[str, str]]  # the app; earlier turns as user, query, assistant, already masked
    latest: str  # the app; the newest message alone, when utterance joins it to the one it answers
    asked: list[str]  # the app; questions already asked on this trip
    picked: list[ResolvedPlace]  # the app; places the user chose when asked which one
    seen: list[ResolvedPlace]  # the app; places this chat already routed to, newest first
    locatable: bool  # the app; the browser can be asked where the user is
    resumed: bool  # the app; this trip waited for the device location, see resume
    device: ResolvedPlace | None  # the app; where the user is, on a resumed trip
    device_ends: list[End]  # the graph; ends of the trip the device location fills
    revision_count: int
    deadline: float  # time.monotonic() budget, written by the graph or the app
    guard: GuardResult  # WP1
    rewrite: RewrittenQuery  # the conversation rewriter
    classification: RouteClassification  # WP2; the graph relabels a settled or stale clarify
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
    """allow continues to the rewriter. clarify, refuse, and fixed_reply skip straight to the writer."""
    if _finished(state):
        return END
    decision = _field(state["guard"], "decision")
    if decision == "allow":
        return "rewrite"
    if decision in {"clarify", "refuse", "fixed_reply"}:
        return "write"
    raise ValueError(f"unknown guard decision: {decision}")


def _rewrite_status(state: Mapping[str, Any]) -> str | None:
    result = state.get("rewrite")
    return None if result is None else _field(result, "status")


def is_new_trip(state: Mapping[str, Any]) -> bool:
    """True when the rewriter found that the message moved to another trip."""
    return _rewrite_status(state) == "ready" and bool(_field(state["rewrite"], "new_trip"))


def after_rewrite(state: TripState) -> str:
    """unclear goes to the writer, which asks which trip. Anything else goes to the classifier."""
    if _finished(state):
        return END
    return "write" if _rewrite_status(state) == "unclear" else "classify"


def _with_request(state: TripState) -> TripState:
    """The state as the reading stages see it. A rewritten request is complete, so it starts a trip of its own."""
    guard = state.get("guard")
    if guard is None or _rewrite_status(state) != "ready":
        return state
    text = _field(state["rewrite"], "query")
    changed = (
        guard.model_copy(update={"clean_text": text})
        if isinstance(guard, GuardResult)
        else {**guard, "clean_text": text}
    )
    return {**state, "guard": changed, "known": None, "turn_index": 0}  # type: ignore[typeddict-item]


def _asks_for_a_place(classification: Any) -> bool:
    return (
        _field(classification, "route_action") == "clarify"
        and _field(classification, "clarify_field") in _PLACE_FIELDS
    )


def after_classify(state: TripState) -> str:
    """plan, replan, compare_modes, and lookup_only go to the extractor.

    So does a clarify about the origin or the destination: the extractor reads
    the places, so it decides whether one is missing. The handbook sends every
    clarify to the writer. Other clarifies, escalate, and refuse go to the
    writer. A clarify that has been asked enough (_asked_enough) is relabelled
    as escalate by the write wrapper before the writer runs.
    """
    if _finished(state):
        return END
    action = _field(state["classification"], "route_action")
    if action in _EXTRACT_ACTIONS:
        return "extract"
    if _asks_for_a_place(state["classification"]):
        return "extract"
    if action in _WRITE_ACTIONS:
        return "write"
    raise ValueError(f"unknown route_action: {action}")


def after_extract(state: TripState) -> str:
    """A complete trip goes to the map tools. A missing field or too many stops goes to the writer.

    A trip that needs the device location stops here. The app asks the browser and calls resume.
    """
    if _finished(state) or wants_device(state):
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


def _relabel(classification: Any, action: str, clarify_field: str) -> Any:
    changes = {"route_action": action, "clarify_field": clarify_field}
    if isinstance(classification, RouteClassification):
        return classification.model_copy(update=changes)
    return {**classification, **changes}


def _as_trip(state: TripState) -> TripState:
    """The state the extractor reads when it double-checks a clarify.

    It is shown a trip label, because its prompt is written for trips and a
    clarify label would tell it the answer.
    """
    action = "plan" if state.get("known") is None else "replan"
    return {**state, "classification": _relabel(state["classification"], action, "none")}  # type: ignore[typeddict-item]


def _settle_clarify(state: TripState, trip: TripState, update: dict) -> dict:
    """Keep the clarify only if the extractor also finds a place missing.

    When it names the missing end, the question asks for that one. When the
    trip is complete, or only has too many stops, it goes on as a trip. An
    unreadable message keeps the classifier's question.
    """
    missing = _field(update["constraints"], "missing_field")
    if missing is None:
        return {**update, "classification": trip["classification"]}
    if missing in _PLACE_FIELDS:
        return {**update, "classification": _relabel(state["classification"], "clarify", missing)}
    return update


def wants_device(state: Mapping[str, Any]) -> bool:
    """True when the trip is complete but for the device location, and the browser can be asked for it."""
    if not state.get("locatable") or not state.get("device_ends"):
        return False
    constraints = state["constraints"]
    return _field(constraints, "missing_field") is None and not _field(constraints, "too_many_stops")


def _mark_device(state: TripState, update: dict) -> dict:
    """Name the ends the device location would fill. With no browser to ask, they are left missing."""
    constraints = update.get("constraints")
    if constraints is None:
        return update
    constraints = ConstraintSet.model_validate(constraints)
    guard = state.get("guard")
    ends = device_ends(constraints, state["utterance"] if guard is None else _field(guard, "clean_text"))
    if not ends:
        return update
    filled = fill(constraints, ends, bool(state.get("locatable")))
    return {**update, "constraints": filled, "device_ends": ends}


def _take_up(state: TripState) -> dict:
    """The waiting trip with the device location, or the question for the place when there is none."""
    constraints = fill(state["constraints"], state["device_ends"], state.get("device") is not None)
    update: dict = {"constraints": constraints}
    if constraints.missing_field in _PLACE_FIELDS:
        update["classification"] = _relabel(state["classification"], "clarify", constraints.missing_field)
    return update


def question_key(state: Mapping[str, Any]) -> str | None:
    """Name the question this turn asks, or None. The same name twice is the same question."""
    report = state.get("report")
    if report is not None and _field(report, "decision") == "clarify":
        reason = _field(report, "handoff_reason")
        places = state.get("places") or []
        if reason == "place_unclear" and places:
            return f"place:{_field(places[0], 'role')}"
        if reason == "place_not_found":
            found = {_field(place, "role") for place in places}
            return f"place:{next((role for role in ('origin', 'destination') if role not in found), 'stop')}"
        return str(reason)
    classification = state.get("classification")
    if classification is None and _rewrite_status(state) == "unclear":
        return "which_trip"
    if classification is not None and _field(classification, "route_action") == "clarify":
        return f"missing:{_field(classification, 'clarify_field')}"
    return None


_ESCALATE = RouteClassification(
    route_action="escalate",
    primary_mode="unspecified",
    needs_live_data=False,
    clarify_field="none",
    refusal_reason="none",
)


def _asked_enough(state: TripState) -> bool:
    """The app names its questions. A caller that only counts them keeps the old rule.

    Questions about another trip do not count against a new one.
    """
    if is_new_trip(state):
        return False
    asked = state.get("asked")
    if asked is None:
        return state.get("clarify_count", 0) >= 1
    return question_key(state) in asked or len(asked) >= _MAX_QUESTIONS


def _escalate_stale_clarify(state: TripState) -> tuple[TripState, dict]:
    """Turn a clarify into escalate when enough has been asked, before the writer sees the state.

    The classifier can ask, and so can the verifier when the map tools find no
    mode or no such place. Under P-08 the same question twice, or a fourth one,
    goes to a person in either case. A caller that only counts its questions,
    such as the evaluation, gets one follow-up (_asked_enough).

    The app keeps clarify_count between turns. The routing table calls this
    case "write, as escalate". The writer only picks a template from
    route_action, so the label change lives here, in the graph, rather than
    inside the writer's package.
    """
    classification = state.get("classification")
    if classification is None:
        if _rewrite_status(state) != "unclear" or not _asked_enough(state):
            return state, {}
        updated = _ESCALATE
        return {**state, "classification": updated}, {"classification": updated}  # type: ignore[return-value]
    report = state.get("report")
    verifier_asks = report is not None and _field(report, "decision") == "clarify"
    if _field(classification, "route_action") != "clarify" and not verifier_asks:
        return state, {}
    if not _asked_enough(state):
        return state, {}
    updated = _relabel(classification, "escalate", "none")
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
    if stage in _READS_REQUEST:
        call_state = _with_request(state)
    taking_up = stage == "extract" and bool(state.get("resumed"))
    checking_clarify = stage == "extract" and not taking_up and _asks_for_a_place(state["classification"])
    if checking_clarify:
        call_state = _as_trip(call_state)
    try:
        update = _take_up(state) if taking_up else fn(call_state)
        if stage == "extract" and isinstance(update, dict) and not taking_up:
            update = _mark_device(call_state, update)
    except Exception as exc:
        latency_ms = round((time.perf_counter() - started) * 1000, 3)
        return _abort(state, stage, "system_error", type(exc).__name__, latency_ms)
    if not isinstance(update, dict):
        latency_ms = round((time.perf_counter() - started) * 1000, 3)
        return _abort(state, stage, "system_error", TypeError.__name__, latency_ms)
    if stage == "verify":
        update = _cap_revision(state, update)
    if checking_clarify:
        update = _settle_clarify(state, call_state, update)
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
        "rewrite": _load("routechat.agents.rewriter", ("rewrite_node",)),
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
    """Compile the graph. Nodes without a rewriter run the message as it was written.

    Pass nodes to inject fakes in tests. Production code omits that argument
    and gets resolve_nodes(), which is the stubs or the real stage packages.
    """
    resolved = {"rewrite": lambda _state: {}, **(resolve_nodes() if nodes is None else nodes)}
    missing = [name for name in NODE_ORDER if name not in resolved]
    if missing:
        raise KeyError(f"graph is missing nodes: {', '.join(missing)}")

    graph = StateGraph(TripState)
    for stage in NODE_ORDER:
        graph.add_node(stage, _bind(stage, resolved[stage]))
    stop = {END: END}
    graph.add_conditional_edges(
        START,
        lambda state: "extract" if state.get("resumed") else "guard_in",
        {"guard_in": "guard_in", "extract": "extract"},
    )
    graph.add_conditional_edges(
        "guard_in", after_guard, {"rewrite": "rewrite", "write": "write", **stop}
    )
    graph.add_conditional_edges(
        "rewrite", after_rewrite, {"classify": "classify", "write": "write", **stop}
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


StepFn = Callable[[str, "dict | None"], None]


def _run(graph: Any, payload: dict, on_step: StepFn | None) -> dict:
    """Run the graph. on_step hears each stage twice: None when it starts, its update when it is done."""
    if on_step is None:
        return graph.invoke(payload)
    state: dict = {}
    for kind, event in graph.stream(payload, stream_mode=["tasks", "updates", "values"]):
        if kind == "values":
            state = event
        elif kind == "updates":
            for stage, update in event.items():
                on_step(stage, update or {})
        elif "input" in event:
            on_step(event["name"], None)
    return state


def invoke(
    payload: dict, nodes: Mapping[str, NodeFn] | None = None, on_step: StepFn | None = None
) -> dict:
    """Run one request. The app reads state['final'] and state.get('handoff').

    WP7's logger copies the stages' JSON lines into the log file and gets one
    request line here, after guard_out, with the end-to-end latency. A trip
    that waits for the device location gets its line when it is resumed.
    """
    install_stage_log()
    started = time.perf_counter()
    spent_ms = payload.get("spent_ms", 0.0)
    state = _run(
        build_graph(nodes), {key: value for key, value in payload.items() if key != "spent_ms"}, on_step
    )
    spent_ms += (time.perf_counter() - started) * 1000
    if wants_device(state) and state.get("final") is None:
        # The wait for the user to allow it is not the planner's time, so only its own is kept.
        return {**state, "spent_ms": spent_ms}
    log_request(state, spent_ms)
    return state


def resume(
    waiting: Mapping[str, Any],
    device: ResolvedPlace | None,
    nodes: Mapping[str, NodeFn] | None = None,
    on_step: StepFn | None = None,
) -> dict:
    """Finish a trip that waited for the device location. None means there is none, and the start is asked for."""
    # The time budget starts again: the wait for the browser must not spend it.
    payload = {key: value for key, value in waiting.items() if key != "deadline"}
    return invoke({**payload, "locatable": False, "resumed": True, "device": device}, nodes, on_step)
