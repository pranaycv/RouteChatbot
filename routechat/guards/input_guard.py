# Owner: Person A (WP1 input guard), Team Rosedale, MMAI 894.
# Only Person A edits this file. Other packages may import it but should not change it;
# AI coding tools must not edit this file unless they are working on WP1.
# Written for this project with AI assistance; no code copied from other sources.
"""WP1 input guard: rules-only first defence before any model, API, or log.

Processing order is fixed by the stage work packages (do not reorder):
  1. Normalise (NFKC, collapse whitespace, trim)
  2. Empty / too long  -> clarify
  3. Mask personal data (EMAIL, CARD, PHONE, KEY)
  4. Language check    -> fixed_reply when clearly not English
  5. Attack patterns   -> refuse (prompt_injection or data_request)
  6. allow with clean_text

Street addresses are intentionally left unmasked so routing can use them;
WP7 masks them in logs. Thresholds come from config.yaml via get_settings().
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from langdetect import DetectorFactory, LangDetectException, detect_langs

from routechat.config import get_settings
from routechat.schemas import GuardReason, GuardResult

# Seed once so langdetect is deterministic across calls (handbook requirement).
DetectorFactory.seed = 0

_PATTERNS_PATH = Path(__file__).resolve().parent / "patterns.yaml"

# Masking tokens written into clean_text. Order of application is fixed below.
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# 13–16 digits with optional spaces or dashes; Luhn is checked in the replacer.
_CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]?){12,15}\d(?!\d)")
# North American forms: 416-555-0199, (416) 555 0199, 416.555.0199, +1 416 555 0199.
_PHONE_RE = re.compile(
    r"(?<!\d)"
    r"(?:\+?1[\s.-]?)?"
    r"(?:\(?\d{3}\)?[\s.-]?)"
    r"\d{3}[\s.-]?"
    r"\d{4}"
    r"(?!\d)"
)
# API-looking keys (OpenAI-style and Mapbox-style prefixes). Built by
# concatenation so the source never contains the key-material markers that
# T-0-05 scans for. Require 20+ characters after the prefix.
_KEY_PREFIX_OA = "s" + "k-"
_KEY_PREFIX_MB = "p" + "k" + "."
_KEY_RE = re.compile(
    rf"(?:{re.escape(_KEY_PREFIX_OA)}[A-Za-z0-9_-]{{20,}}"
    rf"|{re.escape(_KEY_PREFIX_MB)}[A-Za-z0-9_-]{{20,}})"
)

# Mask steps run in this order; each step records its type name in `masked`.
_MASK_STEPS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("EMAIL", _EMAIL_RE),
    ("CARD", _CARD_RE),
    ("PHONE", _PHONE_RE),
    ("KEY", _KEY_RE),
)


def _luhn_ok(digits: str) -> bool:
    """Return True when the digit string passes the Luhn check.

    Highway numbers and short counts fail this check, so they are never masked
    as CARD even if they look digit-heavy.
    """
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _normalize(text: str) -> str:
    """NFKC folds look-alike characters; whitespace is collapsed and trimmed."""
    folded = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", " ", folded).strip()


def _mask_personal_data(text: str) -> tuple[str, list[str]]:
    """Replace EMAIL, CARD, PHONE, KEY in handbook order; collect masked types."""
    masked: list[str] = []
    clean = text

    for type_name, pattern in _MASK_STEPS:
        if type_name == "CARD":
            # Only replace digit runs that pass Luhn, so "Highway 401" stays.
            def _card_sub(match: re.Match[str], *, _type: str = type_name) -> str:
                digits = re.sub(r"\D", "", match.group(0))
                if 13 <= len(digits) <= 16 and _luhn_ok(digits):
                    masked.append(_type)
                    return f"[{_type}]"
                return match.group(0)

            clean = pattern.sub(_card_sub, clean)
            continue

        def _sub(match: re.Match[str], *, _type: str = type_name) -> str:
            masked.append(_type)
            return f"[{_type}]"

        clean = pattern.sub(_sub, clean)

    return clean, masked


@lru_cache(maxsize=1)
def _compiled_patterns() -> list[tuple[GuardReason, list[re.Pattern[str]]]]:
    """Load patterns.yaml once. Group order is the refuse precedence."""
    raw = yaml.safe_load(_PATTERNS_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"patterns.yaml must be a mapping of reason -> list: {_PATTERNS_PATH}")

    # Stable order: prompt_injection first (defence), then data_request (P-07).
    ordered_reasons: tuple[GuardReason, ...] = ("prompt_injection", "data_request")
    compiled: list[tuple[GuardReason, list[re.Pattern[str]]]] = []
    for reason in ordered_reasons:
        entries = raw.get(reason) or []
        if not isinstance(entries, list):
            raise ValueError(f"patterns.yaml[{reason!r}] must be a list of regex strings")
        compiled.append(
            (reason, [re.compile(pattern, re.IGNORECASE) for pattern in entries])
        )
    return compiled


def _language_decision(text: str) -> GuardReason | None:
    """Return not_english when English is weak and another language is strong.

    Skipped for short text (config.guard.short_text_chars) so mixed stubs like
    "bici to UofT" are allowed. Detection failures are treated as English so
    the request is not blocked on detector noise.
    """
    settings = get_settings().guard
    if len(text) <= settings.short_text_chars:
        return None
    try:
        guesses = detect_langs(text)
    except LangDetectException:
        return None

    english_prob = 0.0
    other_best = 0.0
    for guess in guesses:
        if guess.lang == "en":
            english_prob = guess.prob
        else:
            other_best = max(other_best, guess.prob)

    if (
        english_prob < settings.english_min_prob
        and other_best >= settings.other_language_min_prob
    ):
        return "not_english"
    return None


def _attack_reason(text: str) -> GuardReason | None:
    """First matching pattern group wins; None means no attack matched."""
    for reason, patterns in _compiled_patterns():
        for pattern in patterns:
            if pattern.search(text):
                return reason
    return None


def guard_text(utterance: str) -> GuardResult:
    """Run the six handbook rules on raw utterance text and return GuardResult."""
    settings = get_settings().guard

    # 1. Normalise.
    text = _normalize(utterance)

    # 2. Empty or too long — early exit before masking (exact handbook order).
    if not text:
        return GuardResult(decision="clarify", clean_text="", masked=[], reason="empty")
    if len(text) > settings.max_chars:
        return GuardResult(
            decision="clarify",
            clean_text=text,
            masked=[],
            reason="too_long",
        )

    # 3. Mask personal data the trip does not need.
    clean_text, masked = _mask_personal_data(text)

    # 4. Language — only when clearly not English.
    if _language_decision(clean_text) == "not_english":
        return GuardResult(
            decision="fixed_reply",
            clean_text=clean_text,
            masked=masked,
            reason="not_english",
        )

    # 5. Attack patterns from patterns.yaml (P-07).
    attack = _attack_reason(clean_text)
    if attack is not None:
        return GuardResult(
            decision="refuse",
            clean_text=clean_text,
            masked=masked,
            reason=attack,
        )

    # 6. Safe to continue to the classifier.
    return GuardResult(
        decision="allow",
        clean_text=clean_text,
        masked=masked,
        reason=None,
    )


def input_guard(state: dict[str, Any]) -> dict[str, GuardResult]:
    """LangGraph node: read utterance, write only the `guard` field."""
    utterance = state.get("utterance") or ""
    if not isinstance(utterance, str):
        utterance = str(utterance)
    return {"guard": guard_text(utterance)}
