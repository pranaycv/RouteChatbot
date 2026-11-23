# WP9 fine-tuning: how to run it

Two ways to run `kaggle_train.ipynb` on a free Kaggle T4. Both read D1 from the repository (`data/d1/`), so no upload is needed. The repository is public, so no GitHub token is needed either.

## A. In the browser (no setup)

1. kaggle.com, Create, New Notebook, File, Import Notebook, upload `finetune/kaggle_train.ipynb`.
2. Settings on the right: Accelerator GPU T4 x1, Internet On.
3. In the first code cell set `BRANCH` to `main` (after the PR is merged) or `stage/classifier-v2` (before).
4. Run All. One run takes about 20 to 30 minutes.
5. When it finishes, download `/kaggle/working/routechatbot_adapter.zip` from the Output panel, and note the GPU hours and cost printed by the last cell.

## B. From the command line (Kaggle API)

1. kaggle.com, Settings, API, Create New Token. Save the downloaded `kaggle.json` to `C:\Users\<you>\.kaggle\kaggle.json`.
2. Put your Kaggle username in `finetune/kernel-metadata.json` in place of `KAGGLE_USERNAME`.
3. From the repository root:

```bash
cd finetune
kaggle kernels push -p .            # uploads and starts the notebook on a T4
kaggle kernels status <username>/routechatbot-wp9-finetune
kaggle kernels output <username>/routechatbot-wp9-finetune -p artifacts/kaggle_output   # when complete
```

The adapter zip lands in `artifacts/kaggle_output/`. Unzip it to `finetune/artifacts/adapter/` (git-ignored) or set `CLASSIFIER_ADAPTER_DIR`, and `CLASSIFIER_BACKEND=ft` works.

## After training

- Share the adapter zip as a private Kaggle dataset so the team can run the `ft` backend.
- `finetune/artifacts/<run>/run.json` holds GPU hours and cost for WP11; `select_results.json` holds the validation scores.
- The four-backend comparison on the frozen test set is WP10's `uv run python -m eval.run_eval --classifier`, with `CLASSIFIER_ADAPTER_DIR` set and an API key in `.env` for the two prompted backends.
