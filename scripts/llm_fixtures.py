"""Seed or record the replay fixtures the classifier and extractor tests read (Person C, WP2/WP3).

    uv run python scripts/llm_fixtures.py --synthetic   # no key needed: writes the expected outputs as fixtures
    uv run python scripts/llm_fixtures.py --record      # needs OPENAI_API_KEY or OPENROUTER_API_KEY: records live

Synthetic fixtures let `FIXTURES=replay uv run pytest` pass before the team's API key exists. They
exercise the whole pipeline except the model itself, and each file says "source": "synthetic".
Run --record once a key is in .env to replace them with real responses; the tests do not change.

Fixture keys hash the full prompt, so any edit to prompts/label_guide_v1.md,
prompts/classifier_fewshot_v1.jsonl or prompts/extractor_v1.md invalidates the fixtures. Re-run
this script after a prompt change (and note it in prompts/CHANGELOG.md).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from routechat.agents import extractor  # noqa: E402
from routechat.classifier import predict  # noqa: E402
from routechat.config import get_settings  # noqa: E402
from routechat.llm import write_fixture  # noqa: E402
from routechat.schemas import ConstraintSet, RouteClassification, UserTurn  # noqa: E402

SMOKE_PATH = ROOT / "tests" / "data" / "classifier_smoke.json"
EXTRA_PATH = ROOT / "tests" / "data" / "extractor_extra_cases.json"


def extractor_cases() -> list[dict]:
    return extractor.prompt_examples() + json.loads(EXTRA_PATH.read_text(encoding="utf-8"))


def classifier_turns() -> list[dict]:
    return json.loads(SMOKE_PATH.read_text(encoding="utf-8"))


def seed_synthetic() -> int:
    settings = get_settings()
    n = 0
    for case in extractor_cases():
        cls = RouteClassification.model_validate(case["classification"])
        known = ConstraintSet.model_validate(case["known"]) if case["known"] else None
        messages = extractor.build_messages(case["clean_text"], cls, known)
        text = ConstraintSet.model_validate(case["model_output"]).model_dump_json()
        write_fixture(messages, settings.extractor.model, settings.extractor.temperature, "ConstraintSet", text)
        n += 1
    for backend_name in ("prompted_small", "prompted_large"):
        backend = predict.resolve_backend(backend_name)
        for row in classifier_turns():
            turn = UserTurn.model_validate(row["turn"])
            text = predict.labels_json(RouteClassification.model_validate(row["labels"]))
            messages = predict.build_messages(turn, include_examples=backend.uses_examples)
            write_fixture(messages, backend.model, backend.temperature, None, text)
            n += 1
    return n


def record_live() -> int:
    os.environ["FIXTURES"] = "record"
    n = 0
    for case in extractor_cases():
        extractor.extract(case["clean_text"], case["classification"], case["known"], request_id=case["id"])
        n += 1
    for backend_name in ("prompted_small", "prompted_large"):
        backend = predict.resolve_backend(backend_name)
        for row in classifier_turns():
            predict.classify(UserTurn.model_validate(row["turn"]), backend=backend, request_id=row["id"])
            n += 1
    return n


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--synthetic", action="store_true", help="write expected outputs as fixtures (no key needed)")
    group.add_argument("--record", action="store_true", help="call the live model and record real responses")
    args = parser.parse_args()
    n = seed_synthetic() if args.synthetic else record_live()
    print(f"{'seeded' if args.synthetic else 'recorded'} {n} fixtures in {extractor.ROOT / 'routechat' / 'fixtures' / 'llm'}")


if __name__ == "__main__":
    main()
