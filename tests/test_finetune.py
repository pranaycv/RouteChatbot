"""WP9 tests. T-9-01 runs in the core environment. T-9-02 and T-9-03 need a trained adapter and torch,
so they skip until finetune/artifacts/adapter exists (or CLASSIFIER_ADAPTER_DIR points at one)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from finetune import prepare
from finetune import select_checkpoint as select
from finetune.train import effective_settings, load_ft_config
from routechat.classifier.predict import adapter_dir, build_messages, labels_json
from routechat.schemas import RouteClassification, UserTurn

ROOT = Path(__file__).resolve().parents[1]
FEWSHOT = ROOT / "prompts" / "classifier_fewshot_v1.jsonl"


def _rows(n: int) -> list[dict]:
    rows = [json.loads(l) for l in FEWSHOT.read_text(encoding="utf-8").splitlines() if l.strip()]
    return rows[:n]


def test_T_9_01_prepare_output_matches_runtime_prompt_byte_for_byte():
    for row in _rows(5):
        turn, labels = prepare.parse_record(row)
        record = prepare.to_prompt_completion(turn, labels)
        runtime = build_messages(UserTurn.model_validate(row["turn"]), include_examples=False)
        assert json.dumps(record["prompt"], ensure_ascii=False) == json.dumps(runtime, ensure_ascii=False)
        assert record["completion"] == [{"role": "assistant", "content": labels_json(RouteClassification.model_validate(row["labels"]))}]
        assert "parse_error" not in record["completion"][0]["content"]


def test_prepare_accepts_flat_records_and_cli(tmp_path):
    flat = tmp_path / "d1.jsonl"
    rows = []
    for row in _rows(3):
        rows.append({"id": row["id"], **row["turn"], **row["labels"]})
    flat.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    out = tmp_path / "train.jsonl"
    n = prepare.convert(flat, out)
    assert n == 3
    lines = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
    assert all(set(l) == {"prompt", "completion"} for l in lines)
    assert prepare.convert(flat, tmp_path / "two.jsonl", limit=2) == 2


def test_train_dry_run_reads_config_and_overrides(tmp_path):
    val = tmp_path / "val.jsonl"
    prepare.convert(FEWSHOT, val)
    cmd = [sys.executable, str(ROOT / "finetune" / "train.py"), "--train", str(val), "--val", str(val), "--out", str(tmp_path / "run"), "--r", "8", "--lr", "1e-4", "--dry-run"]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT)})
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["train_records"] == 8 and summary["settings"]["lora"]["r"] == 8
    assert summary["settings"]["sft"]["learning_rate"] == 1e-4 and summary["settings"]["sft"]["seed"] == 42
    assert summary["settings"]["base_model"].startswith("Qwen/")


def test_handbook_settings_are_in_config():
    cfg = load_ft_config()
    assert cfg["lora"] == {"r": 16, "lora_alpha": 32, "lora_dropout": 0.05, "target_modules": "all-linear", "task_type": "CAUSAL_LM"}
    sft = cfg["sft"]
    # Effective batch 8 as the handbook (4 x 2), split 2 x 4 for ~1,500-token records; max_length 2048
    # because the label guide alone is 1,430 tokens and the handbook's 512 would drop every completion.
    assert sft["num_train_epochs"] == 3
    assert sft["per_device_train_batch_size"] * sft["gradient_accumulation_steps"] == 8
    assert (sft["learning_rate"], sft["lr_scheduler_type"], sft["warmup_ratio"], sft["max_length"]) == (2e-4, "cosine", 0.05, 2048)
    assert sft["fp16"] is True and sft["eval_steps"] == 50 and sft["save_steps"] == 50 and cfg["seed"] == 42
    assert cfg["sweep"] == {"r": [8, 16], "learning_rate": [1e-4, 2e-4]}
    assert cfg["learning_curve"]["sizes"] == [200, 400, 800, 1600]


def test_select_metrics():
    golds = [{"route_action": "plan", "primary_mode": "bike", "needs_live_data": True, "clarify_field": "none", "refusal_reason": "none"}] * 4
    preds = [dict(golds[0]), dict(golds[0]), {**golds[0], "primary_mode": "car"}, {**golds[0], "parse_error": True, "route_action": "escalate"}]
    s = select.score(preds, golds)
    assert s["joint_exact_match"] == 0.5
    assert s["per_field_accuracy"]["primary_mode"] == 0.75 and s["per_field_accuracy"]["route_action"] == 0.75
    assert s["parse_errors"] == 1 and s["n"] == 4
    assert select.macro_f1(["a", "a", "b"], ["a", "b", "b"]) == pytest.approx((2 / 3 + 2 / 3) / 2)


def test_select_loads_prepared_val(tmp_path):
    val = tmp_path / "val.jsonl"
    prepare.convert(FEWSHOT, val)
    rows = select.load_val(val)
    assert len(rows) == 8 and set(rows[0]["labels"]) == set(select.LABEL_KEYS)
    assert rows[0]["turn"]["utterance"].startswith("Plan a bike route")


_adapter_missing = not adapter_dir().exists()


@pytest.mark.skipif(_adapter_missing, reason="T-9-02 needs the trained adapter (WP9) and torch")
def test_T_9_02_adapter_loads_through_predict_and_classifies_the_running_example():
    from routechat.classifier.predict import classify, resolve_backend

    row = _rows(1)[0]
    c = classify(UserTurn.model_validate(row["turn"]), backend=resolve_backend("ft"))
    assert labels_json(c) == labels_json(RouteClassification.model_validate(row["labels"]))


@pytest.mark.skipif(not os.environ.get("RUN_REPRO_TEST"), reason="T-9-03 retrains twice on a T4; run with RUN_REPRO_TEST=1 on Kaggle")
def test_T_9_03_training_is_reproducible_within_one_point():
    pytest.skip("executed from finetune/kaggle_train.ipynb, which trains twice with seed 42 and compares select_results.json")
