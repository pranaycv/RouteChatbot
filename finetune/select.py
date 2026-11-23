"""WP9 step 4. Pick the checkpoint with the best joint exact match on the 200 validation records.

    uv run --group finetune python finetune/select.py --run finetune/artifacts/run_r16_lr2e-4 --val finetune/data/val.jsonl
    python finetune/select.py --run ... --val ... --copy-to finetune/artifacts/adapter

Each checkpoint-*/ directory under --run is loaded as a LoRA adapter on the base model and run
through routechat.classifier.predict.classify (the same parser and normalisation as runtime), so
the selection metric is the production metric. Results go to <run>/select_results.json. Never use
the test set here; that is WP10's job.

The metric functions at the top need no torch and are unit-tested.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LABEL_KEYS = ("route_action", "primary_mode", "needs_live_data", "clarify_field", "refusal_reason")


def joint_exact_match(preds: list[dict], golds: list[dict]) -> float:
    """Share of records where all five labels are correct."""
    if not golds:
        return 0.0
    hits = sum(1 for p, g in zip(preds, golds) if all(p.get(k) == g.get(k) for k in LABEL_KEYS))
    return hits / len(golds)


def per_field_accuracy(preds: list[dict], golds: list[dict]) -> dict[str, float]:
    out = {}
    for k in LABEL_KEYS:
        out[k] = sum(1 for p, g in zip(preds, golds) if p.get(k) == g.get(k)) / len(golds) if golds else 0.0
    return out


def macro_f1(preds: list, golds: list) -> float:
    """Macro F1 over the classes present in gold or predictions, without sklearn so the dry path stays light."""
    classes = sorted(set(golds) | set(preds), key=str)
    f1s = []
    for c in classes:
        tp = sum(1 for p, g in zip(preds, golds) if p == c and g == c)
        fp = sum(1 for p, g in zip(preds, golds) if p == c and g != c)
        fn = sum(1 for p, g in zip(preds, golds) if p != c and g == c)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1s.append(2 * precision * recall / (precision + recall) if precision + recall else 0.0)
    return sum(f1s) / len(f1s) if f1s else 0.0


def score(preds: list[dict], golds: list[dict]) -> dict:
    return {
        "joint_exact_match": joint_exact_match(preds, golds),
        "per_field_accuracy": per_field_accuracy(preds, golds),
        "macro_f1": {k: macro_f1([p.get(k) for p in preds], [g[k] for g in golds]) for k in LABEL_KEYS},
        "parse_errors": sum(1 for p in preds if p.get("parse_error")),
        "n": len(golds),
    }


def load_val(path: Path) -> list[dict]:
    """Prepared val.jsonl (prompt-completion) or raw D1 lines; returns the gold labels with the turn."""
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if "prompt" in row and "completion" in row:
            rows.append({"turn": json.loads(row["prompt"][-1]["content"]), "labels": json.loads(row["completion"][0]["content"])})
        else:
            from finetune.prepare import parse_record

            turn, labels = parse_record(row)
            rows.append({"turn": turn.model_dump(), "labels": json.loads(labels.model_dump_json(exclude={"parse_error"}))})
    return rows


def evaluate_checkpoint(checkpoint: Path, val: list[dict]) -> dict:
    from routechat.classifier.backends.hf_local import LocalBackend
    from routechat.classifier.predict import classify
    from routechat.config import get_settings
    from routechat.schemas import UserTurn

    cfg = get_settings().classifier
    backend = LocalBackend(f"ckpt:{checkpoint.name}", cfg.base_model, checkpoint, cfg.max_new_tokens)
    preds = []
    for row in val:
        c = classify(UserTurn.model_validate(row["turn"]), backend=backend, request_id=f"select:{checkpoint.name}")
        preds.append(json.loads(c.model_dump_json()))
    return score(preds, [r["labels"] for r in val])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", type=Path, required=True, help="training output directory with checkpoint-*/")
    parser.add_argument("--val", type=Path, required=True)
    parser.add_argument("--copy-to", type=Path, default=None, help="copy the best checkpoint here (the adapter WP2 loads)")
    args = parser.parse_args()

    val = load_val(args.val)
    checkpoints = sorted(args.run.glob("checkpoint-*"), key=lambda p: int(p.name.split("-")[-1]))
    if (args.run / "final").exists():
        checkpoints.append(args.run / "final")
    if not checkpoints:
        sys.exit(f"no checkpoints under {args.run}")

    results = {}
    for ckpt in checkpoints:
        results[ckpt.name] = evaluate_checkpoint(ckpt, val)
        print(ckpt.name, json.dumps(results[ckpt.name]["joint_exact_match"]))
    best = max(results, key=lambda k: results[k]["joint_exact_match"])
    out = {"best": best, "results": results, "label_counts": dict(Counter(r["labels"]["route_action"] for r in val))}
    (args.run / "select_results.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"best checkpoint: {best} joint exact match {results[best]['joint_exact_match']:.3f}")
    if args.copy_to is not None:
        if args.copy_to.exists():
            shutil.rmtree(args.copy_to)
        shutil.copytree(args.run / best, args.copy_to)
        print(f"copied to {args.copy_to}")


if __name__ == "__main__":
    main()
