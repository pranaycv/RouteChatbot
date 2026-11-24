"""WP3. Constraint extractor, stage 3. Owner: Person C.

Turns clean text plus the classifier's labels (and, on a follow-up, the previous ConstraintSet)
into a ConstraintSet. Runs only for plan, replan, compare_modes and lookup_only; the graph never
calls it for anything else. It makes one model call (two on a validation retry), then applies the
code-side rules the handbook assigns to this stage. It never invents places, coordinates or hours.

Graph entry point: extract_node(state) -> {"constraints": ConstraintSet}
Function form:     extract(clean_text, classification, known) -> ConstraintSet
Prompt:            prompts/extractor_v<N>.md (role, field rules, prohibitions, worked examples); see prompt_version()
"""

from __future__ import annotations

import json
import os
import re
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from pydantic import ValidationError

from routechat.config import ROOT, get_settings
from routechat.llm import LLMResult, Message, api_calls_for, complete, log_stage
from routechat.schemas import ConstraintSet, RouteClassification

# v1 (18 Nov): first version. v2 (24 Nov): the origin may sit anywhere in the message, a stop only when the user
# asks to stop, no leading article or "entrance to" on place names (prompts/CHANGELOG.md has the D2 numbers).
# EXTRACTOR_PROMPT_VERSION overrides the default, so scripts/extractor_eval.py can score a new version without
# changing what the app serves.
DEFAULT_PROMPT_VERSION = "extractor_v2"
PROMPT_VERSION = DEFAULT_PROMPT_VERSION  # the module constant some callers read; prompt_version() is authoritative


def prompt_version() -> str:
    return os.environ.get("EXTRACTOR_PROMPT_VERSION") or DEFAULT_PROMPT_VERSION


def prompt_path(version: str) -> Path:
    return ROOT / "prompts" / f"{version}.md"


PROMPT_PATH = prompt_path(DEFAULT_PROMPT_VERSION)
STAGE = "extract"
_TIME_RE = re.compile(r"^\d{2}:\d{2}$")


@lru_cache(maxsize=4)
def _read_prompt(version: str) -> str:
    return prompt_path(version).read_text(encoding="utf-8")


def load_prompt(version: str | None = None) -> str:
    """The prompt file for the given version, else EXTRACTOR_PROMPT_VERSION or the default."""
    return _read_prompt(version or prompt_version())


def _as_classification(value: Any) -> RouteClassification:
    return value if isinstance(value, RouteClassification) else RouteClassification.model_validate(value)


def _as_known(value: Any) -> ConstraintSet | None:
    if value is None or isinstance(value, ConstraintSet):
        return value
    return ConstraintSet.model_validate(value)


def labels_json(classification: RouteClassification) -> str:
    """The five labels as the prompt shows them. parse_error is a runtime flag, not a label."""
    return classification.model_dump_json(exclude={"parse_error"})


def build_messages(clean_text: str, classification: RouteClassification, known: ConstraintSet | None) -> list[Message]:
    """System: the prompt file. User: the three inputs in the same layout as the worked examples."""
    user = (
        f"clean_text: {clean_text}\n"
        f"classification: {labels_json(classification)}\n"
        f"known: {known.model_dump_json() if known is not None else 'null'}"
    )
    return [{"role": "system", "content": load_prompt()}, {"role": "user", "content": user}]


def unparseable_constraints(classification: RouteClassification) -> ConstraintSet:
    """Returned after two failed validations. The writer turns missing_field = unparseable into a rephrase request."""
    return ConstraintSet(
        origin=None,
        destination=None,
        stops=[],
        mode=classification.primary_mode,
        modes_to_compare=[],
        compare_threshold_min=None,
        avoid=[],
        prefer=[],
        minimize_elevation=False,
        check_closures=False,
        max_duration_min=None,
        max_distance_km=None,
        loop=False,
        target_distance_km=None,
        depart_at=None,
        arrive_by=None,
        required_accessibility=False,
        ambiguities=["the request could not be read into trip fields; please rephrase"],
        missing_field="unparseable",
        too_many_stops=False,
    )


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def postprocess(constraints: ConstraintSet, classification: RouteClassification) -> ConstraintSet:
    """The code-side rules from the handbook (WP3 processing steps 3 and 4) and the agreements with WP4/WP5.

    1. mode unspecified: copy the classifier's primary_mode.
    2. avoid and prefer: no duplicates. modes_to_compare: at most trip.max_modes_to_compare.
    3. missing_field: origin when origin is null; destination when destination is null and the trip
       is not a loop. unparseable is left alone.
    4. too_many_stops: more than trip.max_required_stops required stops.
    5. Agreed with WP4/WP5 on 18 Nov: a loop without a target distance, or an arrive_by earlier
       than depart_at, becomes an ambiguity rather than a guess.
    """
    trip = get_settings().trip
    data = constraints.model_dump()

    if data["mode"] == "unspecified":
        data["mode"] = classification.primary_mode
    data["avoid"] = _dedupe(data["avoid"])
    data["prefer"] = _dedupe(data["prefer"])
    data["modes_to_compare"] = _dedupe(data["modes_to_compare"])[: trip.max_modes_to_compare]

    if data["missing_field"] != "unparseable":
        if data["origin"] is None:
            data["missing_field"] = "origin"
        elif data["destination"] is None and not data["loop"]:
            data["missing_field"] = "destination"
        else:
            data["missing_field"] = None

    required = sum(1 for s in data["stops"] if s["required"])
    data["too_many_stops"] = required > trip.max_required_stops

    ambiguities = list(data["ambiguities"])
    if data["loop"] and data["target_distance_km"] is None:
        note = "loop length not given: map tools need a target distance"
        if note not in ambiguities:
            ambiguities.append(note)
    depart, arrive = data["depart_at"], data["arrive_by"]
    if depart and arrive and _TIME_RE.match(depart) and _TIME_RE.match(arrive) and arrive < depart:
        note = "arrive_by is earlier than depart_at: does the user mean tomorrow?"
        if note not in ambiguities:
            ambiguities.append(note)
    data["ambiguities"] = ambiguities

    return ConstraintSet.model_validate(data)


def _call(messages: list[Message]) -> tuple[ConstraintSet | None, LLMResult, str | None]:
    settings = get_settings().extractor
    result = complete(messages, settings.model, settings.temperature, schema=ConstraintSet)
    try:
        return ConstraintSet.model_validate_json(result.text), result, None
    except (ValidationError, ValueError) as exc:
        return None, result, str(exc)


def extract(
    clean_text: str,
    classification: RouteClassification | Mapping[str, Any],
    known: ConstraintSet | Mapping[str, Any] | None = None,
    request_id: str = "",
) -> ConstraintSet:
    """One extraction with one validation retry, then the code-side rules, then one log line."""
    started = time.perf_counter()
    classification = _as_classification(classification)
    known = _as_known(known)
    messages = build_messages(clean_text, classification, known)

    parsed, first, error = _call(messages)
    tokens_in, tokens_out, cost = first.tokens_in, first.tokens_out, first.cost_usd
    provider = first.provider
    calls = 1
    if parsed is None:
        retry = messages + [
            {"role": "assistant", "content": first.text},
            {"role": "user", "content": f"That output failed validation:\n{error}\nReturn the corrected JSON object only."},
        ]
        parsed, second, error = _call(retry)
        tokens_in += second.tokens_in
        tokens_out += second.tokens_out
        cost += second.cost_usd
        calls = 2
    if parsed is None:
        parsed = unparseable_constraints(classification)

    constraints = postprocess(parsed, classification)
    if constraints.missing_field is not None:
        decision = f"missing_field:{constraints.missing_field}"
    elif constraints.too_many_stops:
        decision = "too_many_stops"
    else:
        decision = "complete"
    api_calls = api_calls_for(provider)
    if api_calls:
        api_calls = {k: v * calls for k, v in api_calls.items()}
    log_stage(
        STAGE,
        request_id,
        decision,
        round((time.perf_counter() - started) * 1000, 3),
        tokens_in,
        tokens_out,
        round(cost, 8),
        api_calls,
        model=get_settings().extractor.model,
        prompt_version=prompt_version(),
        extra={"retries": calls - 1},
    )
    return constraints


def extract_node(state: Mapping[str, Any]) -> dict:
    """Stage 3 node. Reads the guard's clean_text when present, so masked text is what the model sees."""
    guard = state.get("guard")
    if guard is None:
        clean_text = state["utterance"]
    else:
        clean_text = guard["clean_text"] if isinstance(guard, dict) else guard.clean_text
    constraints = extract(clean_text, state["classification"], state.get("known"), request_id=str(state.get("request_id", "")))
    return {"constraints": constraints}


# Worked examples, parsed from the prompt file so tests and fixture seeding use one source of truth.

_EXAMPLE_RE = re.compile(
    r"### Example (?P<n>\d+): (?P<title>[^\n]+)\n\nINPUT\nclean_text: (?P<text>[^\n]+)\n"
    r"classification: (?P<cls>\{[^\n]+\})\nknown: (?P<known>[^\n]+)\n\nOUTPUT\n(?P<out>\{[^\n]+\})",
)


def prompt_examples() -> list[dict[str, Any]]:
    """The six worked examples as data: id, clean_text, classification, known, model_output."""
    examples = []
    for m in _EXAMPLE_RE.finditer(load_prompt()):
        known_raw = m.group("known").strip()
        examples.append(
            {
                "id": f"example_{m.group('n')}",
                "title": m.group("title").strip(),
                "clean_text": m.group("text").strip(),
                "classification": json.loads(m.group("cls")),
                "known": None if known_raw == "null" else json.loads(known_raw),
                "model_output": json.loads(m.group("out")),
            }
        )
    return examples
