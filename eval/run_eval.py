# Owner: Person E (WP10 evaluation / WP11 economics), Team Rosedale, MMAI 894.
# Only Person E edits this file. Other packages may import it but should not change it;
# AI coding tools must not edit this file unless they are working on WP10 or WP11.
# Written for this project with AI assistance (see eval/README.md, "AI use"); no code copied from other sources.
"""WP10 single command.

    FIXTURES=replay uv run python -m eval.run_eval --all

Writes eval/results/*.csv, each with the git commit hash and run_id, as listed in WP10.
Steps that need code from other packages import it lazily. Until that code is merged the step
prints what it is waiting for and is skipped, so this file can be merged in week 1 against stubs.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import uuid
from pathlib import Path

import pandas as pd

from eval import metrics
from eval.log_schema import validate_line

RESULTS = Path("eval/results")
DATASETS = Path("eval/datasets")      # D2, D3, D5 from WP8
D1_TEST = Path("data/d1/test.jsonl")   # frozen classifier test set from WP8 [path to confirm with WP8]
EXTRACTOR_FIELDS = ["origin", "destination", "stops", "mode", "avoid", "prefer", "minimize_elevation",
                    "check_closures", "max_duration_min", "max_distance_km", "arrive_by", "required_accessibility"]


def git_commit() -> str:
    """Short commit hash written into every results file, so each number traces back to the code that made it."""
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "unknown"


def read_jsonl(p: Path) -> list[dict]:
    """Read a JSON-lines file; a missing file gives an empty list so the step can report what it is waiting for."""
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []


def save(df: pd.DataFrame, name: str, run_id: str):
    """Write one results CSV, stamped with run_id and git commit."""
    RESULTS.mkdir(parents=True, exist_ok=True)
    df = df.copy(); df["run_id"] = run_id; df["git_commit"] = git_commit()
    df.to_csv(RESULTS / name, index=False)
    print(f"wrote {RESULTS / name} ({len(df)} rows)")


def run_graph(records: list[dict], dataset: str, run_id: str) -> tuple[Path, list[dict]]:
    """Send each record through the real graph; WP7's logger writes logs/<run_id>.jsonl.

    Returns the log path and, per item, the predicted ConstraintSet. The ConstraintSet is kept out of
    the app log on purpose (the origin can be a street address); it is saved only in the evaluation
    results for that item, and the gold copy stays in the D2 file.

    Uses routechat.graph.invoke, the same entry point the app uses (README, "Plugging in a stage").
    RUN_ID, EVAL_DATASET and EVAL_ITEM_ID are passed as environment variables so WP7's logger can stamp
    the request line without the graph knowing about evaluation [names to agree with WP7].
    """
    from routechat.graph import invoke  # WP0
    os.environ["RUN_ID"] = run_id; os.environ["EVAL_DATASET"] = dataset
    predicted = []
    for r in records:
        os.environ["EVAL_ITEM_ID"] = r["item_id"]
        state = invoke({"request_id": f"{dataset}_{r['item_id']}", "utterance": r["utterance"],
                        "turn_index": r.get("turn_index", 0), "known": r.get("known"),
                        "clarify_count": 0, "revision_count": 0})
        con = _plain(state.get("constraints"))
        predicted.append({"item_id": r["item_id"], "constraints": con,
                          "judge_input": judge_input(r, state)})
    return Path("logs") / f"{run_id}.jsonl", predicted


def _plain(x):
    """Pydantic model or dict -> plain dict (None stays None)."""
    return x.model_dump() if hasattr(x, "model_dump") else x


ROUTE_FACT_FIELDS = ("mode", "distance_m", "duration_s", "duration_traffic_s", "ascend_m", "stops_in_order",
                     "highway_m", "toll_m", "ferry_m", "bike_network_share", "closures")


def judge_input(record: dict, state: dict) -> dict:
    """What the LLM judge sees for one reply (prompts/judge_v1.md): the request, the verifier's checks,
    what had to be mentioned, the facts of the chosen route and the final text. Geometry is left out."""
    report = _plain(state.get("report")) or {}
    final = _plain(state.get("final")) or {}
    chosen = report.get("chosen_route")
    facts = [{k: c.get(k) for k in ROUTE_FACT_FIELDS}
             for c in map(_plain, state.get("candidates") or []) if c and c.get("id") == chosen]
    return {"item_id": record["item_id"], "utterance": record["utterance"],
            "checks": report.get("checks", []), "must_mention": report.get("must_mention", []),
            "route_facts": facts[0] if facts else None, "final_text": final.get("text", "")}


def has_request_lines(req: pd.DataFrame, path: Path, step: str) -> bool:
    """WP7 writes one request line per request; without them there is nothing to score yet."""
    if req.empty or "item_id" not in req:
        print(f"{step}: waiting for WP7 request lines in {path} (graph ran, but nothing was logged)")
        return False
    return True


def load_logs(path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split a run log into request lines and stage lines, warning if any line breaks the agreed schema."""
    lines = read_jsonl(path)
    bad = [(l.get("request_id"), p) for l in lines for p in [validate_line(l)] if p]
    if bad:
        print(f"WARNING: {len(bad)} log lines do not match eval/log_schema.py, first: {bad[0]}")
    req = pd.DataFrame([l for l in lines if l.get("stage") == "request"])
    stg = pd.DataFrame([l for l in lines if l.get("stage") != "request"])
    return req, stg


def step_classifier(run_id: str):
    """Classifier comparison on the frozen D1 test set: every backend, same records, same metrics."""
    gold = pd.DataFrame(read_jsonl(D1_TEST))
    if gold.empty:
        print("classifier: waiting for the frozen D1 test set (WP8)"); return
    try:
        from routechat.classifier.predict import classify  # WP2: classify(turn: UserTurn) -> RouteClassification
        from routechat.schemas import UserTurn
    except ImportError:
        print("classifier: waiting for routechat.classifier.predict (WP2)"); return
    rows = []
    # Backend names follow .env.example (WP0): ft, base, prompted_small, prompted_large.
    for backend in os.environ.get("CLASSIFIER_BACKENDS", "ft,base,prompted_small,prompted_large").split(","):
        os.environ["CLASSIFIER_BACKEND"] = backend
        preds = []
        for r in gold.to_dict("records"):
            t0 = time.perf_counter()
            try:
                out = classify(UserTurn(utterance=r["utterance"], turn_index=r.get("turn_index", 0), known=r.get("known")))
                out = out.model_dump() if hasattr(out, "model_dump") else dict(out); ok = True
            except Exception:
                out, ok = {f: None for f in metrics.CLASSIFIER_FIELDS}, False
            preds.append({"item_id": r["item_id"], **out, "json_ok": ok, "latency_ms": (time.perf_counter() - t0) * 1000})
        p = pd.DataFrame(preds)
        m = metrics.classifier_metrics(p, gold)
        m.update(backend=backend, p50_ms=p.latency_ms.quantile(.5), p95_ms=p.latency_ms.quantile(.95))
        rows.append(m)
    save(pd.DataFrame(rows), "classifier.csv", run_id)


def step_end_to_end(run_id: str, logs_path: Path | None):
    """End-to-end run on D2: trip success, handoff precision/recall, cost per success and latency."""
    gold = pd.DataFrame(read_jsonl(DATASETS / "d2_end_to_end.jsonl"))
    if gold.empty:
        print("end_to_end: waiting for D2 (WP8)"); return
    predicted = None
    if logs_path is None:
        try:
            logs_path, predicted = run_graph(gold.to_dict("records"), "D2", run_id)
        except ImportError:
            print("end_to_end: waiting for routechat.graph (WP0)"); return
    if predicted:
        RESULTS.mkdir(parents=True, exist_ok=True)
        with open(RESULTS / "judge_inputs.jsonl", "w", encoding="utf-8") as f:
            for p in predicted:
                f.write(json.dumps(p["judge_input"], ensure_ascii=False, default=str) + "\n")
        if os.environ.get("RUN_JUDGE") == "1":
            from eval import judge as judge_mod
            judge_mod.run_judge([p["judge_input"] for p in predicted], judge_mod.default_llm()) \
                .to_csv(RESULTS / "judge_answers.csv", index=False)
    req, stg = load_logs(logs_path)
    if not has_request_lines(req, logs_path, "end_to_end"):
        step_extractor(run_id, gold, predicted); return
    truth_p = DATASETS / "d2_truth.csv"   # manual route inspections [file name to agree]
    truth = pd.read_csv(truth_p) if truth_p.exists() else None
    judge_p = RESULTS / "judge_answers.csv"
    judge = pd.read_csv(judge_p) if judge_p.exists() else None
    ts = metrics.trip_success(req, gold, judge, truth)
    save(ts, "end_to_end.csv", run_id)
    summary = {"trip_success_rate": metrics.rate(ts["success"]),
               **metrics.handoff_precision_recall(req, gold),
               **metrics.cost_per_success(req, ts["success"])}
    save(pd.DataFrame([summary]), "end_to_end_summary.csv", run_id)
    save(metrics.latency_table(stg, req), "latency.csv", run_id)
    step_extractor(run_id, gold, predicted)


def step_extractor(run_id: str, gold: pd.DataFrame, predicted: list[dict] | None):
    """Extractor field accuracy on D2. Predicted ConstraintSets come from this run (never from the app
    log); gold ones from the D2 file. Both are written to eval/results, which git ignores."""
    if not predicted or "gold_constraints" not in gold:
        print("extractor: needs a fresh D2 run and gold_constraints in D2 (WP8)"); return
    save(pd.DataFrame(predicted), "predicted_constraints.csv", run_id)
    flat = lambda rows, key: pd.DataFrame([{"item_id": r["item_id"], **(r[key] or {})} for r in rows])
    pred = flat(predicted, "constraints")
    gold_c = flat(gold.to_dict("records"), "gold_constraints")
    fields = [f for f in EXTRACTOR_FIELDS if f in gold_c and f in pred]
    save(metrics.extraction_field_accuracy(pred, gold_c, fields), "extractor.csv", run_id)


def step_adversarial(run_id: str, logs_path: Path | None):
    """Adversarial run on D3: pass rate by attack type and the stage that stopped each attack."""
    gold = pd.DataFrame(read_jsonl(DATASETS / "d3_adversarial.jsonl"))
    if gold.empty:
        print("adversarial: waiting for D3 (WP8)"); return
    if logs_path is None:
        try:
            logs_path, _ = run_graph(gold.to_dict("records"), "D3", run_id)
        except ImportError:
            print("adversarial: waiting for routechat.graph (WP0)"); return
    req, _ = load_logs(logs_path)
    if not has_request_lines(req, logs_path, "adversarial"):
        return
    save(metrics.adversarial_pass_rate(req, gold), "adversarial.csv", run_id)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--classifier", action="store_true")
    ap.add_argument("--e2e", action="store_true")
    ap.add_argument("--adversarial", action="store_true")
    ap.add_argument("--logs", type=Path, help="reuse an existing log file instead of running the graph")
    a = ap.parse_args()
    run_id = f"eval_{time.strftime('%Y%m%d_%H%M')}_{uuid.uuid4().hex[:4]}"
    print("run_id", run_id, "commit", git_commit())
    if a.all or a.classifier:
        step_classifier(run_id)
    if a.all or a.e2e:
        step_end_to_end(run_id, a.logs)
    if a.all or a.adversarial:
        step_adversarial(run_id, a.logs)
    # The LLM judge runs inside the D2 step when RUN_JUDGE=1, or on its own: python -m eval.judge


if __name__ == "__main__":
    main()
