

1 — High-level Goal

Create a Django management command train_models (inside the gregory app) that trains GregoryAI’s three production classifiers for any combination of teams and subjects, writes versioned model artifacts to disk, and records a PredictionRunLog row for every (subject × algorithm) run.

⸻

2 — CLI Contract

Flag	Type / Values	Default	Semantics
--team <slug>	string (team slug)	required unless --all-teams	Scope limiter
--subject <slug>	string (subject slug within the chosen team)	optional	Scope limiter
--all-teams	boolean	false	Train for every team in DB
--all-articles	boolean	false	Use all labeled articles (ignores 90-day window)
--lookback-days N	int	none	Override 90-day window (error if used with --all-articles)
--algo a,b,c	comma-list of pubmed_bert,lgbm_tfidf,lstm	all three	Subset of algorithms to train
--prob-threshold X	float	0.8	Cut-off used both for metrics and persisted threshold
--version V	string	auto (YYYYMMDD + optional _n)	Manual version tag
--pseudo-label	flag	off	Run BERT self-training loop before final training
--verbose N	0·1·2·3	1	0 quiet → 3 (progress + warnings + summary)

Scope logic
	•	No --team and no --subject ⇒ print usage, exit.
	•	--team alone ⇒ all subjects of that team.
	•	--subject without --team ⇒ usage error.
	•	--all-teams ignores --team/--subject if they’re supplied.

⸻

3 — Data Assembly Pipeline
	1.	Article filter
	•	Discovery window: last 90 days on discovery_date unless
	•	--all-articles (everything) or
	•	--lookback-days N (past N days).
	2.	Label rule
	•	Include only rows with an ArticleSubjectRelevance for this subject.
	•	Drop any unlabeled articles.
	3.	Text material
	•	Always concatenate title + summary (summary will be generated).
	•	If summary is NULL/"", fall back to title only.
	4.	Mandatory summarisation
	•	Model facebook/bart-large-cnn, max 300 tokens, batch 4, GPU auto-detect.
	•	No caching.
	5.	Dataset split (stratified)
	•	70 % train, 15 % validation, 15 % test.
	•	Fixed random seed 69.
	•	If stratification impossible, skip (subject × algo) and mark log row success=False.
	6.	Pseudo-labelling (opt-in)
	•	PubMed BERT self-training (confidence_threshold=0.9, max_iterations=7).
	•	Generates CSV → /home/brunoamaral/gregory-ai/django/models/<team>/<subject>/pseudo_labels/<YYYYMMDD_HHMMSS>[_n].csv.
	•	File gets numeric suffix if needed.

⸻

4 — Training Specifications

Algorithm	Fixed Hyper-params	Epochs / Rounds	Input Text	Extra Artifacts
PubMed BERT	lr = 2e-5, dense_units = 48, freeze_weights = False, max_len = 400	10 epochs	title + generated summary	—
LGBM + TF-IDF	defaults in LGBM_TFIDF_Classifier()	100 boosting rounds	title + generated summary	tfidf_vectorizer.joblib
LSTM	params in LSTM_algorithm_utils.py	10 epochs	title + generated summary	tokenizer.json

	•	Class imbalance: no weighting or resampling.
	•	Training order: sequential; no parallelism.
	•	Probability threshold: global per run (--prob-threshold, default 0.8) and used in metrics.

⸻

5 — Output Layout

/home/brunoamaral/gregory-ai/django/models/
    <team_slug>/<subject_slug>/<algorithm>/<version>/
        bert_weights.h5                # PubMed BERT
        lgbm_classifier.joblib         # LGBM
        lstm_weights.h5                # LSTM
        tfidf_vectorizer.joblib        # (if algo = lgbm_tfidf)
        tokenizer.json                 # (if algo = lstm)
        metrics.json

	•	Version auto-naming: first run on a day → YYYYMMDD; subsequent runs append _2, _3, … .
	•	metrics.json – flat dict holding val_* and test_* keys for
accuracy, precision, recall, f1, roc_auc, pr_auc.
	•	No changeable output dir; path is hard-coded.

⸻

6 — Database Changes

class PredictionRunLog(models.Model):
    ...
    algorithm = models.CharField(
        max_length=20,
        choices=[
            ('pubmed_bert', 'PubMed BERT'),
            ('lgbm_tfidf', 'LGBM + TF-IDF'),
            ('lstm', 'LSTM')
        ],
        null=True, blank=True        # stays optional
    )

	•	Migration sets "unknown" for existing rows.
	•	Every new log row must fill algorithm.

⸻

7 — Execution Flow

for each team/subject selected:
    for each algorithm selected:
        create PredictionRunLog (run_type='train', success=None)
        build dataset → split
        if pseudo-label flag: generate & merge pseudo-labels
        train model
        compute metrics  ➜ metrics.json
        save model & aux files
        mark log row success=True
        except Exception:
            log error, success=False, continue
print summary table if --verbose >=3


⸻

8 — Verbosity

Level	Console Output
0	nothing
1 (default)	progress messages (Started …, Finished …)
2	level 1 + warnings/skips
3	level 2 + final summary table of all metrics


⸻

9 — Error Handling & Validation
	•	Mutually exclusive --all-articles vs --lookback-days → usage error.
	•	--subject without --team → usage error.
	•	Unknown slugs or algos → usage error.
	•	Training error ⇒ mark that log row success=False, capture traceback string, continue.
	•	Stratification error ⇒ treat as training error above.

⸻

10 — Testing Plan

Layer	Tests
Unit	• Flag parser edge-cases• Dataset filter logic• Version name generator (counter suffix)• Metrics computation with threshold
Integration	• End-to-end dry-run using a tiny fixture DB (sqlite) and monkey-patched summariser/trainer that returns dummy artifacts.• Verify artifacts on disk & PredictionRunLog contents.
Regression	• Re-run command twice on same day ⇒ second version gets _2.• --verbose levels produce expected stdout.
Performance smoke	• Run with --team small-team --subject small-subject --algo pubmed_bert and assert completes < X minutes on CI box (summariser mocked).


⸻

11 — Next Steps for Dev
	1.	Create migration adding algorithm field.
	2.	Implement command skeleton & argument parser.
	3.	Code dataset builder & summariser wrapper (re-use existing helpers).
	4.	Wire training classes; lift hyper-params from utils modules.
	5.	Implement metrics collector.
	6.	Implement file-system writer & versioning helper.
	7.	Unit + integration tests.
	8.	Update docs / README.