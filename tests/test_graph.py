"""WP0 graph tests: T-0-01 through T-0-05."""

import logging
import subprocess
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from routechat.config import get_settings
from routechat.graph import (
    NODE_ORDER,
    SYSTEM_ERROR_TEXT,
    after_classify,
    after_extract,
    after_guard,
    after_verify,
    build_graph,
    invoke,
)
from routechat.schemas import (
    ConstraintSet,
    FinalAnswer,
    GuardResult,
    ReplyDraft,
    RouteClassification,
    Stop,
    VerificationReport,
)
from routechat.stubs import RUNNING_REPLY, RUNNING_UTTERANCE, running_constraints, stub_nodes

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = {
    "request_id": "r_0193",
    "utterance": RUNNING_UTTERANCE,
    "turn_index": 0,
    "known": None,
    "clarify_count": 0,
    "revision_count": 0,
}


def _guard(decision: str) -> dict:
    return {"guard": GuardResult(decision=decision, clean_text="hello", masked=[], reason=None)}


def _classification(action: str) -> RouteClassification:
    return RouteClassification(
        route_action=action,
        primary_mode="bike",
        needs_live_data=False,
        clarify_field="none",
        refusal_reason="none",
    )


def _constraints(**overrides) -> ConstraintSet:
    data = running_constraints().model_dump()
    data.update(overrides)
    return ConstraintSet.model_validate(data)


def _report(decision: str) -> VerificationReport:
    return VerificationReport(
        decision=decision,
        chosen_route="gh_bike_1",
        checks=[],
        must_mention=[],
        sponsored=None,
        handoff_reason=None,
    )


@pytest.mark.parametrize(
    ("router", "state", "expected"),
    [
        (after_guard, {**_guard("allow")}, "classify"),
        (after_guard, {**_guard("clarify")}, "write"),
        (after_guard, {**_guard("refuse")}, "write"),
        (after_guard, {**_guard("fixed_reply")}, "write"),
        (after_classify, {"classification": _classification("plan"), "clarify_count": 0}, "extract"),
        (after_classify, {"classification": _classification("replan"), "clarify_count": 0}, "extract"),
        (after_classify, {"classification": _classification("compare_modes"), "clarify_count": 0}, "extract"),
        (after_classify, {"classification": _classification("lookup_only"), "clarify_count": 0}, "extract"),
        (after_classify, {"classification": _classification("clarify"), "clarify_count": 0}, "write"),
        (after_classify, {"classification": _classification("escalate"), "clarify_count": 0}, "write"),
        (after_classify, {"classification": _classification("refuse"), "clarify_count": 0}, "write"),
        (after_classify, {"classification": _classification("clarify"), "clarify_count": 1}, "write"),
        (after_extract, {"constraints": _constraints()}, "maps"),
        (after_extract, {"constraints": _constraints(missing_field="origin")}, "write"),
        (after_extract, {"constraints": _constraints(too_many_stops=True)}, "write"),
        (after_verify, {"report": _report("revise"), "revision_count": 1}, "maps"),
        (after_verify, {"report": _report("accept"), "revision_count": 0}, "write"),
        (after_verify, {"report": _report("handoff"), "revision_count": 1}, "write"),
        (after_verify, {"report": _report("revise"), "revision_count": 2}, "write"),
    ],
)
def test_t_0_02_routing_table(router, state, expected):
    assert router(state) == expected


def test_t_0_01_stubs_reach_guard_out(monkeypatch):
    monkeypatch.setenv("STUBS", "1")
    app = build_graph()
    seen: list[str] = []
    for chunk in app.stream(PAYLOAD, stream_mode="updates"):
        seen.extend(chunk)
    assert "guard_out" in seen
    state = app.invoke(PAYLOAD)
    assert state["final"].approved is True
    assert state["final"].text == RUNNING_REPLY
    assert state.get("handoff") is None


def test_t_0_03_revise_loop_happens_once():
    visits: list[str] = []
    nodes = stub_nodes()

    def maps(_state):
        visits.append("maps")
        return {"places": [], "candidates": []}

    def verify(_state):
        visits.append("verify")
        return {"report": _report("revise"), "revision_count": 1}

    nodes["maps"] = maps
    nodes["verify"] = verify
    state = invoke(PAYLOAD, nodes)
    # The first maps call is the normal path. The second is the single revise.
    assert visits == ["maps", "verify", "maps", "verify"]
    assert state["final"].approved is True


def test_clarify_after_one_followup_is_escalate():
    seen: dict[str, str] = {}
    nodes = stub_nodes()

    def classify(_state):
        return {"classification": _classification("clarify")}

    def write(state):
        seen["action"] = state["classification"].route_action
        return {"draft": ReplyDraft(message="passed to support", mentioned_ids=[], kind="template")}

    nodes["classify"] = classify
    nodes["write"] = write
    state = invoke({**PAYLOAD, "clarify_count": 1}, nodes)
    assert seen["action"] == "escalate"
    assert state["classification"].route_action == "escalate"
    assert "extract" not in seen


@pytest.mark.parametrize("stage", NODE_ORDER)
def test_t_0_04_exception_yields_handoff(stage, caplog):
    caplog.set_level(logging.ERROR, logger="routechat.graph")
    utterance = "SECRET-UTTERANCE 42 Linden Ave"
    nodes = stub_nodes()

    def boom(boom_state):
        raise RuntimeError(f"injected {boom_state['utterance']}")

    nodes[stage] = boom
    state = invoke({**PAYLOAD, "utterance": utterance}, nodes)
    assert state["handoff"].reason == "system_error"
    assert state["handoff"].redacted_request == ""
    assert state["final"].text == SYSTEM_ERROR_TEXT
    assert utterance not in caplog.text
    assert "injected" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_timeout_hands_off_instead_of_crashing():
    state = invoke({**PAYLOAD, "deadline": time.monotonic() - 1}, stub_nodes())
    assert state["handoff"].reason == "timeout"
    assert state["final"].text == SYSTEM_ERROR_TEXT
    assert state["final"].used_fallback_template is True


def test_timeout_in_a_revise_loop_stops_before_maps():
    visits: list[str] = []
    nodes = stub_nodes()

    def verify(_state):
        visits.append("verify")
        return {
            "report": _report("revise"),
            "revision_count": 1,
            "deadline": time.monotonic() - 1,
        }

    def maps(_state):
        visits.append("maps")
        return {"places": [], "candidates": []}

    nodes["verify"] = verify
    nodes["maps"] = maps
    state = invoke(PAYLOAD, nodes)
    # The first maps call is the normal path. The revise is not started: the
    # deadline is already spent, so the maps function does not run again.
    assert visits == ["maps", "verify"]
    assert state["handoff"].reason == "timeout"


def _repository_files() -> list[Path]:
    """Files git tracks or would add. A git-ignored file, such as .env, cannot be committed by accident.

    A tracked file is listed even if it also matches .gitignore.
    """
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")
    paths = [ROOT / name for name in listed.split("\0") if name]
    assert paths, "git listed no files"
    return [path for path in paths if path.is_file()]


def test_t_0_05_repository_has_no_key_material():
    # Built by concatenation so this test file does not itself contain the markers it scans for.
    markers = ("s" + "k-", "p" + "k" + ".")
    offenders: list[str] = []
    for path in _repository_files():
        if path.name == ".env.example":
            continue
        text = path.read_bytes().decode("utf-8", errors="ignore")
        for marker in markers:
            if marker in text:
                offenders.append(f"{path.relative_to(ROOT)} contains {marker}")
    assert offenders == []


def test_schemas_reject_unknown_fields():
    with pytest.raises(ValidationError):
        GuardResult.model_validate(
            {"decision": "allow", "clean_text": "hi", "masked": [], "reason": None, "unexpected": True}
        )


def test_config_loads_named_thresholds():
    settings = get_settings()
    assert settings.graph.request_budget_s == 30
    assert settings.verifier.bike_network_min_share == 0.60
    assert settings.maps.http_timeout_s == 8
    assert settings.trip.max_required_stops == 3


def test_running_constraints_match_the_handbook_example():
    constraints = running_constraints()
    assert constraints.origin == "King Station, Toronto"
    assert constraints.stops == [Stop(query="Canadian Tire", category=None, required=True, order=None)]
    assert constraints.avoid == ["highways"]
    assert constraints.prefer == ["bike_lanes"]
    assert constraints.minimize_elevation is True
    assert constraints.check_closures is True
    assert constraints.missing_field is None
    assert isinstance(constraints, ConstraintSet)
    assert isinstance(FinalAnswer.model_validate(running_final_dump()), FinalAnswer)


def running_final_dump():
    from routechat.stubs import running_final

    return running_final().model_dump()
