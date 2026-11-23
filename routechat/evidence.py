# Owner: Person A (WP6 and WP7), Team Rosedale, MMAI 894.
# Only Person A edits this file. Other packages may import it but should not change it;
# AI coding tools must not edit this file unless they are working on WP6 or WP7.
# Written for this project with AI assistance; no code copied from other sources.
"""The verifier's evidence, in the units a person reads.

The writer sees this text, the app shows it, and the output guard accepts the
numbers in it. One function makes all three agree.
"""

from __future__ import annotations

import re

_N = r"-?\d+(?:\.\d+)?"
# "4220.55 m", "4500 to 5500 m", "679 s". The unit belongs to both ends of a range.
_MEASURE = re.compile(rf"(?<![\d.])({_N})(?:\s+to\s+({_N}))?\s+(m|s)\b")
_SHOWN = re.compile(rf"(?<![\d.])({_N})(?:\s+to\s+({_N}))?\s+(km|m|minutes)\b")


def _short(value: float, places: int = 1) -> str:
    return f"{round(value, places):g}"


def _tidy(match: re.Match[str]) -> str:
    values = [float(part) for part in match.group(1, 2) if part is not None]
    if match.group(3) == "s":
        shown, unit = [_short(value / 60, 0) for value in values], "minutes"
    elif max(abs(value) for value in values) >= 1000:
        # Two places, so 5.54 km does not read as inside a 5.5 km limit.
        shown, unit = [_short(value / 1000, 2) for value in values], "km"
    else:
        shown, unit = [_short(value) for value in values], "m"
    return f"{' to '.join(shown)} {unit}"


def tidy_evidence(evidence: str) -> str:
    """Round the measurements. 4220.55 m becomes 4.22 km, and 679 s becomes 11 minutes."""
    return _MEASURE.sub(_tidy, evidence)


def evidence_numbers(evidence: str) -> dict[str, set[float]]:
    """The numbers a reply may take from this evidence, by unit, also rounded as the route's own are."""
    numbers: dict[str, set[float]] = {"km": set(), "min": set(), "m": set()}
    for low, high, unit in _SHOWN.findall(tidy_evidence(evidence)):
        key = "min" if unit == "minutes" else unit
        for part in (low, high):
            if part:
                value = float(part)
                numbers[key] |= {value, round(value, 1) if key == "km" else float(round(value))}
    return numbers
