"""WP2. Request classifier, stage 2. Owner: Person C.

Maps one UserTurn to the five labels in RouteClassification. It does not extract places, call
tools or write text. The same function runs four backends, chosen by CLASSIFIER_BACKEND, so the
fine-tuned model can be compared fairly with the prompted ones:

    ft              Qwen2.5-1.5B-Instruct + the WP9 LoRA adapter   (label guide only)
    base            the same model without the adapter              (label guide only)
    prompted_small  gpt-4.1-mini                                    (label guide + 8 few-shot examples)
    prompted_large  gpt-4.1                                         (label guide + 8 few-shot examples)

Every backend's text goes through the same parser: take the first {...} block, validate it as
RouteClassification, retry once on failure, and on a second failure return escalate with
parse_error = true. A bad output can never produce a route. After parsing, two normalisation
rules apply and each correction is logged so it can be counted.

Graph entry points: classify_node(state) -> {"classification": ...}, or classify(turn).
Prompts: prompts/label_guide_v1.md (system) and prompts/classifier_fewshot_v1.jsonl (examples).
WP9's finetune/prepare.py imports build_messages and labels_json so training data is byte-identical
to what this module sends at runtime (test T-9-01).
"""

from __future__ import annotations

import json
import os
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from pydantic import ValidationError

from routechat.classifier.backends import Backend, GenResult, Message
from routechat.config import ROOT, get_settings
from routechat.llm import api_calls_for, log_stage
from routechat.schemas import ConstraintSet, RouteClassification, UserTurn

PROMPT_VERSION = "label_guide_v2"  # v2 (23 Nov): transit is refused with out_of_scope_mode, matching D1 and schemas v1.1
LABEL_GUIDE_PATH = ROOT / "prompts" / f"{PROMPT_VERSION}.md"
FEWSHOT_PATH = ROOT / "prompts" / "classifier_fewshot_v1.jsonl"
BACKEND_NAMES = ("ft", "base", "prompted_small", "prompted_large")
DEFAULT_BACKEND = "prompted_small"
STAGE = "classify"
RETRY_HINT = "That was not a valid answer. Reply with only the JSON object with the five keys and allowed values."


@lru_cache(maxsize=1)
def load_label_guide() -> str:
    return LABEL_GUIDE_PATH.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def load_fewshot() -> tuple[tuple[UserTurn, RouteClassification], ...]:
    pairs = []
    for line in FEWSHOT_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        pairs.append((UserTurn.model_validate(row["turn"]), RouteClassification.model_validate(row["labels"])))
    return tuple(pairs)


def serialize_turn(turn: UserTurn) -> str:
    """The user message. The same serialisation is used for training (WP9)."""
    return turn.model_dump_json()


def labels_json(classification: RouteClassification) -> str:
    """The five labels as the model must emit them. parse_error is a runtime flag, never in the output."""
    return classification.model_dump_json(exclude={"parse_error"})


def build_messages(turn: UserTurn, include_examples: bool) -> list[Message]:
    """System: the label guide. Optional few-shot pairs. User: the serialised turn."""
    messages: list[Message] = [{"role": "system", "content": load_label_guide()}]
    if include_examples:
        for example_turn, example_labels in load_fewshot():
            messages.append({"role": "user", "content": serialize_turn(example_turn)})
            messages.append({"role": "assistant", "content": labels_json(example_labels)})
    messages.append({"role": "user", "content": serialize_turn(turn)})
    return messages


def extract_first_json(text: str) -> str | None:
    """The first balanced {...} block in the text, or None. Prose around the JSON is ignored."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def parse_labels(text: str) -> RouteClassification:
    """Validate the first JSON object as the five labels. Raises ValueError on any problem."""
    block = extract_first_json(text)
    if block is None:
        raise ValueError("no JSON object in the model output")
    try:
        data = json.loads(block)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict) or "parse_error" in data:
        raise ValueError("output must be an object with the five label keys only")
    try:
        return RouteClassification.model_validate(data)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc


def normalise(classification: RouteClassification) -> tuple[RouteClassification, list[str]]:
    """refusal_reason is none unless refuse; clarify_field is none unless clarify. Returns the corrections made."""
    corrections: list[str] = []
    update: dict[str, Any] = {}
    if classification.route_action != "refuse" and classification.refusal_reason != "none":
        update["refusal_reason"] = "none"
        corrections.append(f"refusal_reason:{classification.refusal_reason}->none")
    if classification.route_action != "clarify" and classification.clarify_field != "none":
        update["clarify_field"] = "none"
        corrections.append(f"clarify_field:{classification.clarify_field}->none")
    if update:
        classification = classification.model_copy(update=update)
    return classification, corrections


def parse_error_classification() -> RouteClassification:
    return RouteClassification(
        route_action="escalate",
        primary_mode="unspecified",
        needs_live_data=False,
        clarify_field="none",
        refusal_reason="none",
        parse_error=True,
    )


def adapter_dir() -> Path:
    return Path(os.environ.get("CLASSIFIER_ADAPTER_DIR") or ROOT / "finetune" / "artifacts" / "adapter")


def resolve_backend(name: str | None = None) -> Backend:
    """Build the backend named by the argument or CLASSIFIER_BACKEND. Model names come from config.yaml."""
    name = (name or os.environ.get("CLASSIFIER_BACKEND") or DEFAULT_BACKEND).strip()
    cfg = get_settings().classifier
    if name == "prompted_small":
        from routechat.classifier.backends.api_prompted import PromptedBackend

        return PromptedBackend(name, cfg.prompted_small, cfg.temperature, cfg.max_new_tokens)
    if name == "prompted_large":
        from routechat.classifier.backends.api_prompted import PromptedBackend

        return PromptedBackend(name, cfg.prompted_large, cfg.temperature, cfg.max_new_tokens)
    if name == "base":
        from routechat.classifier.backends.hf_local import LocalBackend

        return LocalBackend(name, cfg.base_model, None, cfg.max_new_tokens)
    if name == "ft":
        from routechat.classifier.backends.hf_local import LocalBackend

        return LocalBackend(name, cfg.base_model, adapter_dir(), cfg.max_new_tokens)
    raise ValueError(f"unknown CLASSIFIER_BACKEND {name!r}; expected one of {BACKEND_NAMES}")


def classify(turn: UserTurn, backend: Backend | None = None, request_id: str = "") -> RouteClassification:
    """Classify one turn: generate, parse, retry once, normalise, log."""
    started = time.perf_counter()
    backend = backend or resolve_backend()
    messages = build_messages(turn, include_examples=backend.uses_examples)

    first: GenResult = backend.generate(messages)
    tokens_in, tokens_out, cost = first.tokens_in, first.tokens_out, first.cost_usd
    calls = 1
    error_type: str | None = None
    try:
        classification = parse_labels(first.text)
    except ValueError:
        second = backend.generate(
            messages + [{"role": "assistant", "content": first.text}, {"role": "user", "content": RETRY_HINT}]
        )
        tokens_in += second.tokens_in
        tokens_out += second.tokens_out
        cost += second.cost_usd
        calls = 2
        try:
            classification = parse_labels(second.text)
        except ValueError:
            classification = parse_error_classification()
            error_type = "ParseError"

    classification, corrections = normalise(classification)
    api_calls = api_calls_for(first.provider)
    if api_calls:
        api_calls = {k: v * calls for k, v in api_calls.items()}
    log_stage(
        STAGE,
        request_id,
        classification.route_action,
        round((time.perf_counter() - started) * 1000, 3),
        tokens_in,
        tokens_out,
        round(cost, 8),
        api_calls,
        model=f"{backend.name}:{backend.model}",
        prompt_version=PROMPT_VERSION,
        error_type=error_type,
        extra={"retries": calls - 1, "corrections": corrections, "parse_error": classification.parse_error},
    )
    return classification


def _turn_from_state(state: Mapping[str, Any]) -> UserTurn:
    guard = state.get("guard")
    if guard is None:
        utterance = state["utterance"]
    else:
        utterance = guard["clean_text"] if isinstance(guard, dict) else guard.clean_text
    known = state.get("known")
    if known is not None and not isinstance(known, ConstraintSet):
        known = ConstraintSet.model_validate(known)
    return UserTurn(utterance=utterance, turn_index=int(state.get("turn_index", 0)), known=known)


def classify_node(state: Mapping[str, Any]) -> dict:
    """Stage 2 node. Uses the guard's clean_text so the model never sees unmasked personal data."""
    return {"classification": classify(_turn_from_state(state), request_id=str(state.get("request_id", "")))}
