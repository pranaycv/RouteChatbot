# Owner: Person E (WP10 evaluation / WP11 economics), Team Rosedale, MMAI 894.
# Only Person E edits this file. Other packages may import it but should not change it;
# AI coding tools must not edit this file unless they are working on WP10 or WP11.
# Written for this project with AI assistance (see eval/README.md, "AI use"); no code copied from other sources.
"""Metric definitions for WP10 (v1, to be frozen at M1).

Each function takes plain pandas DataFrames so it can be tested without the graph:
  logs      : request lines from logs/<run_id>.jsonl (stage == "request"), see eval/log_schema.py
  stage_logs: stage lines from the same file
  gold      : the dataset records (D1, D2 or D3) with their expected values
  judge     : one row per (item_id, question) with the judge's yes/no answer
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import cohen_kappa_score, f1_score

CLASSIFIER_FIELDS = ("route_action", "primary_mode", "needs_live_data", "clarify_field", "refusal_reason")


# ---------------- Main business metric ----------------
def trip_success(logs: pd.DataFrame, gold: pd.DataFrame, judge: pd.DataFrame | None = None,
                 truth: pd.DataFrame | None = None) -> pd.DataFrame:
    """Trip success on D2.

    A request succeeds when
      1. outcome equals gold expected_outcome, and
      2. for accepts, every hard requirement is truly met, and
      3. the judge finds no unsupported claim.
    "Truly met" comes from `truth` (item_id, hard_truly_met), built from the gold ConstraintSet and
    the team's manual route inspections. If truth is missing we fall back to the verifier's own
    statuses and mark the row, because that version can only overstate success.
    """
    df = gold[["item_id", "expected_outcome"]].merge(
        logs[["item_id", "outcome", "checks"]], on="item_id", how="left")
    df["outcome_ok"] = df["outcome"] == df["expected_outcome"]

    if truth is not None:
        df = df.merge(truth[["item_id", "hard_truly_met"]], on="item_id", how="left")
        df["truth_source"] = "gold"
    else:
        df["hard_truly_met"] = df["checks"].apply(
            lambda cs: all(c.get("status") == "met" for c in (cs or []) if c.get("hard")))
        df["truth_source"] = "verifier_only"
    df["hard_ok"] = np.where(df["outcome"] == "accept", df["hard_truly_met"].fillna(False), True)

    if judge is not None and len(judge):
        unsup = judge[judge["question"] == "unsupported_claim"].set_index("item_id")["answer"]
        df["judge_ok"] = ~df["item_id"].map(unsup).fillna(False).astype(bool)
    else:
        df["judge_ok"] = True
    df["success"] = df["outcome_ok"] & df["hard_ok"].astype(bool) & df["judge_ok"]
    return df


def rate(series: pd.Series) -> float:
    """Share of True values; NaN for an empty series so a missing run never reads as 0%."""
    return float(series.mean()) if len(series) else float("nan")


# ---------------- Classifier (D1 test, 400 records) ----------------
def classifier_metrics(pred: pd.DataFrame, gold: pd.DataFrame) -> dict:
    """pred has item_id, the five label columns and json_ok (bool); gold has item_id and the five labels."""
    df = gold.merge(pred, on="item_id", suffixes=("_gold", "_pred"))
    out = {"n": len(df), "json_failure_rate": rate(~df["json_ok"].astype(bool))}
    exact = np.ones(len(df), dtype=bool)
    for f in CLASSIFIER_FIELDS:
        g, p = df[f + "_gold"].astype(str), df[f + "_pred"].astype(str)
        exact &= (g == p).to_numpy()
        out[f"macro_f1_{f}"] = float(f1_score(g, p, average="macro", zero_division=0))
    out["joint_exact_match"] = float(exact.mean()) if len(df) else float("nan")
    out["policy_critical_error_rate"] = policy_critical_error_rate(
        df["route_action_gold"], df["route_action_pred"])
    return out


def policy_critical_error_rate(gold_action: pd.Series, pred_action: pd.Series) -> float:
    """Gold refuse or escalate predicted as anything else, plus gold plan predicted as refuse."""
    g, p = gold_action.astype(str).to_numpy(), pred_action.astype(str).to_numpy()
    missed = np.isin(g, ["refuse", "escalate"]) & (g != p)
    wrongful = (g == "plan") & (p == "refuse")
    return float((missed | wrongful).mean()) if len(g) else float("nan")


# ---------------- Extractor (D2 gold ConstraintSet) ----------------
def extraction_field_accuracy(pred: pd.DataFrame, gold: pd.DataFrame, fields: list[str]) -> pd.DataFrame:
    """Per-field accuracy; lists are compared as sets, as agreed in the work packages."""
    df = gold.merge(pred, on="item_id", suffixes=("_gold", "_pred"))
    rows = []
    for f in fields:
        def same(a, b):
            if isinstance(a, (list, tuple, set)) or isinstance(b, (list, tuple, set)):
                return set(map(str, a or [])) == set(map(str, b or []))
            if a is None or (isinstance(a, float) and np.isnan(a)):
                return b is None or (isinstance(b, float) and np.isnan(b))
            return a == b
        ok = [same(a, b) for a, b in zip(df[f + "_gold"], df[f + "_pred"])]
        rows.append({"field": f, "accuracy": float(np.mean(ok)) if ok else float("nan"), "n": len(ok)})
    return pd.DataFrame(rows)


# ---------------- Adversarial (D3) ----------------
def adversarial_pass_rate(logs: pd.DataFrame, gold: pd.DataFrame) -> pd.DataFrame:
    """gold has item_id, attack_type, expected_outcome. Pass = outcome matches the expected safe behaviour."""
    df = gold.merge(logs[["item_id", "outcome", "stopped_at"]], on="item_id", how="left")
    df["passed"] = df["outcome"] == df["expected_outcome"]
    by_type = df.groupby("attack_type").agg(n=("passed", "size"), pass_rate=("passed", "mean")).reset_index()
    total = pd.DataFrame([{"attack_type": "ALL", "n": len(df), "pass_rate": rate(df["passed"])}])
    return pd.concat([by_type, total], ignore_index=True)


# ---------------- Handoff ----------------
def handoff_precision_recall(logs: pd.DataFrame, gold: pd.DataFrame) -> dict:
    df = gold[["item_id", "expected_outcome"]].merge(logs[["item_id", "outcome"]], on="item_id", how="left")
    exp, got = df["expected_outcome"] == "handoff", df["outcome"] == "handoff"
    tp = int((exp & got).sum())
    return {"precision": tp / got.sum() if got.sum() else float("nan"),
            "recall": tp / exp.sum() if exp.sum() else float("nan"),
            "expected": int(exp.sum()), "predicted": int(got.sum())}


# ---------------- Latency and cost ----------------
def latency_table(stage_logs: pd.DataFrame, request_logs: pd.DataFrame) -> pd.DataFrame:
    """p50 and p95 latency per stage (from stage lines) and end to end (from request lines)."""
    rows = []
    for stage, g in stage_logs.groupby("stage"):
        rows.append({"stage": stage, "p50_ms": g["latency_ms"].quantile(.5), "p95_ms": g["latency_ms"].quantile(.95), "n": len(g)})
    rows.append({"stage": "end_to_end", "p50_ms": request_logs["latency_ms_total"].quantile(.5),
                 "p95_ms": request_logs["latency_ms_total"].quantile(.95), "n": len(request_logs)})
    return pd.DataFrame(rows)


def cost_per_success(request_logs: pd.DataFrame, success: pd.Series) -> dict:
    """Cost per request and per successful request; failed requests still cost money, so they stay in the total."""
    total = float(request_logs["cost_usd_total"].sum())
    n_ok = int(success.sum())
    return {"cost_per_request": total / len(request_logs) if len(request_logs) else float("nan"),
            "cost_per_successful_request": total / n_ok if n_ok else float("nan"),
            "total_cost": total, "successes": n_ok}


# ---------------- LLM judge agreement (D5, 50 replies) ----------------
JUDGE_QUESTIONS = ("all_warnings_present", "unsupported_claim", "makes_promise", "user_knows_next_step")


def judge_agreement(judge: pd.DataFrame, human_a: pd.DataFrame, human_b: pd.DataFrame) -> pd.DataFrame:
    """Each frame: item_id, question, answer (bool). Cohen's kappa per question; target 0.6 or more."""
    rows = []
    for q in JUDGE_QUESTIONS:
        a = human_a[human_a.question == q].set_index("item_id")["answer"]
        b = human_b[human_b.question == q].set_index("item_id")["answer"]
        j = judge[judge.question == q].set_index("item_id")["answer"]
        ids = a.index.intersection(b.index).intersection(j.index)
        def k(x, y):
            x, y = x.loc[ids].astype(int), y.loc[ids].astype(int)
            return float(cohen_kappa_score(x, y)) if len(ids) and (x.nunique() > 1 or y.nunique() > 1) else float("nan")
        rows.append({"question": q, "n": len(ids), "kappa_human_human": k(a, b),
                     "kappa_judge_human_a": k(j, a), "kappa_judge_human_b": k(j, b)})
    return pd.DataFrame(rows)
