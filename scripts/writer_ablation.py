# Owner: Person A (WP6 writer ablation), Team Rosedale, MMAI 894.
# Written for this project with AI assistance; no code copied from other sources.
"""WP6 ablation. The same checked routes, written by two models.

    uv run python scripts/writer_ablation.py --states     # find and check routes for D2 trips (map calls, no model)
    uv run python scripts/writer_ablation.py --write      # write each reply with each model, and judge it

Only the writer's model changes. The routes come from D2's gold constraints, so no classifier or
extractor takes part. Each model sees the same facts and the same prompt, and the output guard checks
every reply. The sponsor is left out: code adds that sentence, not the model.

The judge is the one the evaluation uses (gpt-4.1, prompts/judge_v1.md). It has not been compared with
people yet, so its columns are a guide. Files go to eval/results/, which git ignores:
writer_ablation_states.json, writer_ablation_replies.jsonl and writer_ablation.csv.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval.judge import default_llm, run_judge  # noqa: E402
from eval.run_eval import judge_input  # noqa: E402
from routechat.agents.maps_node import maps_node  # noqa: E402
from routechat.agents.writer import _messages, build_facts, template_message  # noqa: E402
from routechat.config import get_settings  # noqa: E402
from routechat.guards.output_guard import output_guard  # noqa: E402
from routechat.llm import complete  # noqa: E402
from routechat.schemas import (  # noqa: E402
    ConstraintSet,
    GuardResult,
    ReplyDraft,
    ResolvedPlace,
    RouteCandidate,
    RouteClassification,
    VerificationReport,
)
from routechat.verify.decide import verify_node  # noqa: E402

RESULTS = ROOT / "eval" / "results"
STATES = RESULTS / "writer_ablation_states.json"
MODES = ("walk", "run", "bike", "car")


def _records(limit: int, seed: int) -> list[dict]:
    """D2 trips the pilot can route, the same number of each mode while they last."""
    rows = [json.loads(line) for line in (ROOT / "eval/datasets/d2_end_to_end.jsonl").read_text(encoding="utf-8").splitlines() if line]
    by_mode: dict[str, list[dict]] = {mode: [] for mode in MODES}
    for row in rows:
        gold = row.get("gold_constraints")
        if gold and row["expected_outcome"] in ("accept", "handoff") and gold["mode"] in by_mode:
            by_mode[gold["mode"]].append(row)
    for group in by_mode.values():
        random.Random(seed).shuffle(group)
    picked: list[dict] = []
    while len(picked) < limit and any(by_mode.values()):
        picked += [by_mode[mode].pop() for mode in MODES if by_mode[mode]]
    return picked[:limit]


def find_states(limit: int, seed: int, pause_s: float) -> None:
    kept = []
    for number, row in enumerate(_records(limit, seed)):
        if number:
            time.sleep(pause_s)  # GraphHopper's free plan limits calls per minute
        state = {
            "request_id": f"writer_{row['item_id']}",
            "utterance": row["utterance"],
            "guard": GuardResult(decision="allow", clean_text=row["utterance"], masked=[], reason=None),
            "classification": RouteClassification(
                route_action="plan", primary_mode=row["gold_constraints"]["mode"], needs_live_data=False,
                clarify_field="none", refusal_reason="none",
            ),
            "constraints": ConstraintSet.model_validate(row["gold_constraints"]),
            "revision_count": 0,
        }
        state |= maps_node(state)
        state |= verify_node(state)
        report = state["report"] = state["report"].model_copy(update={"sponsored": None})
        usable = template_message(state) is None
        print(f"{row['item_id']} {row['gold_constraints']['mode']:<5}{report.decision:<9}{report.handoff_reason or '':<22}{'kept' if usable else ''}", flush=True)
        if usable:
            kept.append({
                "item_id": row["item_id"], "utterance": row["utterance"],
                **{key: state[key].model_dump(mode="json") for key in ("guard", "classification", "constraints", "report")},
                **{key: [item.model_dump(mode="json") for item in state[key]] for key in ("places", "candidates")},
            })
    RESULTS.mkdir(parents=True, exist_ok=True)
    STATES.write_text(json.dumps(kept, ensure_ascii=False), encoding="utf-8")
    print(f"{len(kept)} states in {STATES}")


def _load(saved: dict) -> dict:
    return {
        "request_id": f"writer_{saved['item_id']}", "utterance": saved["utterance"], "revision_count": 0,
        "guard": GuardResult.model_validate(saved["guard"]),
        "classification": RouteClassification.model_validate(saved["classification"]),
        "constraints": ConstraintSet.model_validate(saved["constraints"]),
        "places": [ResolvedPlace.model_validate(item) for item in saved["places"]],
        "candidates": [RouteCandidate.model_validate(item) for item in saved["candidates"]],
        "report": VerificationReport.model_validate(saved["report"]),
    }


def write(models: list[str]) -> None:
    settings = get_settings()
    rows, judged = [], {model: [] for model in models}
    for saved in json.loads(STATES.read_text(encoding="utf-8")):
        state = _load(saved)
        messages = _messages(build_facts(state))
        for model in models:
            result = complete(messages, model, settings.writer.temperature, schema=ReplyDraft)
            row = {"item_id": saved["item_id"], "model": model, "latency_ms": result.latency_ms,
                   "tokens_in": result.tokens_in, "tokens_out": result.tokens_out, "cost_usd": result.cost_usd}
            try:
                draft = ReplyDraft.model_validate_json(result.text).model_copy(update={"kind": "llm"})
            except ValueError:
                rows.append({**row, "valid": False, "message": result.text, "fallback": True})
                continue
            final = output_guard(state | {"draft": draft})["final"]
            rows.append({**row, "valid": True, "message": draft.message, "words": len(draft.message.split()),
                         "fallback": final.used_fallback_template})
            # The judge reads what the model wrote, before the guard could replace it.
            judged[model].append(judge_input(saved, state | {"final": {"text": draft.message}}))
    llm, answers = default_llm(), []
    for model, inputs in judged.items():
        answers.append(run_judge(inputs, llm).assign(model=model))
    answer = pd.concat(answers)
    frame = pd.DataFrame(rows)
    out = []
    for model in models:
        mine = frame[frame.model == model]
        judge = answer[(answer.model == model) & answer.judge_ok]
        share = lambda q: judge[judge.question == q].answer.astype(bool).mean()  # noqa: E731
        out.append({
            "model": model, "n": len(mine), "invalid": int((~mine.valid).sum()),
            "replaced_by_guard": int(mine.fallback.sum()), "fallback_rate": mine.fallback.mean(),
            "words_mean": mine.words.mean(), "over_word_cap": int((mine.words > settings.writer.max_words).sum()),
            "p50_ms": mine.latency_ms.quantile(.5), "p95_ms": mine.latency_ms.quantile(.95),
            "cost_per_1000_usd": 1000 * mine.cost_usd.mean(),
            "judged": judge.item_id.nunique(), "judge_all_warnings": share("all_warnings_present"),
            "judge_unsupported_claim": share("unsupported_claim"), "judge_promise": share("makes_promise"),
            "judge_next_step": share("user_knows_next_step"),
        })
    RESULTS.mkdir(parents=True, exist_ok=True)
    notes = answer.pivot_table(index=["item_id", "model"], columns="question", values="answer", aggfunc="first").reset_index()
    note = answer.drop_duplicates(["item_id", "model"])[["item_id", "model", "note"]]
    frame.merge(notes, how="left").merge(note, how="left").to_json(
        RESULTS / "writer_ablation_replies.jsonl", orient="records", lines=True, force_ascii=False)
    table = pd.DataFrame(out)
    table.to_csv(RESULTS / "writer_ablation.csv", index=False)
    print(table.T.to_string())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--states", action="store_true")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--limit", type=int, default=40, help="D2 trips to try")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--pause", type=float, default=12, help="seconds between map requests")
    parser.add_argument("--models", default="gpt-4.1-mini,gpt-4.1-nano")
    args = parser.parse_args()
    if args.states:
        find_states(args.limit, args.seed, args.pause)
    if args.write:
        write(args.models.split(","))


if __name__ == "__main__":
    main()
