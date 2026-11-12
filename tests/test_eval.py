# Owner: Person E (WP10 evaluation / WP11 economics), Team Rosedale, MMAI 894.
# Only Person E edits this file. Other packages may import it but should not change it;
# AI coding tools must not edit this file unless they are working on WP10 or WP11.
# Written for this project with AI assistance (see eval/README.md, "AI use"); no code copied from other sources.
"""Tests for WP10/WP11 helpers on small hand-made data (no graph, no network)."""
import math
import sys
from pathlib import Path

import pandas as pd

# eval/ sits at the repo root and is not part of the installed routechat package, so add the root
# to the path here instead of changing pyproject.toml (WP0's file).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval import cost_model, metrics  # noqa: E402
from eval.log_schema import outcome_from_state, validate_line  # noqa: E402


def stage_line(**kw):
    base = {"request_id": "r1", "stage": "maps", "latency_ms": 742, "tokens_in": 0, "tokens_out": 0,
            "cost_usd": 0.0021, "api_calls": {"searchbox": 3, "graphhopper": 1, "directions": 1},
            "decision": "ok", "ts": "2026-11-12T15:04:11Z"}
    base.update(kw); return base


def request_line(item, outcome, checks=None, cost=0.01, lat=3000):
    return {"request_id": f"r_{item}", "stage": "request", "run_id": "t", "git_commit": "abc", "dataset": "D2",
            "item_id": item, "turn_index": 0, "outcome": outcome, "route_action": "plan", "primary_mode": "bike",
            "revision_count": 0, "checks": checks or [], "must_mention": [], "mentioned_ids": [],
            "used_fallback_template": False, "approved": True, "sponsored_shown": False, "handoff_reason": "",
            "stopped_at": "", "latency_ms_total": lat, "cost_usd_total": cost, "ts": "2026-11-12T15:04:11Z"}


def test_T_10_01_work_package_example_line_is_valid():
    assert validate_line(stage_line()) == []


def test_T_10_02_missing_and_unknown_fields_are_reported():
    bad = stage_line(stage="mapz"); del bad["tokens_in"]
    probs = validate_line(bad)
    assert any("tokens_in" in p for p in probs) and any("mapz" in p for p in probs)


def test_T_10_03_outcome_follows_routing_table():
    assert outcome_from_state({"guard": {"decision": "refuse"}}) == "refuse"
    assert outcome_from_state({"guard": {"decision": "allow"}, "classification": {"route_action": "clarify"}}) == "clarify"
    assert outcome_from_state({"guard": {"decision": "allow"}, "classification": {"route_action": "clarify"},
                               "clarify_count": 1}) == "handoff"
    assert outcome_from_state({"guard": {"decision": "allow"}, "classification": {"route_action": "plan"},
                               "constraints": {"missing_field": "destination"}}) == "clarify"
    assert outcome_from_state({"guard": {"decision": "allow"}, "classification": {"route_action": "plan"},
                               "constraints": {}, "report": {"decision": "accept"}}) == "accept"


def test_T_10_04_trip_success_needs_outcome_truth_and_judge():
    met = [{"id": "highways", "status": "met", "hard": True}]
    logs = pd.DataFrame([request_line("a", "accept", met), request_line("b", "accept", met),
                         request_line("c", "refuse"), request_line("d", "accept", met)])
    gold = pd.DataFrame({"item_id": list("abcd"), "expected_outcome": ["accept", "accept", "refuse", "clarify"]})
    truth = pd.DataFrame({"item_id": ["a", "b"], "hard_truly_met": [True, False]})
    judge = pd.DataFrame({"item_id": ["a"], "question": ["unsupported_claim"], "answer": [False]})
    ts = metrics.trip_success(logs, gold, judge, truth)
    assert ts.set_index("item_id")["success"].to_dict() == {"a": True, "b": False, "c": True, "d": False}


def test_T_10_05_policy_critical_errors():
    g = pd.Series(["refuse", "escalate", "plan", "plan", "refuse"])
    p = pd.Series(["refuse", "plan", "refuse", "plan", "clarify"])
    assert metrics.policy_critical_error_rate(g, p) == 3 / 5


def test_T_10_06_handoff_precision_recall():
    logs = pd.DataFrame([request_line("a", "handoff"), request_line("b", "handoff"), request_line("c", "accept")])
    gold = pd.DataFrame({"item_id": list("abc"), "expected_outcome": ["handoff", "accept", "handoff"]})
    m = metrics.handoff_precision_recall(logs, gold)
    assert m["precision"] == 0.5 and m["recall"] == 0.5


def test_T_11_01_tiered_prices_use_config_and_discounts():
    prices, a = cost_model.load_prices(), cost_model.load_assumptions()
    d = prices.mapbox_usd_per_thousand["directions"]
    tiers = cost_model.mapbox_tiers(d, a["mapbox_volume_discounts"]["directions"])
    assert cost_model.tiered_cost(d.free_monthly, tiers) == 0
    # 1M calls: 100k free, 400k at $2.00, 500k at $1.60 = 800 + 800
    assert math.isclose(cost_model.tiered_cost(1_000_000, tiers), 1600.0)
    assert "eur_to_usd" in cost_model.missing_inputs(a)


def test_T_11_02_graphhopper_plan_by_volume():
    prices, a = cost_model.load_prices(), cost_model.load_assumptions()
    a["eur_to_usd"] = 1.0
    assert cost_model.graphhopper_plan(1, 1000, prices, a) == ("Basic", 69.0)        # 33 credits/day
    assert cost_model.graphhopper_plan(1, 1_000_000, prices, a) == ("Premium", 479.0)  # 33,333 credits/day
    assert cost_model.graphhopper_plan(2, 1_000_000, prices, a)[0].startswith("Custom")


def test_T_11_03_monthly_table_shape_and_breakeven():
    prices, a = cost_model.load_prices(), cost_model.load_assumptions()
    stage = pd.DataFrame([stage_line(request_id="r1"),
                          stage_line(request_id="r2", stage="extract", api_calls={}, cost_usd=0.0009)])
    t = cost_model.monthly_table(cost_model.per_request_usage(stage), 0.8, {}, prices, a)
    assert list(t["volume_per_month"]) == [1000, 100000, 1000000]
    assert t["total_cost"].isna().all()   # unpriced assumptions stay NaN, never zero
    a["fixed_monthly_usd"].update(classifier_hosting=30, training_one_off=12, amortise_months=12)
    assert math.isclose(cost_model.finetune_breakeven(0.0004, 0.0001, a), 31 / 0.0003)


def test_T_10_07_log_schema_accepts_graph_failure_line():
    # graph._log_failure writes error_type (may be null) and no ts; only the missing ts is reported.
    line = {"request_id": "r1", "stage": "maps", "latency_ms": 3.0, "tokens_in": 0, "tokens_out": 0,
            "cost_usd": 0.0, "api_calls": {}, "decision": "system_error", "error_type": None}
    assert validate_line(line) == ["missing ts"]
