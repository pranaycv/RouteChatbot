"""Classifier latency of the fine-tuned backend on this machine (Person C, WP2/WP9; handbook "latency on the demo laptop").

    uv run --group finetune python scripts/classifier_latency_bench.py                # the device the backend picks: cuda, else mps, else cpu
    uv run --group finetune python scripts/classifier_latency_bench.py --device cpu   # force the CPU path (fp32, as the backend runs without a GPU)
    uv run --group finetune python scripts/classifier_latency_bench.py --device mps   # Apple GPU on a Mac
    uv run --group finetune python scripts/classifier_latency_bench.py --n 20 --out eval/results/latency_bench_mac.json

Times the deployed adapter (CLASSIFIER_ADAPTER_DIR or finetune/artifacts/adapter) through the normal classify()
path on the first n/2 hand-written and n/2 generated records of the frozen test set, after one warm-up call that is
not timed, and reports p50, min and max seconds per call, the device, dtype, thread count, model load time and the
labels' agreement with gold on the sample. Writes a JSON summary with --out so the number can go next to the others
in the report. --quantize tries the cheap CPU path (adapter merged, PyTorch dynamic int8 on every Linear); on 24 Nov
that was slower than fp32 and its outputs stopped parsing, so it is here for the record.

Measured 24 Nov 2026, 12 records, greedy decoding, about 31 output tokens per call: RTX 3080 fp16 p50 2.8 s;
Intel i7-10700KF (8 cores) fp32 p50 19.9 s; int8 dynamic p50 44.7 s and every output unparseable.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import time
from pathlib import Path

os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("RUN_ID", "latency_bench")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FIELDS = ("route_action", "primary_mode", "needs_live_data", "clarify_field", "refusal_reason")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=12, help="records: n/2 hand-written + n/2 generated, the first of each in the test set")
    ap.add_argument("--device", choices=("auto", "cpu", "mps", "cuda"), default="auto", help="auto = what the backend picks")
    ap.add_argument("--quantize", action="store_true", help="CPU only: merge the adapter and apply dynamic int8 quantisation")
    ap.add_argument("--out", type=Path, default=None, help="write the JSON summary here as well as printing it")
    a = ap.parse_args()

    import torch

    from routechat.classifier import predict
    from routechat.classifier.backends import hf_local
    from routechat.schemas import UserTurn

    if a.device != "auto":
        if a.device == "cuda" and not torch.cuda.is_available():
            sys.exit("--device cuda but no CUDA device is available")
        if a.device == "mps" and not getattr(torch.backends, "mps", None) or (a.device == "mps" and not torch.backends.mps.is_available()):
            sys.exit("--device mps but MPS is not available (needs a Mac with Apple silicon and a torch build with MPS)")
        hf_local.pick_device = lambda: a.device  # load_base() looks it up at call time
    if a.quantize and a.device not in ("cpu",):
        sys.exit("--quantize is a CPU experiment; add --device cpu")

    rows = [json.loads(l) for l in (ROOT / "data" / "d1" / "test.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    hw = [r for r in rows if r["id"].startswith("hw_")][: a.n // 2]
    gen = [r for r in rows if not r["id"].startswith("hw_")][: a.n // 2]
    sample = hw + gen

    backend = predict.resolve_backend("ft")
    t0 = time.perf_counter()
    tokenizer, model, device = hf_local.load_model(backend.model, backend._resolved_adapter())
    load_s = time.perf_counter() - t0
    dtype = str(next(model.parameters()).dtype).replace("torch.", "")
    label = f"{device} {dtype}"
    if a.quantize:
        t0 = time.perf_counter()
        merged = model.merge_and_unload()
        qmodel = torch.ao.quantization.quantize_dynamic(merged, {torch.nn.Linear}, dtype=torch.qint8)
        qmodel.eval()
        hf_local.load_model = lambda model_id, adapter_dir: (tokenizer, qmodel, "cpu")  # the backend looks this up per call
        label = "cpu int8-dynamic (adapter merged)"
        print(f"merge + quantize: {time.perf_counter() - t0:.1f} s", flush=True)
    print(f"device={device} dtype={dtype} threads={torch.get_num_threads()} load={load_s:.1f} s adapter={backend.adapter_dir}", flush=True)

    warm = rows[-1]  # outside the sample, not timed
    predict.classify(UserTurn(utterance=warm["utterance"], turn_index=warm.get("turn_index", 0), known=warm.get("known")), backend=backend, request_id="bench:warm")

    secs, hits, tokens_in = [], 0, []
    for r in sample:
        turn = UserTurn(utterance=r["utterance"], turn_index=r.get("turn_index", 0), known=r.get("known"))
        msgs = predict.build_messages(turn, backend.uses_examples)
        tokens_in.append(len(tokenizer.apply_chat_template(msgs, add_generation_prompt=True, tokenize=True, return_dict=True)["input_ids"]))
        t0 = time.perf_counter()
        c = predict.classify(turn, backend=backend, request_id=f"bench:{r['id']}")
        secs.append(time.perf_counter() - t0)
        p = json.loads(c.model_dump_json())
        ok = all(p[f] == r[f] for f in FIELDS)
        hits += ok
        print(f"  {r['id']:<14} {secs[-1]:6.2f} s  tokens_in={tokens_in[-1]:<5} exact={'y' if ok else 'n'}  {p['route_action']}/{p['primary_mode']}", flush=True)

    summary = {
        "device": label, "machine": platform.platform(), "processor": platform.processor() or platform.machine(),
        "threads": torch.get_num_threads(), "torch": torch.__version__, "adapter": str(backend.adapter_dir), "load_s": round(load_s, 1),
        "n": len(sample), "p50_s": round(statistics.median(secs), 2), "min_s": round(min(secs), 2), "max_s": round(max(secs), 2),
        "tokens_in_mean": round(sum(tokens_in) / len(tokens_in)), "exact_on_sample": hits,
    }
    print(json.dumps(summary, indent=2))
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
