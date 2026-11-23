"""Local backends: ft (Qwen2.5-1.5B-Instruct plus the WP9 LoRA adapter) and base (same model, no adapter).

torch, transformers and peft are imported only when a local backend generates, so the rest of
the system runs in the core environment. `uv sync --group finetune` installs them. The model and
tokenizer are loaded once per process. Device: cuda, else mps on a Mac, else cpu; the device is
part of the provider string so the latency log records it.
"""

from __future__ import annotations

import logging
import time
from functools import lru_cache
from pathlib import Path

from routechat.classifier.backends import GenResult, Message

logger = logging.getLogger("routechat.classify")


def pick_device() -> str:
    import torch

    if torch.cuda.is_available():
        return "cuda"
    mps = getattr(torch.backends, "mps", None)
    if mps is not None and mps.is_available():
        return "mps"
    return "cpu"


@lru_cache(maxsize=2)
def load_model(model_id: str, adapter_dir: str | None):
    """Load once per (model, adapter). Returns (tokenizer, model, device)."""
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    device = pick_device()
    dtype = torch.float16 if device in ("cuda", "mps") else torch.float32
    tokenizer = AutoTokenizer.from_pretrained(model_id)
    model = AutoModelForCausalLM.from_pretrained(model_id, dtype=dtype)
    if adapter_dir:
        from peft import PeftModel

        model = PeftModel.from_pretrained(model, adapter_dir)
    model.to(device)
    model.eval()
    logger.info('{"event": "classifier_model_loaded", "model": "%s", "adapter": "%s", "device": "%s"}', model_id, adapter_dir or "", device)
    return tokenizer, model, device


class LocalBackend:
    uses_examples = False

    def __init__(self, name: str, model_id: str, adapter_dir: Path | None, max_new_tokens: int) -> None:
        self.name = name
        self.model = model_id
        self.adapter_dir = adapter_dir
        self.max_new_tokens = max_new_tokens

    def _resolved_adapter(self) -> str | None:
        if self.adapter_dir is None:
            return None
        if not Path(self.adapter_dir).exists():
            raise FileNotFoundError(
                f"adapter not found at {self.adapter_dir}; train it with finetune/train.py (WP9) "
                "or set CLASSIFIER_ADAPTER_DIR, or use CLASSIFIER_BACKEND=base"
            )
        return str(self.adapter_dir)

    def generate(self, messages: list[Message]) -> GenResult:
        import torch

        tokenizer, model, device = load_model(self.model, self._resolved_adapter())
        started = time.perf_counter()
        encoded = tokenizer.apply_chat_template(messages, add_generation_prompt=True, return_tensors="pt", return_dict=True)
        input_ids = encoded["input_ids"].to(device)
        attention = encoded.get("attention_mask")
        attention = attention.to(device) if attention is not None else None
        with torch.inference_mode():
            out = model.generate(
                input_ids=input_ids,
                attention_mask=attention,
                max_new_tokens=self.max_new_tokens,
                do_sample=False,
                pad_token_id=tokenizer.pad_token_id if tokenizer.pad_token_id is not None else tokenizer.eos_token_id,
            )
        new_tokens = out[0, input_ids.shape[1] :]
        text = tokenizer.decode(new_tokens, skip_special_tokens=True)
        latency_ms = round((time.perf_counter() - started) * 1000, 3)
        return GenResult(text, int(input_ids.shape[1]), int(new_tokens.shape[0]), latency_ms, 0.0, f"local:{device}")
