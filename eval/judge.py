# Owner: Person E (WP10 evaluation / WP11 economics), Team Rosedale, MMAI 894.
# Only Person E edits this file. Other packages may import it but should not change it;
# AI coding tools must not edit this file unless they are working on WP10 or WP11.
# Written for this project with AI assistance (see eval/README.md, "AI use"); no code copied from other sources.
"""LLM judge (WP10) and the check of the judge against people.

The judge answers the four yes/no questions in prompts/judge_v1.md for every end-to-end reply.
Trip success uses one of them (unsupported_claim), so the judge has to be checked against people:
two teammates grade the same replies and we report Cohen's kappa per question (target 0.6 or more).

    uv run python -m eval.judge                                   # judge every reply from the last D2 run
    uv run python -m eval.judge --agreement report/judge_grading_A.csv report/judge_grading_B.csv
    uv run python -m eval.judge --agreement report/judge_grading_A.csv     # if only one person grades

Inputs come from eval/results/judge_inputs.jsonl, written by run_eval's D2 step. Like the rest of
eval/results it is git-ignored, because replies can quote the user's places.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Callable

import pandas as pd

from eval import metrics

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "judge_v1.md"
RESULTS = Path("eval/results")
INPUTS = RESULTS / "judge_inputs.jsonl"
ANSWERS = RESULTS / "judge_answers.csv"
QUESTIONS = metrics.JUDGE_QUESTIONS


def load_prompt(path: Path = PROMPT_PATH) -> tuple[str, str]:
    """Split judge_v1.md into its System and User sections, so the prompt lives in one file only."""
    text = path.read_text(encoding="utf-8")
    system = text.split("## System", 1)[1].split("## User", 1)[0].strip()
    user = text.split("## User", 1)[1].split("## How the answers are used", 1)[0].strip()
    return system, user


def build_messages(rec: dict, prompt: tuple[str, str] | None = None) -> list[dict]:
    """Fill the template for one reply. Lists and dicts go in as JSON so the judge sees exact values."""
    system, user = prompt or load_prompt()
    as_json = lambda v: json.dumps(v if v is not None else [], ensure_ascii=False)
    user = (user.replace("{utterance}", rec.get("utterance", ""))
                .replace("{checks_json}", as_json(rec.get("checks")))
                .replace("{must_mention}", as_json(rec.get("must_mention")))
                .replace("{route_facts_json}", as_json(rec.get("route_facts")))
                .replace("{final_text}", rec.get("final_text", "")))
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def parse_answer(text: str) -> dict | None:
    """Read the judge's JSON. Anything without all four booleans counts as a failed judgement (None),
    so a broken reply from the judge is visible instead of being read as a pass."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not all(isinstance(obj.get(q), bool) for q in QUESTIONS):
        return None
    return obj


def default_llm() -> Callable[[list[dict]], str]:
    """OpenRouter if its key is set, else OpenAI. Model from JUDGE_MODEL (default gpt-4.1, as in WP10).
    Temperature 0 so a rerun gives the same grades as far as the provider allows."""
    from openai import OpenAI
    if os.environ.get("OPENROUTER_API_KEY"):
        client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=os.environ["OPENROUTER_API_KEY"])
        model = os.environ.get("JUDGE_MODEL", "openai/gpt-4.1")
    else:
        client = OpenAI()  # reads OPENAI_API_KEY
        model = os.environ.get("JUDGE_MODEL", "gpt-4.1")

    def call(messages: list[dict]) -> str:
        r = client.chat.completions.create(model=model, messages=messages, temperature=0, max_tokens=300)
        return r.choices[0].message.content or ""
    return call


def run_judge(records: list[dict], llm: Callable[[list[dict]], str], retries: int = 1) -> pd.DataFrame:
    """One row per (item_id, question). A reply the judge could not grade gets answer = NaN and
    judge_ok = False; trip success then treats it as not graded rather than as clean."""
    prompt = load_prompt()
    rows = []
    for rec in records:
        ans = None
        for _ in range(retries + 1):
            ans = parse_answer(llm(build_messages(rec, prompt)))
            if ans:
                break
        for q in QUESTIONS:
            rows.append({"item_id": rec["item_id"], "question": q,
                         "answer": ans[q] if ans else float("nan"),
                         "note": ans.get("note", "") if ans else "judge reply not valid JSON",
                         "judge_ok": ans is not None})
    return pd.DataFrame(rows)


def load_human_grading(path: Path) -> pd.DataFrame:
    """A grader's sheet (one row per reply, yes/no per question) in the long form judge_agreement uses."""
    wide = pd.read_csv(path)
    long = wide.melt(id_vars=["item_id"], value_vars=list(QUESTIONS), var_name="question", value_name="answer")
    long["answer"] = long["answer"].astype(str).str.strip().str.lower().map(
        {"yes": True, "true": True, "1": True, "no": False, "false": False, "0": False})
    return long.dropna(subset=["answer"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", type=Path, default=INPUTS)
    ap.add_argument("--agreement", nargs="+", type=Path, metavar="GRADER",
                    help="one or two filled grading sheets; compares them with the judge (and with each other)")
    a = ap.parse_args()
    RESULTS.mkdir(parents=True, exist_ok=True)
    if a.agreement:
        if not ANSWERS.exists():
            print(f"agreement: run the judge first ({ANSWERS} is missing)"); return
        judge = pd.read_csv(ANSWERS).dropna(subset=["answer"])
        judge["answer"] = judge["answer"].astype(bool)
        if len(a.agreement) > 2:
            print("agreement: give one or two grading sheets"); return
        second = load_human_grading(a.agreement[1]) if len(a.agreement) == 2 else None
        k = metrics.judge_agreement(judge, load_human_grading(a.agreement[0]), second)
        k.to_csv(RESULTS / "judge_agreement.csv", index=False)
        print(k.to_string(index=False)); return
    if not a.inputs.exists():
        print(f"judge: waiting for a D2 run ({a.inputs} is missing)"); return
    records = [json.loads(x) for x in a.inputs.read_text().splitlines() if x.strip()]
    out = run_judge(records, default_llm())
    out.to_csv(ANSWERS, index=False)
    bad = (~out.drop_duplicates("item_id")["judge_ok"]).sum()
    print(f"wrote {ANSWERS}: {out.item_id.nunique()} replies, {bad} the judge could not grade")


if __name__ == "__main__":
    main()
