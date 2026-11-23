# data_gen: WP8 synthetic data (criterion 1)

Builds every dataset RouteChatbot needs. All data is synthetic: personal details come from
Faker, places are public landmarks from `places.csv`, sponsors are invented.

| Id | What | Size | Where it ends up |
|----|------|------|------------------|
| D1 | Classifier data: one user turn + five labels | train/val follow quotas, **test 420** (60 per action) | `data/d1/{train,val,test}.jsonl` |
| D2 | End-to-end: request, gold ConstraintSet, gold coordinates, expected outcome | 120 (about 40 hand-written) | `eval/datasets/d2_end_to_end.jsonl` |
| D3 | Adversarial: attack, family, expected safe behaviour | 120 (10 two-turn) | `eval/datasets/d3_adversarial.jsonl` |
| D4 | Sponsor catalog: fake businesses | 40 | `data/sponsors.json` |
| D5 | Judge calibration sample (needs real replies) | 50 | `data_gen/review/d5_judge_calibration.csv` |

## Setup

```bash
uv sync                                       # faker, scikit-learn, httpx, langdetect are already in pyproject.toml
cp .env.example .env                          # then put OPENROUTER_API_KEY in .env (never commit it)
uv run pytest tests/test_data_gen.py -q       # 33 tests, offline and free (schema tests run against routechat/schemas.py)
ROUTECHAT_TRANSIT_POLICY=named_mode uv run pytest tests/test_data_gen.py -q   # same tests under the other transit policy
```

No key? Every command falls back to a **mock generator** so you can test the flow. Mock data is for
testing the pipeline only. It must never be used for training or reported results (the manifest records
`"generator": "offline-mock"`).

## Workflow (D1)

```bash
python -m data_gen.pipeline plan                                  # quota table + reachability check
python -m data_gen.pipeline generate --n 3000                     # sample -> generate -> QC -> blind relabel
#   writes data/d1/{jobs,raw,rejected,candidates}.jsonl, review.csv (disagreements), diversity.json, run_manifest.json
python -m data_gen.pipeline apply-review --csv data/d1/review.csv # after humans fill decision (keep/fix/reject)
python -m data_gen.pipeline double-label                          # 100 records for two annotators -> kappa
python -m data_gen.pipeline kappa --csv data/d1/double_label.csv
python -m data_gen.pipeline handwriting-sheet                     # blank sheet for the 150 hand-written test messages
python -m data_gen.pipeline import-handwritten --csv data_gen/review/handwritten_test_TEMPLATE.csv
python -m data_gen.pipeline split                                 # grouped split + leakage removal + test_review.csv
python -m data_gen.pipeline apply-review --csv data/d1/test_review.csv --records test.jsonl --save-as test.jsonl
python -m data_gen.pipeline freeze                                # sha256 of the reviewed test set into the manifest
python -m data_gen.pipeline verify                                # anyone can re-check the hash
```

**Test-set protection.** The 150 hand-written messages are written from the spec sheet only (a different seed
from generation), so writers never see generated data. Groups (job templates) never cross splits. Any train or
val record with TF-IDF cosine >= 0.85 to a test record is dropped (kept in `leakage_dropped.jsonl`). Every test
record must be human-reviewed before `freeze`, and a frozen test set refuses to be re-split.

## Workflow (D2-D5)

```bash
python -m data_gen.datasets all                 # D2 -> D4 -> D3 (order matters), writes sha256 manifest
python -m data_gen.datasets import-d2-handwritten --csv data_gen/review/d2_handwrite_TODO.csv
python -m data_gen.datasets d5 --replies replies.jsonl   # when WP6/WP10 have real replies
```

Gold coordinates come from `places.csv` and are **approximate**. Verify them by hand using
`data_gen/review/d2_coords_verification.csv` (fill `verified_by`).

## Layout

- `spec.py`: all quotas, vocabularies, the 30 use-case rows, hard/attack types, models, budget. Edit here to re-aim generation.
- `sampling.py`: IPF quota solver (finds the joint table that meets all four marginals) and job builder.
- `pipeline.py`: LLM client with cache and budget cap, Faker slots, QC, blind relabel, review CSVs, split, freeze, CLI.
- `datasets.py`: D2-D5 builders.
- `prompts/`: `gen_v1.md`, `relabel_v1.md`, `gen_d2_v1.md`, `gen_d3_v1.md`, `label_guide_draft.md`. Bump the version suffix and log the change in `prompts/CHANGELOG.md` when you edit one.
- `personas.md`: 12 personas. `places.csv`: ~146 public places in Toronto, Ottawa, Montreal, Banff.

## Cost and reproducibility

Responses are cached by (model, prompt, temperature, seed), so reruns are free. A hard budget cap
(`spec.MAX_COST_USD`, default $5, and `MAX_CALLS`) stops a run and keeps what it has. Model slugs and prices
drift: check openrouter.ai/models before a live run.
