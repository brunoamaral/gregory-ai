Below you’ll find a three-pass planning sequence that gradually refines the work into right-sized tasks, followed by a set of ready-to-run prompts you can feed to a code-generation LLM (e.g., ChatGPT o3, Codex, etc.).
Each prompt is fenced in a ```text block and builds on the artifacts produced by the previous ones, so nothing is left dangling.

⸻

1 — Blueprint (first pass, high-level)
	1.	Schema update – add algorithm to PredictionRunLog with choices & migration.
	2.	Utilities
2.1 Text summariser wrapper (Bart-large-CNN, 300 tokens, batching).
2.2 Dataset builder (filters, label merge, 70/15/15 split, seed 69).
2.3 Version + path helper (auto-increment _n).
2.4 Metrics helper (val & test metrics @ threshold).
	3.	Algorithm wrappers – unify training interfaces for PubMed BERT, LGBM-TFIDF, LSTM.
	4.	Pseudo-label pipeline (BERT self-training, CSV writer).
	5.	Management command skeleton – parse CLI flags; validate rules.
	6.	Training orchestrator – for every team/subject/algo, call utilities, train, save, log.
	7.	Verbosity / logging layer.
	8.	Tests – unit + integration; CI smoke job.
	9.	Docs update.

⸻

2 — Milestones (second pass, coarse chunks)

M#	Milestone	Deliverable
M1	Migration & model tweak	Field + migration + admin registration
M2	Utility foundation	summariser.py, versioning.py
M3	Data prep	dataset.py with filter & split
M4	Algo wrappers	bert_wrapper.py, lgbm_wrapper.py, lstm_wrapper.py
M5	Metrics helper	metrics.py
M6	Pseudo-labeler	pseudo.py (opt-in)
M7	Command core	train_models.py CLI skeleton
M8	Orchestrator integration	command calls wrappers & utils
M9	Logging & verbosity	integrate PredictionRunLog + console layers
M10	Tests & docs	pytest suite + README section


⸻

3 — Task list (third pass, right-sized steps)

Below each milestone is decomposed into atomic tasks that still move the needle.

M1 — Migration & model tweak
	1.	m1-t1 Add algorithm field to PredictionRunLog model (choices, null=True).
	2.	m1-t2 Create and generate Django migration.
	3.	m1-t3 Set default “unknown” for existing rows in migration.
	4.	m1-t4 Register field in admin list display.

M2 — Utility foundation
	1.	m2-t1 Implement summariser.py → summarise(text: str) -> str.
	2.	m2-t2 Batch helper summarise_bulk(texts: List[str]).
	3.	m2-t3 Implement versioning.py with make_version_path(base_dir, team, subject, algo).
	4.	m2-t4 Unit tests for both modules.

M3 — Data prep
	1.	m3-t1 dataset.py → collect_articles(team, subject, window) query.
	2.	m3-t2 build_dataset(df) merges labels, drops unlabeled.
	3.	m3-t3 train_val_test_split(df) with stratified 70/15/15 & seed 69.
	4.	m3-t4 Add fast pytest using a small fixture.

M4 — Algorithm wrappers
	1.	m4-t1 BertTrainer class with train() and save().
	2.	m4-t2 LGBMTfidfTrainer same interface.
	3.	m4-t3 LSTMTrainer same interface.
	4.	m4-t4 Factory get_trainer(algo) returns correct class.

M5 — Metrics helper
	1.	m5-t1 metrics.py → evaluate(y_true, y_prob, threshold).
	2.	m5-t2 Return dict with six metrics + val_/test_ prefixes.
	3.	m5-t3 Unit tests against scikit-learn reference.

M6 — Pseudo-labeler (opt-in)
	1.	m6-t1 pseudo.py – BERT self-training loop.
	2.	m6-t2 CSV writer into pseudo_labels folder with timestamp.
	3.	m6-t3 Unit smoke test (mock small texts).

M7 — Command core
	1.	m7-t1 Create gregory/management/commands/train_models.py.
	2.	m7-t2 Add argparse flags, enforce mutual-exclusion rules.
	3.	m7-t3 Verbose enum 0-3 with cumulative behaviour.

M8 — Orchestrator
	1.	m8-t1 Loop teams/subjects/algs; create PredictionRunLog pre-row.
	2.	m8-t2 Call dataset → summarise → pseudo-label (if flag).
	3.	m8-t3 Invoke trainer; save artifacts + metrics.json.
	4.	m8-t4 Mark log success / error.

M9 — Logging layer
	1.	m9-t1 Console logger respecting --verbose.
	2.	m9-t2 Helper to summarise run table at end (level≥3).
	3.	m9-t3 Unit tests for logger levels.

M10 — Tests & docs
	1.	m10-t1 Integration test: tiny fixture DB, run command, assert artifacts.
	2.	m10-t2 CI smoke script (optional).
	3.	m10-t3 Update README / developer guide.

⸻

4 — Prompts for a code-generation LLM

Below is a prompt sequence—one per task—that a code-gen model can follow.
Each prompt assumes previous steps’ code is present in the repo and tested.

Prompt 1 (m1-t1 + m1-t2 + m1-t3)

You are working in the Django project “gregory-ai”.

**Task**:  
1. Edit `gregory/models.py` to add a `algorithm = models.CharField(...)` field to `PredictionRunLog`.  
   * choices: pubmed_bert, lgbm_tfidf, lstm  
   * null=True, blank=True  
2. Create a Django migration that sets `"unknown"` for existing rows.  
3. Ensure `PredictionRunLog` admin shows the new column.

Follow best practices: verbose_name, help_text, and keep imports sorted.

Prompt 2 (m2-t1)

Create `gregory/utils/summariser.py` with a `summarise(text: str) -> str` function.

**Requirements**
* Use Hugging Face transformers, model `facebook/bart-large-cnn`.
* Limit output to 300 tokens.
* Detect CUDA: if available run on GPU else CPU.
* Cache the loaded model/tokenizer at module level.
* Provide doctrings and type hints.
* Include a quick self-test under `if __name__ == "__main__":` that summarises a 200-word lorem ipsum.

Prompt 3 (m2-t2)

Extend `gregory/utils/summariser.py`.

Add `summarise_bulk(texts: list[str], batch_size: int = 4) -> list[str]` that:
* Splits `texts` into batches, applies `summarise` in parallel on GPU or sequentially on CPU.
* Keeps input order.
* Handles empty strings safely.
* Includes basic unit tests in `tests/test_summariser.py` (pytest) using monkeypatch to stub the model for speed.

Prompt 4 (m2-t3 + m2-t4)

Create `gregory/utils/versioning.py` with:

* `make_version_path(base_dir: Path, team: str, subject: str, algo: str) -> Path`
  * Auto-creates dirs.
  * Uses today’s YYYYMMDD; if it already exists, add `_2`, `_3`, etc.
  * Returns final Path object.

Add pytest `tests/test_versioning.py` with param cases showing suffix generation.

Prompt 5 (m3-t1 → m3-t4)

Implement `gregory/utils/dataset.py`:

1. `collect_articles(team_slug: str, subject_slug: str, window_days: int | None) -> QuerySet`.
   * Filter Articles by discovery_date and relevance labels.

2. `build_dataset(qs) -> pd.DataFrame` merging title + summary; drop unlabeled.

3. `train_val_test_split(df) -> tuple[pd.DataFrame, ...]`
   * Stratified 70/15/15, seed 69.

Add pytest covering:
* correct split sizes
* stratification failure raises ValueError.

Prompt 6 (m4-t1)

Create `gregory/ml/bert_wrapper.py`:

* Class `BertTrainer`
  * `__init__(self, max_len=400, lr=2e-5, dense_units=48, freeze=False)`
  * `train(train_df, val_df, test_df, threshold=0.8) -> dict` returning metrics dict.
  * `save(path: Path)` writes `bert_weights.h5`.
* Use Keras / transformers; keep seed 69.
* Factor any duplicate preprocessing into helper functions.

Prompt 7 (m4-t2 + m4-t3 + m4-t4)

Add two modules:

1. `gregory/ml/lgbm_wrapper.py` with `LGBMTfidfTrainer` — follow same interface, save `lgbm_classifier.joblib` + `tfidf_vectorizer.joblib`.

2. `gregory/ml/lstm_wrapper.py` with `LSTMTrainer` — save `lstm_weights.h5` + `tokenizer.json`.

3. In `gregory/ml/__init__.py` implement `get_trainer(algo: str)` returning the correct trainer or raising ValueError.

Prompt 8 (m5-t1 → m5-t3)

Create `gregory/utils/metrics.py`:

* `evaluate_binary(y_true: np.ndarray, y_prob: np.ndarray, threshold: float, prefix: str) -> dict`
  * accuracy, precision, recall, f1, roc_auc, pr_auc keys prefixed.

Add pytest using sklearn’s metric funcs to assert match up to 1e-6 on small synthetic data.

Prompt 9 (m6-t1 → m6-t3)

Build `gregory/ml/pseudo.py`:

* Function `generate_pseudo_labels(train_df, val_df, unlabelled_df, tokenizer, confidence=0.9, max_iter=7) -> pd.DataFrame`
  * Implements BERT self-training loop (reuse BertTrainer with frozen base).
* Function `save_pseudo_csv(df, dest_dir: Path) -> Path`
  * Filename pattern `<YYYYMMDD_HHMMSS>.csv`, suffix `_2` if exists.

Unit test with monkeypatched summariser & BertTrainer to ensure CSV path logic.

Prompt 10 (m7-t1 → m7-t3)

Create the management command file:

`gregory/management/commands/train_models.py`

* Use `BaseCommand`.
* Add argparse flags exactly as spec.
* Implement mutual-exclusion validation.
* Implement verbosity helper (enum or int) with cumulative output.

Write docstring summarising usage; no training yet.

Prompt 11 (m8-t1 → m8-t4)

Expand `train_models.py`:

* For each team & subject selected, loop through algorithms:
  * Create PredictionRunLog row with success=None, algorithm=algo.
  * Call dataset utils; on stratify error → mark fail, continue.
  * Always summarise via summariser.bulk.
  * If `--pseudo-label`, run pseudo pipeline and append data.
  * Instantiate trainer = get_trainer(algo), call `.train(...)`.
  * Compute metrics via utils.metrics, write `metrics.json`.
  * Save model + aux files via trainer.save().
  * Mark log success=True and run_finished timestamp.
* Wrap each algo in try/except → mark success=False with traceback.

Prompt 12 (m9-t1 → m9-t3)

Add `gregory/utils/verboser.py`:

* Class `Verboser(level: int)` with `.info()`, `.warn()`, `.summary(table: str)`.
* Integrate into `train_models.py` replacing bare prints.
* Unit tests asserting suppressed / shown output per level.

Prompt 13 (m10-t1 → m10-t3)

Write comprehensive tests:

1. Fixture: create in-memory SQLite DB with one team, one subject, minimal Articles & labels.
2. Run management command via Django’s call_command in test.
3. Assert:
   * PredictionRunLog rows = 3 (all algos).
   * Artifacts exist on disk & metrics.json has both val_ and test_ keys.
4. Add a README snippet documenting how to invoke the command in dev & prod.

Push all code, ensure `pytest` passes.


⸻

