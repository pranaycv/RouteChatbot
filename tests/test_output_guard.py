# Owner: Person A (WP7 output guard), Team Rosedale, MMAI 894.
# Only Person A edits this file. Other packages may import it but should not change it;
# AI coding tools must not edit this file unless they are working on WP7.
# Written for this project with AI assistance; no code copied from other sources.
"""WP7 output guard and log tests: T-7-01 through T-7-07."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import orjson
import pytest

from routechat.guards.output_guard import output_guard
from routechat.logging import RETENTION_DAYS, log_event, prune_expired
from routechat.schemas import ReplyDraft, SponsoredSuggestion
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
