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
    log_cols = [c for c in ("item_id", "outcome", "checks", "handoff_reason") if c in logs]
    df = gold[["item_id", "expected_outcome"]].merge(logs[log_cols], on="item_id", how="left")
    df["outcome_ok"] = df["outcome"] == df["expected_outcome"]

    # P-09: hiking, transit and combined modes are not in the pilot. When the gold mode is one of them
    # and the system says it is not covered yet, that is the behaviour the policy asks for, so it counts
    # as the right outcome. The column keeps these visible so they can be reported on their own.
    if "gold_constraints" in gold and "handoff_reason" in df:
        mode = gold.set_index("item_id")["gold_constraints"].apply(lambda c: c.get("mode") if isinstance(c, dict) else None)
        df["not_covered_p09"] = (df["item_id"].map(mode).isin(NOT_COVERED_MODES)
                                 & df["outcome"].isin(NOT_COVERED_OUTCOMES)
                                 & df["handoff_reason"].isin(NOT_COVERED_REASONS))
    else:
        df["not_covered_p09"] = False
    df["outcome_ok"] = df["outcome_ok"] | df["not_covered_p09"]

    by_verifier = df["checks"].apply(
        lambda cs: all(c.get("status") == "met" for c in (cs if isinstance(cs, list) else []) if c.get("hard")))
    if truth is not None:
        # Only some routes are inspected by hand (50 in the plan). An inspected route uses the person's
        # answer; any other route falls back to the verifier's statuses and is marked, never failed.
        human = truth.drop_duplicates("item_id").set_index("item_id")["hard_truly_met"]
        checked = df["item_id"].isin(human.index)
        df["hard_truly_met"] = np.where(checked, df["item_id"].map(human).fillna(False).astype(bool), by_verifier)
        df["truth_source"] = np.where(checked, "human", "verifier_only")
    else:
        df["hard_truly_met"] = by_verifier
        df["truth_source"] = "verifier_only"
    df["hard_ok"] = np.where(df["outcome"] == "accept", df["hard_truly_met"], True)

    if judge is not None and len(judge):
        unsup = judge[judge["question"] == "unsupported_claim"].set_index("item_id")["answer"]
        # Clean only when the judge explicitly said "no unsupported claim". A reply the judge could
        # not grade, or skipped, does not count as clean, so a judge failure can't inflate success.
        df["judge_ok"] = df["item_id"].map(unsup).eq(False)
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
        # A failed call has no label. Newer pandas keeps None as NaN even after astype(str), and
        # scikit-learn rejects NaN, so a missing label becomes its own value and simply counts as wrong.
        as_label = lambda col: col.astype(object).where(col.notna(), "<missing>").astype(str)
        g, p = as_label(df[f + "_gold"]), as_label(df[f + "_pred"])
        exact &= (g == p).to_numpy()
        out[f"macro_f1_{f}"] = float(f1_score(g, p, average="macro", zero_division=0))
    out["joint_exact_match"] = float(exact.mean()) if len(df) else float("nan")
    out["policy_critical_error_rate"] = policy_critical_error_rate(
        df["route_action_gold"], df["route_action_pred"])
    return out


def policy_critical_error_rate(gold_action: pd.Series, pred_action: pd.Series) -> float:
    """Gold refuse or escalate predicted as anything else, plus gold plan predicted as refuse."""
    as_label = lambda col: col.astype(object).where(col.notna(), "<missing>").astype(str)
    g, p = as_label(gold_action).to_numpy(), as_label(pred_action).to_numpy()
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
            none = lambda x: x is None or (isinstance(x, float) and np.isnan(x))   # pandas turns a missing value into NaN
            if isinstance(a, (list, tuple, set)) or isinstance(b, (list, tuple, set)):
                return set(map(str, [] if none(a) else a)) == set(map(str, [] if none(b) else b))
            if none(a):
                return none(b)
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
    for stage, g in (stage_logs.groupby("stage") if "stage" in stage_logs else []):  # stubs log no stage lines
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
NOT_COVERED_MODES = ("hike", "transit", "multimodal")                     # P-09: one mode per trip in the pilot
NOT_COVERED_REASONS = ("mode_not_supported", "transit_not_supported")     # codes the map tools put on the route
# Since PR #18 the verifier refuses these without a ticket; older runs show them as a handoff.
NOT_COVERED_OUTCOMES = ("refuse", "handoff")


def judge_agreement(judge: pd.DataFrame, human_a: pd.DataFrame, human_b: pd.DataFrame | None = None) -> pd.DataFrame:
    """Each frame: item_id, question, answer (bool). Cohen's kappa per question; target 0.6 or more.
    human_b is optional: with one grader we report judge vs that person only, and the memo says so."""
    rows = []
    for q in JUDGE_QUESTIONS:
        a = human_a[human_a.question == q].set_index("item_id")["answer"]
        j = judge[judge.question == q].set_index("item_id")["answer"]
        ids = a.index.intersection(j.index)
        b = None
        if human_b is not None:
            b = human_b[human_b.question == q].set_index("item_id")["answer"]
            ids = ids.intersection(b.index)
        def k(x, y):
            x, y = x.loc[ids].astype(int), y.loc[ids].astype(int)
            return float(cohen_kappa_score(x, y)) if len(ids) and (x.nunique() > 1 or y.nunique() > 1) else float("nan")
        rows.append({"question": q, "n": len(ids),
                     "kappa_human_human": k(a, b) if b is not None else float("nan"),
                     "kappa_judge_human_a": k(j, a),
                     "kappa_judge_human_b": k(j, b) if b is not None else float("nan")})
    return pd.DataFrame(rows)
