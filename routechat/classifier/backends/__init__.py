"""The four classifier backends share one tiny interface so predict.py can compare them fairly.

A backend turns a list of chat messages into text. It knows nothing about labels or parsing;
every backend's output goes through the same parser in predict.py (handbook WP2, step 4).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

Message = dict[str, str]


@dataclass(frozen=True)
class GenResult:
    text: str
    tokens_in: int
    tokens_out: int
    latency_ms: float
    cost_usd: float
    provider: str  # openai | openrouter | replay | local:<device>


class Backend(Protocol):
    name: str
    model: str
    uses_examples: bool

    def generate(self, messages: list[Message]) -> GenResult: ...
