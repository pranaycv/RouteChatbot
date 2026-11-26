"""WP9 step 3. LoRA fine-tune of Qwen2.5-1.5B-Instruct on the prepared D1 records (Kaggle or Colab T4).

    uv run --group finetune python finetune/train.py --train finetune/data/train.jsonl --val finetune/data/val.jsonl --out finetune/artifacts/run_r16_lr2e-4
    python finetune/train.py ... --r 8 --lr 1e-4            # one sweep cell
    python finetune/train.py ... --subset 400 --out ...      # one learning-curve point
    python finetune/train.py ... --dry-run                   # check data and settings, no torch needed

Settings come from finetune/config.yaml (LoraConfig and SFTConfig exactly as the handbook lists
them). Prompt-completion data makes TRL compute the loss on the completion only. Checkpoints are
saved every eval_steps; finetune/select_checkpoint.py then picks the one with the best joint exact match on
validation. Never pass the test set here.

Outputs in --out: checkpoint-*/ (adapter weights per save), final/ (last adapter), train_log.json
(loss and eval loss per step), run.json (settings, record counts, elapsed seconds, GPU hours), and
prompt_version.txt in the run folder, in final/ and in every checkpoint: the label guide the prepared
records embed, read from the marker prepare.py wrote next to the data, so select_checkpoint.py and
predict.py use the same guide the adapter was trained with.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FT_CONFIG = ROOT / "finetune" / "config.yaml"


def load_ft_config() -> dict:
    return yaml.safe_load(FT_CONFIG.read_text(encoding="utf-8"))


def base_model_name() -> str:
    from routechat.config import get_settings

    return get_settings().classifier.base_model


def count_records(path: Path) -> int:
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def warmup_steps_for(n_train: int, sft: dict, warmup_ratio: float) -> int:
    """Warm-up as a whole number of optimizer steps. transformers 5 removed warmup_ratio; steps work everywhere."""
    per_step = int(sft["per_device_train_batch_size"]) * int(sft["gradient_accumulation_steps"])
    steps_per_epoch = max(1, -(-n_train // per_step))
    total = int(round(steps_per_epoch * float(sft["num_train_epochs"])))
    return max(1, int(round(total * warmup_ratio)))


def effective_settings(args: argparse.Namespace, cfg: dict, n_train: int) -> dict:
    lora = dict(cfg["lora"])
    sft = dict(cfg["sft"])
    if args.r is not None:
        lora["r"] = args.r
    if args.lr is not None:
        sft["learning_rate"] = args.lr
    if args.epochs is not None:
        sft["num_train_epochs"] = args.epochs
    if args.max_length is not None:
        sft["max_length"] = args.max_length
    sft["seed"] = cfg["seed"]
    warmup_ratio = float(sft.pop("warmup_ratio", 0.0))
    sft["warmup_steps"] = warmup_steps_for(n_train, sft, warmup_ratio)
    return {"base_model": base_model_name(), "lora": lora, "sft": sft, "warmup_ratio": warmup_ratio, "subset": args.subset}


def data_prompt_version(train_path: Path) -> tuple[str, str]:
    """The label guide the prepared records embed: prepare.py's prompt_version.txt next to them. Older data
    without the marker falls back to the current setting, and says so."""
    from routechat.classifier.predict import prompt_version

    marker = train_path.parent / "prompt_version.txt"
    if marker.exists() and marker.read_text(encoding="utf-8").strip():
        version = marker.read_text(encoding="utf-8").strip()
        if version != prompt_version():
            print(f"note: the prepared data embeds {version}; the current setting is {prompt_version()}. The data wins.", file=sys.stderr)
        return version, str(marker)
    return prompt_version(), "current setting (no prompt_version.txt next to the data; finetune/prepare.py writes it)"


def write_guide_markers(out_dir: Path, version: str) -> list[Path]:
    """prompt_version.txt in the run folder, final/ and every checkpoint-*/ that exists."""
    folders = [out_dir] + [p for p in [out_dir / "final", *sorted(out_dir.glob("checkpoint-*"))] if p.is_dir()]
    written = []
    for folder in folders:
        marker = folder / "prompt_version.txt"
        marker.write_text(version + "\n", encoding="utf-8")
        written.append(marker)
    return written


def longest_record_tokens(train_path: Path, base_model: str) -> int | None:
    """Longest prompt + completion in tokens with the chat template applied, or None when the tokenizer is not
    available offline (the dry run never downloads)."""
    try:
        from transformers import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(base_model, local_files_only=True)
    except Exception:
        return None
    longest = 0
    with train_path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                longest = max(longest, len(tokenizer(tokenizer.apply_chat_template(r["prompt"] + r["completion"], tokenize=False))["input_ids"]))
    return longest


def train(args: argparse.Namespace) -> dict:
    cfg = load_ft_config()
    n_train, n_val = count_records(args.train), count_records(args.val)
    settings = effective_settings(args, cfg, min(n_train, args.subset) if args.subset else n_train)
    version, version_source = data_prompt_version(args.train)
    summary = {
        "settings": settings, "train_records": n_train, "val_records": n_val, "out": str(args.out),
        "prompt_version": version, "prompt_version_source": version_source,
    }
    if args.dry_run:
        # The same check the training path makes, so the notebook sees a too-small max_length before it rents a GPU.
        longest = longest_record_tokens(args.train, settings["base_model"])
        summary["longest_record_tokens"] = longest
        if longest is None:
            summary["longest_record_tokens_note"] = "tokenizer not cached offline; train.py measures before training"
        elif longest > int(settings["sft"]["max_length"]):
            summary["warning"] = f"longest training record is {longest} tokens but max_length is {settings['sft']['max_length']}; training will stop"
        print(json.dumps(summary, indent=2))
        return summary

    # One GPU per process. Kaggle sometimes attaches two T4s, and SFTTrainer refuses nn.DataParallel.
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", "0")
    # Sequence lengths vary from record to record, so the caching allocator fragments and its reserved
    # pool creeps up to the whole card even though one forward/backward peaks at ~6.5 GB (measured,
    # bf16, 1,700 tokens, batch 1). Expandable segments stop the creep.
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    import torch

    # On Windows the driver does not fail an over-full card; it spills into system memory and each
    # step gets 10 to 50 times slower. Capping the allocator makes PyTorch free its cache (or fail
    # fast) instead. GPU_MEMORY_FRACTION overrides; empty disables.
    fraction = os.environ.get("GPU_MEMORY_FRACTION", "0.9" if sys.platform == "win32" else "")
    if fraction and torch.cuda.is_available():
        torch.cuda.set_per_process_memory_fraction(float(fraction))
    from datasets import load_dataset
    from peft import LoraConfig
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from trl import SFTConfig, SFTTrainer

    torch.manual_seed(cfg["seed"])
    data = load_dataset("json", data_files={"train": str(args.train), "validation": str(args.val)})
    if args.subset is not None:
        data["train"] = data["train"].shuffle(seed=cfg["seed"]).select(range(min(args.subset, len(data["train"]))))
    tokenizer = AutoTokenizer.from_pretrained(settings["base_model"])
    # A record longer than max_length loses the end of its completion silently (the handbook's 512 would have
    # dropped every label), so measure first and fail fast. The chat template adds a few tokens per message.
    longest_tokens = max(
        len(tokenizer(tokenizer.apply_chat_template(r["prompt"] + r["completion"], tokenize=False))["input_ids"])
        for r in data["train"]
    )
    if longest_tokens > int(settings["sft"]["max_length"]):
        raise SystemExit(
            f"longest training record is {longest_tokens} tokens but max_length is {settings['sft']['max_length']}: "
            "raise --max-length (or config sft.max_length) or the completions will be truncated away"
        )
    summary["longest_record_tokens"] = longest_tokens
    if args.bf16:
        # Ampere or newer GPU (e.g. an RTX 3080): bf16 base weights and bf16 mixed precision need no
        # gradient scaler and halve the memory of the fp32 path. Kaggle's T4 has no bf16; it takes the path below.
        model = AutoModelForCausalLM.from_pretrained(settings["base_model"], dtype=torch.bfloat16)
        settings["sft"]["fp16"] = False
        settings["sft"]["bf16"] = True
    else:
        # Base weights stay fp32; SFTConfig(fp16=True) runs mixed precision. A fp16 base with fp16 LoRA
        # parameters trips the gradient scaler ("attempting to unscale FP16 gradients"), fp32 does not.
        model = AutoModelForCausalLM.from_pretrained(settings["base_model"])
    lora = LoraConfig(**settings["lora"])
    sft = SFTConfig(output_dir=str(args.out), **settings["sft"])
    trainer = SFTTrainer(model=model, args=sft, train_dataset=data["train"], eval_dataset=data["validation"], processing_class=tokenizer, peft_config=lora)

    from transformers import TrainerCallback

    class GuideMarker(TrainerCallback):
        """prompt_version.txt into each checkpoint as it is saved, so an interrupted run leaves no checkpoint without it."""

        def on_save(self, args_, state, control, **kwargs):
            folder = Path(args_.output_dir) / f"checkpoint-{state.global_step}"
            if folder.is_dir():
                (folder / "prompt_version.txt").write_text(version + "\n", encoding="utf-8")
            return control

    trainer.add_callback(GuideMarker())

    started = time.perf_counter()
    trainer.train(resume_from_checkpoint=str(args.resume_from) if args.resume_from else None)
    elapsed = time.perf_counter() - started

    args.out.mkdir(parents=True, exist_ok=True)
    trainer.save_model(str(args.out / "final"))
    # The guide the prepared records embed, in the run folder, final/ and every checkpoint (also the ones a
    # resumed run inherited), so select_checkpoint.py evaluates and predict.py serves each adapter with that guide.
    write_guide_markers(args.out, version)
    (args.out / "train_log.json").write_text(json.dumps(trainer.state.log_history, indent=2), encoding="utf-8")
    gpu_hours = elapsed / 3600 if torch.cuda.is_available() else 0.0
    summary.update(
        {
            "train_records_used": len(data["train"]),
            "elapsed_s": round(elapsed, 1),
            "gpu_hours": round(gpu_hours, 4),
            "gpu_cost_usd": round(gpu_hours * cfg["cost"]["t4_usd_per_hour"], 4),
            "device": "cuda" if torch.cuda.is_available() else "cpu",
        }
    )
    summary["resumed_from"] = str(args.resume_from) if args.resume_from else None
    (args.out / "run.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--train", type=Path, required=True)
    parser.add_argument("--val", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--r", type=int, default=None, help="LoRA rank override (sweep)")
    parser.add_argument("--lr", type=float, default=None, help="learning rate override (sweep)")
    parser.add_argument("--max-length", type=int, default=None, help="override sft.max_length (a longer label guide needs more)")
    parser.add_argument("--epochs", type=float, default=None)
    parser.add_argument("--subset", type=int, default=None, help="train on the first N shuffled records (learning curve)")
    parser.add_argument("--dry-run", action="store_true", help="print the effective settings and record counts, no training")
    parser.add_argument("--bf16", action="store_true", help="bf16 weights and mixed precision for an Ampere or newer GPU; the T4 path is fp32 weights with fp16")
    parser.add_argument("--resume-from", type=Path, default=None, help="continue an interrupted run from this checkpoint dir (optimizer and scheduler state included); run.json then covers only the resumed segment")
    train(parser.parse_args())


if __name__ == "__main__":
    main()
