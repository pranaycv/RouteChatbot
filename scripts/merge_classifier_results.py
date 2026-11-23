"""Merge per-backend classifier result files into the one classifier.csv WP10 reads (Person C, WP2/WP9).

    uv run python scripts/merge_classifier_results.py

eval/run_eval.py --classifier writes eval/results/classifier.csv for whichever backends ran. When the
backends were run in separate invocations (prompted on the API, base and ft on the GPU), each run's
file was copied aside as classifier_<group>.csv. This script stacks them, and for backends whose
responses were replayed from routechat/fixtures/llm (FIXTURES=record on a rerun) it replaces the
wall-clock p50/p95 with the latency recorded at the original live call, so the latency columns
describe the model and not the cache.
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from routechat.classifier import predict  # noqa: E402
from routechat.llm import FIXTURE_DIR, fixture_key  # noqa: E402
from routechat.schemas import UserTurn  # noqa: E402

RESULTS = ROOT / "eval" / "results"
PARTS = ("prompted", "base", "ft")
D1_TEST = ROOT / "data" / "d1" / "test.jsonl"


def recorded_latencies(backend_name: str) -> list[float]:
    """Latency of the first live call for every test record, read from the recorded fixtures."""
    backend = predict.resolve_backend(backend_name)
    rows = [json.loads(l) for l in D1_TEST.read_text(encoding="utf-8").splitlines() if l.strip()]
    out = []
    for r in rows:
        turn = UserTurn(utterance=r["utterance"], turn_index=r.get("turn_index", 0), known=r.get("known"))
        key = fixture_key(backend.model, backend.temperature, predict.build_messages(turn, backend.uses_examples), None)
        path = FIXTURE_DIR / f"{key}.json"
        if path.exists():
            out.append(float(json.loads(path.read_text(encoding="utf-8")).get("latency_ms", 0.0)))
    return out


def percentile(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    s = sorted(values)
    k = (len(s) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def main() -> None:
    frames = []
    for part in PARTS:
        p = RESULTS / f"classifier_{part}.csv"
        if p.exists():
            frames.append(pd.read_csv(p))
            print(f"read {p.name}")
        else:
            print(f"missing {p.name} (skipped)")
    if not frames:
        sys.exit("nothing to merge")
    df = pd.concat(frames, ignore_index=True).drop_duplicates("backend", keep="last")

    for name in ("prompted_small", "prompted_large"):
        if name in set(df.backend):
            lat = recorded_latencies(name)
            if len(lat) >= 0.9 * 420:
                live = [x for x in lat if x > 0]
                df.loc[df.backend == name, "p50_ms"] = percentile(live, 0.5)
                df.loc[df.backend == name, "p95_ms"] = percentile(live, 0.95)
                df.loc[df.backend == name, "latency_source"] = f"recorded at live call ({len(live)} calls)"
    df["latency_source"] = df.get("latency_source", pd.Series(dtype=str)).fillna("wall clock during the run")
    df["run_id"] = "d1_final"

    order = {"ft": 0, "base": 1, "prompted_small": 2, "prompted_large": 3}
    df = df.sort_values("backend", key=lambda s: s.map(order)).reset_index(drop=True)
    out = RESULTS / "classifier.csv"
    df.to_csv(out, index=False)
    print(f"wrote {out} ({len(df)} rows)")
    cols = ["backend", "joint_exact_match", "policy_critical_error_rate", "json_failure_rate", "macro_f1_route_action", "p50_ms", "p95_ms", "cost_per_1000_calls_usd"]
    print(df[cols].round(3).to_string(index=False))


if __name__ == "__main__":
    main()
