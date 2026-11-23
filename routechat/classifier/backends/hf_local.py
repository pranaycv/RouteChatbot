"""Local backends: ft (Qwen2.5-1.5B-Instruct plus the WP9 LoRA adapter) and base (same model, no adapter).

torch, transformers and peft are imported only when a local backend generates, so the rest of
the system runs in the core environment. `uv sync --group finetune` installs them. The base model
and tokenizer are loaded once per process; adapters are attached to that one base and swapped by
name, so checkpoint selection (finetune/select_checkpoint.py) never holds two copies of the model.
Device: cuda, else mps on a Mac, else cpu; the device is part of the provider string so the
latency log records it.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from routechat.classifier.backends import GenResult, Message

logger = logging.getLogger("routechat.classify")

_BASE: dict[str, Any] = {}  # model_id -> (tokenizer, base_model, device)
_PEFT: dict[str, Any] = {}  # model_id -> PeftModel wrapping that base (once any adapter is loaded)
_LOADED_ADAPTERS: dict[str, set[str]] = {}


def pick_device() -> str:
    import torch

    if torch.cuda.is_available():
        return "cuda"
    mps = getattr(torch.backends, "mps", None)
    if mps is not None and mps.is_available():
        return "mps"
    return "cpu"


def _from_pretrained(model_id: str, dtype):
    """transformers >= 4.56 takes dtype=; older releases take torch_dtype=. Try the new name first."""
    from transformers import AutoModelForCausalLM

    try:
        return AutoModelForCausalLM.from_pretrained(model_id, dtype=dtype)
    except TypeError:
        return AutoModelForCausalLM.from_pretrained(model_id, torch_dtype=dtype)


def load_base(model_id: str):
    """Load the base model and tokenizer once per process."""
    if model_id in _BASE:
        return _BASE[model_id]
    import torch
    from transformers import AutoTokenizer

    device = pick_device()
    dtype = torch.float16 if device in ("cuda", "mps") else torch.float32
    tokenizer = AutoTokenizer.from_pretrained(model_id)
    model = _from_pretrained(model_id, dtype)
    model.to(device)
    model.eval()
    _BASE[model_id] = (tokenizer, model, device)
    logger.info('{"event": "classifier_base_loaded", "model": "%s", "device": "%s"}', model_id, device)
    return _BASE[model_id]


def load_model(model_id: str, adapter_dir: str | None):
    """Return (tokenizer, model, device) with the given adapter active, or the plain base when adapter_dir is None."""
    tokenizer, base, device = load_base(model_id)
    if adapter_dir is None:
        peft = _PEFT.get(model_id)
        if peft is not None:
            # An adapter was loaded earlier in this process; run the base weights without it.
            with peft.disable_adapter():
                pass
            return tokenizer, _BaseView(peft), device
        return tokenizer, base, device

    from peft import PeftModel

    # PEFT uses the adapter name as a module name, which may not contain a dot (a run dir such as
    # run_r16_lr0.0002 would otherwise fail on Kaggle).
    name = Path(adapter_dir).resolve().as_posix().replace("/", "_").replace(":", "").replace(".", "_")
    peft = _PEFT.get(model_id)
    if peft is None:
        peft = PeftModel.from_pretrained(base, adapter_dir, adapter_name=name)
        peft.eval()
        _PEFT[model_id] = peft
        _LOADED_ADAPTERS[model_id] = {name}
    elif name not in _LOADED_ADAPTERS[model_id]:
        peft.load_adapter(adapter_dir, adapter_name=name)
        _LOADED_ADAPTERS[model_id].add(name)
    peft.set_adapter(name)
    logger.info('{"event": "classifier_adapter_active", "model": "%s", "adapter": "%s", "device": "%s"}', model_id, adapter_dir, device)
    return tokenizer, peft, device


class _BaseView:
    """Generate from a PeftModel with every adapter disabled, so `base` stays comparable after `ft` has run."""

    def __init__(self, peft) -> None:
        self._peft = peft

    def generate(self, **kwargs):
        with self._peft.disable_adapter():
            return self._peft.generate(**kwargs)


class LocalBackend:
    uses_examples = False

    def __init__(self, name: str, model_id: str, adapter_dir: Path | None, max_new_tokens: int) -> None:
        self.name = name
        self.model = model_id
        self.adapter_dir = adapter_dir
        self.max_new_tokens = max_new_tokens

    @property
    def prompt_version(self) -> str | None:
        """The label guide the adapter was trained with (prompt_version.txt next to its weights), else None."""
        if self.adapter_dir is None:
            return None
        marker = Path(self.adapter_dir) / "prompt_version.txt"
        if not marker.exists():
            return None
        return marker.read_text(encoding="utf-8").strip() or None

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
        pad_id = tokenizer.pad_token_id if tokenizer.pad_token_id is not None else tokenizer.eos_token_id
        with torch.inference_mode():
            out = model.generate(
                input_ids=input_ids,
                attention_mask=attention,
                max_new_tokens=self.max_new_tokens,
                do_sample=False,
                pad_token_id=pad_id,
            )
        new_tokens = out[0, input_ids.shape[1] :]
        text = tokenizer.decode(new_tokens, skip_special_tokens=True)
        latency_ms = round((time.perf_counter() - started) * 1000, 3)
        return GenResult(text, int(input_ids.shape[1]), int(new_tokens.shape[0]), latency_ms, 0.0, f"local:{device}")
