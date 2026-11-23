"""Classifier error analysis by test subset (Person C, WP2/WP9): hand-written vs generated records.

    uv run python scripts/classifier_subsets.py --backends prompted_small,prompted_large   # replays fixtures, no API cost
    uv run python scripts/classifier_subsets.py --backends ft,base                        # needs the GPU and the adapter

eval/run_eval.py --classifier reports one number per backend over the whole frozen test set. The
150 hand-written records (ids hw_*) were written by the team, the other 270 by the D1 generator that
also produced the training set, so the split says how much of each backend's agreement is with the
generator's conventions and how much carries over to human requests. Metrics as WP10's: joint exact
match, policy-critical error rate, JSON failure rate, per-field accuracy. Writes
eval/results/classifier_subsets_<split>.csv (one row per backend x prompt version x subset) and merges into it
on reruns so the GPU backends can be added later. --split val is for prompt iteration; test is run once per version.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("FIXTURES", "record")
os.environ.setdefault("PYTHONUTF8", "1")

import pandas as pd  # noqa: E402

from routechat.classifier import predict  # noqa: E402
from routechat.schemas import UserTurn  # noqa: E402

FIELDS = ("route_action", "primary_mode", "needs_live_data", "clarify_field", "refusal_reason")
D1 = ROOT / "data" / "d1"


def policy_critical(gold: str, pred: str) -> bool:
    return (gold in ("refuse", "escalate") and pred != gold) or (gold == "plan" and pred == "refuse")


def run(backend_name: str, rows: list[dict]) -> list[dict]:
    backend = predict.resolve_backend(backend_name)
    per = {"hw": [], "generated": []}
    for r in rows:
        turn = UserTurn(utterance=r["utterance"], turn_index=r.get("turn_index", 0), known=r.get("known"))
        c = predict.classify(turn, backend=backend, request_id=f"subsets:{r['id']}")
        p = json.loads(c.model_dump_json())
        per["hw" if r["id"].startswith("hw_") else "generated"].append((r, p))
    out = []
    for subset, pairs in list(per.items()) + [("all", per["hw"] + per["generated"])]:
        n = len(pairs)
        if n == 0:  # validation has no hand-written records
            continue
        row = {"backend": backend_name, "prompt_version": getattr(backend, "prompt_version", None) or predict.prompt_version(), "subset": subset, "n": n,
               "joint_exact_match": sum(all(p[f] == r[f] for f in FIELDS) for r, p in pairs) / n,
               "policy_critical_error_rate": sum(policy_critical(r["route_action"], p["route_action"]) for r, p in pairs) / n,
               "json_failure_rate": sum(bool(p.get("parse_error")) for r, p in pairs) / n}
        row.update({f"acc_{f}": sum(p[f] == r[f] for r, p in pairs) / n for f in FIELDS})
        out.append(row)
        print(f"{backend_name:<16}{subset:<10} n={n:<4} jem={row['joint_exact_match']:.3f} pce={row['policy_critical_error_rate']:.3f} "
              + " ".join(f"{f[:6]}={row['acc_' + f]:.2f}" for f in FIELDS))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backends", default="prompted_small,prompted_large")
    ap.add_argument("--split", default="test", choices=("val", "test"), help="val for prompt iteration, test once per version")
    a = ap.parse_args()
    rows = [json.loads(l) for l in (D1 / f"{a.split}.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    out = ROOT / "eval" / "results" / f"classifier_subsets_{a.split}.csv"
    results = []
    for b in a.backends.split(","):
        results += run(b.strip(), rows)
    df = pd.DataFrame(results)
    if out.exists():
        old = pd.read_csv(out)
        keys = set(zip(df.backend, df.prompt_version))
        old = old[[k not in keys for k in zip(old.backend, old.prompt_version)]]
        df = pd.concat([old, df], ignore_index=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"wrote {out} ({len(df)} rows)")


if __name__ == "__main__":
    main()
