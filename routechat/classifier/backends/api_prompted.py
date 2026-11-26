"""Prompted API backends: prompted_small (gpt-4.1-mini) and prompted_large (gpt-4.1).

Plain text output, temperature 0, no structured-output mode. Every backend must go through the
same JSON parser in predict.py, or the comparison with the local model is not fair (handbook WP2,
step 4). Usage comes back for the cost log. Record and replay are handled by routechat.llm.
"""

from __future__ import annotations

from routechat.classifier.backends import GenResult, Message
from routechat.llm import complete


class PromptedBackend:
    uses_examples = True
    prompt_version = None  # always the current label guide

    def __init__(self, name: str, model: str, temperature: float, max_new_tokens: int) -> None:
        self.name = name
        self.model = model
        self.temperature = temperature
        self.max_new_tokens = max_new_tokens

    def generate(self, messages: list[Message]) -> GenResult:
        r = complete(messages, self.model, self.temperature, max_tokens=self.max_new_tokens)
        return GenResult(r.text, r.tokens_in, r.tokens_out, r.latency_ms, r.cost_usd, r.provider)
