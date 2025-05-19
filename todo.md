# TODO Checklist for `train_models` Feature

Use this file to track progress. Check off each item as it’s completed.

---

## M1 — Migration & Model Tweak
- [x] m1-t1: Add `algorithm` field to `PredictionRunLog` in `gregory/models.py`
  - `CharField(max_length=20, choices=[('pubmed_bert', …), ('lgbm_tfidf', …), ('lstm', …)], null=True, blank=True)`
- [x] m1-t2: Generate & apply Django migration
- [x] m1-t3: In migration, set existing `PredictionRunLog` rows’ `algorithm` to `"unknown"`
- [x] m1-t4: Register `algorithm` in Django admin list display for `PredictionRunLog`

---

## M2 — Utility Foundation
- [x] m2-t1: Create `gregory/utils/summariser.py` with:
  - `summarise(text: str) -> str` using HF `facebook/bart-large-cnn`, 300 tokens, batch size 4, auto GPU/CPU
- [x] m2-t2: Add `summarise_bulk(texts: list[str], batch_size: int = 4) -> list[str]`
- [x] m2-t3: Create `gregory/utils/versioning.py` with:
  - `make_version_path(base_dir: Path, team: str, subject: str, algo: str) -> Path`
  - Auto-increment `_2`, `_3`, etc. for same-date versions
- [ ] m2-t4: Write pytest unit tests for summariser and versioning

---

## M3 — Data Preparation
- [x] m3-t1: Implement `collect_articles(team_slug: str, subject_slug: str, window_days: int | None) -> QuerySet` in `gregory/utils/dataset.py`
  - Filters by `discovery_date`, labeled via `ArticleSubjectRelevance`, and excludes unlabeled
- [x] m3-t2: Add `build_dataset(qs: QuerySet) -> pd.DataFrame`
  - Merge labels, concatenate `title + summary`, drop rows with no relevance label
- [x] m3-t3: Add `train_val_test_split(df: pd.DataFrame) -> (train, val, test)`
  - Stratified 70/15/15 split, seed = 69; raise ValueError if stratification fails
- [ ] m3-t4: Write pytest for dataset logic, split proportions, and stratification errors

---

## M4 — Algorithm Wrappers
- [x] m4-t1: Create `gregory/ml/bert_wrapper.py`:
  - `BertTrainer` with `__init__(...)`, `train(train_df, val_df, test_df, threshold) -> dict`, `save(path)`
  - Uses fixed hyper-params; saves `bert_weights.h5`
- [x] m4-t2: Create `gregory/ml/lgbm_wrapper.py`:
  - `LGBMTfidfTrainer` matching interface; saves `lgbm_classifier.joblib` + `tfidf_vectorizer.joblib`
- [x] m4-t3: Create `gregory/ml/lstm_wrapper.py`:
  - `LSTMTrainer` matching interface; saves `lstm_weights.h5` + `tokenizer.json`
- [x] m4-t4: In `gregory/ml/__init__.py`, implement `get_trainer(algo: str)`

---

## M5 — Metrics Helper
- [x] m5-t1: Create `gregory/utils/metrics.py` with:
  - `evaluate_binary(y_true, y_prob, threshold, prefix) -> dict`
- [x] m5-t2: Ensure output includes `accuracy`, `precision`, `recall`, `f1`, `roc_auc`, `pr_auc` with `val_` and `test_` prefixes
- [ ] m5-t3: Write pytest validating metrics against sklearn on synthetic data

---

## M6 — Pseudo-labeler (Opt-in)
- [x] m6-t1: Create `gregory/ml/pseudo.py` with BERT self-training loop:
  - `generate_pseudo_labels(train_df, val_df, unlabelled_df, tokenizer, confidence=0.9, max_iter=7) -> pd.DataFrame`
- [x] m6-t2: Add `save_pseudo_csv(df, dest_dir: Path) -> Path`:
  - Writes `<YYYYMMDD_HHMMSS>.csv` under `/models/<team>/<subject>/pseudo_labels/`, suffix `_2`, etc.
- [ ] m6-t3: Write pytest for pseudo-label CSV naming logic (monkeypatched trainer)

---

## M7 — Command Core
- [x] m7-t1: Create `gregory/management/commands/train_models.py`
- [x] m7-t2: Define argparse flags:
  - `--team`, `--subject`, `--all-teams`
  - `--all-articles`, `--lookback-days`
  - `--algo`, `--prob-threshold`, `--version`
  - `--pseudo-label`, `--verbose`
- [ ] m7-t3: Enforce usage rules:
  - Error if no scope or `--subject` without `--team`
  - Error if `--all-articles` + `--lookback-days`

---

## M8 — Orchestration & Saving
- [ ] m8-t1: Loop through selected teams, subjects, algorithms
  - Create `PredictionRunLog` entry with `run_type='train'`, `algorithm`, `success=None`
- [ ] m8-t2: Build dataset, summarise text, optionally pseudo-label
- [ ] m8-t3: Instantiate trainer, call `train()`, compute metrics, write `metrics.json`
- [ ] m8-t4: Save model files & aux artifacts
- [ ] m8-t5: Update `PredictionRunLog` row: `success=True` or `False` with `error_message`, set `run_finished`

---

## M9 — Logging & Verbosity
- [ ] m9-t1: Implement verbosity helper: levels 0–3 cumulative
- [ ] m9-t2: Replace prints in command with helper calls (`.info()`, `.warn()`, `.summary()`)
- [ ] m9-t3: Write pytest for verbosity behavior

---

## M10 — Tests & Documentation
- [ ] m10-t1: Integration test:
  - Fixture SQLite DB with sample data, run `train_models`, assert:
    - 3 log rows per subject
    - Files exist on disk
    - `metrics.json` contains both `val_` and `test_` keys for all six metrics
- [ ] m10-t2: CI smoke test script (optional)
- [ ] m10-t3: Update README:
  - Usage examples
  - Flag descriptions
  - Output directory layout

---

Review this checklist periodically. Each item should be checked off only after code, tests, and documentation are merged and verified.