# Owner: Person E (WP10 evaluation / WP11 economics), Team Rosedale, MMAI 894.
# Only Person E edits this file. Other packages may import it but should not change it;
# AI coding tools must not edit this file unless they are working on WP10 or WP11.
# Written for this project with AI assistance (see eval/README.md, "AI use"); no code copied from other sources.
"""The 50 manual route inspections (WP5's false-met rate, and the "truly met" part of trip success).

After a D2 run:

    uv run python -m eval.route_review --reviewers Preeti Arvin     # writes report/route_review_sheet.csv
    ... each reviewer fills reviewer_status and evidence for their rows ...
    uv run python -m eval.route_review --score report/route_review_sheet.csv

The sheet has one row per check on an accepted route: route id, the check, the verifier's status and
its evidence, then the reviewer's own status and evidence. --score writes eval/datasets/d2_truth.csv,
which run_eval reads on its next run, and prints the false-met rate: checks the verifier marked met
that the reviewer found unmet.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

RESULTS = Path("eval/results")
SHEET = Path("report/route_review_sheet.csv")
TRUTH = Path("eval/datasets/d2_truth.csv")
STATUSES = ("met", "unmet", "unverifiable")
COLUMNS = ["reviewer", "item_id", "utterance", "check_id", "hard", "verifier_status", "verifier_evidence",
           "route_facts", "reviewer_status", "reviewer_evidence"]


def build_sheet(judge_inputs: list[dict], end_to_end: pd.DataFrame, reviewers: list[str],
                n_routes: int = 50, seed: int = 894) -> pd.DataFrame:
    """Pick n accepted routes (fixed seed, so the sample can be reproduced) and split them evenly
    between the reviewers. A route with no checks still gets one row, so it is not silently dropped."""
    accepted = set(end_to_end.loc[end_to_end["outcome"] == "accept", "item_id"])
    routes = [r for r in judge_inputs if r["item_id"] in accepted]
    routes = pd.DataFrame(routes).sample(n=min(n_routes, len(routes)), random_state=seed).to_dict("records") if routes else []
    rows = []
    for i, r in enumerate(sorted(routes, key=lambda r: r["item_id"])):
        facts = json.dumps(r.get("route_facts"), ensure_ascii=False)
        checks = r.get("checks") or [{"id": "(no checks on this route)", "hard": "", "status": "", "evidence": ""}]
        for c in checks:
            rows.append({"reviewer": reviewers[i % len(reviewers)], "item_id": r["item_id"], "utterance": r["utterance"],
                         "check_id": c.get("id"), "hard": c.get("hard"), "verifier_status": c.get("status"),
                         "verifier_evidence": c.get("evidence", ""), "route_facts": facts,
                         "reviewer_status": "", "reviewer_evidence": ""})
    return pd.DataFrame(rows, columns=COLUMNS)


def score(sheet: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Truth per route and the false-met rate. A route's hard requirements are truly met only when the
    reviewer marked every hard check met. Rows the reviewer left blank are not counted either way."""
    s = sheet.copy()
    s["reviewer_status"] = s["reviewer_status"].astype(str).str.strip().str.lower()
    done = s[s["reviewer_status"].isin(STATUSES)]
    hard = done[done["hard"].astype(str).str.lower() == "true"]
    # a route counts as inspected only when every one of its hard checks has a reviewer answer
    all_hard = s[s["hard"].astype(str).str.lower() == "true"].groupby("item_id").size()
    complete = [i for i, n in all_hard.items() if (hard["item_id"] == i).sum() == n]
    no_hard = [i for i in done["item_id"].unique() if i not in all_hard.index]
    truth = pd.DataFrame(
        [{"item_id": i, "hard_truly_met": bool((hard.loc[hard["item_id"] == i, "reviewer_status"] == "met").all())}
         for i in complete] + [{"item_id": i, "hard_truly_met": True} for i in no_hard])
    marked_met = done[done["verifier_status"] == "met"]
    false_met = marked_met[marked_met["reviewer_status"] == "unmet"]
    stats = {"routes_inspected": len(truth), "checks_reviewed": len(done),
             "checks_verifier_met": len(marked_met), "false_met": len(false_met),
             "false_met_rate": len(false_met) / len(marked_met) if len(marked_met) else float("nan"),
             "rows_left_blank": int(len(s) - len(done))}
    return truth, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reviewers", nargs="+", default=["A", "B"], help="names; routes are split evenly between them")
    ap.add_argument("--routes", type=int, default=50)
    ap.add_argument("--score", type=Path, help="a filled sheet: writes d2_truth.csv and prints the false-met rate")
    a = ap.parse_args()
    if a.score:
        truth, stats = score(pd.read_csv(a.score))
        TRUTH.parent.mkdir(parents=True, exist_ok=True)
        truth.to_csv(TRUTH, index=False)
        pd.DataFrame([stats]).to_csv(RESULTS / "route_review.csv", index=False)
        print(f"wrote {TRUTH} ({len(truth)} routes)"); print(stats); return
    inputs, e2e = RESULTS / "judge_inputs.jsonl", RESULTS / "end_to_end.csv"
    if not inputs.exists() or not e2e.exists():
        print(f"route_review: waiting for a D2 run ({inputs} and {e2e})"); return
    recs = [json.loads(x) for x in inputs.read_text(encoding="utf-8").splitlines() if x.strip()]
    sheet = build_sheet(recs, pd.read_csv(e2e), a.reviewers, a.routes)
    SHEET.parent.mkdir(parents=True, exist_ok=True)
    sheet.to_csv(SHEET, index=False)
    print(f"wrote {SHEET}: {sheet['item_id'].nunique()} routes, {len(sheet)} checks, "
          f"{sheet.groupby('reviewer')['item_id'].nunique().to_dict()}")


if __name__ == "__main__":
    main()
