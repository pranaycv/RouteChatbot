# Owner: Person A (WP7 logging), Team Rosedale, MMAI 894.
# Only Person A edits this file. Other packages may import it but should not change it;
# AI coding tools must not edit this file unless they are working on WP7.
# Written for this project with AI assistance; no code copied from other sources.
"""One JSON line per event. Logs are masked, and lines older than 30 days are dropped.

Policy P-06: location data is for the trip only. Street addresses become
[ADDRESS], WP1 masks email, card, phone and keys, and coordinates keep three
decimals. The handoff queue is the support tab's source.

Two kinds of line go to the file, as agreed with WP10 (eval/log_schema.py):
- a stage line per stage per request. Stages call stage_line(), which sends
  the JSON through the "routechat" logger; install_stage_log() copies every
  such line into the file. Stages that already log JSON stage lines on that
  logger (routechat.llm.log_stage) are picked up the same way.
- a request line per request (stage = "request"), written by graph.invoke
  through log_request().
RUN_ID, EVAL_DATASET and EVAL_ITEM_ID come from the environment, so an
evaluation run is stamped without the graph knowing about evaluation.
App traffic gets dataset "live" and an empty item id.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import orjson

from routechat.guards.input_guard import _mask_personal_data

# P-06 sets deletion after 30 days, for log lines and support tickets. This is that decision, not a tuning knob.
RETENTION_DAYS = 30
# Several visitors share one process. A ticket added while the queue is pruned must not be lost.
_queue_lock = threading.Lock()

_ROOT = Path(__file__).resolve().parents[1]
_STREET = r"(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Lane|Ln|Way|Court|Ct)"
_ADDRESS_RE = re.compile(rf"\b\d{{1,6}}\s+(?:[A-Za-z][A-Za-z.'-]*\s+){{0,3}}{_STREET}\b\.?", re.IGNORECASE)
# A check id is the stop with underscores, such as stop:120_adelaide_street.
_ADDRESS_ID_RE = re.compile(rf"(?<![A-Za-z0-9_])\d{{1,6}}_(?:[a-z]+_){{0,3}}{_STREET}(?![A-Za-z0-9])", re.IGNORECASE)
# Four or more decimals is precise enough to point at a building. Three is about 100 m.
_COORD_RE = re.compile(r"-?\d{1,3}\.\d{4,}")


def log_path() -> Path:
    override = os.environ.get("ROUTECHAT_LOG")
    return Path(override) if override else _ROOT / "logs" / "events.jsonl"


def handoff_path() -> Path:
    override = os.environ.get("ROUTECHAT_HANDOFF_QUEUE")
    return Path(override) if override else _ROOT / "data" / "handoff_queue.jsonl"


def mask_for_log(text: str) -> str:
    """Mask personal data, street addresses, and precise coordinates."""
    masked, _types = _mask_personal_data(text)
    masked = _ADDRESS_ID_RE.sub("[ADDRESS]", _ADDRESS_RE.sub("[ADDRESS]", masked))

    def _round_coord(match: re.Match[str]) -> str:
        return f"{float(match.group(0)):.3f}"

    return _COORD_RE.sub(_round_coord, masked)


def _mask_value(value: Any) -> Any:
    """Mask every text inside a record. A ticket holds the trip, and its origin can be a street address."""
    if isinstance(value, str):
        return mask_for_log(value)
    if isinstance(value, dict):
        return {key: _mask_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_mask_value(item) for item in value]
    return value


def _clean_record(record: dict[str, Any]) -> dict[str, Any]:
    # The timestamp's fractional seconds would look like a coordinate to the mask.
    clean = {key: value if key == "ts" else _mask_value(value) for key, value in record.items()}
    clean.setdefault("ts", datetime.now(timezone.utc).isoformat())
    run_id = os.environ.get("RUN_ID")
    if run_id:
        clean.setdefault("run_id", run_id)
    return clean


def log_event(record: dict[str, Any]) -> str:
    """Append one JSON object. Returns the masked line that was written."""
    line = orjson.dumps(_clean_record(record)).decode("utf-8")
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        handle.write(line.encode("utf-8") + b"\n")
    prune_expired(path)
    return line


def prune_expired(path: Path | None = None, *, now: datetime | None = None) -> None:
    """Drop log lines older than RETENTION_DAYS. Lines without a timestamp stay."""
    target = path or log_path()
    if not target.exists():
        return
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=RETENTION_DAYS)
    kept: list[bytes] = []
    lines = [raw for raw in target.read_bytes().splitlines() if raw.strip()]
    for raw in lines:
        try:
            record = orjson.loads(raw)
            stamp = datetime.fromisoformat(record["ts"])
        except (KeyError, TypeError, ValueError):
            kept.append(raw)
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        if stamp >= cutoff:
            kept.append(raw)
    if len(kept) == len(lines):
        return
    body = b"\n".join(kept)
    target.write_bytes(body + (b"\n" if body else b""))


def append_handoff(ticket: dict[str, Any]) -> None:
    """Add one support ticket, masked, and drop the ones past the retention period."""
    path = handoff_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = orjson.dumps(_clean_record(ticket))
    with _queue_lock:
        with path.open("ab") as handle:
            handle.write(line + b"\n")
        prune_expired(path)


def read_handoffs() -> list[dict[str, Any]]:
    path = handoff_path()
    if not path.exists():
        return []
    with _queue_lock:
        prune_expired(path)
        body = path.read_bytes()
    tickets: list[dict[str, Any]] = []
    for raw in body.splitlines():
        if raw.strip():
            tickets.append(orjson.loads(raw))
    return tickets


# ---------------------------------------------------------------------------
# Stage lines
# ---------------------------------------------------------------------------

_STAGE_LOGGER = "routechat"
_totals_lock = threading.Lock()
# Model cost per request, summed from stage lines until the request line is written.
_cost_totals: dict[str, float] = defaultdict(float)


def token_cost(model: str | None, tokens_in: int, tokens_out: int) -> float:
    """USD for one call, from prices in config.yaml. An unpriced model costs 0 here."""
    if not model:
        return 0.0
    from routechat.config import get_settings

    price = get_settings().prices.openai_usd_per_million_tokens.get(model)
    if price is None:
        return 0.0
    return (tokens_in * price.input + tokens_out * price.output) / 1_000_000


def stage_line(
    stage: str,
    request_id: str,
    decision: str,
    latency_ms: float,
    *,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cost_usd: float = 0.0,
    api_calls: dict[str, int] | None = None,
    model: str | None = None,
    prompt_version: str | None = None,
    error_type: str | None = None,
) -> dict[str, Any]:
    """Emit one stage line in the WP10 format. It never carries message text."""
    line: dict[str, Any] = {
        "request_id": request_id,
        "stage": stage,
        "latency_ms": round(float(latency_ms), 3),
        "tokens_in": int(tokens_in),
        "tokens_out": int(tokens_out),
        "cost_usd": float(cost_usd),
        "api_calls": dict(api_calls or {}),
        "decision": decision,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    run_id = os.environ.get("RUN_ID")
    if run_id:
        line["run_id"] = run_id
    if model:
        line["model"] = model
    if prompt_version:
        line["prompt_version"] = prompt_version
    if error_type:
        line["error_type"] = error_type
    install_stage_log()
    logging.getLogger(f"{_STAGE_LOGGER}.{stage}").info(json.dumps(line))
    return line


class _StageFileHandler(logging.Handler):
    """Copies JSON stage lines from the routechat logger into the log file."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = json.loads(record.getMessage())
        except (TypeError, ValueError):
            return
        if not isinstance(line, dict) or "stage" not in line or "request_id" not in line:
            return
        if line["stage"] == "request":
            return
        try:
            log_event(line)
        except OSError:
            # A full disk must not break the user's answer.
            return
        cost = line.get("cost_usd")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            with _totals_lock:
                _cost_totals[str(line["request_id"])] += float(cost)


def install_stage_log() -> None:
    """Attach the file handler once. Safe to call on every request."""
    root = logging.getLogger(_STAGE_LOGGER)
    if any(isinstance(handler, _StageFileHandler) for handler in root.handlers):
        return
    root.addHandler(_StageFileHandler(level=logging.INFO))
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Request line
# ---------------------------------------------------------------------------

OUTCOMES = ("accept", "clarify", "refuse", "handoff", "fixed_reply")


@lru_cache(maxsize=1)
def git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=_ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _plain(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def request_outcome(state: dict[str, Any]) -> str:
    """One outcome label per request, matching D2's expected outcome.

    The first stage that leaves the normal path decides (WP0 routing table).
    Same rules as eval/log_schema.outcome_from_state.
    """
    guard = _plain(state.get("guard")) or {}
    if guard.get("decision") in ("refuse", "clarify", "fixed_reply"):
        return guard["decision"]
    action = (_plain(state.get("classification")) or {}).get("route_action")
    if action == "refuse":
        return "refuse"
    if action == "escalate":
        return "handoff"
    if action == "clarify":
        return "handoff" if "asked" not in state and state.get("clarify_count", 0) >= 1 else "clarify"
    if not action and (_plain(state.get("rewrite")) or {}).get("status") == "unclear":
        return "clarify"
    constraints = _plain(state.get("constraints")) or {}
    if constraints.get("missing_field") or constraints.get("too_many_stops"):
        return "clarify"
    report = _plain(state.get("report")) or {}
    if report.get("decision") == "handoff" or state.get("handoff"):
        return "handoff"
    if report.get("decision") in ("clarify", "refuse"):
        return report["decision"]
    return "accept"


def _stopped_at(state: dict[str, Any]) -> str:
    guard = _plain(state.get("guard")) or {}
    if guard.get("decision") not in (None, "allow"):
        return "guard_in"
    if (_plain(state.get("rewrite")) or {}).get("status") == "unclear":
        return "rewrite"
    # Read before the action: a verifier question asked twice is relabelled escalate by the graph.
    if (_plain(state.get("report")) or {}).get("decision") in ("handoff", "clarify", "refuse"):
        return "verify"
    action = (_plain(state.get("classification")) or {}).get("route_action")
    if action in ("refuse", "escalate", "clarify"):
        return "classify"
    constraints = _plain(state.get("constraints")) or {}
    if constraints.get("missing_field") or constraints.get("too_many_stops"):
        return "extract"
    return ""


def request_line(state: dict[str, Any], latency_ms_total: float) -> dict[str, Any]:
    """The per-request summary line (Evaluation Spec, Table 3). No message text."""
    request_id = str(state.get("request_id", ""))
    classification = _plain(state.get("classification")) or {}
    report = _plain(state.get("report")) or {}
    draft = _plain(state.get("draft")) or {}
    final = _plain(state.get("final")) or {}
    ticket = _plain(state.get("handoff")) or {}
    with _totals_lock:
        cost = _cost_totals.pop(request_id, 0.0)
    handoff_reason = ticket.get("reason") or report.get("handoff_reason") or ""
    stopped_at = _stopped_at(state)
    if handoff_reason in ("system_error", "timeout"):
        stopped_at = stopped_at or "graph"
    return {
        "request_id": request_id,
        "stage": "request",
        "run_id": os.environ.get("RUN_ID") or "live",
        "git_commit": git_commit(),
        "dataset": os.environ.get("EVAL_DATASET") or "live",
        "item_id": os.environ.get("EVAL_ITEM_ID", ""),
        "turn_index": int(state.get("turn_index", 0) or 0),
        "outcome": request_outcome(state),
        "route_action": classification.get("route_action", ""),
        "primary_mode": classification.get("primary_mode", ""),
        "revision_count": int(state.get("revision_count", 0) or 0),
        "checks": [
            {"id": check.get("id"), "status": check.get("status"), "hard": check.get("hard")}
            for check in report.get("checks", [])
        ],
        "must_mention": list(report.get("must_mention", [])),
        "mentioned_ids": list(draft.get("mentioned_ids", [])),
        "used_fallback_template": bool(final.get("used_fallback_template", False)),
        "approved": bool(final.get("approved", False)),
        "sponsored_shown": bool(report.get("sponsored")),
        "handoff_reason": str(handoff_reason),
        "stopped_at": stopped_at,
        "latency_ms_total": round(float(latency_ms_total), 3),
        "cost_usd_total": float(cost),
        "ts": datetime.now(timezone.utc).isoformat(),
    }


def log_request(state: dict[str, Any], latency_ms_total: float) -> dict[str, Any] | None:
    """Write the request line. A logging failure never breaks the user's answer."""
    line = request_line(state, latency_ms_total)
    try:
        log_event(line)
    except OSError:
        return None
    return line
