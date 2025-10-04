---
applyTo: "*"
---
# Project Overview  GregoryAI
Django-based ML-powered platform that aggregates biomedical research papers and clinical trials, uses NLP/ML to predict relevance, and sends automated newsletters to stakeholders.

This is a django backend service called GregoryAi, it is designed to fetch data from several sources via rss, api posts, and manual submissions. 

 
Main features:
- organize and store science papers (articles) and clinical trials (trials) from various sources
- provide a REST API for accessing article data
- main objects in the database: Article, Author, Sources, Subject, Team, Trials
- automated newsletter with latest articles and trials per subject, and categories
- admin email with latest articles and trials per subject to manually review the ML predictions
- Automatic prediction of relevant articles based on Machine Learning models described in `files_repo_PBL_nsbe` folder

## Architecture & Data Flow
 
## Folder Structure
**Core Pipeline** (orchestrated by `pipeline.py` management command):
1. **Ingest**: `feedreader_articles`, `feedreader_trials` fetch from RSS/APIs
2. **Enrich**: `find_doi` → `update_articles_info` → `get_authors` → `update_orcid` queries CrossRef/ORCID for metadata
3. **Categorize**: `rebuild_categories` assigns TeamCategory based on keyword matching in titles
4. **ML Predict**: `predict_articles --all-teams` runs ensemble models (BERT, LGBM, LSTM) with configurable consensus rules
5. **Output**: `send_weekly_summary`, `send_admin_summary` deliver filtered content via email
 
- `/django`: Contains the Django backend code.
- `files_repo_PBL_nsbe`: Contains documentation and examples for the machine learning models used in the project.
- `/docs`: Contains documentation for the project, including API specifications and user guides.
**Key Django Apps**:
- `gregory/`: Core models, ML trainers (`ml/`), management commands (`management/commands/`)
- `api/`: DRF viewsets with custom authentication (API key in `Authorization` header, validated via `APIAccessScheme`)
- `subscriptions/`: Newsletter system with `Lists`, `Subscribers`, tracking sent notifications
- `sitesettings/`: `CustomSetting` key-value store for runtime config

**Critical Models** (always reference `django/gregory/models.py`):
- `Articles`: Main content, M2M to `Authors`, `Sources`, `Team`, `Subject`, `TeamCategory`
- `ArticleSubjectRelevance`: Junction table tracking ML predictions + manual review (`relevant` field: None/True/False)
- `MLPredictions`: Stores per-model scores, linked to `PredictionRunLog` for versioning
- `Subject`: Has `ml_consensus_type` ('any'/'majority'/'all') controlling how ensemble predictions combine

## Libraries and Frameworks
- Django: The main web framework used for the backend.
- Django REST Framework: Used to build the API endpoints.
## Coding Standards
**All commands run inside `gregory` container**:
```bash
# Standard pattern for management commands
docker exec gregory python manage.py <command>
```

- use tabs for indentation
- always check django/gregory/models.py when creating new models, adding features, or writing sql queries.
# Common operations
docker exec gregory python manage.py pipeline                    # Run full pipeline
docker exec gregory python manage.py train_models --team ms-research --subject ms --algo pubmed_bert
docker exec gregory python manage.py predict_articles --all-teams
docker exec gregory python manage.py send_weekly_summary --days 7
docker exec gregory python manage.py test gregory.tests.test_encrypted_field
 
# Database migrations
```bash
docker exec gregory python manage.py makemigrations
docker exec gregory python manage.py migrate
```
 

**Environment**: Managed via `docker-compose.yaml` + `.env` file. Critical vars: `FERNET_SECRET_KEY` (for encrypted fields), `ORCID_ClientID/Secret`, `EMAIL_*` (Mailgun/Postmark), `POSTGRES_*`.
 
## UI guidelines
- Application should have a modern and clean design.

## Machine Learning System

- **Three Model Types** (in `django/gregory/ml/`):
- `pubmed_bert`: PubMedBERT fine-tuned (96.5% recall target)
- `lgbm_tfidf`: LightGBM with TF-IDF features
- `lstm`: LSTM neural network

-- tests are located in the `tests` folder within the Django app.
-- when running commands, assume that the app is running as a docker container called `gregory`.
\ No newline at end of file
**Training Pattern**:
```bash
python manage.py train_models --team SLUG --subject SLUG --algo pubmed_bert --epochs 5 --threshold 0.5
```
- Writes versioned artifacts to `django/models/TEAM/SUBJECT/ALGO/vN/`
- Creates `PredictionRunLog` row with metrics + model path
- Uses pseudo-labeling for semi-supervised learning (high-confidence predictions become training data)

**Prediction Flow**:
- `predict_articles` loads latest models per team/subject
- Scores saved to `MLPredictions` (one row per model per article)
- `ArticleSubjectRelevance.ml_prediction` set based on `Subject.ml_consensus_type`:
  - `'any'`: At least one model predicts relevant
  - `'majority'`: 2+ models agree
  - `'all'`: All models must agree
- Threshold check: model score >= list's `ml_prediction_threshold` (configurable per newsletter list)

## API Authentication

**Custom scheme** (NOT DRF token auth):
```python
# Client sends:
headers = {'Authorization': '<raw_api_key>'}  # NO "Api-Key" prefix!

# Backend validates via:
api_key = getAPIKey(request)  # Extracts from Authorization header
access_scheme = checkValidAccess(api_key, ip_addr)  # Checks APIAccessScheme table
```

**POST /articles/post/** expects:
```json
{
  "doi": "10.1234/...",
  "kind": "science paper",
  "source_id": 11,
  "title": "...",       // Optional - fetched from CrossRef if missing
  "summary": "...",     // Optional - abstract text
  "link": "...",
  "container_title": "..." // Journal name
}
```

Backend auto-fetches missing metadata via `SciencePaper` class (queries CrossRef API).

## Testing Conventions

- Tests in `django/APP/tests/test_*.py`
- Use `TransactionTestCase` for tests involving signals or multi-model transactions
- Standalone test files (e.g., `test_train_models_standalone.py`) can run outside Django test runner
- Run via: `docker exec gregory python manage.py test gregory.tests.test_module`

## Code Style

- **Tabs for indentation** (not spaces)
- Model changes: Always check `django/gregory/models.py` first - it's the source of truth for schema
- Management commands: Subclass `BaseCommand`, use `self.stdout.write(self.style.SUCCESS(...))` for output
- Encrypted fields: Use `EncryptedTextField` from `gregory.fields` (requires `FERNET_SECRET_KEY`)

## Critical Patterns

**Category Assignment** (`rebuild_categories`):
- Scans `TeamCategory.category_terms` (array of keywords)
- Searches in `Articles.utitle` (uppercase generated field with GIN index for fast ILIKE)
- M2M link via `article.team_categories.add(category)`

**Newsletter Filtering** (`send_weekly_summary`):
- Excludes articles where `relevant=False` for ALL subjects in the list
- Respects `ml_prediction_threshold` per list (default 0.5)
- Tracks sent items in `SentArticleNotification` to avoid duplicates
- Uses `--dry-run` flag for testing without sending

**CrossRef Integration** (`SciencePaper` class):
- DOI → metadata lookup via `refresh()` method
- Populates `container_title`, `publisher`, `access`, `published_date`
- Handles rate limiting and retries

## Common Gotchas

1. **Import errors in ML code**: ML dependencies optional - wrap imports in try/except, check `ML_AVAILABLE` flag
2. **API 401s**: Ensure `Authorization` header has raw key (no prefix). Check `APIAccessScheme` has valid `begin_date`/`end_date`
3. **Missing authors**: Run `get_authors` after `update_articles_info` - author extraction depends on CrossRef data
4. **Category not assigning**: Check `utitle` field populated, verify `category_terms` contains exact match (case-insensitive ILIKE)
5. **Newsletter not sending**: Verify `Lists.send_weekly_digest=True`, check `ml_prediction_threshold`, ensure articles not in `SentArticleNotification`

## Key Files Reference

- `django/gregory/models.py`: All model definitions (711 lines - read this first!)
- `django/gregory/management/commands/pipeline.py`: Orchestrates full data pipeline
- `django/api/views.py`: API endpoints with custom auth (1699 lines)
- `django/gregory/classes.py`: `SciencePaper` class for CrossRef integration
- `files_repo_PBL_nsbe/`: ML research context + training notebooks