# [GregoryAI](https://gregory-ai.com/) API EndPoints

[TOC]

## Preferred Endpoints vs Legacy

> **📋 MIGRATION COMPLETE:** The API has been successfully migrated from separate endpoints to query parameter filtering for better consistency and flexibility.

**Status**: All deprecated legacy endpoints have been removed. The migration to parameter-based filtering is complete.

**✅ New Parameter-Based Features Available:**
- `relevant=true` - Filter for ML-predicted and manually-reviewed relevant articles
- `open_access=true` - Filter for open access articles  
- `unsent=true` - Filter for articles not yet sent to subscribers
- `last_days=N` - Filter for articles from the last N days (can combine with other filters)
- `week=N&year=YYYY` - Filter for articles from a specific week (can combine with other filters)

**Usage Examples:**
```bash
# Basic usage with monthly counts (default threshold: 0.5)
GET /categories/?category_id=6&monthly_counts=true

# High confidence ML predictions (0.8)
GET /categories/?category_id=6&monthly_counts=true&ml_threshold=0.8

# Low threshold to include more predictions (0.3)
GET /categories/?category_id=6&monthly_counts=true&ml_threshold=0.3

# Very high confidence predictions only (0.95)
GET /categories/?category_id=6&monthly_counts=true&ml_threshold=0.95

# Get category data without monthly counts
GET /categories/?category_id=6
```

The endpoints use a flexible query parameter system that allows combining multiple filters.

| Filter | Preferred Endpoint | Benefits | 
|----------|-------------------|----------|
| Team articles | `GET /articles/?team_id=1` | Unified filtering, parameter combinations |
| Team subjects | `GET /subjects/?team_id=1` | Consistent filtering approach |
| Team + subject | `GET /articles/?team_id=1&subject_id=4` | Mix with other filters |
| Team + category (slug) | `GET /articles/?team_id=1&category_slug=natalizumab` | All filtering options available |
| Team + category (ID) | `GET /articles/?team_id=1&category_id=5` | Flexible ID-based filtering |
| Team + source | `GET /articles/?team_id=1&source_id=123` | Consistent with other endpoints |
| Relevant articles | `GET /articles/?relevant=true` | Boolean filtering for ML predictions and manual selection |
| Recent relevant articles | `GET /articles/?relevant=true&last_days=15` | Time-based filtering with relevance |
| Weekly relevant articles | `GET /articles/?relevant=true&week=52&year=2024` | Specific week filtering with relevance |
| Open access articles | `GET /articles/?open_access=true` | Filter by accessibility |
| Unsent articles | `GET /articles/?unsent=true` | Filter by notification status |
| Complex filtering | `GET /articles/?team_id=1&subject_id=4&author_id=123&search=stem&relevant=true` | Only possible with new approach |

### Legacy Endpoints Migration Complete

All legacy endpoints have been successfully migrated to the new parameter-based filtering system. The following endpoints have been **removed** and replaced:

| Former Legacy Pattern | Status | Current Endpoint |
|----------------------|--------|------------------|
| `GET /teams/{id}/articles/` | ✅ **Migrated** | `/articles/?team_id={id}` |
| `GET /teams/{id}/subjects/` | ✅ **Migrated** | `/subjects/?team_id={id}` |
| `GET /teams/{id}/articles/subject/{subject_id}/` | ✅ **Migrated** | `/articles/?team_id={id}&subject_id={subject_id}` |
| `GET /teams/{id}/articles/category/{category_slug}/` | ✅ **Migrated** | `/articles/?team_id={id}&category_slug={category_slug}` |
| `GET /teams/{id}/articles/source/{source_id}/` | ✅ **Migrated** | `/articles/?team_id={id}&source_id={source_id}` |
| `GET /articles/relevant/` | ✅ **Migrated** | `/articles/?relevant=true` |
| `GET /articles/relevant/last/{days}/` | ✅ **Migrated** | `/articles/?relevant=true&last_days={days}` |
| `GET /articles/relevant/week/{year}/{week}/` | ✅ **Migrated** | `/articles/?relevant=true&week={week}&year={year}` |
| `GET /articles/open-access/` | ✅ **Migrated** | `/articles/?open_access=true` |
| `GET /articles/unsent/` | ✅ **Migrated** | `/articles/?unsent=true` |

**Migration Benefits:**
- **Unified Filtering**: All filters can be combined in a single endpoint
- **Better Performance**: Optimized query structure for faster responses
- **Enhanced Flexibility**: Mix and match any combination of filters
- **Consistent API**: All endpoints follow the same parameter pattern

## Data Formats

All API endpoints support three formats:

1. **JSON** (default): Standard JSON format for API responses
2. **HTML** (browsable API): A user-friendly HTML interface for browsing the API
3. **CSV**: Data in comma-separated values format for spreadsheet applications

To get the response in CSV format, you can either:

1. Add the format parameter: `?format=csv` to the URL
2. Set the Accept header to `text/csv`

Example:
```bash
# Using format parameter
curl https://api.example.com/articles/?format=csv

# Using Accept header
curl -H "Accept: text/csv" https://api.example.com/articles/
```

### CSV Export Features

When exporting data as CSV, the following features are available:

1. **Pagination Bypass**: Add `all_results=true` to retrieve all data without pagination (useful for downloading complete datasets)
2. **Clean Data Format**: The CSV export automatically:
   - Consolidates nested fields (authors, subjects, clinical_trials, etc.) into single, readable columns
   - Properly formats and escapes text fields to handle special characters
   - Sets meaningful column names
   - Uses a standardized filename format (e.g., gregory-ai-articles-2023-07-15.csv)
3. **Type Safety**: All values are properly converted to strings to ensure compatibility with CSV format

Example for exporting all search results as CSV:
```bash
# Export all articles matching a search query as CSV
curl https://api.example.com/articles/search/?team_id=1&subject_id=1&search=covid&format=csv&all_results=true

# Export all clinical trials matching a search query as CSV
curl https://api.example.com/trials/search/?team_id=1&subject_id=1&status=Recruiting&format=csv&all_results=true
```

## Endpoints

| Model                   | API Endpoint                             | Description                                         | Parameters                                              | Status                                                   |
| ----------------------- | ---------------------------------------- | --------------------------------------------------- | ------------------------------------------------------- | -------------------------------------------------------- |
| Authors                 | GET /authors/                            | List all authors with comprehensive filtering       | `author_id`, `full_name`, `orcid`, `country`, `sort_by`, `order`, `team_id`, `subject_id`, `category_slug`, `date_from`, `date_to`, `timeframe` | ✅ **Available**                                       |
| Authors                 | POST /authors/                           | Create a new author                                 | N/A                                                     | ❌ **Not Available**                                  |
| Authors                 | GET /authors/{id}/                       | Retrieve a specific author by ID                    | `id` (path)                                             | ✅ **Available**                                       |
| Authors                 | PUT /authors/{id}/                       | Update a specific author by ID                      | N/A                                                     | ❌ **Not Available**                                  |
| Authors                 | DELETE /authors/{id}/                    | Delete a specific author by ID                      | N/A                                                     | ❌ **Not Available**                                  |
| Authors                 | GET /authors/search/                     | Search authors by full name with filters & optional CSV export | `team_id` *(req)*, `subject_id` *(req)*, `full_name`, `format`, `all_results` | ✅ **Available**                                       |
| Authors                 | GET /authors/by_team_subject/            | Get authors filtered by team and subject            | `team_id` *(req)*, `subject_id` *(req)*, additional filters | ✅ **Available**                                       |
| Authors                 | GET /authors/by_team_category/           | Get authors filtered by team and category           | `team_id` *(req)*, `category_slug` *(req)*, additional filters | ✅ **Available**                                       |
| Categories              | GET /categories/                         | List all categories with optional filters           | `team_id`, `subject_id`, `category_id`, `search`, `ordering`, `include_authors`, `max_authors`, pagination | ✅ **Available**                                       |
| Categories              | POST /categories/                        | Create a new category                               | N/A                                                     | ❌ **Not Available**                                  |
| Categories              | GET /categories/{id}/                    | Retrieve a specific category by ID                  | `id` (path)                                             | ✅ **Available**                                       |
| Categories              | GET /categories/{id}/authors/            | Get detailed author statistics for a category       | `id` (path), `min_articles`, `sort_by`, `order`, date filters | ✅ **Available**                                       |
| Categories              | GET /categories/{id}/monthly_counts/     | Get monthly article/trial counts with ML predictions| `id` (path), `ml_threshold` (0.0-1.0, default: 0.5)   | ✅ **Available**                                       |
| Categories              | PUT /categories/{id}/                    | Update a specific category by ID                    | N/A                                                     | ❌ **Not Available**                                  |
| Categories              | DELETE /categories/{id}/                 | Delete a specific category by ID                    | N/A                                                     | ❌ **Not Available**                                  |
| Team Categories         | GET /team-categories/                    | List all team categories                            | Standard pagination params                               | ✅ **Available** (via /categories/)                   |
| Team Categories         | POST /team-categories/                   | Create a new team category                          | N/A                                                     | ❌ **Not Available**                                  |
| Team Categories         | GET /team-categories/{id}/               | Retrieve a specific team category by ID             | `id` (path)                                             | ✅ **Available** (via /categories/{id}/)              |
| Team Categories         | PUT /team-categories/{id}/               | Update a specific team category by ID               | N/A                                                     | ❌ **Not Available**                                  |
| Team Categories         | DELETE /team-categories/{id}/            | Delete a specific team category by ID               | N/A                                                     | ❌ **Not Available**                                  |
| Entities                | GET /entities/                           | List all entities                                   | N/A                                                     | ❌ **Not Available**                                  |
| Entities                | POST /entities/                          | Create a new entity                                 | N/A                                                     | ❌ **Not Available**                                  |
| Entities                | GET /entities/{id}/                      | Retrieve a specific entity by ID                    | N/A                                                     | ❌ **Not Available**                                  |
| Entities                | PUT /entities/{id}/                      | Update a specific entity by ID                      | N/A                                                     | ❌ **Not Available**                                  |
| Entities                | DELETE /entities/{id}/                   | Delete a specific entity by ID                      | N/A                                                     | ❌ **Not Available**                                  |
| Subjects                | GET /subjects/                           | List all subjects with optional team filtering      | `team_id`, `search`, `ordering`, pagination        | ✅ **Available**                                       |
| Subjects                | POST /subjects/                          | Create a new subject                                | N/A                                                     | ❌ **Not Available**                                  |
| Subjects                | GET /subjects/{id}/                      | Retrieve a specific subject by ID                   | `id` (path)                                             | ✅ **Available**                                       |
| Subjects                | PUT /subjects/{id}/                      | Update a specific subject by ID                     | N/A                                                     | ❌ **Not Available**                                  |
| Subjects                | DELETE /subjects/{id}/                   | Delete a specific subject by ID                     | N/A                                                     | ❌ **Not Available**                                  |
| Sources                 | GET /sources/                            | List all sources with optional filters              | `team_id`, `subject_id`, `source_for`, `search`, `ordering`, pagination | ✅ **Available**                                       |
| Sources                 | POST /sources/                           | Create a new source                                 | N/A                                                     | ❌ **Not Available**                                  |
| Sources                 | GET /sources/{id}/                       | Retrieve a specific source by ID                    | `id` (path)                                             | ✅ **Available**                                       |
| Sources                 | PUT /sources/{id}/                       | Update a specific source by ID                      | N/A                                                     | ❌ **Not Available**                                  |
| Sources                 | DELETE /sources/{id}/                    | Delete a specific source by ID                      | N/A                                                     | ❌ **Not Available**                                  |
| Articles                | GET /articles/                           | List all articles with comprehensive filters        | `team_id`, `subject_id`, `author_id`, `category_slug`, `category_id`, `journal_slug`, `source_id`, `search`, `ordering`, `relevant`, `open_access`, `unsent`, `last_days`, `week`, `year`, pagination | ✅ **Available**                                       |
| Articles                | POST /articles/                          | Create a new article                                | `title`, `link`, `doi`, `summary`, `source_id`, etc.   | ✅ **Available** (via /articles/post/)                |
| Articles                | GET /articles/{id}/                      | Retrieve a specific article by ID                   | `id` (path)                                             | ✅ **Available**                                       |
| Articles                | PUT /articles/{id}/                      | Update a specific article by ID                     | N/A                                                     | ❌ **Not Available**                                  |
| Articles                | DELETE /articles/{id}/                   | Delete a specific article by ID                     | N/A                                                     | ❌ **Not Available**                                  |
| Articles                | GET /articles/search/                    | Search articles with filters & optional CSV export  | `team_id` *(req)*, `subject_id` *(req)*, `title`, `summary`, `search`, `format`, `all_results` | ✅ **Available**                                       |
| Articles                | POST /articles/search/                   | Search articles (POST method)                       | Same as GET but in request body                         | ✅ **Available**                                       |
| Trials                  | GET /trials/                             | List all trials with optional filters               | `team_id`, `subject_id`, `category_id`, `source_id`, `status`, `search`, `ordering`, pagination | ✅ **Available**                                       |
| Trials                  | POST /trials/                            | Create a new trial                                  | N/A                                                     | ❌ **Not Available**                                  |
| Trials                  | GET /trials/{id}/                        | Retrieve a specific trial by ID                     | `id` (path)                                             | ✅ **Available**                                       |
| Trials                  | PUT /trials/{id}/                        | Update a specific trial by ID                       | N/A                                                     | ❌ **Not Available**                                  |
| Trials                  | DELETE /trials/{id}/                     | Delete a specific trial by ID                       | N/A                                                     | ❌ **Not Available**                                  |
| Trials                  | GET /trials/search/                      | Search trials with filters & optional CSV export    | `team_id` *(req)*, `subject_id` *(req)*, `title`, `summary`, `search`, `status`, `format`, `all_results` | ✅ **Available**                                       |
| Trials                  | POST /trials/search/                     | Search trials (POST method)                         | Same as GET but in request body                         | ✅ **Available**                                       |
| Teams                   | GET /teams/                              | List all teams                                      | Standard pagination params                               | ✅ **Available**                                       |
| Teams                   | POST /teams/                             | Create a new team                                   | N/A                                                     | ❌ **Not Available**                                  |
| Teams                   | GET /teams/{id}/                         | Retrieve a specific team by ID                      | `id` (path)                                             | ✅ **Available**                                       |
| Teams                   | PUT /teams/{id}/                         | Update a specific team by ID                        | N/A                                                     | ❌ **Not Available**                                  |
| Teams                   | DELETE /teams/{id}/                      | Delete a specific team by ID                        | N/A                                                     | ❌ **Not Available**                                  |
| Teams                   | GET /teams/{id}/articles/                | ⚠️ **DEPRECATED** - List all articles for a specific team by ID | `id` (path), enhanced filtering params, pagination     | ⚠️ **Use /articles/?team_id={id} instead**           |
| Teams                   | GET /teams/{id}/subjects/                | ⚠️ **DEPRECATED** - List all subjects for specific team by ID | `id` (path), enhanced filtering params, pagination | ⚠️ **Use /subjects/?team_id={id} instead**           |
| Teams                   | GET /teams/{id}/subjects/{subject_id}/categories/ | List all categories for a team filtered by subject | `id` (path), `subject_id` (path), pagination params | ✅ **Available**                                       |
| Teams                   | GET /teams/{id}/articles/subject/{subject_id}/     | ⚠️ **DEPRECATED** - List all articles for a team filtered by subject | `id` (path), `subject_id` (path), enhanced filtering params | ⚠️ **Use /articles/?team_id={id}&subject_id={subject_id} instead** |
| Teams                   | GET /teams/{id}/articles/category/{category_slug}/ | ⚠️ **DEPRECATED** - List all articles for a team filtered by category | `id` (path), `category_slug` (path)           | ⚠️ **Use /articles/?team_id={id}&category_slug={category_slug} instead** |
| Teams                   | GET /teams/{id}/articles/source/{source_id}/       | ⚠️ **DEPRECATED** - List all articles for a team filtered by source | `id` (path), `source_id` (path)               | ⚠️ **Use /articles/?team_id={id}&source_id={source_id} instead** |
| Teams                   | GET /teams/{id}/categories/{category_slug}/monthly_counts/ | Get monthly counts for a category | `id` (path), `category_slug` (path), `ml_threshold` | ✅ **Available**                                       |
| MLPredictions           | GET /ml-predictions/                     | List all ML predictions                             | N/A                                                     | ❌ **Not Available**                                  |
| MLPredictions           | POST /ml-predictions/                    | Create a new ML prediction                          | N/A                                                     | ❌ **Not Available**                                  |
| MLPredictions           | GET /ml-predictions/{id}/                | Retrieve a specific ML prediction by ID             | N/A                                                     | ❌ **Not Available**                                  |
| MLPredictions           | PUT /ml-predictions/{id}/                | Update a specific ML prediction by ID               | N/A                                                     | ❌ **Not Available**                                  |
| MLPredictions           | DELETE /ml-predictions/{id}/             | Delete a specific ML prediction by ID               | N/A                                                     | ❌ **Not Available**                                  |
| ArticleSubjectRelevance | GET /article-subject-relevances/         | List all article subject relevances                 | N/A                                                     | ❌ **Not Available**                                  |
| ArticleSubjectRelevance | POST /article-subject-relevances/        | Create a new article subject relevance              | N/A                                                     | ❌ **Not Available**                                  |
| ArticleSubjectRelevance | GET /article-subject-relevances/{id}/    | Retrieve a specific article subject relevance by ID | N/A                                                     | ❌ **Not Available**                                  |
| ArticleSubjectRelevance | PUT /article-subject-relevances/{id}/    | Update a specific article subject relevance by ID   | N/A                                                     | ❌ **Not Available**                                  |
| ArticleSubjectRelevance | DELETE /article-subject-relevances/{id}/ | Delete a specific article subject relevance by ID   | N/A                                                     | ❌ **Not Available**                                  |

## Additional Endpoints

| Category                | API Endpoint                             | Description                                         | Parameters                                              | Status                                                   |
| ----------------------- | ---------------------------------------- | --------------------------------------------------- | ------------------------------------------------------- | -------------------------------------------------------- |
| **Authentication**      | POST /api/token/                         | Obtain JWT authentication token                     | `username`, `password` (body)                          | ✅ **Available**                                       |
| **Authentication**      | POST /api/token/get/                     | Obtain auth token (alternative)                     | `username`, `password` (body)                          | ✅ **Available**                                       |
| **Authentication**      | GET /protected_endpoint/                 | Test protected endpoint                             | Requires authentication header                          | ✅ **Available**                                       |
| **Subscriptions**       | POST /subscriptions/new/                 | Subscribe to email lists                            | `first_name`, `last_name`, `email`, `profile`, `list` (body) | ✅ **Available**                                       |
| **Email Templates**     | GET /emails/                             | Email template preview dashboard                    | None                                                    | ✅ **Available**                                       |
| **Email Templates**     | GET /emails/preview/{template_name}/     | Preview specific email template                     | `template_name` (path)                                  | ✅ **Available**                                       |
| **Email Templates**     | GET /emails/context/{template_name}/     | Get template context as JSON                        | `template_name` (path)                                  | ✅ **Available**                                       |
| **RSS Feeds**           | GET /feed/author/<str:orcid>/            | RSS feed for articles by author (ORCID)            | `orcid` (path)                                          | ✅ **Available**                                       |

## Remaining Team-Based Endpoints

Some team-based URL patterns are still available for backward compatibility, though they are marked as deprecated in favor of the new parameter-based filtering approach.

### Still Available (Deprecated)

The following legacy endpoints are still functional but **deprecated**. They continue to work with enhanced filtering capabilities:

```bash
# Still available: Get all articles for a team (enhanced with full filtering)
GET /teams/{team_id}/articles/?format=json&page=1
# Enhanced capabilities: &search=keyword&author_id=X&category_slug=Y&journal_slug=Z&ordering=field

# Still available: Get articles for a team filtered by subject (enhanced with full filtering) 
GET /teams/{team_id}/articles/subject/{subject_id}/?format=json
# Enhanced capabilities: &search=keyword&author_id=X&category_slug=Y&journal_slug=Z&ordering=field

# Still available: Get subjects for a team (enhanced with full filtering)
GET /teams/{team_id}/subjects/?format=json
# Enhanced capabilities: &search=keyword&ordering=field

# Still available: Get articles for a team filtered by category
GET /teams/{team_id}/articles/category/{category_slug}/

# Still available: Get articles for a team filtered by source
GET /teams/{team_id}/articles/source/{source_id}/
```

### Active Team-Based Endpoints

These team-based endpoints remain active and are **not deprecated**:
```bash
# Active: Get categories for a team and subject
GET /teams/{team_id}/subjects/{subject_id}/categories/

# Active: Get monthly counts for a category
GET /teams/{team_id}/categories/{category_slug}/monthly_counts/
# Optional ML filtering: ?ml_threshold=0.8
```

**Enhanced filtering capabilities available on deprecated endpoints:**
- `?author_id=X` - Filter by author ID (articles only)
- `?category_slug=slug` - Filter by category (articles only)
- `?journal_slug=slug` - Filter by journal (articles only, URL-encoded)
- `?source_id=Y` - Filter by source ID (articles only)
- `?search=keyword` - Full-text search in title/summary (articles) or name/description (subjects)
- `?ordering=field` - Order by discovery_date, published_date, title, article_id (articles) or id, subject_name (subjects) (add `-` for reverse)

### Migration Recommendation

While the deprecated endpoints continue to work, we **strongly recommend** migrating to the new parameter-based endpoints for better performance and flexibility:

# Standard: Get monthly counts for a category
GET /categories/{category_id}/monthly_counts/
# Optional ML filtering: ?ml_threshold=0.8
```

```bash
# Legacy: Get all articles for a team
GET /teams/{team_id}/articles/?format=json&page=1

# Legacy: Get articles for a team filtered by subject
GET /teams/{team_id}/articles/subject/{subject_id}/?format=json

# Legacy: Get articles for a team filtered by category
GET /teams/{team_id}/articles/category/{category_slug}/

# Legacy: Get articles for a team filtered by source
GET /teams/{team_id}/articles/source/{source_id}/

# Legacy: Get subjects for a team
GET /teams/{team_id}/subjects/

# Legacy: Get categories for a team and subject
GET /teams/{team_id}/subjects/{subject_id}/categories/

```bash
# DEPRECATED: Basic legacy usage
GET /teams/1/articles/?format=json&page=1

# ENHANCED: Legacy usage with new filtering (still deprecated)
GET /teams/1/articles/?format=json&page=1&search=multiple+sclerosis&author_id=123&ordering=-published_date

# PREFERRED: Equivalent using main endpoint
GET /articles/?team_id=1&search=multiple+sclerosis&author_id=123&ordering=-published_date

# DEPRECATED: Subject filtering
GET /teams/1/articles/subject/4/?format=json&search=regeneration&category_slug=stem-cells

# PREFERRED: Equivalent using main endpoint
GET /articles/?team_id=1&subject_id=4&search=regeneration&category_slug=stem-cells

# DEPRECATED: Subjects for team
GET /teams/1/subjects/?format=json&search=multiple&ordering=subject_name

# PREFERRED: Equivalent using main endpoint  
GET /subjects/?team_id=1&search=multiple&ordering=subject_name
```

- **Consistent API**: All endpoints follow the same parameter pattern
- **Better Performance**: Optimized for the new filtering architecture
- **Enhanced Flexibility**: All filters can be combined in any way
- **Future-Proof**: Built for scalability and new features

## Parameter Details

### Common Parameters
- **Common Parameters**: `page`, `page_size`
- **Format params**: `format` (json, csv, html), `all_results` (true/false for CSV export)
- **Author filter params**: `author_id`, `full_name`, `orcid`, `country`, `sort_by`, `order`, `team_id`, `subject_id`, `category_slug`, `date_from`, `date_to`, `timeframe`
- **Articles filter params**: `team_id`, `subject_id`, `author_id`, `category_slug`, `journal_slug` (URL-encoded journal name), `source_id`, `search` (title/summary), `ordering` (discovery_date, published_date, title, article_id)
- **Subjects filter params**: `team_id`, `search` (subject_name/description), `ordering` (id, subject_name, team)
- **Sources filter params**: `source_id`, `team_id`, `subject_id`, `active`, `source_for` (articles, trials, both), `link`, `search` (name/description), `ordering` (name, source_id)
- **Categories filter params**: `category_id`, `team_id`, `subject_id`, `category_terms`, `search` (name/description), `ordering` (category_name, id)
- **Trials filter params**: `trial_id`, `team_id`, `subject_id`, `category_id`, `source_id`, `status`/`recruitment_status`, `internal_number`, `phase`, `study_type`, `primary_sponsor`, `source_register`, `countries`, `condition`, `intervention`, `therapeutic_areas`, `inclusion_agemin`, `inclusion_agemax`, `inclusion_gender`, `search` (title/summary), `ordering` (discovery_date, published_date, title, trial_id)

### Sources Endpoint Filtering

The `/sources/` endpoint supports comprehensive filtering and searching:

**Filtering:**
- `?team_id=X` - Filter sources by team ID
- `?subject_id=Y` - Filter sources by subject ID
- `?source_for=type` - Filter sources by content type (articles, trials, both)
- `?team_id=X&subject_id=Y` - Filter by both team and subject

**Searching:**
- `?search=keyword` - Search in source name and description fields

**Ordering:**
- `?ordering=name` - Order by name (default)
- `?ordering=source_id` - Order by source ID
- `?ordering=-name` - Reverse order (add `-` prefix)

**Example Usage:**
```bash
# Filter sources by team
GET /sources/?team_id=1

# Filter sources by team and subject
GET /sources/?team_id=1&subject_id=2

# Filter sources by content type
GET /sources/?source_for=articles

# Filter sources for trials only
GET /sources/?source_for=trials

# Filter sources that provide both articles and trials
GET /sources/?source_for=both

# Search and filter combined
GET /sources/?team_id=1&search=pubmed&ordering=name&source_for=articles
```

### Authors Endpoint Filtering

The `/authors/` endpoint supports comprehensive filtering and searching:

**Filtering:**
- `?author_id=X` - Filter by specific author ID
- `?full_name=name` - Search authors by full name (case-insensitive)
- `?orcid=identifier` - Filter by ORCID identifier (case-insensitive contains search)
- `?country=code` - Filter authors by country code (exact match)
- `?team_id=X` - Filter authors by team ID
- `?subject_id=Y` - Filter authors by subject ID (use with team_id)
- `?category_slug=slug` - Filter authors by category slug (use with team_id)
- `?category_id=Y` - Filter authors by category ID (use with team_id)

**Date Filtering:**
- `?date_from=YYYY-MM-DD` - Filter articles from this date
- `?date_to=YYYY-MM-DD` - Filter articles to this date  
- `?timeframe=year|month|week` - Relative timeframe filtering

**Sorting:**
- `?sort_by=author_id|article_count` - Sort by field (default: author_id)
- `?order=asc|desc` - Sort order (default: desc for article_count, asc for others)

**Special Endpoints:**
- `/authors/by_team_subject/?team_id=X&subject_id=Y` - Authors for specific team+subject combination
- `/authors/by_team_category/?team_id=X&category_slug=slug` - Authors for specific team+category combination (by slug)
- `/authors/by_team_category/?team_id=X&category_id=Y` - Authors for specific team+category combination (by ID)

**Example Usage:**
```bash
# Get specific author by ID
GET /authors/?author_id=380002

# Search authors by name
GET /authors/?full_name=John%20Smith

# Filter authors by ORCID
GET /authors/?orcid=0000-0000-0000-0001

# Filter authors by country
GET /authors/?country=US

# Authors by team
GET /authors/?team_id=1

# Authors by team and subject with article count sorting
GET /authors/?team_id=1&subject_id=2&sort_by=article_count&order=desc

# Authors by team and category (by slug)
GET /authors/?team_id=1&category_slug=natalizumab&sort_by=article_count

# Authors by team and category (by ID)
GET /authors/?team_id=1&category_id=5&sort_by=article_count

# Authors with date filtering
GET /authors/?team_id=1&subject_id=2&timeframe=year&sort_by=article_count

# Specific team+subject endpoint
GET /authors/by_team_subject/?team_id=1&subject_id=2

# Specific team+category endpoint (by slug)  
GET /authors/by_team_category/?team_id=1&category_slug=natalizumab

# Specific team+category endpoint (by ID)
GET /authors/by_team_category/?team_id=1&category_id=5
```

### Subjects Endpoint Filtering

The `/subjects/` endpoint supports filtering and searching:

**Filtering:**
- `?team_id=X` - Filter subjects by team ID

**Searching:**
- `?search=keyword` - Search in subject name and description fields

**Ordering:**
- `?ordering=id` - Order by ID (default)
- `?ordering=subject_name` - Order by subject name
- `?ordering=team` - Order by team
- `?ordering=-subject_name` - Reverse order (add `-` prefix)

**Example Usage:**
```bash
# Filter subjects by team
GET /subjects/?team_id=1

# Search subjects by name or description
GET /subjects/?search=multiple

# Team filter with search combined
GET /subjects/?team_id=1&search=sclerosis&ordering=subject_name
```

### Articles Endpoint Filtering

The `/articles/` endpoint supports comprehensive filtering and searching:

**Filtering:**
- `?team_id=X` - Filter articles by team ID
- `?subject_id=Y` - Filter articles by subject ID
- `?author_id=Z` - Filter articles by author ID
- `?category_slug=slug` - Filter articles by category slug
- `?journal_slug=slug` - Filter articles by journal (URL-encoded journal name)
- `?source_id=W` - Filter articles by source ID
- Multiple filters can be combined

**Searching:**
- `?search=keyword` - Search in article title and summary fields

**Ordering:**
- `?ordering=-discovery_date` - Order by discovery date (default, newest first)
- `?ordering=published_date` - Order by published date
- `?ordering=title` - Order by title
- `?ordering=article_id` - Order by article ID
- `?ordering=-published_date` - Reverse order (add `-` prefix)

**Example Usage:**
```bash
# Replace old endpoints with new filtering
# OLD: /articles/author/123/
# NEW: /articles/?author_id=123

# OLD: /articles/category/natalizumab/
# NEW: /articles/?category_slug=natalizumab

# OLD: /articles/journal/the-lancet-neurology/
# NEW: /articles/?journal_slug=The%20Lancet%20Neurology

# Multiple filters combined
GET /articles/?team_id=1&subject_id=2&author_id=123&category_slug=natalizumab&search=multiple+sclerosis&ordering=-published_date
```

### Categories Endpoint Filtering

The `/categories/` endpoint supports comprehensive filtering, searching, and analytics:

**Filtering:**
- `?team_id=X` - Filter categories by team ID
- `?subject_id=Y` - Filter categories by subject ID
- `?category_id=Z` - Filter to specific category ID
- `?team_id=X&subject_id=Y` - Filter by both team and subject

**Author Analytics:**
- `?include_authors=false` - Exclude top authors data (default: true)
- `?max_authors=20` - Maximum number of top authors to return (default: 10, max: 50)

**Monthly Analytics:**
- `?monthly_counts=true` - Include monthly article/trial counts with ML predictions (default: false)
- `?ml_threshold=0.8` - ML prediction probability threshold when monthly_counts=true (0.0-1.0, default: 0.5)

**Date Filtering:**
- `?date_from=2024-01-01` - Filter articles from this date (YYYY-MM-DD)
- `?date_to=2024-12-31` - Filter articles to this date (YYYY-MM-DD) 
- `?timeframe=year` - Filter by relative timeframe ('year', 'month', 'week')

**Searching:**
- `?search=keyword` - Search in category name and description fields

**Ordering:**
- `?ordering=category_name` - Order by category name (default)
- `?ordering=id` - Order by category ID
- `?ordering=-category_name` - Reverse order (add `-` prefix)

**Example Usage:**
```bash
# Filter categories by team
GET /categories/?team_id=1

# Filter categories by team and subject
GET /categories/?team_id=1&subject_id=2

# Single category with monthly analytics
GET /categories/?category_id=6&monthly_counts=true&ml_threshold=0.8

# Category analytics for current year
GET /categories/?team_id=1&timeframe=year&monthly_counts=true

# Search and filter combined
GET /categories/?team_id=1&search=natalizumab&ordering=category_name
```

### Trials Endpoint Filtering

The `/trials/` endpoint supports comprehensive filtering and searching:

**Core Filtering:**
- `?trial_id=X` - Filter by specific trial ID
- `?team_id=X` - Filter trials by team ID
- `?subject_id=Y` - Filter trials by subject ID
- `?category_id=Z` - Filter trials by category ID
- `?source_id=W` - Filter trials by source ID
- `?status=recruiting` or `?recruitment_status=recruiting` - Filter by recruitment status

**Trial-Specific Filtering:**
- `?internal_number=INT-2024-001` - Filter by WHO internal number
- `?phase=Phase III` - Filter by trial phase
- `?study_type=Interventional` - Filter by study type
- `?primary_sponsor=University` - Filter by sponsor organization
- `?source_register=ClinicalTrials.gov` - Filter by source registry
- `?countries=United States` - Filter by trial countries

**Medical/Research Filtering:**
- `?condition=COVID-19` - Filter by medical condition
- `?intervention=vaccine` - Filter by intervention type
- `?therapeutic_areas=Infectious` - Filter by therapeutic areas
- `?inclusion_agemin=18` - Filter by minimum age
- `?inclusion_agemax=65` - Filter by maximum age  
- `?inclusion_gender=All` - Filter by gender inclusion

**Searching:**
- `?search=keyword` - Search in trial title and summary fields

**Ordering:**
- `?ordering=-discovery_date` - Order by discovery date (default, newest first)
- `?ordering=published_date` - Order by published date
- `?ordering=title` - Order by title
- `?ordering=trial_id` - Order by trial ID
- `?ordering=-published_date` - Reverse order (add `-` prefix)

**Example Usage:**
```bash
# Filter trials by specific trial ID
GET /trials/?trial_id=12345

# Filter trials by phase and condition
GET /trials/?phase=Phase III&condition=COVID-19

# Filter by sponsor and country
GET /trials/?primary_sponsor=University&countries=United States

# Medical research filtering
GET /trials/?therapeutic_areas=Neurology&inclusion_agemin=18&inclusion_agemax=65

# Combined filtering with search
GET /trials/?team_id=1&phase=Phase II&search=alzheimer&ordering=-published_date

# Filter by internal number and recruitment status
GET /trials/?internal_number=INT-2024&recruitment_status=Recruiting
```

### Monthly Counts Endpoint with ML Filtering

The `/categories/{category_id}/monthly_counts/` endpoint provides monthly aggregated data with optional ML prediction filtering:

**Parameters:**
- `category_id` (path, required) - Category ID
- `ml_threshold` (query, optional) - ML prediction probability threshold (0.0-1.0, default: 0.5)

**Response Fields:**
- `monthly_article_counts` - Total articles by month
- `monthly_ml_article_counts_by_model` - Articles with latest ML predictions >= threshold by month, separated by model
- `monthly_trial_counts` - Total trials by month
- `ml_threshold` - The threshold value used for ML filtering
- `available_models` - List of ML algorithms found in the data (e.g., ["pubmed_bert", "lgbm_tfidf", "lstm"])
- `category_name` - Category name
- `category_slug` - Category slug

**Important Notes:**
- Only the most recent ML prediction for each article-model combination is considered
- Each model (pubmed_bert, lgbm_tfidf, lstm, etc.) provides separate monthly counts
- Articles can have predictions from multiple models

**Example Usage:**
```bash
# Default ML threshold (0.5)
GET /teams/1/categories/natalizumab/monthly-counts/

# High confidence ML predictions (0.8)
GET /teams/1/categories/natalizumab/monthly-counts/?ml_threshold=0.8

# Low threshold to include more predictions (0.3)
GET /teams/1/categories/natalizumab/monthly-counts/?ml_threshold=0.3

# Very high confidence predictions only (0.95)
GET /teams/1/categories/natalizumab/monthly-counts/?ml_threshold=0.95
```

**Example Response:**
```json
{
  "category_name": "Natalizumab",
  "category_slug": "natalizumab",
  "ml_threshold": 0.8,
  "monthly_article_counts": [
    {"month": "2023-01-01T00:00:00Z", "count": 25},
    {"month": "2023-02-01T00:00:00Z", "count": 18}
  ],
  "monthly_ml_article_counts": [
    {"month": "2023-01-01T00:00:00Z", "count": 8},
    {"month": "2023-02-01T00:00:00Z", "count": 5}
  ],
  "monthly_trial_counts": [
    {"month": "2023-01-01T00:00:00Z", "count": 3},
    {"month": "2023-02-01T00:00:00Z", "count": 1}
  ]
}
```

### Search Endpoint Requirements
- **Required params** *(marked with req)*: Must be provided for the endpoint to function
- **Optional params**: Can be omitted, will use default values
- **Path params**: Part of the URL path, must be provided when using the endpoint

## Legend

- ✅ **Available**: Endpoint is implemented and functional
- ❌ **Not Available**: Endpoint is not implemented (write operations are generally disabled for security)
- ⚠️ **Limited**: Endpoint exists but has restrictions or limitations