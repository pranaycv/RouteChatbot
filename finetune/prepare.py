"""WP9 step 2. Convert D1 records into TRL prompt-completion records for SFTTrainer.

    uv run python finetune/prepare.py --in data/d1/train.jsonl --out finetune/data/train.jsonl
    uv run python finetune/prepare.py --in data/d1/val.jsonl   --out finetune/data/val.jsonl

The system prompt and the user serialisation are imported from routechat.classifier.predict, so a
training record is byte-for-byte what the classifier sends at runtime (test T-9-01). Any difference
would teach the model the wrong input.

Accepted D1 line shapes (WP8 may use either):
  {"id": ..., "turn": {"utterance", "turn_index", "known"}, "labels": {five labels}}
  {"id": ..., "utterance": ..., "turn_index": ..., "known": ..., "route_action": ..., ...five labels flat...}
No torch needed; this runs in the core environment.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from routechat.classifier.predict import build_messages, labels_json  # noqa: E402
from routechat.schemas import RouteClassification, UserTurn  # noqa: E402

LABEL_KEYS = ("route_action", "primary_mode", "needs_live_data", "clarify_field", "refusal_reason")


def parse_record(row: dict) -> tuple[UserTurn, RouteClassification]:
    if "turn" in row and "labels" in row:
        return UserTurn.model_validate(row["turn"]), RouteClassification.model_validate(row["labels"])
    turn = UserTurn(utterance=row["utterance"], turn_index=int(row.get("turn_index", 0)), known=row.get("known"))
    labels = row.get("labels") or {k: row[k] for k in LABEL_KEYS}
    return turn, RouteClassification.model_validate(labels)


def to_prompt_completion(turn: UserTurn, labels: RouteClassification) -> dict:
    """One TRL conversational prompt-completion record. The loss is computed on the completion only."""
    return {
        "prompt": build_messages(turn, include_examples=False),
        "completion": [{"role": "assistant", "content": labels_json(labels)}],
    }


def convert(in_path: Path, out_path: Path, limit: int | None = None) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with in_path.open(encoding="utf-8") as src, out_path.open("w", encoding="utf-8") as dst:
        for line in src:
            if not line.strip():
                continue
            turn, labels = parse_record(json.loads(line))
            dst.write(json.dumps(to_prompt_completion(turn, labels), ensure_ascii=False) + "\n")
            n += 1
            if limit is not None and n >= limit:
                break
    return n


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--in", dest="src", required=True, type=Path)
    parser.add_argument("--out", dest="dst", required=True, type=Path)
    parser.add_argument("--limit", type=int, default=None, help="keep only the first N records (learning curve)")
    args = parser.parse_args()
    n = convert(args.src, args.dst, args.limit)
    print(f"wrote {n} records to {args.dst}")


if __name__ == "__main__":
    main()
