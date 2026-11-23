# Owner: Person A (WP7 output guard), Team Rosedale, MMAI 894.
# Only Person A edits this file. Other packages may import it but should not change it;
# AI coding tools must not edit this file unless they are working on WP7.
# Written for this project with AI assistance; no code copied from other sources.
"""WP7 output guard and log tests: T-7-01 through T-7-07."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import orjson
import pytest

from routechat.agents.templates import ESCALATE
from routechat.guards.output_guard import _mention_sentence, output_guard
from routechat.logging import RETENTION_DAYS, log_event, prune_expired
from routechat.schemas import Check, ReplyDraft, SponsoredSuggestion
from routechat.stubs import (
    RUNNING_REPLY,
    running_candidate,
    running_places,
    running_report,
)


@pytest.fixture
def log_file(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    monkeypatch.setenv("ROUTECHAT_LOG", str(path))
    return path


def _state(message: str, *, mentioned=None, report=None, candidate=None):
    return {
        "request_id": "r_test",
        "draft": ReplyDraft(
            message=message,
            mentioned_ids=["closures"] if mentioned is None else mentioned,
            kind="llm",
        ),
        "report": running_report() if report is None else report,
        "candidates": [running_candidate() if candidate is None else candidate],
        "places": running_places(),
    }


def test_t_7_01_missing_disclosure_uses_the_fallback(log_file):
    result = output_guard(_state(RUNNING_REPLY, mentioned=[]))
    final = result["final"]
    assert final.used_fallback_template is True
    assert final.approved is False
    assert "closure" in final.text.lower()
    assert "fallback:disclosure" in log_file.read_text(encoding="utf-8")


def test_t_7_02_hidden_sponsor_uses_the_fallback(log_file):
    sponsored = SponsoredSuggestion(
        name="Dark Horse Espresso",
        category="cafe",
        lon=-79.39,
        lat=43.65,
        rating=4.6,
        distance_m=180,
    )
    report = running_report().model_copy(update={"sponsored": sponsored})
    final = output_guard(_state(RUNNING_REPLY, report=report))["final"]
    assert final.used_fallback_template is True
    assert "Sponsored: Dark Horse Espresso" in final.text
    assert "fallback:sponsorship" in log_file.read_text(encoding="utf-8")


def test_t_7_03_a_promise_uses_the_fallback(log_file):
    final = output_guard(_state(RUNNING_REPLY + " This route is guaranteed."))["final"]
    assert final.used_fallback_template is True
    assert "guaranteed" not in final.text.lower()
    assert "fallback:promises" in log_file.read_text(encoding="utf-8")


def test_t_7_04_an_invented_distance_uses_the_fallback(log_file):
    invented = RUNNING_REPLY.replace("4.2 km", "9 km")
    final = output_guard(_state(invented))["final"]
    assert final.used_fallback_template is True
    assert "4.2 km" in final.text
    assert "9 km" not in final.text
    assert "fallback:numbers" in log_file.read_text(encoding="utf-8")


def test_t_7_05_personal_data_uses_the_fallback(log_file):
    leaked = RUNNING_REPLY + " Email ada@example.com."
    final = output_guard(_state(leaked))["final"]
    assert final.used_fallback_template is True
    assert "ada@example.com" not in final.text
    saved = log_file.read_text(encoding="utf-8")
    # The stage line records which check failed and never the reply text.
    assert "ada@example.com" not in saved
    assert "Canadian Tire" not in saved
    assert "fallback:personal_data" in saved


def test_t_7_06_a_correct_draft_passes_unchanged(log_file):
    final = output_guard(_state(RUNNING_REPLY))["final"]
    assert final.approved is True
    assert final.used_fallback_template is False
    assert final.text == RUNNING_REPLY
    assert final.route_geojson is not None
    assert final.stops[0].name == "Canadian Tire (Yonge)"
    assert '"decision":"ok"' in log_file.read_text(encoding="utf-8")


def test_t_7_07_a_log_line_masks_contact_details_and_addresses(log_file):
    line = log_event(
        {
            "request_id": "r_0193",
            "stage": "app",
            "text": "Meet at 123 King Street, email ada@example.com or call 416-555-0199. Pin -79.377812, 43.649045.",
            "ts": datetime.now(timezone.utc).isoformat(),
        }
    )
    assert "ada@example.com" not in line
    assert "416-555-0199" not in line
    assert "123 King Street" not in line
    assert "[EMAIL]" in line
    assert "[PHONE]" in line
    assert "[ADDRESS]" in line
    assert "-79.377812" not in line
    assert "-79.378" in line

    old = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS + 1)).isoformat()
    log_file.write_text(
        orjson.dumps({"ts": old, "text": "stale"}).decode() + "\n" + line + "\n",
        encoding="utf-8",
    )
    prune_expired(log_file)
    kept = log_file.read_text(encoding="utf-8")
    assert "stale" not in kept
    assert "[ADDRESS]" in kept


# --- Numbers from the checks and the compared route; what the fallback says ---


def _handoff_report(*checks: Check):
    return running_report().model_copy(
        update={
            "decision": "handoff",
            "handoff_reason": "requirements_not_met",
            "checks": list(checks),
            "must_mention": [check.id for check in checks if check.status != "met"],
        }
    )


_LOOP = Check(
    id="loop_distance", status="unmet", hard=True, evidence="4220.55 m; allowed range 4500 to 5500 m."
)
_HIGHWAY = Check(
    id="avoid:highways", status="unmet", hard=True, evidence="25342.1 m on motorway or trunk roads (maximum 200 m)."
)


def test_a_reply_may_use_the_numbers_in_the_evidence(log_file):
    message = "The loop is 4.22 km, outside the 4.5 to 5.5 km you need."
    state = _state(message, mentioned=["loop_distance"], report=_handoff_report(_LOOP))
    assert output_guard(state)["final"].text == message
    invented = message.replace("4.22 km", "4.9 km")
    state = _state(invented, mentioned=["loop_distance"], report=_handoff_report(_LOOP))
    assert output_guard(state)["final"].used_fallback_template is True


def test_a_compare_reply_may_use_the_other_route_and_nothing_else(log_file):
    other = running_candidate().model_copy(
        update={"id": "gh_walk_1", "mode": "walk", "distance_m": 3100.0, "duration_s": 2220.0, "duration_traffic_s": None}
    )
    report = running_report().model_copy(update={"must_mention": []})
    message = "The bike route is 4.2 km, about 18 minutes. Walking is 3.1 km, about 37 minutes."
    state = _state(message, mentioned=[], report=report)
    state["candidates"].append(other)
    assert output_guard(state)["final"].text == message
    # The same reply is refused when nothing was compared, or when the other route failed.
    assert output_guard(_state(message, mentioned=[], report=report))["final"].used_fallback_template is True
    state["candidates"][1] = other.model_copy(update={"error": "no_route"})
    assert output_guard(state)["final"].used_fallback_template is True


def test_the_fallback_says_what_failed_and_that_a_person_has_the_trip(log_file):
    closures = Check(id="closures", status="unverifiable", hard=True, evidence="Closure data is unavailable.")
    text = output_guard(
        _state("9 km", mentioned=[], report=_handoff_report(_HIGHWAY, _LOOP, closures))
    )["final"].text
    assert text.startswith(ESCALATE)
    assert "The closest route I found is 4.2 km" in text
    assert "It uses highways." in text
    assert "loop distance you asked for" in text
    assert "I couldn't check for closures." in text
    assert "avoid:" not in text and "_" not in text


@pytest.mark.parametrize("status", ["unmet", "unverifiable"])
@pytest.mark.parametrize(
    "check_id",
    [
        "stop:canadian_tire", "hours:canadian_tire", "avoid:highways", "avoid:tolls", "avoid:ferries",
        "avoid:unpaved", "prefer:bike_lanes", "prefer:quiet_roads", "flat", "max_distance", "max_duration",
        "loop_distance", "arrive_by", "closures", "step_free",
    ],
)
def test_a_fallback_sentence_never_shows_a_raw_check_id(check_id: str, status: str):
    sentence = _mention_sentence(check_id, status)
    assert ":" not in sentence.removeprefix("It doesn't meet this request:")
    assert "_" not in sentence
    assert ("couldn't" in sentence) == (status == "unverifiable")
