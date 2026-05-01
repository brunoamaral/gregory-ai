# GregoryAI API — Endpoint Reference

Complete reference for all API endpoints with request/response schemas.

---

## Articles

### GET /articles/

List all articles with comprehensive filtering. Default ordering: `-discovery_date`.

**Auth**: Optional (read-only without auth)

**Query Parameters**:

| Parameter | Type | Description |
|-----------|------|-------------|
| `team_id` | int | Filter by team ID |
| `subject_id` | int | Filter by subject ID |
| `subjects` | string | Comma-separated subject IDs with AND semantics — returns only articles tagged with *all* listed subjects (e.g., `1,2`) |
| `author_id` | int | Filter by author ID |
| `doi` | string | Filter by exact DOI (case-insensitive) |
| `category_slug` | string | Filter by category slug |
| `category_id` | int | Filter by category ID |
| `journal_slug` | string | Filter by journal name (URL-encoded, spaces as dashes) |
| `source_id` | int | Filter by source ID |
| `title` | string | Search in title only |
| `summary` | string | Search in summary/abstract only |
| `search` | string | Search in both title and summary |
| `relevant` | bool | Filter for relevant articles (ML or manual). Scoped to `subject_id` when provided |
| `ml_threshold` | float (0.0-1.0) | Minimum ML prediction confidence. Default 0.8 when used with `relevant` |
| `open_access` | bool | Filter open access articles |
| `last_days` | int | Articles from last N days |
| `week` | int | Week number (requires `year`) |
| `year` | int | Year (used with `week`) |
| `ordering` | string | Sort field: `discovery_date`, `published_date`, `title`, `article_id`. Prefix `-` for desc |
| `page` | int | Page number (default: 1) |
| `page_size` | int | Items per page (default: 10, max: 100) |
| `all_results` | bool | Bypass pagination when `true` |
| `format` | string | Response format: `json` (default) or `csv` |

**Response fields per article**:

```json
{
  "article_id": 12345,
  "title": "Article title",
  "summary": "Abstract text...",
  "link": "https://...",
  "published_date": "2025-01-15T00:00:00Z",
  "discovery_date": "2025-01-16T10:30:00Z",
  "doi": "10.1234/...",
  "access": "open",
  "publisher": "Publisher Name",
  "container_title": "Journal Name",
  "takeaways": "Key takeaways...",
  "sources": ["Source Name 1", "Source Name 2"],
  "teams": [{"id": 1, "name": "Team Name", ...}],
  "subjects": [{"id": 4, "subject_name": "Subject", "description": "...", "team_id": 1}],
  "authors": [
    {"author_id": 100, "given_name": "John", "family_name": "Doe", "full_name": "John Doe", "ORCID": "0000-...", "country": "US"}
  ],
  "team_categories": [
    {"id": 5, "category_name": "Category", "category_description": "...", "category_slug": "slug", "category_terms": ["term1", "term2"]}
  ],
  "ml_predictions": [
    {"id": 1, "algorithm": "pubmed_bert", "model_version": "v1", "probability_score": 0.95, "predicted_relevant": true, "created_date": "2025-01-16", "subject": 4}
  ],
  "article_subject_relevances": [
    {"subject": {"id": 4, "subject_name": "...", "description": "...", "team_id": 1}, "is_relevant": true}
  ],
  "clinical_trials": [
    {"trial_id": 1, "title": "Trial title", "summary": "...", "link": "https://..."}
  ]
}
```

### GET /articles/{id}/

Get a single article by ID. Same response schema as list items.

---

## POST /articles/post/

Create a new article. Uses custom API key auth (not JWT).

**Auth**: Required — raw API key in `Authorization` header (NO prefix)

**Request Body**:

```json
{
  "doi": "10.1234/example",
  "kind": "science paper",
  "source_id": 11,
  "title": "Optional if DOI provided",
  "summary": "Optional abstract",
  "link": "https://...",
  "published_date": "2025-01-15",
  "access": "open",
  "publisher": "Publisher Name",
  "container_title": "Journal Name"
}
```

**Required fields**: `kind`, `source_id`, and at least one of `doi` or `title`.

**Behavior**: If DOI is provided, missing fields are auto-fetched from CrossRef. If an article with the same DOI already exists, the source/team/subject are updated on the existing article.

**Success Response** (200):

```json
{
  "name": "Gregory | API",
  "version": "0.1b",
  "data_received": {...},
  "data_processed_from_doi": {...},
  "article_id": 12345
}
```

**Error Response** (various codes):

```json
{
  "code": 6,
  "error_msg": "One or more fields wasn't found in the payload",
  "extra_data": "field `kind` was not found in the payload"
}
```

| Code | Meaning |
|------|---------|
| 0 | Unexpected error |
| 1 | No API key provided |
| 2 | Access denied (period/quota) |
| 3 | Invalid API key |
| 4 | Unauthorized IP address |
| 5 | Source not found |
| 6 | Missing required field |
| 7 | Article already exists |
| 8 | Article could not be saved |

---

## Trials

### GET /trials/

List all clinical trials. Default ordering: `-discovery_date`.

**Auth**: Optional

**Query Parameters**:

| Parameter | Type | Description |
|-----------|------|-------------|
| `trial_id` | int | Filter by specific trial ID |
| `team_id` | int | Filter by team ID |
| `subject_id` | int | Filter by subject ID |
| `subjects` | string | Comma-separated subject IDs with AND semantics — returns only trials tagged with *all* listed subjects (e.g., `1,2`) |
| `category_slug` | string | Filter by category slug |
| `category_id` | int | Filter by category ID |
| `source_id` | int | Filter by source ID |
| `title` | string | Search in title only |
| `summary` | string | Search in summary only |
| `search` | string | Search in title and summary |
| `recruitment_status` / `status` | string | Filter by recruitment status (e.g., Recruiting, Completed) |
| `internal_number` | string | Filter by WHO internal number (contains) |
| `phase` | string | Filter by trial phase (contains) |
| `study_type` | string | Filter by study type (contains) |
| `primary_sponsor` | string | Filter by sponsor (contains) |
| `source_register` | string | Filter by source registry (contains) |
| `countries` | string | Filter by countries (contains) |
| `condition` | string | Filter by medical condition (contains) |
| `intervention` | string | Filter by intervention (contains) |
| `therapeutic_areas` | string | Filter by therapeutic areas (contains) |
| `inclusion_agemin` | string | Filter by min age |
| `inclusion_agemax` | string | Filter by max age |
| `inclusion_gender` | string | Filter by gender criteria |
| `ordering` | string | Sort field: `discovery_date`, `published_date`, `title`, `trial_id` |
| `page`, `page_size`, `all_results`, `format` | | Same as articles |

**Additional response field — trials list includes `stats` object**:

```json
{
  "count": 500,
  "stats": {
    "total": 500,
    "no_status": 10,
    "recruiting": 150,
    "active_not_recruiting": 50,
    "not_yet_recruiting": 30,
    "completed": 200,
    "enrolling_by_invitation": 5,
    "terminated": 20,
    "suspended": 5,
    "withdrawn": 10,
    "available": 5,
    "not_available": 3,
    "withheld": 2,
    "authorised": 10
  },
  "results": [...]
}
```

### GET /trials/{id}/

Get a single trial by ID.

**Response fields per trial**:

```json
{
  "trial_id": 1,
  "title": "...",
  "summary": "...",
  "published_date": "...",
  "discovery_date": "...",
  "link": "...",
  "source": "Source Name",
  "identifiers": "...",
  "team_categories": [...],
  "scientific_title": "...",
  "primary_sponsor": "...",
  "recruitment_status": "Recruiting",
  "phase": "Phase III",
  "study_type": "Interventional",
  "study_design": "...",
  "countries": "...",
  "condition": "...",
  "intervention": "...",
  "primary_outcome": "...",
  "secondary_outcome": "...",
  "inclusion_criteria": "...",
  "exclusion_criteria": "...",
  "target_size": "...",
  "contact_firstname": "...",
  "contact_lastname": "...",
  "contact_email": "...",
  "contact_affiliation": "...",
  "articles": [
    {"article_id": 1, "title": "...", "summary": "...", "link": "..."}
  ]
}
```

---

## Search Endpoints

### GET/POST /articles/search/

Advanced article search. **Requires `team_id` AND `subject_id`**.

**Auth**: None required

**Parameters** (query params for GET, body for POST):

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `team_id` | int | Yes | Team ID |
| `subject_id` | int | Yes | Subject ID |
| `title` | string | No | Search in title |
| `summary` | string | No | Search in summary |
| `search` | string | No | Search in both |
| `ordering` | string | No | Sort field |
| `page`, `page_size`, `all_results`, `format` | | No | Pagination/export |

All ArticleFilter parameters also apply.

### GET/POST /trials/search/

Advanced trial search. **Requires `team_id` AND `subject_id`**.

Same parameter pattern as article search, plus:
- `status`: Filter by recruitment status

All TrialFilter parameters also apply.

### GET/POST /authors/search/

Advanced author search. **Requires `team_id` AND `subject_id`**.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `team_id` | int | Yes | Team ID |
| `subject_id` | int | Yes | Subject ID |
| `full_name` | string | No | Search by full name (case-insensitive, partial match) |

---

## Authors

### GET /authors/

List all authors with sorting and filtering.

**Auth**: Optional

**Query Parameters**:

| Parameter | Type | Description |
|-----------|------|-------------|
| `author_id` | int | Filter by specific author ID |
| `full_name` | string | Search by full name (case-insensitive) |
| `given_name` | string | Search by given name (case-insensitive) |
| `family_name` | string | Search by family name (case-insensitive) |
| `orcid` | string | Filter by ORCID (contains) |
| `country` | string | Filter by country code (exact) |
| `sort_by` | string | `article_count` or `author_id` (default) |
| `order` | string | `asc` or `desc` |
| `team_id` | int | Filter by team |
| `subject_id` | int | Filter by subject (requires team_id) |
| `category_slug` | string | Filter by category slug (requires team_id) |
| `category_id` | int | Filter by category ID (requires team_id) |
| `date_from` | date (YYYY-MM-DD) | Filter articles from date |
| `date_to` | date (YYYY-MM-DD) | Filter articles to date |
| `timeframe` | string | `year`, `month`, `week` (relative) |

**Note**: `subject_id`, `category_slug`, and `category_id` require `team_id` to be set, otherwise an empty result is returned.

**Response fields per author**:

```json
{
  "author_id": 100,
  "given_name": "John",
  "family_name": "Doe",
  "full_name": "John Doe",
  "ORCID": "0000-0001-2345-6789",
  "country": "US",
  "articles_count": 15,
  "articles_list": "https://api.example.com/articles/?author_id=100"
}
```

### GET /authors/by_team_subject/

Requires `team_id` and `subject_id`.

### GET /authors/by_team_category/

Requires `team_id` and either `category_slug` or `category_id`.

---

## Categories

### GET /categories/

List all categories with optional statistics.

**Auth**: Optional

**Query Parameters**:

| Parameter | Type | Description |
|-----------|------|-------------|
| `team_id` | int | Filter by team |
| `subject_id` | int | Filter by subject |
| `category_id` | int | Filter by specific category |
| `get_categories` | string | Comma-separated list of category IDs (e.g., `1,2,3`) |
| `include_authors` | bool | Include top authors (default: `true`) |
| `max_authors` | int | Max top authors per category (default: 10, max: 50) |
| `date_from` | date | Article date range start |
| `date_to` | date | Article date range end |
| `timeframe` | string | `year`, `month`, `week` |
| `monthly_counts` | bool | Include monthly article/trial counts with ML predictions (default: `false`) |
| `ml_threshold` | float | ML threshold for monthly counts (default: 0.5) |

**Response fields per category**:

```json
{
  "id": 5,
  "category_name": "Natalizumab",
  "category_description": "...",
  "category_slug": "natalizumab",
  "category_terms": ["natalizumab", "tysabri"],
  "article_count_total": 150,
  "trials_count_total": 20,
  "authors_count": 300,
  "top_authors": [
    {"author_id": 100, "given_name": "John", "family_name": "Doe", "full_name": "John Doe", "ORCID": "...", "country": "US", "articles_count": 15}
  ],
  "monthly_counts": null
}
```

When `monthly_counts=true`:

```json
{
  "monthly_counts": {
    "ml_threshold": 0.5,
    "available_models": ["pubmed_bert", "lgbm_tfidf", "lstm"],
    "monthly_article_counts": [{"month": "2025-01-01T00:00:00Z", "count": 10}],
    "monthly_ml_article_counts_by_model": {
      "pubmed_bert": [{"month": "2025-01-01T00:00:00Z", "count": 8}]
    },
    "monthly_trial_counts": [{"month": "2025-01-01T00:00:00Z", "count": 3}]
  }
}
```

### GET /categories/{id}/authors/

Detailed author statistics for a specific category.

**Query Parameters**:

| Parameter | Type | Description |
|-----------|------|-------------|
| `min_articles` | int | Minimum articles per author (default: 1) |
| `sort_by` | string | `articles_count` (default) or `author_name` |
| `order` | string | `asc` or `desc` (default) |
| `date_from`, `date_to`, `timeframe` | | Date filtering (same as main) |

---

## Sources

### GET /sources/

List all data sources.

**Auth**: Optional

**Query Parameters**:

| Parameter | Type | Description |
|-----------|------|-------------|
| `source_id` | int | Filter by source ID |
| `team_id` | int | Filter by team |
| `subject_id` | int | Filter by subject |
| `active` | bool | Filter active/inactive sources |
| `source_for` | string | Filter by source type |
| `link` | string | Filter by link (contains) |

**Response fields per source**:

```json
{
  "source_id": 11,
  "source_for": "articles",
  "name": "PubMed RSS",
  "description": "...",
  "link": "https://...",
  "subject_id": 4,
  "team_id": 1
}
```

---

## Teams

### GET /teams/

List all teams.

**Auth**: Optional

**Response**:

```json
[
  {"id": 1, "name": "MS Research", ...}
]
```

---

## Subjects

### GET /subjects/

List all subjects with optional team filtering.

**Auth**: Optional

**Query Parameters**:

| Parameter | Type | Description |
|-----------|------|-------------|
| `team_id` | int | Filter by team |
| `search` | string | Search in name and description |
| `ordering` | string | `id`, `subject_name`, `team` |

**Response fields per subject**:

```json
{
  "id": 4,
  "subject_name": "Multiple Sclerosis",
  "description": "...",
  "team_id": 1
}
```

---

## Stats

### GET /stats/

Aggregate statistics. No auth required.

**Query Parameters**:

| Parameter | Type | Description |
|-----------|------|-------------|
| `team` | string | Team ID or comma-separated IDs (e.g., `1` or `1,2,3`) |

**Response**:

```json
{
  "articles": 5000,
  "trials": 800,
  "subscribers": 150,
  "authors": 12000,
  "sources": {
    "total": 25,
    "by_type": {"articles": 20, "trials": 5},
    "by_domain": [
      {"domain": "pubmed.ncbi.nlm.nih.gov", "count": 8},
      {"domain": "clinicaltrials.gov", "count": 5}
    ]
  }
}
```

---

## RSS Feeds

### GET /feed/author/{orcid}/

RSS feed of articles by a specific author (by ORCID).

### GET /feed/trials/subject/{subject_slug}/

RSS feed of trials for a specific subject.

---

## Rate Limiting (POST /articles/post/ only)

The custom API key auth enforces rate limits configured per `APIAccessScheme`:
- Per-minute limit (default: 60)
- Per-hour limit (default: 3600)
- Per-day limit (default: 86400)
- Valid date range (`begin_date` to `end_date`)
- Optional IP address whitelist
