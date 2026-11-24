"""Model-call helper for the two stages Person C owns: the classifier (WP2) and the extractor (WP3).

What it does
- Builds the chat client. With OPENAI_API_KEY set it calls OpenAI directly, with the model
  names from config.yaml. Otherwise, with OPENROUTER_API_KEY set, it calls OpenRouter and adds
  the "openai/" prefix OpenRouter expects.
- Record and replay. FIXTURES=record saves every response as
  routechat/fixtures/llm/<sha256 of model, temperature, messages, schema>.json. FIXTURES=replay
  reads them back and never touches the network, so tests and evaluation run offline and free.
  A missing fixture in replay raises LLMFixtureMissing with the key, so the fix is one record run.
- Prices from config.yaml give cost_usd per call.
- log_stage writes one JSON line per stage call in the WP10 stage-line format (eval/log_schema.py).

Only the classifier and extractor import this module. It never sees raw user text beyond the
messages it is asked to send, and it never logs message content.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from routechat.config import ROOT, get_settings

FIXTURE_DIR = ROOT / "routechat" / "fixtures" / "llm"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

Message = dict[str, str]  # {"role": "system" | "user" | "assistant", "content": str}


class LLMFixtureMissing(RuntimeError):
    """FIXTURES=replay found no recorded response for this exact call."""


class LLMNoKey(RuntimeError):
    """No OPENAI_API_KEY or OPENROUTER_API_KEY, and FIXTURES is not replay."""


@dataclass(frozen=True)
class LLMResult:
    text: str
    tokens_in: int
    tokens_out: int
    model: str
    provider: str  # openai | openrouter | replay
    latency_ms: float
    cost_usd: float


def fixture_key(model: str, temperature: float, messages: list[Message], schema_name: str | None) -> str:
    """Stable key for one call. Any change to the prompt text changes the key, on purpose."""
    payload = json.dumps(
        {"model": model, "temperature": temperature, "messages": messages, "schema": schema_name},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def fixture_path(key: str) -> Path:
    return FIXTURE_DIR / f"{key}.json"


def cost_usd(model: str, tokens_in: int, tokens_out: int) -> float:
    """Model cost from config.yaml prices. An unlisted model costs 0 and is the cost model's problem to flag."""
    prices = get_settings().prices.openai_usd_per_million_tokens
    price = prices.get(model)
    if price is None:
        return 0.0
    return round(tokens_in * price.input / 1e6 + tokens_out * price.output / 1e6, 8)


def provider_and_client_kwargs() -> tuple[str, dict[str, Any]]:
    openai_key = os.environ.get("OPENAI_API_KEY")
    if openai_key:
        return "openai", {"api_key": openai_key}
    router_key = os.environ.get("OPENROUTER_API_KEY")
    if router_key:
        return "openrouter", {"api_key": router_key, "base_url": OPENROUTER_BASE_URL}
    raise LLMNoKey("set OPENAI_API_KEY or OPENROUTER_API_KEY in .env, or run with FIXTURES=replay")


def _live_call(
    messages: list[Message], model: str, temperature: float, max_tokens: int | None, schema: type | None,
    options: dict[str, Any] | None = None,
) -> tuple[str, int, int, str]:
    """One network call through langchain-openai. Returns (text, tokens_in, tokens_out, provider)."""
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
    from langchain_openai import ChatOpenAI

    provider, kwargs = provider_and_client_kwargs()
    model_name = f"openai/{model}" if provider == "openrouter" and "/" not in model else model
    llm = ChatOpenAI(model=model_name, temperature=temperature, max_tokens=max_tokens, **kwargs, **(options or {}))
    roles = {"system": SystemMessage, "user": HumanMessage, "assistant": AIMessage}
    lc_messages = [roles[m["role"]](content=m["content"]) for m in messages]
    if schema is not None:
        # Handbook WP3: with_structured_output(schema, include_raw=True). The raw message keeps
        # usage for the log; the text is parsed again by the caller so replay takes the same path.
        out = llm.with_structured_output(schema, method="json_schema", include_raw=True).invoke(lc_messages)
        raw = out["raw"]
    else:
        raw = llm.invoke(lc_messages)
    usage = getattr(raw, "usage_metadata", None) or {}
    content = raw.content if isinstance(raw.content, str) else json.dumps(raw.content)
    return content, int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0)), provider


def complete(
    messages: list[Message],
    model: str,
    temperature: float = 0.0,
    max_tokens: int | None = None,
    schema: type | None = None,
    options: dict[str, Any] | None = None,
) -> LLMResult:
    """Send one chat completion, honouring FIXTURES=record|replay.

    options are extra ChatOpenAI arguments, such as a timeout. They are not part of the fixture key.
    """
    mode = os.environ.get("FIXTURES", "").strip().lower()
    schema_name = getattr(schema, "__name__", None) if schema is not None else None
    key = fixture_key(model, temperature, messages, schema_name)
    path = fixture_path(key)

    if mode == "replay":
        if not path.exists():
            raise LLMFixtureMissing(
                f"no fixture {key} for model={model} schema={schema_name}; "
                "run the same call once with FIXTURES=record, or scripts/llm_fixtures.py"
            )
        rec = json.loads(path.read_text(encoding="utf-8"))
        tokens_in, tokens_out = int(rec.get("tokens_in", 0)), int(rec.get("tokens_out", 0))
        return LLMResult(rec["text"], tokens_in, tokens_out, model, "replay", 0.0, cost_usd(model, tokens_in, tokens_out))

    started = time.perf_counter()
    text, tokens_in, tokens_out, provider = _live_call(
        messages, model, temperature, max_tokens, schema, **({"options": options} if options else {})
    )
    latency_ms = round((time.perf_counter() - started) * 1000, 3)
    if mode == "record":
        write_fixture(messages, model, temperature, schema_name, text, tokens_in, tokens_out, source="live", provider=provider)
    return LLMResult(text, tokens_in, tokens_out, model, provider, latency_ms, cost_usd(model, tokens_in, tokens_out))


def write_fixture(
    messages: list[Message],
    model: str,
    temperature: float,
    schema_name: str | None,
    text: str,
    tokens_in: int = 0,
    tokens_out: int = 0,
    source: str = "synthetic",
    provider: str = "none",
) -> Path:
    """Write one replay fixture. source is "live" from a real call or "synthetic" when seeded by hand."""
    key = fixture_key(model, temperature, messages, schema_name)
    path = fixture_path(key)
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "key": key,
        "model": model,
        "temperature": temperature,
        "schema": schema_name,
        "source": source,
        "provider": provider,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "messages": messages,
        "text": text,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
    }
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def api_calls_for(provider: str) -> dict[str, int]:
    """api_calls value for the stage log. Replay made no call."""
    return {} if provider in ("replay", "none") or provider.startswith("local") else {provider: 1}


def log_stage(
    stage: str,
    request_id: str,
    decision: str,
    latency_ms: float,
    tokens_in: int,
    tokens_out: int,
    cost: float,
    api_calls: dict[str, int],
    model: str | None = None,
    prompt_version: str | None = None,
    error_type: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One stage line in the WP10 format. Never includes message text."""
    line: dict[str, Any] = {
        "request_id": request_id,
        "stage": stage,
        "latency_ms": latency_ms,
        "tokens_in": int(tokens_in),
        "tokens_out": int(tokens_out),
        "cost_usd": cost,
        "api_calls": api_calls,
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
    if extra:
        line.update(extra)
    logging.getLogger(f"routechat.{stage}").info(json.dumps(line))
    return line
