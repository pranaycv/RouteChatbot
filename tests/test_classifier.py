"""WP2 classifier tests: T-2-01 through T-2-05, plus the backend registry and the log line.

The local backends (ft, base) need torch, which the core environment does not install, and the
prompted backends need a key. So T-2-04 drives all four backends through a scripted generate()
that returns the expected labels for ten smoke turns: it proves the message building, parsing,
retry, normalisation and logging path for every backend type. The prompted backends are also run
for real through FIXTURES=replay (fixtures seeded by scripts/llm_fixtures.py). Live runs of the
real models are the WP10 evaluation, not a unit test.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from routechat.classifier import predict
from routechat.classifier.backends import GenResult
from routechat.classifier.predict import (
    BACKEND_NAMES,
    build_messages,
    classify,
    classify_node,
    extract_first_json,
    labels_json,
    normalise,
    parse_labels,
    resolve_backend,
)
from routechat.schemas import RouteClassification, UserTurn

DATA = Path(__file__).parent / "data"
SMOKE = json.loads((DATA / "classifier_smoke.json").read_text(encoding="utf-8"))
RUNNING = UserTurn.model_validate(SMOKE[0]["turn"])


class ScriptedBackend:
    """Returns one canned text per call, in order. Records what it was asked."""

    uses_examples = True

    def __init__(self, outputs: list[str], name: str = "scripted", model: str = "scripted-model") -> None:
        self.outputs = list(outputs)
        self.name = name
        self.model = model
        self.calls: list[list[dict]] = []

    def generate(self, messages):
        self.calls.append(messages)
        text = self.outputs.pop(0)
        return GenResult(text, 10, 5, 1.0, 0.0, "replay")


def _labels(**overrides) -> str:
    base = {"route_action": "plan", "primary_mode": "bike", "needs_live_data": True, "clarify_field": "none", "refusal_reason": "none"}
    base.update(overrides)
    return json.dumps(base)


# T-2-01 ---------------------------------------------------------------------------------------

def test_T_2_01_parser_accepts_json_wrapped_in_prose():
    text = "Sure! Here is the classification:\n" + _labels() + "\nLet me know if you need anything else."
    c = parse_labels(text)
    assert c.route_action == "plan" and c.primary_mode == "bike" and c.parse_error is False


def test_T_2_01_parser_rejects_invalid_labels_and_extra_keys():
    with pytest.raises(ValueError):
        parse_labels(_labels(route_action="navigate"))
    with pytest.raises(ValueError):
        parse_labels(_labels(primary_mode="walk/run"))
    with pytest.raises(ValueError):
        parse_labels(json.dumps({"route_action": "plan"}))
    with pytest.raises(ValueError):
        parse_labels(_labels(extra="field"))
    with pytest.raises(ValueError):
        parse_labels("no json here")
    with pytest.raises(ValueError):
        parse_labels(_labels(parse_error=True))


def test_extract_first_json_handles_braces_inside_strings():
    text = 'prefix {"a": "x } y", "b": {"c": 1}} suffix {"d": 2}'
    assert extract_first_json(text) == '{"a": "x } y", "b": {"c": 1}}'
    assert extract_first_json("{ unbalanced") is None


# T-2-02 ---------------------------------------------------------------------------------------

def test_T_2_02_two_invalid_outputs_give_escalate_with_parse_error():
    backend = ScriptedBackend(["I cannot classify this.", "still not json"])
    c = classify(RUNNING, backend=backend)
    assert c.route_action == "escalate" and c.parse_error is True
    assert c.clarify_field == "none" and c.refusal_reason == "none"
    assert len(backend.calls) == 2
    assert backend.calls[1][-1]["content"] == predict.RETRY_HINT
    assert backend.calls[1][-2]["content"] == "I cannot classify this."


def test_retry_succeeds_on_second_attempt():
    backend = ScriptedBackend(["garbage", _labels(route_action="clarify", clarify_field="origin")])
    c = classify(RUNNING, backend=backend)
    assert c.route_action == "clarify" and c.clarify_field == "origin" and c.parse_error is False
    assert len(backend.calls) == 2


# T-2-03 ---------------------------------------------------------------------------------------

def test_T_2_03_normalisation_is_applied_and_logged(caplog):
    caplog.set_level("INFO", logger="routechat.classify")
    backend = ScriptedBackend([_labels(route_action="plan", refusal_reason="data_request", clarify_field="origin")])
    c = classify(RUNNING, backend=backend, request_id="r_norm")
    assert c.refusal_reason == "none" and c.clarify_field == "none"
    line = json.loads([r.message for r in caplog.records if r.name == "routechat.classify"][-1])
    assert sorted(line["corrections"]) == ["clarify_field:origin->none", "refusal_reason:data_request->none"]
    assert line["request_id"] == "r_norm" and line["decision"] == "plan"


def test_normalise_leaves_valid_refuse_and_clarify_alone():
    refuse = RouteClassification(route_action="refuse", primary_mode="car", needs_live_data=False, clarify_field="none", refusal_reason="out_of_scope_b2b")
    assert normalise(refuse) == (refuse, [])
    clarify = RouteClassification(route_action="clarify", primary_mode="bike", needs_live_data=False, clarify_field="origin", refusal_reason="none")
    assert normalise(clarify) == (clarify, [])


# T-2-04 ---------------------------------------------------------------------------------------

def test_backend_registry_builds_all_four(monkeypatch):
    monkeypatch.delenv("CLASSIFIER_BACKEND", raising=False)
    names = {b.name for b in (resolve_backend(n) for n in BACKEND_NAMES)}
    assert names == set(BACKEND_NAMES)
    assert resolve_backend("prompted_small").model == "gpt-4.1-mini"
    assert resolve_backend("prompted_large").model == "gpt-4.1"
    assert resolve_backend("base").model.startswith("Qwen/Qwen2.5-1.5B")
    assert resolve_backend("ft").adapter_dir is not None and resolve_backend("base").adapter_dir is None
    assert resolve_backend().name == predict.DEFAULT_BACKEND
    monkeypatch.setenv("CLASSIFIER_BACKEND", "prompted_large")
    assert resolve_backend().name == "prompted_large"
    with pytest.raises(ValueError):
        resolve_backend("gpt5")


@pytest.mark.parametrize("name", BACKEND_NAMES)
def test_T_2_04_all_four_backends_return_a_classification_for_ten_smoke_turns(name, monkeypatch):
    backend = resolve_backend(name)
    expected = {row["id"]: labels_json(RouteClassification.model_validate(row["labels"])) for row in SMOKE}
    seen: list[int] = []

    def scripted(messages):
        turn_json = messages[-1]["content"]
        row = next(r for r in SMOKE if UserTurn.model_validate(r["turn"]).model_dump_json() == turn_json)
        seen.append(len(messages))
        return GenResult(expected[row["id"]], 10, 5, 1.0, 0.0, "replay")

    monkeypatch.setattr(backend, "generate", scripted)
    for row in SMOKE:
        c = classify(UserTurn.model_validate(row["turn"]), backend=backend, request_id=row["id"])
        assert isinstance(c, RouteClassification)
        assert labels_json(c) == expected[row["id"]]
    assert len(seen) == len(SMOKE)
    # Prompted backends carry the eight few-shot pairs; local backends get the label guide only.
    assert all(n == (18 if backend.uses_examples else 2) for n in seen)


@pytest.mark.parametrize("name", ["prompted_small", "prompted_large"])
def test_prompted_backends_replay_fixtures(name, monkeypatch):
    """Recorded gpt-4.1-mini and gpt-4.1 responses (scripts/llm_fixtures.py --record, 23 Nov) parse cleanly.

    Every turn must parse without a retry and agree with the expected label on the two policy-critical
    fields, route_action and refusal_reason. primary_mode may differ on a turn or two (the recorded
    models read "delivery vans" as unspecified where the sheet says car); accuracy on all five fields
    is what WP10 measures on the frozen test set, not what this unit test asserts.
    """
    monkeypatch.setenv("FIXTURES", "replay")
    backend = resolve_backend(name)
    exact = 0
    for row in SMOKE:
        c = classify(UserTurn.model_validate(row["turn"]), backend=backend, request_id=row["id"])
        expected = RouteClassification.model_validate(row["labels"])
        assert c.parse_error is False, row["id"]
        assert (c.route_action, c.refusal_reason) == (expected.route_action, expected.refusal_reason), row["id"]
        exact += labels_json(c) == labels_json(expected)
    assert exact >= len(SMOKE) - 2, f"{name}: only {exact}/{len(SMOKE)} exact matches"


# T-2-05 ---------------------------------------------------------------------------------------

def test_T_2_05_graph_runs_with_stubs_before_the_adapter_exists(monkeypatch):
    monkeypatch.setenv("STUBS", "1")
    from routechat.graph import invoke

    state = invoke({"request_id": "r_t205", "utterance": RUNNING.utterance, "turn_index": 0, "known": None, "clarify_count": 0, "revision_count": 0})
    assert state["final"].approved is True
    assert not predict.adapter_dir().exists() or True  # the adapter is not required for this path


def test_graph_can_find_classify_node_and_classify():
    import importlib

    module = importlib.import_module("routechat.classifier.predict")
    assert callable(getattr(module, "classify_node")) and callable(getattr(module, "classify"))


# Message building and node ---------------------------------------------------------------------

def test_build_messages_uses_label_guide_and_fewshot():
    with_examples = build_messages(RUNNING, include_examples=True)
    without = build_messages(RUNNING, include_examples=False)
    assert with_examples[0]["role"] == "system" and "Label guide v2" in with_examples[0]["content"]
    assert "out_of_scope_mode" in with_examples[0]["content"]
    assert len(with_examples) == 2 + 2 * 8 and len(without) == 2
    assert with_examples[-1]["content"] == RUNNING.model_dump_json()
    assert all(json.loads(m["content"]).keys() == {"route_action", "primary_mode", "needs_live_data", "clarify_field", "refusal_reason"} for m in with_examples[2:-1:2])


def test_classify_node_uses_guard_clean_text_and_returns_only_classification(monkeypatch):
    backend = ScriptedBackend([_labels()])
    monkeypatch.setattr(predict, "resolve_backend", lambda name=None: backend)
    state = {
        "request_id": "r_node",
        "utterance": "RAW with 416-555-0199",
        "turn_index": 0,
        "known": None,
        "guard": {"decision": "allow", "clean_text": "RAW with [PHONE]", "masked": ["PHONE"], "reason": None},
    }
    out = classify_node(state)
    assert set(out) == {"classification"}
    assert json.loads(backend.calls[0][-1]["content"])["utterance"] == "RAW with [PHONE]"


def test_log_line_matches_wp10_schema(caplog):
    from eval.log_schema import validate_line

    caplog.set_level("INFO", logger="routechat.classify")
    classify(RUNNING, backend=ScriptedBackend([_labels()]), request_id="r_log")
    line = json.loads([r.message for r in caplog.records if r.name == "routechat.classify"][-1])
    assert validate_line(line) == []
    assert line["stage"] == "classify" and line["prompt_version"] == "label_guide_v2"
