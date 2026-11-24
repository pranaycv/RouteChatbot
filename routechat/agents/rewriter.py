# Owner: Person A (conversation rewriter), Team Rosedale, MMAI 894.
# Written for this project with AI assistance; no code copied from other sources.
"""Conversation rewriter, the stage after the input guard.

It reads the conversation and the latest message and writes one request that
makes sense on its own, so the classifier and the extractor do not have to work
out which of several trips the user means. It also corrects spelling and
grammar. When two trips fit, it returns unclear and the graph asks through the
writer.

The app supplies `history`: earlier turns the input guard allowed, already
masked. A caller that keeps none, such as the evaluation, makes no model call.
The message goes on as the user wrote it when the call fails, when the answer
names something nobody said, or when a first message would lose part of itself.
"""

from __future__ import annotations

import re
import time
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from routechat.config import get_settings
from routechat.guards.input_guard import _attack_reason, _mask_personal_data, _normalize
from routechat.llm import Message, api_calls_for, complete, log_stage, provider_and_client_kwargs
from routechat.schemas import RewrittenQuery

_PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "rewriter_v1.md"
PROMPT_VERSION = _PROMPT_PATH.stem
STAGE = "rewrite"
_MAX_OPTIONS = 3
_SAME_WORD = 0.75  # two spellings of one word
_SHORT_FORMS = {
    "st": "street", "rd": "road", "ave": "avenue", "blvd": "boulevard", "dr": "drive", "ct": "court", "stn": "station",
    "e": "east", "w": "west", "n": "north", "s": "south", "prk": "park",
}
_MIN_KEPT = 0.6  # a first message is corrected, not shortened
# Words a refusal rests on. A rewrite that loses one from the latest message is not used.
_MUST_STAY = frozenset(
    "rules prompt prompts instructions reveal system developer jailbreak pretend override "
    "guarantee book booking pay payment refund parking fleet delivery deliveries courier couriers".split()
)
PASS = RewrittenQuery(status="pass", query="", options=[])


@lru_cache(maxsize=1)
def load_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def clean_message(text: str) -> str:
    """One message as the input guard would pass it on: normalised, with personal data masked."""
    return _mask_personal_data(_normalize(text))[0]


def transcript(history: list[dict[str, str]], limit: int) -> str:
    """The conversation, oldest first. The oldest turns go when it is longer than limit."""
    blocks: list[str] = []
    size = 0
    for turn in reversed(history):
        block = f"User: {turn['user']}\nUnderstood as: {turn['query']}\nAssistant: {turn['assistant']}"
        size += len(block) + 1
        if blocks and size > limit:
            break
        blocks.append(block)
    return "\n".join(reversed(blocks))


def build_messages(history: list[dict[str, str]], latest: str) -> list[Message]:
    talk = transcript(history, get_settings().rewriter.max_history_chars)
    return [
        {"role": "system", "content": load_prompt()},
        {"role": "user", "content": f"Conversation:\n{talk}\n\nLatest message: {latest}"},
    ]


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.casefold()))


def _like(word: str, other: str) -> bool:
    """The same word, or the same word with a spelling mistake. Numbers must be equal."""
    if word == other:
        return True
    if _SHORT_FORMS.get(other) == word:
        return True
    if any(char.isdigit() for char in word + other) or min(len(word), len(other)) < 3 or word[0] != other[0]:
        return False
    return SequenceMatcher(None, word, other).ratio() >= _SAME_WORD


def _said(text: str) -> set[str]:
    """The words of text, and each two neighbours as one: "haven dale" may be corrected to Havendale."""
    words = re.findall(r"\w+", text.casefold())
    return {*words, *(first + second for first, second in zip(words, words[1:]))}


def grounded(text: str, source: str) -> bool:
    """True when every name and number in text was said in source. A sentence's first word is just grammar."""
    said = _said(source)
    for sentence in re.split(r"(?<=[.?!])\s+", text.strip()):
        for word in re.findall(r"\w+", sentence)[1:]:
            named = word[0].isupper() and word != "I"
            if (named or any(char.isdigit() for char in word)) and not any(_like(word.casefold(), other) for other in said):
                return False
    return True


def kept(message: str, query: str) -> float:
    """The share of the message's longer words and numbers that query still has, spelling aside."""
    words = {word for word in _tokens(message) if len(word) > 3 or any(char.isdigit() for char in word)}
    have = _said(query)
    joined = " ".join(_said(message) & have)
    found = sum(word in joined or any(_like(word, other) for other in have) for word in words)
    return found / len(words) if words else 1.0


def _usable(text: str, source: str) -> bool:
    """The text invents nothing and is not an attack pieced together from several turns."""
    return grounded(text, source) and _attack_reason(text) is None


def check(result: RewrittenQuery, source: str, latest: str, first: bool = False) -> tuple[RewrittenQuery, str]:
    """Keep the result only if it can be trusted. Returns it with the decision to log."""
    if result.status == "ready":
        query = result.query.strip()
        if (_tokens(latest) & _MUST_STAY) - _tokens(query):
            return PASS, "pass:word_lost"
        if first and kept(latest, query) < _MIN_KEPT:
            return PASS, "pass:shortened"
        if query and _usable(query, source):
            return result.model_copy(update={"query": query, "options": []}), "ready"
        return PASS, "pass:not_grounded"
    if result.status == "unclear":
        options = list(dict.fromkeys(option.strip() for option in result.options if option.strip()))[:_MAX_OPTIONS]
        if all(_usable(option, source) for option in options):
            return result.model_copy(update={"query": "", "options": options, "new_trip": False}), "unclear"
        return PASS, "pass:not_grounded"
    return PASS, "pass"


def _options(provider: str) -> dict[str, Any]:
    settings = get_settings().rewriter
    options: dict[str, Any] = {"timeout": settings.timeout_s, "max_retries": 0}
    if provider == "openrouter":
        # The conversation goes only to providers that do not keep it for training.
        body: dict[str, Any] = {"provider": {"data_collection": "deny"}}
        if settings.effort:
            body["reasoning"] = {"effort": settings.effort}
        options["extra_body"] = body
    return options


def rewrite(history: list[dict[str, str]], latest: str, request_id: str = "") -> RewrittenQuery:
    """One model call, the checks above, and one log line. Any failure is a pass."""
    started = time.perf_counter()
    settings = get_settings().rewriter
    tokens_in = tokens_out = 0
    cost = 0.0
    calls: dict[str, int] = {}
    error_type: str | None = None
    try:
        provider = "openrouter"
        try:
            provider = provider_and_client_kwargs()[0]
        except RuntimeError:
            pass  # replay needs no key
        answer = complete(
            build_messages(history, latest),
            settings.model,
            settings.temperature,
            settings.max_tokens,
            schema=RewrittenQuery,
            options=_options(provider),
        )
        tokens_in, tokens_out, cost = answer.tokens_in, answer.tokens_out, answer.cost_usd
        calls = api_calls_for(answer.provider)
        source = " ".join([*(f"{t['user']} {t['query']} {t['assistant']}" for t in history), latest])
        result, decision = check(RewrittenQuery.model_validate_json(answer.text), source, latest, not history)
    except Exception as exc:  # the trip must go on without the rewrite
        result, decision, error_type = PASS, "pass:error", type(exc).__name__
    log_stage(
        STAGE,
        request_id,
        decision,
        round((time.perf_counter() - started) * 1000, 3),
        tokens_in,
        tokens_out,
        cost,
        calls,
        model=settings.model,
        prompt_version=PROMPT_VERSION,
        error_type=error_type,
    )
    return result


def rewrite_node(state: Mapping[str, Any]) -> dict:
    """Node after the input guard. A caller without a conversation gets no rewrite."""
    history = state.get("history")
    if history is None:
        return {}
    latest = clean_message(state.get("latest") or state["utterance"])
    return {"rewrite": rewrite(history, latest, request_id=str(state.get("request_id", "")))}
