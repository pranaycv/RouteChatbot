"""WP3 extractor tests: T-3-01 through T-3-10, plus the retry path and the code-side rules.

All model calls run with FIXTURES=replay against routechat/fixtures/llm. The fixtures are seeded by
scripts/llm_fixtures.py (synthetic until a key exists, then recorded live). The eight worked examples
come straight from the default prompt (prompts/extractor_v<N>.md), so the prompt is the single source of truth.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from routechat.agents import extractor
from routechat.agents.extractor import build_messages, extract, extract_node, postprocess, prompt_examples
from routechat.llm import LLMResult
from routechat.schemas import ConstraintSet, RouteClassification

DATA = Path(__file__).parent / "data"
EXTRA = {c["id"]: c for c in json.loads((DATA / "extractor_extra_cases.json").read_text(encoding="utf-8"))}
EXAMPLES = {e["id"]: e for e in prompt_examples()}
CODE_SET = {"missing_field", "too_many_stops"}


@pytest.fixture(autouse=True)
def replay(monkeypatch):
    monkeypatch.setenv("FIXTURES", "replay")


def _cls(case: dict) -> RouteClassification:
    return RouteClassification.model_validate(case["classification"])


def _run(case: dict) -> ConstraintSet:
    return extract(case["clean_text"], case["classification"], case["known"], request_id=case["id"])


def test_prompt_has_eight_worked_examples():
    assert len(EXAMPLES) == 8
    assert [e["title"] for e in prompt_examples()] == [
        "the running example",
        "a loop",
        "a compare",
        "a replan",
        "a vague request",
        "two trips in one message",
        "the origin comes late, and an afterthought is not a stop",
        "a loop through one place, with a wish that is not a stop",
    ]


@pytest.mark.parametrize("n", range(1, 9))
def test_T_3_01_to_06_examples_round_trip(n):
    """Each worked example comes back as the OUTPUT the prompt shows, with the two code-set fields recomputed."""
    case = EXAMPLES[f"example_{n}"]
    result = _run(case)
    shown = ConstraintSet.model_validate(case["model_output"])
    assert result.model_dump(exclude=CODE_SET) == shown.model_dump(exclude=CODE_SET)
    assert postprocess(shown, _cls(case)) == result


def test_T_3_07_missing_origin_gives_missing_field_origin():
    result = _run(EXAMPLES["example_5"])
    assert result.origin is None
    assert result.missing_field == "origin"
    assert any("my office" in a for a in result.ambiguities)


def test_T_3_08_four_required_stops_gives_too_many_stops():
    result = _run(EXTRA["four_required_stops"])
    assert len(result.stops) == 4
    assert result.too_many_stops is True
    assert result.missing_field is None


def test_T_3_09_replan_keeps_every_field_the_user_did_not_mention():
    case = EXAMPLES["example_4"]
    known = ConstraintSet.model_validate(case["known"])
    result = _run(case)
    assert result.stops == []  # the one change the user asked for
    for field in ("origin", "destination", "mode", "avoid", "prefer", "minimize_elevation", "check_closures", "loop", "required_accessibility"):
        assert getattr(result, field) == getattr(known, field), field


def test_T_3_10_no_invented_coordinates_or_hours():
    """No output string or number looks like a coordinate, and no clock time appears that the text did not imply."""
    coord = re.compile(r"-?\d{2,3}\.\d{3,}")
    clock = re.compile(r"\b\d{1,2}:\d{2}\b")
    for case in list(EXAMPLES.values()) + list(EXTRA.values()):
        result = _run(case)
        dumped = result.model_dump_json()
        assert not coord.search(dumped), case["id"]
        for t in clock.findall(dumped):
            hour = int(t.split(":")[0])
            text = case["clean_text"].lower()
            assert str(hour) in text or str(hour - 12) in text or str(hour + 12) in text, (case["id"], t)
        assert all(s.query for s in result.stops)


def test_postprocess_copies_mode_and_flags_loop_without_length():
    case = EXTRA["mode_from_classifier_and_loop_without_length"]
    result = _run(case)
    assert result.mode == "bike"  # classifier's primary_mode replaced unspecified
    assert result.loop is True and result.destination is None and result.missing_field is None
    assert any("loop length" in a for a in result.ambiguities)


def test_postprocess_flags_arrive_before_depart():
    result = _run(EXTRA["arrive_before_depart"])
    assert result.depart_at == "23:00" and result.arrive_by == "01:00"
    assert any("tomorrow" in a for a in result.ambiguities)


def test_postprocess_dedupes_lists_and_clips_compare_modes():
    shown = ConstraintSet.model_validate(EXAMPLES["example_3"]["model_output"])
    messy = shown.model_copy(update={"avoid": ["tolls", "tolls", "highways"], "prefer": ["scenic", "scenic"], "modes_to_compare": ["walk", "bike", "car"]})
    fixed = postprocess(messy, _cls(EXAMPLES["example_3"]))
    assert fixed.avoid == ["tolls", "highways"]
    assert fixed.prefer == ["scenic"]
    assert fixed.modes_to_compare == ["walk", "bike"]


def test_two_validation_failures_give_unparseable(monkeypatch):
    calls = []

    def bad_complete(messages, model, temperature=0.0, max_tokens=None, schema=None):
        calls.append(messages)
        return LLMResult('{"origin": 12, "nope": true}', 5, 5, model, "replay", 0.0, 0.0)

    monkeypatch.setattr(extractor, "complete", bad_complete)
    case = EXAMPLES["example_1"]
    result = _run(case)
    assert len(calls) == 2
    assert "failed validation" in calls[1][-1]["content"]
    assert result.missing_field == "unparseable"
    assert result.mode == "bike"


def test_extract_node_reads_guard_clean_text_and_returns_only_constraints():
    case = EXAMPLES["example_1"]
    state = {
        "request_id": "r_test",
        "utterance": "RAW TEXT WITH PII",
        "turn_index": 0,
        "known": None,
        "guard": {"decision": "allow", "clean_text": case["clean_text"], "masked": [], "reason": None},
        "classification": case["classification"],
    }
    out = extract_node(state)
    assert set(out) == {"constraints"}
    assert isinstance(out["constraints"], ConstraintSet)
    assert out["constraints"].origin == "King Station"


def test_messages_layout_matches_prompt_examples():
    case = EXAMPLES["example_1"]
    messages = build_messages(case["clean_text"], _cls(case), None)
    assert messages[0]["role"] == "system" and "Worked examples" in messages[0]["content"]
    assert messages[1]["content"].startswith("clean_text: Plan a bike route")
    assert "\nknown: null" in messages[1]["content"]


def test_log_line_matches_wp10_schema(caplog):
    from eval.log_schema import validate_line

    caplog.set_level("INFO", logger="routechat.extract")
    _run(EXAMPLES["example_2"])
    lines = [json.loads(r.message) for r in caplog.records if r.name == "routechat.extract"]
    assert lines, "no log line written"
    assert validate_line(lines[-1]) == []
    assert lines[-1]["decision"] == "complete" and lines[-1]["prompt_version"] == extractor.prompt_version()
