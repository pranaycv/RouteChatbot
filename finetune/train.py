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
(loss and eval loss per step), run.json (settings, record counts, elapsed seconds, GPU hours).
"""

from __future__ import annotations

import argparse
import json
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


def effective_settings(args: argparse.Namespace, cfg: dict) -> dict:
    lora = dict(cfg["lora"])
    sft = dict(cfg["sft"])
    if args.r is not None:
        lora["r"] = args.r
    if args.lr is not None:
        sft["learning_rate"] = args.lr
    if args.epochs is not None:
        sft["num_train_epochs"] = args.epochs
    sft["seed"] = cfg["seed"]
    return {"base_model": base_model_name(), "lora": lora, "sft": sft, "subset": args.subset}


def train(args: argparse.Namespace) -> dict:
    cfg = load_ft_config()
    settings = effective_settings(args, cfg)
    n_train, n_val = count_records(args.train), count_records(args.val)
    summary = {"settings": settings, "train_records": n_train, "val_records": n_val, "out": str(args.out)}
    if args.dry_run:
        print(json.dumps(summary, indent=2))
        return summary

    import torch
    from datasets import load_dataset
    from peft import LoraConfig
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from trl import SFTConfig, SFTTrainer

    torch.manual_seed(cfg["seed"])
    data = load_dataset("json", data_files={"train": str(args.train), "validation": str(args.val)})
    if args.subset is not None:
        data["train"] = data["train"].shuffle(seed=cfg["seed"]).select(range(min(args.subset, len(data["train"]))))
    tokenizer = AutoTokenizer.from_pretrained(settings["base_model"])
    model = AutoModelForCausalLM.from_pretrained(settings["base_model"], dtype=torch.float16 if torch.cuda.is_available() else torch.float32)
    lora = LoraConfig(**settings["lora"])
    sft = SFTConfig(output_dir=str(args.out), **settings["sft"])
    trainer = SFTTrainer(model=model, args=sft, train_dataset=data["train"], eval_dataset=data["validation"], processing_class=tokenizer, peft_config=lora)

    started = time.perf_counter()
    trainer.train()
    elapsed = time.perf_counter() - started

    args.out.mkdir(parents=True, exist_ok=True)
    trainer.save_model(str(args.out / "final"))
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
    parser.add_argument("--epochs", type=float, default=None)
    parser.add_argument("--subset", type=int, default=None, help="train on the first N shuffled records (learning curve)")
    parser.add_argument("--dry-run", action="store_true", help="print the effective settings and record counts, no training")
    train(parser.parse_args())


if __name__ == "__main__":
    main()
