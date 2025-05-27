




## **Part 1: High-Level Blueprint**





1. **Database migrations**

   

   1. Add auto_predict to Subject.
   2. Add algorithm to MLPredictions and update its unique constraint.

   

2. **Command skeleton**

   

   1. Create predict_articles.py under gregory/management/commands/.
   2. Wire up BaseCommand, add all CLI flags, and validate mutual exclusions.

   

3. **Configuration & constants**

   

   1. Hard-code the model base path.
   2. Define defaults (lookback=90, threshold=0.8, algorithms list).

   

4. **Filtering logic**

   

   1. Resolve scope flags → list of teams and subjects.
   2. For each subject, fetch articles by discovery_date + no existing prediction.

   

5. **Model loading**

   

   1. Given <team>/<subject>/<algorithm> folder, pick latest or --model-version.
   2. Load weights, vectorizer/tokenizer as needed.

   

6. **Preprocessing**

   

   1. Concatenate title+summary → cleanHTML → cleanText.

   

7. **Prediction loop**

   

   1. Create PredictionRunLog entry.
   2. For each article: run model → compute predicted_relevant.
   3. On exception, record failed IDs; otherwise prepare MLPredictions rows.
   4. Bulk create new rows (skip duplicates).

   

8. **Logging & output**

   

   1. Update PredictionRunLog.success, timestamps, counts.
   2. Print progress per --verbose.
   3. On --verbose 3, render ASCII summary table.

   

9. **Dry-run support**

   

   1. Wrap all DB writes behind a if not dry_run guard.

   

10. **Exit code**

    

    - Always exit 0.

    

11. **Testing**

    

    1. Unit tests for parser, filters, version resolver, duplicate logic, table formatter.
    2. Integration dry-run against SQLite with mocks.
    3. Regression test: no new rows on repeat runs.

    





------





## **Part 2: Iterative Chunks**





1. **Migrations**
2. **Command entrypoint & parser**
3. **Scope resolution & filtering**
4. **Model-version resolution & loader**
5. **Preprocessing utilities integration**
6. **Per-run prediction driver**
7. **MLPredictions bulk insert + duplicate handling**
8. **Logging, verbosity, and summary**
9. **Dry-run mode**
10. **Unit test suite**





------





## **Part 3: Breaking Chunks into Small Steps**







### **Chunk 1: Migrations**





- Create migration file to add auto_predict to Subject.
- Create migration file to add algorithm to MLPredictions.
- Modify MLPredictions.Meta.unique_together to include algorithm.
- Backfill existing MLPredictions.algorithm='unknown'.







### **Chunk 2: Command entrypoint & parser**





- Scaffold predict_articles.py with class Command(BaseCommand).
- Add add_arguments() registering all flags.
- Implement error checks for missing/mutually-exclusive flags.
- Write stub handle() that just prints parsed options.







### **Chunk 3: Scope resolution & filtering**





- In handle(), load teams based on --team/--all-teams.
- For each team, filter its Subject.objects.filter(auto_predict=True), apply --subject.
- Write function get_articles(subject, lookback) returning preliminary QuerySet.







### **Chunk 4: Model-version resolution & loader**





- Write helper resolve_model_version(path, explicit=None).
- Write helper load_model(team, subject, algorithm, version) that raises if missing.







### **Chunk 5: Preprocessing utilities integration**





- Import cleanHTML, cleanText from gregory/utils/text_utils.py.
- Write function prepare_text(article) performing concat + cleaning.
- Unit-test prepare_text() on a small sample.







### **Chunk 6: Per-run prediction driver**





- Write run_for(subject, algorithm, options) that:

  

  - Creates PredictionRunLog.
  - Calls model loader.
  - Iterates articles → predict or record failure.
  - Returns stats dict.

  







### **Chunk 7: MLPredictions bulk insert + duplicate handling**





- Build list of MLPredictions instances.
- Use bulk_create(..., ignore_conflicts=True) to skip duplicates.
- Count how many were inserted vs skipped.







### **Chunk 8: Logging, verbosity, and summary**





- After each run_for, update its log row, print per-verbosity.
- Collect all stats; if --verbose>=3, render ASCII table.







### **Chunk 9: Dry-run mode**





- At top of handle(), if dry_run: wrap PredictionRunLog.objects.create and bulk_create in no-ops.
- Ensure summary still reflects what would have happened.







### **Chunk 10: Unit test suite**





- For parser: simulate call_command() with various flag combos → expect success or error.
- For get_articles(), freeze time and create sample articles → assert correct filtering.
- For resolve_model_version(), create temp dirs → assert correct version choice.
- For duplicate logic: pre-seed a row → bulk_create(ignore_conflicts) → count skip.
- For summary formatter: feed synthetic stats → verify ASCII output.





------





## **Part 4: Refine & Validate Step Size**





Each chunk contains 3–6 concrete tasks with clear inputs/outputs. They can be tackled in isolation, with unit tests ensuring safety before moving on. No step relies on unimplemented later functionality.



------





## **Part 5: Code-Generation Prompts**

Below are the ordered prompts to hand to a code-generation LLM. Each is self-contained and builds on the previous.

```
Context: We’re adding two schema changes to our Django project ‘gregory’. First, add a BooleanField(auto_predict) to the Subject model. Second, add a CharField(algorithm) to the MLPredictions model and update its unique_together to ('article','subject','model_version','algorithm'). Backfill existing rows to algorithm='unknown'. Write the Django migration files and model updates only.
```

```
Context: Scaffold a new Django management command file at gregory/management/commands/predict_articles.py. The command should subclass BaseCommand, define add_arguments() with flags: --team, --subject, --all-teams, --lookback-days, --algo, --model-version, --prob-threshold, --verbose, --dry-run. In handle(), validate that either --team or --all-teams is provided, and that --subject requires --team. If validation fails, call self.print_usage() and exit 1.
```

```
Context: In predict_articles.handle(), implement scope resolution: if --all-teams, get all Team.objects.all(); else get Team.objects.filter(slug=team_slug). For each team, select subjects = team.subjects.filter(auto_predict=True), and if --subject is set, further filter subjects by subject_slug. Raise CommandError if no teams or no subjects found.
```

```
Context: Write a helper function get_articles(subject, lookback_days) that returns Articles queryset for that subject with discovery_date >= today - timedelta(days=lookback_days). Import datetime.date and use timezone.now().date() for today. Also filter out any article that already has an MLPredictions entry for the current subject, algorithm, and current model_version (pass algorithm and model_version as parameters).
```

```
Context: Write resolve_model_version(base_path, explicit_version=None) which lists subdirectories under base_path; if explicit_version is provided, return it (error if not exists); else pick the lexicographically largest folder name. Raise FileNotFoundError if none found.
```

```
Context: Write load_model(team, subject, algorithm, model_version) that constructs the path "{models_base}/{team.slug}/{subject.subject_slug}/{algorithm}/{model_version}", checks for expected files (e.g. bert_weights.h5 for pubmed_bert, lstm_weights.h5 & tokenizer.json for lstm, classifier.joblib & tfidf_vectorizer.joblib for lgbm_tfidf), and loads them (e.g. via keras.models.load_model or joblib.load). Wrap missing-file errors in a custom exception.
```

```
Context: Import cleanHTML and cleanText from gregory/utils/text_utils.py. Implement prepare_text(article) to return cleanText(cleanHTML(f"{title} {summary}")). If article.summary is empty, return cleanText(cleanHTML(article.title)).
```

```
Context: Implement function run_for(subject, algorithm, options): 1) create PredictionRunLog with run_type='predict', triggered_by='cli', timestamps auto; 2) call resolve_model_version & load_model; 3) loop over articles from get_articles(); for each, call prepare_text, run model.predict → probability; compare to options.prob_threshold; collect successes and failures; 4) bulk_create MLPredictions(ignore_conflicts=True); 5) update log.success, run_finished, error_message if any; return stats dict.
```

```
Context: In handle(), iterate teams→subjects→algorithms, call run_for(), collect stats. After all runs, if options.verbose>=3, print an ASCII table with columns “team | subject | algorithm | processed | skipped | success | failures”. Use tabulate or manual string formatting.
```

```
Context: Add dry-run support: wrap all Model.objects.create() and bulk_create() calls inside “if not options.dry_run” guards. Ensure that even in dry-run mode, get_articles, resolve_model_version, load_model, prepare_text, and run_for logic execute fully but no DB writes happen.
```

```
Context: Create unit tests in gregory/tests/management/test_predict_articles.py. Write tests for: argument parsing (valid & invalid flag combos), get_articles filtering logic (using freeze_time and sample Articles), resolve_model_version behavior (with temporary directories), duplicate-skipping via bulk_create(ignore_conflicts), and summary table formatting for verbose=3. Use pytest and Django’s call_command for CLI tests.
```

