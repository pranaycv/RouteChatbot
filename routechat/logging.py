# Owner: Person A (WP7 logging), Team Rosedale, MMAI 894.
# Only Person A edits this file. Other packages may import it but should not change it;
# AI coding tools must not edit this file unless they are working on WP7.
# Written for this project with AI assistance; no code copied from other sources.
"""One JSON line per event. Logs are masked, and lines older than 30 days are dropped.

Policy P-06: location data is for the trip only. Street addresses become
[ADDRESS], WP1 masks email, card, phone and keys, and coordinates keep three
decimals. The handoff queue is the support tab's source.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import orjson

from routechat.guards.input_guard import _mask_personal_data

# P-06 proposes deletion after 30 days. This is that decision, not a tuning knob.
RETENTION_DAYS = 30

_ROOT = Path(__file__).resolve().parents[1]
_ADDRESS_RE = re.compile(
    r"\b\d{1,6}\s+(?:[A-Za-z][A-Za-z.'-]*\s+){0,3}"
    r"(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Lane|Ln|Way|Court|Ct)\b\.?",
    re.IGNORECASE,
)
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
    masked = _ADDRESS_RE.sub("[ADDRESS]", masked)

    def _round_coord(match: re.Match[str]) -> str:
        return f"{float(match.group(0)):.3f}"

    return _COORD_RE.sub(_round_coord, masked)


def _clean_record(record: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in record.items():
        if isinstance(value, str):
            clean[key] = mask_for_log(value)
        else:
            clean[key] = value
    clean.setdefault("ts", datetime.now(timezone.utc).isoformat())
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
    for raw in target.read_bytes().splitlines():
        if not raw.strip():
            continue
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
    body = b"\n".join(kept)
    target.write_bytes(body + (b"\n" if body else b""))


def append_handoff(ticket: dict[str, Any]) -> None:
    """Add one support ticket. The request text is masked before it is stored."""
    path = handoff_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = orjson.dumps(_clean_record(ticket)).decode("utf-8")
    with path.open("ab") as handle:
        handle.write(line.encode("utf-8") + b"\n")


def read_handoffs() -> list[dict[str, Any]]:
    path = handoff_path()
    if not path.exists():
        return []
    tickets: list[dict[str, Any]] = []
    for raw in path.read_bytes().splitlines():
        if raw.strip():
            tickets.append(orjson.loads(raw))
    return tickets
