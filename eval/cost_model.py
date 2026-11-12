# Owner: Person E (WP10 evaluation / WP11 economics), Team Rosedale, MMAI 894.
# Only Person E edits this file. Other packages may import it but should not change it;
# AI coding tools must not edit this file unless they are working on WP10 or WP11.
# Written for this project with AI assistance (see eval/README.md, "AI use"); no code copied from other sources.
"""WP11 cost model (Part C of the work packages).

Where each number comes from:
  - usage per request (model cost, API calls)  -> the run logs (logs/<run_id>.jsonl)
  - unit prices                                -> config.yaml `prices:` (WP0), read through routechat.config
  - WP11 assumptions (volumes, discounts,      -> eval/cost_assumptions.yaml (this package)
    bigger plans, fixed and error costs, revenue)

config.yaml is not edited: its Settings model forbids unknown keys, so WP11's assumptions live in their
own file. A value left null shows up as NaN in the output and in `missing_inputs`, so an unfinished
assumption can never quietly become zero.

    uv run python -m eval.cost_model --logs logs/<run_id>.jsonl --success eval/results/end_to_end.csv
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import pandas as pd
import yaml

NAN = float("nan")
ASSUMPTIONS_PATH = Path(__file__).resolve().parent / "cost_assumptions.yaml"


def _num(x):
    # null in the assumptions means "not decided yet": keep it as NaN so it shows up instead of counting as 0
    return NAN if x is None else float(x)


def load_assumptions(path: Path = ASSUMPTIONS_PATH) -> dict:
    """WP11's own assumptions."""
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def load_prices():
    """Unit prices from config.yaml (WP0), through the shared typed settings."""
    from routechat.config import get_settings
    return get_settings().prices


def missing_inputs(assumptions: dict) -> list[str]:
    """Every assumption that is still null, so the memo never rests on an unset value."""
    out = []
    def walk(d, prefix=""):
        for k, v in d.items():
            if isinstance(v, dict):
                walk(v, f"{prefix}{k}.")
            elif v is None:
                out.append(prefix + k)
    walk(assumptions)
    return out


def per_request_usage(stage_logs: pd.DataFrame) -> dict:
    """Average per request: model cost (already priced per call in the logs) and API calls by name."""
    n = stage_logs["request_id"].nunique()
    model_cost = stage_logs["cost_usd"].sum() / n if n else NAN
    calls = {}
    for d in stage_logs["api_calls"]:
        for k, v in (d or {}).items():
            calls[k] = calls.get(k, 0) + v
    return {"requests": n, "model_cost_per_request": model_cost,
            "api_calls_per_request": {k: v / n for k, v in calls.items()}}


def mapbox_tiers(price, discounts: list | None) -> list:
    """Build graduated tiers: free up to config's free_monthly, config's `then` price,
    then WP11's volume discounts. A None cap means 'and above'."""
    tiers = [[price.free_monthly, 0.0]]
    for up_to, per_1000 in (discounts or [[None, price.then]]):
        tiers.append([up_to, per_1000])
    tiers.append([None, tiers[-1][1]])   # beyond the last listed tier, keep the last price
    return tiers


def tiered_cost(calls: float, tiers: list) -> float:
    """Graduated pricing: each tier's price applies only to the calls that fall inside it."""
    total, prev = 0.0, 0.0
    for up_to, per_1000 in tiers:
        cap = math.inf if up_to is None else float(up_to)
        n = max(0.0, min(calls, cap) - prev)
        total += n / 1000 * _num(per_1000)
        prev = cap
        if calls <= cap:
            break
    return total


def mapbox_cost(calls_per_request: dict, volume: int, prices, a: dict) -> float:
    """Monthly Mapbox cost at a volume. Calls to the same price line (e.g. both Search Box endpoints)
    are added up first, because Mapbox counts them together against one free tier."""
    by_price: dict[str, float] = {}
    for api, per_req in calls_per_request.items():
        line = a["api_to_price"].get(api)
        if line:
            by_price[line] = by_price.get(line, 0.0) + per_req * volume
    total = 0.0
    for line, calls in by_price.items():
        price = prices.mapbox_usd_per_thousand[line]
        total += tiered_cost(calls, mapbox_tiers(price, a["mapbox_volume_discounts"].get(line)))
    return total


def graphhopper_plan(routes_per_request: float, volume: int, prices, a: dict) -> tuple[str, float]:
    """Cheapest paid plan whose daily credit limit covers the volume. Returns (plan, USD per month).
    The free plan is never chosen: config says it is not for commercial use."""
    gh = prices.graphhopper
    per_day = routes_per_request * volume * gh.credits_per_route_2_to_10_points / a.get("days_per_month", 30)
    plans = [["Basic", gh.basic_eur_per_month, gh.basic_credits_per_day]] + a["graphhopper_more_plans"]
    for name, eur, credits_per_day in plans:
        if per_day <= credits_per_day:
            return name, eur * _num(a.get("eur_to_usd"))
    return "Custom (contact sales)", NAN


def fixed_monthly(a: dict) -> float:
    """Hosting plus the one-off training cost spread over amortise_months."""
    f = a["fixed_monthly_usd"]
    return (_num(f.get("app_hosting")) + _num(f.get("classifier_hosting"))
            + _num(f.get("training_one_off")) / _num(f.get("amortise_months")))


def error_cost_per_request(rates: dict, costs: dict) -> float:
    """Sum over error types of (measured rate per request) x (agreed cost per error)."""
    return sum(_num(rates.get(k)) * _num(v) for k, v in costs.items())


def monthly_table(usage: dict, success_rate: float, error_rates: dict, prices, a: dict) -> pd.DataFrame:
    """One row per monthly volume: variable, fixed and error cost, revenue, net, cost per (successful) request."""
    rows = []
    fixed = fixed_monthly(a)
    err = error_cost_per_request(error_rates, a["error_cost_usd"])
    rev = a["revenue"]
    calls = usage["api_calls_per_request"]
    for v in a["volumes"]:
        plan, gh_cost = graphhopper_plan(calls.get("graphhopper", 0), v, prices, a)
        var = usage["model_cost_per_request"] * v + mapbox_cost(calls, v, prices, a) + gh_cost
        errors = err * v
        accepted = v * _num(error_rates.get("accept_rate", success_rate))
        revenue = accepted * _num(rev["cards_per_accepted_route"]) * _num(rev["usd_per_card"])
        total = var + fixed + errors
        rows.append({"volume_per_month": v, "graphhopper_plan": plan, "variable_cost": var,
                     "fixed_cost": fixed, "error_cost": errors, "total_cost": total,
                     "sponsored_revenue": revenue, "net": revenue - total,
                     "cost_per_request": total / v,
                     "cost_per_successful_request": total / (v * success_rate) if success_rate else NAN})
    return pd.DataFrame(rows)


def status_quo_saving(minutes_manual: float, minutes_agent: float, a: dict) -> float:
    """Value of time saved per trip, compared against cost per request in the decision memo."""
    return (minutes_manual - minutes_agent) / 60 * _num(a["status_quo"]["value_of_time_usd_per_hour"])


def finetune_breakeven(api_cost_per_class: float, ft_cost_per_class: float, a: dict,
                       error_cost_diff_per_class: float = 0.0) -> float:
    """Monthly volume where the fine-tuned classifier pays for itself (WP11 Part C, step 8)."""
    f = a["fixed_monthly_usd"]
    fixed = _num(f.get("classifier_hosting")) + _num(f.get("training_one_off")) / _num(f.get("amortise_months"))
    saving = api_cost_per_class - ft_cost_per_class + error_cost_diff_per_class
    return fixed / saving if saving > 0 else math.inf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", required=True)
    ap.add_argument("--success", help="end_to_end.csv from run_eval (needs a 'success' column)")
    ap.add_argument("--out", default="eval/results/cost_model.csv")
    args = ap.parse_args()
    a = load_assumptions()
    lines = [json.loads(x) for x in Path(args.logs).read_text().splitlines() if x.strip()]
    stage = pd.DataFrame([x for x in lines if x.get("stage") != "request"])
    sr = pd.read_csv(args.success)["success"].mean() if args.success else NAN
    table = monthly_table(per_request_usage(stage), sr, {}, load_prices(), a)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(args.out, index=False)
    print(table.to_string(index=False))
    if missing_inputs(a):
        print("\nStill missing in eval/cost_assumptions.yaml (shown as NaN):", ", ".join(missing_inputs(a)))


if __name__ == "__main__":
    main()
