# **Specification:** 

# **predict_articles**

#  **Django Management Command**







## **1 — Purpose**





Create a management command that classifies newly-discovered articles for every team / subject that opted‐in to automatic prediction, stores the results in MLPredictions, and logs each (subject × algorithm) run in PredictionRunLog.



------





## **2 — CLI contract**



| **Flag**           | **Type / Values**                         | **Default**                          | **Semantics**                                    |
| ------------------ | ----------------------------------------- | ------------------------------------ | ------------------------------------------------ |
| --team <slug>      | string                                    | —                                    | Limit scope to one team                          |
| --subject <slug>   | string                                    | —                                    | Limit scope to one subject (must supply --team)  |
| --all-teams        | boolean                                   | false                                | Ignore --team/--subject, predict for every team  |
| --lookback-days N  | int                                       | 90 days                              | Select articles whose discovery_date ≥ today − N |
| --algo a,b,c       | comma-list of pubmed_bert,lgbm_tfidf,lstm | all three                            | Sub-set of algorithms to use                     |
| --model-version V  | string                                    | *latest available*                   | Force a specific model version                   |
| --prob-threshold X | float                                     | value saved with model (default 0.8) | Override probability cut-off                     |
| --verbose N        | 0 · 1 · 2 · 3                             | 1                                    | Console verbosity (see §11)                      |
| --dry-run          | flag                                      | off                                  | Run everything except DB writes; exit afterward  |



### **Scope rules**





- No --team and no --all-teams ⇒ usage error.
- --subject without --team ⇒ usage error.
- --all-teams ignores --team/--subject if supplied.





------





## **3 — Model & data prerequisites**







### **3.1 Model directory**



```
gregory-ai/django/models/<team>/<subject>/<algorithm>/<version>/
```

The command loads the weights/vectoriser present in that folder.

If --model-version is omitted, choose the folder with the lexicographically-largest name (most recent).



Missing artefacts:



- Create a PredictionRunLog with success=False, store the traceback, proceed to next algorithm.







### **3.2 Database schema changes**





1. **Subject.auto_predict** – BooleanField(default=False)

   *Only subjects with* *auto_predict=True* *are considered.*

2. **MLPredictions.algorithm** – CharField(max_length=20, choices=[…])

   Add unique_together = ('article', 'subject', 'model_version', 'algorithm').

   Provide a migration; set "unknown" for legacy rows.





------





## **4 — Article selection pipeline**



| **Step**                | **Logic**                                                    |
| ----------------------- | ------------------------------------------------------------ |
| 1 — Subject filter      | Only Subject objects with auto_predict=True, further reduced by CLI scope flags. |
| 2 — Article base set    | Articles.objects.filter(subjects=<subject>)                  |
| 3 — Newness filter      | discovery_date >= today - timedelta(days=lookback_days) (default 90). |
| 4 — Prediction gap      | Exclude any article that already has an MLPredictions row with the same (subject, model_version, algorithm). |
| 5 — Summary requirement | Skip article if summary is NULL/empty; log as “skipped (no summary)”. |

Any subject-algorithm pair ending with zero **processed+skipped** items still gets a log row (success=True, counts 0) and a level-2 warning.



------





## **5 — Pre-processing**





For each remaining article:

```
from gregory.utils.text_utils import cleanText, cleanHTML

text_in = f"{article.title} {article.summary}"
text_in = cleanHTML(text_in)
text_processed = cleanText(text_in)
```

Pass text_processed to the selected classifier.



------





## **6 — Prediction flow (per subject × algorithm)**





1. Create PredictionRunLog

   *Fields*: team, subject, algorithm, run_type='predict', model_version, triggered_by='cli'.

2. Load model artefacts.

3. For each article:

   

   - Try predict → probability p
   - predicted_relevant = p >= prob_threshold
   - On success: append to results list
   - On exception: record in failed_ids list; continue

   

4. Bulk-create MLPredictions objects (skipping duplicates).

5. Update log: success = len(failed_ids) == 0, run_finished=now, plus counts: processed, skipped, failures.

6. If --verbose >=3, collect counts for the final summary table.





Per-article errors never abort the run; they increment “failures” and are summarised at the end of that run.



------





## **7 — CLI exit behaviour**





- Always exit with **code 0**.
- CI should examine PredictionRunLog for failures.





------





## **8 — Duplicate handling**





If a row with identical (article, subject, model_version, algorithm) already exists:



- Skip insertion.
- Increment “skipped” count.
- No updates to existing row.





------





## **9 — Dry-run mode**





- All filters, model-loading, and predictions execute.
- **No DB writes**: skip creation of MLPredictions and PredictionRunLog.
- Verbose output and summary table behave normally and indicate “DRY-RUN” in the header.





------





## **10 — Iteration order**



```python
for team in teams:
    for subject in subjects_of_team:
        for algorithm in selected_algorithms:
            run_prediction()
```

Sequential execution, no parallelism.



------





## **11 — Verbosity levels**



| **Level** | **Console output**                                |
| --------- | ------------------------------------------------- |
| 0         | silent                                            |
| 1         | Started …, Finished …                             |
| 2         | Level 1 + warnings (skipped, missing model, etc.) |
| 3         | Level 2 + one final ASCII table:                  |
| `         | team                                              |



------





## **12 — Error handling summary**



| **Type**                 | **Action**                                    |
| ------------------------ | --------------------------------------------- |
| Missing model artefact   | Log row success=False; continue               |
| Subject with no articles | Log row success=True, counts 0; warn level 2  |
| Article prediction error | Record in failed_ids; continue                |
| Duplicate prediction row | Increment “skipped”; continue                 |
| CLI misuse               | Print usage + exit 1 *before* any DB activity |



------





## **13 — Testing plan**



| **Layer**                                              | **Tests**                                                    |
| ------------------------------------------------------ | ------------------------------------------------------------ |
| Unit                                                   | * Flag parsing (all flags, mutual exclusions)                |
| * Discovery-date filtering (default & --lookback-days) |                                                              |
| * Model version resolver (latest vs explicit)          |                                                              |
| * Duplicate-skip logic                                 |                                                              |
| * Summary-table formatter                              |                                                              |
| Integration (SQLite fixture)                           | * Dry-run end-to-end: ensure no DB writes, counts correct    |
| Regression                                             | * Execute command twice → second run creates no new MLPredictions rows |
| Performance smoke                                      | * Subject with ≤50 articles completes < X s on CI with mocked models |

Mock model utilities return deterministic predictions to keep tests fast.



------





## **14 — Implementation checklist**





1. **Migrations**

   

   - Add Subject.auto_predict
   - Add MLPredictions.algorithm + adjust uniqueness

   

2. **Command skeleton** gregory/management/commands/predict_articles.py

   

   - ArgParser with all flags
   - Validator for mutual exclusions

   

3. **Helper modules**

   

   - model_io.py → load latest or specified version
   - article_filters.py → centralise queryset logic

   

4. **Prediction runner**

   

   - Handles one (subject, algorithm) combo, returns stats dict

   

5. **Summary printer**

   

   - Generates table for verbose 3

   

6. **Unit tests** in gregory/tests/management/test_predict_articles.py

7. **Documentation**

   

   - Update README and developer wiki with usage examples
