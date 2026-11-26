"""WP3 extractor accuracy on D2 and the model ablation (Person C).

    uv run python scripts/extractor_eval.py                      # extractor_v1 with the configured model on D2
    uv run python scripts/extractor_eval.py --ablation           # also gpt-4.1-nano, gpt-4.1-mini, and a reasoning model at low and medium
    uv run python scripts/extractor_eval.py --limit 20           # smoke run

Reads eval/datasets/d2_end_to_end.jsonl (WP8). Every record has gold_constraints, null for refusals;
only records with a gold ConstraintSet are scored, since a refused request never reaches the
extractor. The classifier labels given to the extractor come from the record when it carries them
("labels", "classification" or "expected_classification"); otherwise route_action is plan and the
mode is the gold form's mode, so the ablation measures extraction and not classification.

Metrics (handbook WP3 "numbers you report"): accuracy per field against gold, lists compared as
sets; exact match of the whole form; invented-value rate, the share of records where origin,
destination or a stop query is a place string that does not occur in the text or in known;
latency and cost from the stage log.

Outputs: eval/results/extractor_ablation.csv (one row per model variant; eval/run_eval.py writes its own
extractor.csv with different columns, so the two never overwrite each other) and
eval/results/extractor_predictions.jsonl (every prediction next to its gold, for error analysis).
Runs with FIXTURES=record so a rerun replays what was already paid for.

Two scores per field. Strict: normalised string equality (case and surrounding space; lists as sets; stops as
(query, category, required)). Lenient, the *_lenient columns: for origin, destination and stop queries a leading
article is ignored and spelling is tolerated (difflib ratio >= 0.85, so "Scoatiabank Arena" matches "Scotiabank
Arena"); stops still have to agree on category and required. The extractor copies the user's spelling by design
and the D2 gold carries canonical place names (kept for the map stage, agreed with WP8 on 24 Nov), so the lenient
score is the headline for the three place fields and strict is reported next to it. Until 24 Nov the lenient
column also accepted a loop destination that repeated the origin and compared stops by query only, because D2
used its own stop vocabulary; D2 follows the ConstraintSet semantics since WP8 commit bf99db7 (loop destination
null, named stop category null, generic stop query == category == type word, ev_charging_station), so both
allowances are gone and the two columns differ by spelling and articles only.

EXTRACTOR_PROMPT_VERSION=extractor_v2 scores another prompt version; --append merges its rows into the existing
CSV and predictions (keyed by variant and prompt_version) instead of overwriting them.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("FIXTURES", "record")
os.environ.setdefault("PYTHONUTF8", "1")

from routechat.agents import extractor  # noqa: E402
from routechat.config import get_settings  # noqa: E402
from routechat.schemas import ConstraintSet, RouteClassification  # noqa: E402

D2 = ROOT / "eval" / "datasets" / "d2_end_to_end.jsonl"
RESULTS = ROOT / "eval" / "results"
FIELDS = [
    "origin", "destination", "stops", "mode", "modes_to_compare", "compare_threshold_min", "avoid", "prefer",
    "minimize_elevation", "check_closures", "max_duration_min", "max_distance_km", "loop", "target_distance_km",
    "depart_at", "arrive_by", "required_accessibility",
]
LIST_FIELDS = {"stops", "modes_to_compare", "avoid", "prefer"}
# Public list prices, USD per million tokens, for models not in config.yaml (WP11 owns config prices).
EXTRA_PRICES = {"gpt-5-mini": (0.25, 2.00), "gpt-5-nano": (0.05, 0.40), "o4-mini": (1.10, 4.40)}


def norm(field: str, value):
    if field == "stops":
        stops = [s.model_dump() if hasattr(s, "model_dump") else s for s in (value or [])]
        return sorted((s["query"].strip().lower(), s.get("category"), bool(s.get("required", True))) for s in stops)
    if field in LIST_FIELDS:
        return sorted(value or [])
    if isinstance(value, str):
        return value.strip().lower()
    return value


_ARTICLE = re.compile(r"^(the|a|an)\s+", re.I)


def place_key(value) -> str:
    return _ARTICLE.sub("", (value or "").strip().lower()).rstrip(".")


def places_match(a, b) -> bool:
    """Article-insensitive and spelling-tolerant equality of two place strings (both empty counts as a match)."""
    ka, kb = place_key(a), place_key(b)
    if ka == kb:
        return True
    if not ka or not kb:
        return False
    return difflib.SequenceMatcher(None, ka, kb).ratio() >= 0.85


def lenient_hit(field: str, pred: ConstraintSet, gold: ConstraintSet) -> bool:
    if field in ("origin", "destination"):
        return places_match(getattr(pred, field), getattr(gold, field))
    if field == "stops":
        # Same number of stops, and every gold stop matched to a distinct predicted stop with the same category
        # and required flag and an article- and spelling-tolerant query.
        if len(pred.stops) != len(gold.stops):
            return False

        def same_stop(s, g) -> bool:
            return (s.category or "").strip().lower() == (g.category or "").strip().lower() and bool(s.required) == bool(g.required) and places_match(s.query, g.query)

        used: set[int] = set()
        for g in gold.stops:
            match = next((i for i, s in enumerate(pred.stops) if i not in used and same_stop(s, g)), None)
            if match is None:
                return False
            used.add(match)
        return True
    return norm(field, getattr(pred, field)) == norm(field, getattr(gold, field))


def classification_for(record: dict, gold: ConstraintSet) -> RouteClassification:
    for key in ("labels", "classification", "expected_classification"):
        if isinstance(record.get(key), dict):
            try:
                return RouteClassification.model_validate({k: v for k, v in record[key].items() if k in RouteClassification.model_fields})
            except Exception:
                pass
    return RouteClassification(route_action="plan", primary_mode=gold.mode, needs_live_data=False, clarify_field="none", refusal_reason="none")


def invented(pred: ConstraintSet, text: str, known: ConstraintSet | None) -> bool:
    haystack = text.lower() + (" " + known.model_dump_json().lower() if known else "")
    places = [pred.origin, pred.destination] + [s.query for s in pred.stops if s.category is None]
    for p in places:
        if p and p.strip().lower() not in haystack:
            core = p.lower().replace("the ", "").replace(", toronto", "").replace(", montreal", "").replace(", ottawa", "").strip()
            if core not in haystack:
                return True
    return False


def run_variant(name: str, model: str, effort: str | None, records: list[dict], writer) -> dict:
    """Run the extractor on every scorable record with one model setting; return the summary row."""
    settings = get_settings()
    per_field = {f: 0 for f in FIELDS}
    per_field_lenient = {f: 0 for f in FIELDS}
    exact = 0
    exact_lenient = 0
    invent = 0
    unparseable = 0
    latencies, costs, tokens_in, tokens_out = [], [], [], []
    scored = 0
    for r in records:
        gold = ConstraintSet.model_validate(r["gold_constraints"])
        known = ConstraintSet.model_validate(r["known"]) if r.get("known") else None
        cls = classification_for(r, gold)
        text = r.get("clean_text") or r.get("utterance")
        messages = extractor.build_messages(text, cls, known)
        t0 = time.perf_counter()
        from routechat.llm import complete

        result = complete(messages, model, settings.extractor.temperature, schema=ConstraintSet, reasoning_effort=effort)
        try:
            parsed = ConstraintSet.model_validate_json(result.text)
        except Exception:
            retry = messages + [{"role": "assistant", "content": result.text}, {"role": "user", "content": "That output failed validation. Return the corrected JSON object only."}]
            result2 = complete(retry, model, settings.extractor.temperature, schema=ConstraintSet, reasoning_effort=effort)
            try:
                parsed = ConstraintSet.model_validate_json(result2.text)
            except Exception:
                parsed = extractor.unparseable_constraints(cls)
                unparseable += 1
            result = result2
        pred = extractor.postprocess(parsed, cls)
        latency = result.latency_ms if result.provider == "replay" else (time.perf_counter() - t0) * 1000
        latencies.append(latency)
        price = settings.prices.openai_usd_per_million_tokens.get(model)
        if price is not None:
            cost = result.tokens_in * price.input / 1e6 + result.tokens_out * price.output / 1e6
        else:
            pin, pout = EXTRA_PRICES.get(model, (0.0, 0.0))
            cost = result.tokens_in * pin / 1e6 + result.tokens_out * pout / 1e6
        costs.append(cost)
        tokens_in.append(result.tokens_in)
        tokens_out.append(result.tokens_out)

        hits = {f: norm(f, getattr(pred, f)) == norm(f, getattr(gold, f)) for f in FIELDS}
        hits_lenient = {f: hits[f] or lenient_hit(f, pred, gold) for f in FIELDS}
        for f, h in hits.items():
            per_field[f] += h
        for f, h in hits_lenient.items():
            per_field_lenient[f] += h
        exact += all(hits.values())
        exact_lenient += all(hits_lenient.values())
        inv = invented(pred, text, known)
        invent += inv
        scored += 1
        writer.write(json.dumps({"variant": name, "item_id": r.get("item_id") or r.get("id"), "text": text, "gold": gold.model_dump(), "pred": pred.model_dump(), "field_hits": hits, "field_hits_lenient": hits_lenient, "invented": inv, "prompt_version": extractor.prompt_version()}, ensure_ascii=False) + "\n")

    n = max(scored, 1)
    row = {"variant": name, "model": model, "reasoning_effort": effort or "", "n": scored, "exact_match": exact / n, "exact_match_lenient": exact_lenient / n, "invented_value_rate": invent / n, "unparseable": unparseable}
    row.update({f"acc_{f}": per_field[f] / n for f in FIELDS})
    row.update({f"acc_{f}_lenient": per_field_lenient[f] / n for f in ("origin", "destination", "stops")})
    row.update({
        "p50_ms": statistics.median(latencies) if latencies else float("nan"),
        "p95_ms": sorted(latencies)[int(0.95 * (len(latencies) - 1))] if latencies else float("nan"),
        "cost_per_1000_calls_usd": 1000 * sum(costs) / n,
        "tokens_in_per_call": sum(tokens_in) / n,
        "tokens_out_per_call": sum(tokens_out) / n,
    })
    print(f"{name:<28} n={scored:<4} exact={row['exact_match']:.3f} lenient={row['exact_match_lenient']:.3f} invented={row['invented_value_rate']:.3f} p50={row['p50_ms']:.0f}ms cost/1k=${row['cost_per_1000_calls_usd']:.2f}")
    return row


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ablation", action="store_true", help="also run nano, mini, and the reasoning model at low and medium")
    ap.add_argument("--reasoning-model", default="gpt-5-mini")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--d2", type=Path, default=D2)
    ap.add_argument("--append", action="store_true", help="merge into the existing extractor_ablation.csv and predictions (keyed by variant and prompt_version) instead of overwriting")
    a = ap.parse_args()

    if not a.d2.exists():
        sys.exit(f"{a.d2} not found: waiting for WP8's D2")
    records = [json.loads(l) for l in a.d2.read_text(encoding="utf-8").splitlines() if l.strip()]
    scorable = [r for r in records if r.get("gold_constraints")]
    if a.limit:
        scorable = scorable[: a.limit]
    print(f"D2: {len(records)} records, {len(scorable)} with a gold ConstraintSet")

    configured = get_settings().extractor.model
    version = extractor.prompt_version()
    variants = [(f"{version}/" + configured, configured, None)]
    if a.ablation:
        for m in ("gpt-4.1-nano", "gpt-4.1-mini"):
            if m != configured:
                variants.append((f"ablation/{m}", m, None))
        variants += [(f"ablation/{a.reasoning_model}-low", a.reasoning_model, "low"), (f"ablation/{a.reasoning_model}-medium", a.reasoning_model, "medium")]

    RESULTS.mkdir(parents=True, exist_ok=True)
    rows = []
    pred_path = RESULTS / "extractor_predictions.jsonl"
    keep: list[str] = []
    if a.append and pred_path.exists():
        new_keys = {(name, version) for name, _, _ in variants}
        for line in pred_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            if (d.get("variant"), d.get("prompt_version", "extractor_v1")) not in new_keys:
                keep.append(line)
    with pred_path.open("w", encoding="utf-8") as writer:
        for line in keep:
            writer.write(line + "\n")
        for name, model, effort in variants:
            rows.append(run_variant(name, model, effort, scorable, writer))

    import pandas as pd

    df = pd.DataFrame(rows)
    df["prompt_version"] = version
    out = RESULTS / "extractor_ablation.csv"
    if a.append and out.exists():
        old = pd.read_csv(out)
        if "prompt_version" not in old.columns:
            old["prompt_version"] = "extractor_v1"
        keys = set(zip(df.variant, df.prompt_version))
        old = old[[k not in keys for k in zip(old.variant, old.prompt_version)]]
        df = pd.concat([old, df], ignore_index=True)
    df.to_csv(out, index=False)
    print(f"wrote {out} ({len(df)} rows) and extractor_predictions.jsonl")


if __name__ == "__main__":
    main()
