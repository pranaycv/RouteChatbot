# Owner: Person A (integration lead), Team Rosedale, MMAI 894.
# Written for this project with AI assistance; no code copied from other sources.
"""Shared test setup: every test writes its log and handoff queue to a temp folder."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_logs(tmp_path, monkeypatch):
    """Keep test runs out of logs/events.jsonl and data/handoff_queue.jsonl."""
    monkeypatch.setenv("ROUTECHAT_LOG", str(tmp_path / "events.jsonl"))
    monkeypatch.setenv("ROUTECHAT_HANDOFF_QUEUE", str(tmp_path / "handoff_queue.jsonl"))
    for name in ("RUN_ID", "EVAL_DATASET", "EVAL_ITEM_ID"):
        monkeypatch.delenv(name, raising=False)
