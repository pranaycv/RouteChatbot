"""Retry profiles in routechat.llm (Person C). The live app has a 30 s budget per turn that the graph
checks only between stages, so one model call must bound its own worst case; recorded evaluation runs
keep the long policy so a one-minute rate limit does not lose rows. No network: the retry loop is driven
with a fake sleep and a function that raises.
"""
from __future__ import annotations

import pytest

from routechat import llm


class RateLimitError(Exception):
    """Named like the SDK's class: _is_transient matches on the type name."""


def _always_429(calls: list[int]):
    def fn():
        calls.append(1)
        raise RateLimitError("429 from a fake provider")
    return fn


def test_profile_is_live_unless_recording_or_overridden(monkeypatch):
    monkeypatch.delenv("LLM_RETRY_PROFILE", raising=False)
    monkeypatch.delenv("FIXTURES", raising=False)
    assert llm.retry_profile() == "live"
    monkeypatch.setenv("FIXTURES", "replay")
    assert llm.retry_profile() == "live"
    monkeypatch.setenv("FIXTURES", "record")
    assert llm.retry_profile() == "eval"
    monkeypatch.setenv("FIXTURES", "refresh")
    assert llm.retry_profile() == "eval"
    monkeypatch.setenv("LLM_RETRY_PROFILE", "live")
    assert llm.retry_profile() == "live"
    monkeypatch.delenv("FIXTURES")
    monkeypatch.setenv("LLM_RETRY_PROFILE", "eval")
    assert llm.retry_profile() == "eval"


def test_live_worst_case_stays_well_under_the_turn_budget(monkeypatch):
    monkeypatch.delenv("LLM_TIMEOUT_S", raising=False)
    assert llm.worst_case_seconds("live") <= 22.0  # 3 s window + 3.5 s longest wait + 15 s timeout
    assert llm.worst_case_seconds("eval") > 60.0  # the long policy is still there for recorded runs
    monkeypatch.setenv("LLM_TIMEOUT_S", "8")
    assert llm.retry_policy("live")["timeout_s"] == 8.0
    assert llm.worst_case_seconds("live") <= 15.0


def test_live_gives_up_after_three_fast_failures_with_short_waits():
    sleeps: list[float] = []
    calls: list[int] = []
    with pytest.raises(RateLimitError):
        llm._retrying("live", sleep=sleeps.append)(_always_429(calls))
    assert len(calls) == 3
    assert len(sleeps) == 2 and sum(sleeps) <= 7.0 and all(0.0 <= w <= 3.5 for w in sleeps)


def test_eval_keeps_six_attempts_with_long_waits():
    sleeps: list[float] = []
    calls: list[int] = []
    with pytest.raises(RateLimitError):
        llm._retrying("eval", sleep=sleeps.append)(_always_429(calls))
    assert len(calls) == 6 and len(sleeps) == 5
    assert all(w <= 61.0 for w in sleeps)


def test_non_transient_errors_are_not_retried():
    calls: list[int] = []

    def bad_request():
        calls.append(1)
        raise ValueError("schema rejected")

    with pytest.raises(ValueError):
        llm._retrying("live", sleep=lambda s: None)(bad_request)
    assert len(calls) == 1


def test_success_after_one_transient_error_returns_the_value():
    calls: list[int] = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RateLimitError("429 once")
        return "ok"

    assert llm._retrying("live", sleep=lambda s: None)(flaky) == "ok"
    assert len(calls) == 2
