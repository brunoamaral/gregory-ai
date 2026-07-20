# API and RSS feeds

> Audience: developers integrating with the GregoryAI REST API.

GregoryAI's API is open and does not require authentication unless you need to create articles or clinical trials. JWT authentication is available for write operations.

---

## RSS feeds

| Feed | URL pattern |
|:-----|:------------|
| Articles by author (ORCID) | `GET /feed/author/{orcid}/` |
| Clinical trials by subject | `GET /feed/trials/subject/{subject_slug}/` |

Both feeds return the 50 most recent items, ordered by newest first.

---

## Sitemaps

| Sitemap | URL pattern |
|:--------|:------------|
| Sitemap index | `GET /sitemap/sites/{site_id}/index.xml` |
| Articles section (paginated) | `GET /sitemap/sites/{site_id}/articles.xml` (`?p=2…N`) |

One sitemap per frontend site, enabled and curated per site in the Django admin (Sites → the site's settings inline → *Generate sitemap*, *Sitemap subjects*, *Relevant only*). URLs point at the requested site's frontend domain, not the API host. Only articles belonging to a publicly visible organisation are included, regardless of caller identity. Each section page holds up to 10,000 URLs; both endpoints are cached for 1 hour. A site with the switch off, no `CustomSetting` row, or no publicly visible sitemap subjects configured returns 404.

---

## Subscription endpoint

`POST /subscriptions/new/` accepts HTML form submissions (no CSRF token required).

| Field | Required | Description |
|:------|:---------|:------------|
| `first_name` | yes | Subscriber first name |
| `last_name` | no | Subscriber last name |
| `email` | yes | Subscriber email address |
| `profile` | yes | One of: `patient`, `caregiver`, `doctor`, `clinical centre`, `researcher` |
| `list` | no | List ID(s) to subscribe to; may be repeated for multiple lists |

On success the subscriber is created or updated and the browser redirects to `/thank-you/`. On failure it redirects to `/error/`.

### Redirect domain

The redirect base URL is not hardcoded. The view reads the `Origin` header (falling back to `Referer`) and checks whether that domain appears in the **Allowed Domains** field of at least one selected list. Configure allowed origins per list in the Django admin under **Subscriptions → Lists → Allowed Domains** as a comma-separated list:

```text
example.com, staging.example.com
```

This prevents open-redirect attacks — only explicitly whitelisted domains are used.

---

## Authentication

| Endpoint | Description |
|:---------|:------------|
| `POST /api/token/` | Obtain JWT token |
| `POST /api/token/get/` | Obtain auth token (alternative) |
| `GET /protected_endpoint/` | Test protected endpoint (requires auth header) |

---

## Accessing private organisation data

By default the API only exposes data belonging to **public organisations** (`OrganizationApiSettings.make_api_public = True`). Callers that need to read a **private** organisation's data must identify themselves in one of two ways.

### Option 1 — API key bound to the organisation

Create an `APIAccessScheme` record in the Django admin with `organization` set to the target private org. The client sends the raw key in the `Authorization` header (no prefix):

```http
GET /articles/
Authorization: <raw_api_key>
```

The key is validated against its date window (`begin_date` / `end_date`) and, if configured, an IP allowlist. A valid key grants access to all data owned by its organisation.

### Option 2 — Authenticated Django user

A user account that is a member of the organisation (an `OrganizationUser` record exists) sees that organisation's data automatically after logging in via the session-based endpoints.

### Including public organisations alongside private data

Both caller types can append `?include_public=true` to any request to also receive data from organisations that have `make_api_public = True`.

```bash
GET /articles/?include_public=true
```

### Visibility rules summary

| Caller | Visible organisations |
|:-------|:----------------------|
| Anonymous (no credentials) | Public orgs only |
| API key with no org (`organization = null`) | Public orgs only |
| API key bound to org X | Org X only (+ public if `?include_public=true`) |
| Authenticated user member of org X | Org X only (+ public if `?include_public=true`) |

> **Note:** An expired key or a key used from a non-allowed IP is treated as anonymous.

---

## Articles query parameters

The `/articles/` endpoint supports the following filters. Multiple parameters can be combined.

| Parameter | Type | Description |
|:----------|:-----|:------------|
| `team_id` | integer | Filter by team |
| `subject_id` | integer | Filter by subject |
| `author_id` | integer | Filter by author |
| `doi` | string | Exact DOI match (case-insensitive) |
| `category_slug` | string | Filter by category slug |
| `category_id` | integer | Filter by category ID |
| `category_modality` | string | Filter by a category's curated intervention modality (`small_molecule`, `biologic_antibody`, `cell_gene_therapy`, `rehabilitation`, `device_neuromodulation`, `natural_product`, `research_topic`, `other`). Matches if *any* of the article's categories carry that modality |
| `journal_slug` | string | Filter by journal (spaces → dashes) |
| `source_id` | integer | Filter by source |
| `search` | string | Search in title and summary |
| `relevant` | boolean | Relevant articles only. Scoped to `subject_id` when provided. |
| `ml_threshold` | float 0–1 | Minimum ML prediction confidence. Scoped to `subject_id` when provided. |
| `open_access` | boolean | Open access articles only |
| `has_clinical_trials` | boolean | Filter by whether articles are linked to at least one trial |
| `unsent` | boolean | Articles not yet sent to subscribers |
| `last_days` | integer | Articles from the last N days |
| `week` | integer 1–52 | Filter by week number (requires `year`) |
| `year` | integer | Year for week filtering |
| `published_date_after` | date (YYYY-MM-DD) | Articles published on or after this date (inclusive). Returns 400 for invalid dates. |
| `published_date_before` | date (YYYY-MM-DD) | Articles published on or before this date (inclusive — the full day is included). Returns 400 for invalid dates. |
| `ordering` | string | Order results (e.g., `-published_date`, `title`) |
| `page` | integer | Page number |
| `page_size` | integer | Items per page (max 100) |
| `all_results` | boolean | Bypass pagination (useful for CSV export) |
| `format` | string | `json` (default) or `csv` |

### Examples

```bash
GET /articles/?team_id=1&subject_id=4&relevant=true
GET /articles/?relevant=true&ml_threshold=0.75
GET /articles/?relevant=true&last_days=15
GET /articles/?team_id=1&search=stem+cells
GET /articles/?format=csv&all_results=true
GET /articles/?has_clinical_trials=true
GET /articles/?published_date_after=2023-01-01&published_date_before=2023-12-31
GET /articles/?team_id=1&subjects=1,3&published_date_after=2022-06-01&format=csv&all_results=true
```

---

## Available endpoints

| Model | Endpoint | Parameters | Notes |
|:------|:---------|:-----------|:------|
| Articles | `GET /articles/` | `team_id`, `subject_id`, `author_id`, `category_slug`, `category_id`, `category_modality`, `journal_slug`, `source_id`, `search`, `ordering`, `relevant`, `open_access`, `unsent`, `last_days`, `week`, `year`, `has_clinical_trials`, `published_date_after`, `published_date_before`, pagination | |
| Articles | `POST /articles/post/` | `title`, `link`, `doi`, `summary`, `source_id`, `kind` | Create article — see [response codes below](#post-articlespost-response-codes) |
| Articles | `GET /articles/{id}/` | `id` (path) | |
| Articles | `GET /articles/stats/` | Same filters as `GET /articles/` | Aggregate counts over the filtered set: `total`, `by_access` (NULL folded into `unknown`), `relevant`, `retracted`, `missing_doi`, `by_subject`. Cached for `STATS_CACHE_TTL` seconds |
| Articles | `GET /articles/search/` | `team_id` *(req)*, `subject_id` *(req)*, `title`, `summary`, `search`, `format`, `all_results` | See [article-search-api.md](article-search-api.md) |
| Articles | `POST /articles/search/` | Same fields in request body | |
| Authors | `GET /authors/` | `author_id`, `full_name`, `orcid`, `country`, `sort_by`, `order`, `team_id`, `subject_id`, `category_slug`, `date_from`, `date_to`, `timeframe` | See [authors-api.md](authors-api.md) |
| Authors | `GET /authors/{id}/` | `id` (path) | |
| Authors | `GET /authors/search/` | `team_id` *(req)*, `subject_id` *(req)*, `full_name`, `format`, `all_results` | |
| Authors | `GET /authors/by_team_subject/` | `team_id` *(req)*, `subject_id` *(req)* | |
| Authors | `GET /authors/by_team_category/` | `team_id` *(req)*, `category_slug` or `category_id` *(req)* | |
| Categories | `GET /categories/` | `team_id`, `subject_id`, `category_id`, `search`, `ordering`, `include_authors`, `max_authors`, `monthly_counts`, `ml_threshold`, `date_from`, `date_to`, `timeframe`, pagination | |
| Categories | `GET /categories/{id}/` | `id` (path) | |
| Categories | `GET /categories/{id}/authors/` | `id` (path), `min_articles`, `sort_by`, `order`, date filters | Author stats for a category |
| Categories | `GET /categories/{slug}/monthly-counts/` | `slug` (path) | Monthly article and trial counts |
| Sources | `GET /sources/` | `team_id`, `subject_id`, `source_for`, `search`, `ordering`, pagination | |
| Sources | `GET /sources/{id}/` | `id` (path) | |
| Sponsors | `GET /sponsors/` | `sponsor_type`, `search`, `ordering` (`name`, `trials_count`), pagination | Canonical, deduplicated sponsor entities — see [Sponsor canonicalization](#sponsor-canonicalization) below |
| Sponsors | `GET /sponsors/{id}/` | `id` (path) | |
| Subjects | `GET /subjects/` | `team_id`, `search`, `ordering`, pagination | |
| Subjects | `GET /subjects/{id}/` | `id` (path) | |
| Teams | `GET /teams/` | Standard pagination | |
| Teams | `GET /teams/{id}/` | `id` (path) | |
| Teams | `GET /teams/{id}/subjects/{subject_id}/categories/` | `id`, `subject_id` (path) | |
| Trials | `GET /trials/` | `team_id`, `subject_id`, `category_id`, `category_modality`, `source_id`, `status`, `search`, `ordering`, trial-specific filters, pagination | See parameter details below |
| Trials | `GET /trials/{id}/` | `id` (path) | |
| Trials | `GET /trials/stats/` | Same filters as `GET /trials/` | Totals per `recruitment_status_normalized` bucket (not_yet_recruiting, recruiting, enrolling_by_invitation, active_not_recruiting, not_recruiting, suspended, completed, terminated, withdrawn, unknown, other — always present, 0 when empty) plus `no_status`, `by_subject`, `by_phase` (per `TrialPhase` + `no_phase`), `by_region` (per `TrialRegion` + `no_region`), `by_country` (`[{country, count}]`, null-country entry last), `by_year` (`[{year, count}]`, null-year entry last), `by_sponsor` (top 25 `[{sponsor_id, slug, name, sponsor_type, count}]`) + `no_sponsor`, `by_sponsor_type` (per `SponsorType` + `no_type`), `by_modality` (per `CategoryModality` + `no_modality`, joined over `team_categories.modality` — **not** a partition of `total`: a trial in two categories of different modalities is counted once per modality, and `no_modality` conflates "no category at all" with "category not yet curated with a modality"), and `by_study_type` (per `TrialStudyType` + `no_study_type`; `no_study_type` is large — ~13.2k globally — mostly legacy rows with no `source_register` rather than a normalization gap) over the filtered set. Replaces the `stats` block formerly embedded in `GET /trials/` list responses (breaking change). Cached for `STATS_CACHE_TTL` seconds |
| Trials | `GET /trials/search/` | `team_id` *(req)*, `subject_id` *(req)*, `title`, `summary`, `search`, `status`, `format`, `all_results` | See [trial-search-api.md](trial-search-api.md) |
| Trials | `POST /trials/search/` | Same fields in request body | |
| Trials | `GET /trials/sites/` | Same filters as `GET /trials/`, plus `latitude__isnull` (`true`/`false`) | Flat, paginated `TrialSite` listing across the filtered trial set: `{trial_id, name, city, country, latitude, longitude}` per row — for a site-level pin map. `trial_sites` itself is detail-only (appears on `GET /trials/{id}/`, never on the list/CSV/search responses). Pagination is mandatory here — `?all_results=true` returns 400; max `page_size` is 500 |
| Email templates | `GET /emails/` | None | Template preview dashboard |
| Email templates | `GET /emails/preview/{template_name}/` | `template_name` (path) | |
| Email templates | `GET /emails/context/{template_name}/` | `template_name` (path) | |
| RSS feeds | `GET /feed/author/{orcid}/` | `orcid` (path) | |
| RSS feeds | `GET /feed/trials/subject/{subject_slug}/` | `subject_slug` (path) | |
| Stats | `GET /stats/` | `team`, `organization` (alias `org`), `include_public` | See [Stats endpoint](#stats-endpoint) below |
| Subscriptions | `POST /subscriptions/new/` | `first_name`, `last_name`, `email`, `profile`, `list` | |

### Stats endpoint

`GET /stats/` returns aggregate counts for the data visible to the caller.

```
GET /stats/
GET /stats/?organization=3
GET /stats/?organization=3,7
GET /stats/?team=12
GET /stats/?organization=3&team=12
GET /stats/?include_public=true
```

Response shape (unchanged across all filter combinations):

```json
{
  "articles": 1234,
  "trials": 56,
  "subscribers": 78,
  "authors": 910,
  "sources": {
    "total": 42,
    "by_type": { "science paper": 35, "clinical trial": 7 },
    "by_domain": [
      { "domain": "pubmed.ncbi.nlm.nih.gov", "count": 12 },
      ...
    ]
  }
}
```

#### Filter parameters

| Parameter | Type | Behaviour |
|:----------|:-----|:----------|
| `organization` | int or CSV of ints | Scope counts to one or more organisations. Alias `org` is accepted. |
| `team` | int or CSV of ints | Scope counts to one or more teams. |
| `include_public` | bool (`true`/`false`) | Handled by the visibility layer — adds public-org data for identified callers. |

When both `organization` and `team` are given the effective scope is their **intersection**: teams that belong to the requested org(s).

#### Error responses

| Status | Condition |
|:-------|:----------|
| `400 Bad Request` | Non-integer value in `team` or `organization`. |
| `404 Not Found` | Any requested `team` or `organization` is not visible to the caller (hidden org — existence is not leaked). |

#### Caching

Results are cached for `STATS_CACHE_TTL` seconds (default 600 s / 10 min) using Django's database cache. All gunicorn workers share the same cached value. The cache key encodes the resolved set of in-scope team IDs, so different filter combinations are cached independently.

### Trials-specific filter parameters

| Parameter | Description |
|:----------|:------------|
| `trial_id` | Filter by specific trial ID |
| `internal_number` | Filter by WHO internal number |
| `phase` | Filter by trial phase (e.g., `Phase III`) |
| `study_type` | Legacy free-text `icontains` filter on the raw registry study-type string — prefer `study_type_normalized` |
| `study_type_normalized` | Exact match against the canonical study type: `interventional`, `observational`, `expanded_access`, `basic_science`, `other` |
| `primary_sponsor` | Legacy free-text filter on the raw registry sponsor string — prefer `sponsor_id`/`sponsor_slug` |
| `sponsor_id` | Exact match against a canonical sponsor's id (see [Sponsor canonicalization](#sponsor-canonicalization)) |
| `sponsor_slug` | Exact match against a canonical sponsor's slug |
| `source_register` | Filter by source registry (e.g., `ClinicalTrials.gov`) |
| `countries` | Filter by trial countries |
| `condition` | Filter by medical condition |
| `intervention` | Filter by intervention type |
| `therapeutic_areas` | Filter by therapeutic areas |
| `inclusion_agemin` / `inclusion_agemax` | Filter by age range |
| `inclusion_gender` | Filter by gender inclusion |
| `date_registration_after` | date (YYYY-MM-DD) | Trials registered on or after this date (inclusive). Returns 400 for invalid dates. |
| `date_registration_before` | date (YYYY-MM-DD) | Trials registered on or before this date (inclusive). Returns 400 for invalid dates. |

### Trials ordering

`GET /trials/?ordering=<field>` (prefix with `-` to reverse). Accepted values: `discovery_date` (default, newest first via `-discovery_date`), `published_date`, `title`, `trial_id`, `last_updated`, `recruiting_first`.

`recruiting_first` sorts by recruitment *availability* — "can a patient join this today?" — not alphabetically on `recruitment_status_normalized`:

`recruiting` → `enrolling_by_invitation` → `not_yet_recruiting` → `active_not_recruiting` → `suspended` → `not_recruiting` → `unknown` → `other` → `completed` → `terminated` → `withdrawn` → null status last.

Many trials share a rank, so ties are broken automatically by `-discovery_date` — page-to-page ordering stays stable.

**Unrecognised `ordering` values are silently ignored, not rejected** (DRF `OrderingFilter`'s default behaviour) — a typo or a stale field name falls back to the default ordering instead of returning an error.

### Sponsor canonicalization

Duplicate/variant spellings of the same real-world sponsor (`"Novartis"`, `"Novartis Pharma AG"`, `"NOVARTIS FARMA"`, ...) are resolved to a single canonical `Sponsor` entity — see `docs/trials-field-normalization.md` for how the resolution works.

Every trial response includes a nested, read-only `sponsor` object resolved from its raw `primary_sponsor` string (the raw `primary_sponsor`/`secondary_sponsor`/`sponsor_type` fields are untouched):

```json
"sponsor": { "id": 12, "slug": "novartis", "name": "Novartis", "sponsor_type": "industry" }
```

`sponsor` is `null` when the raw string hasn't been resolved to a canonical entity yet (tracked by the `no_sponsor` count on `/trials/stats/`). Filter trials by canonical sponsor with `sponsor_id` or `sponsor_slug` (both listed above), and browse the full canonical list at `GET /sponsors/`.

---

## `POST /articles/post/` response codes

This endpoint requires an `APIAccessScheme` API key (sent as the raw value in the `Authorization` header). The following status codes are returned:

| HTTP status | Condition |
|:------------|:----------|
| `200 OK` | Article or trial created successfully |
| `200 OK` | Duplicate — an item with the same DOI, title, or trial identifier already exists; the source/team/subject links on the existing record were updated |
| `400 Bad Request` | A required field is missing or invalid: `kind`, `source_id`, both `doi` and `title` absent, `kind` value does not match the source's `source_for`, or unsupported `kind` value |
| `400 Bad Request` | The `source_id` belongs to an organisation different from the one bound to the API key (cross-org payload) |
| `401 Unauthorized` | No API key provided, key is invalid, or the request IP is not in the key's allowlist |
| `403 Forbidden` | The API key has no organisation assigned (`organization = null`) |
| `404 Not Found` | The `source_id` does not exist in the database, or the source has no team assigned |
| `500 Internal Server Error` | The article or trial could not be saved, or an unexpected error occurred |

> **Breaking change (introduced in this release):** Prior to this release, `FieldNotFoundError` returned `200`, `SourceNotFoundError` returned a non-standard code, and `ArticleNotSavedError` returned `204`. These have been standardised to `400`, `404`, and `500` respectively.

### Required fields

| Field | Required | Description |
|:------|:---------|:------------|
| `kind` | yes | One of: `science paper`, `trials`, `news article` — must match the source's `source_for` value |
| `source_id` | yes | ID of the `Sources` record; must belong to the same org as the API key |
| `doi` or `title` | at least one | Used for dedup and CrossRef enrichment (`science paper` only) |
| `link` | no | URL of the article or trial |
| `summary` | no | Abstract or description |
| `published_date` | no | ISO 8601 date string |
| `identifiers` | no | JSON object with trial identifiers (`euct`, `nct`, `eudract`) — `trials` kind only |

---

## Planned endpoints (not yet implemented)

<details>
<summary>Endpoints not yet available</summary>

| Model | Endpoint | Notes |
|:------|:---------|:------|
| Authors | `POST /authors/` | Write disabled |
| Authors | `PUT /authors/{id}/` | Write disabled |
| Authors | `DELETE /authors/{id}/` | Write disabled |
| Categories | `POST /categories/` | Write disabled |
| Categories | `PUT /categories/{id}/` | Write disabled |
| Categories | `DELETE /categories/{id}/` | Write disabled |
| Entities | All endpoints | Not implemented |
| Subjects | `POST /subjects/` | Write disabled |
| Subjects | `PUT /subjects/{id}/` | Write disabled |
| Subjects | `DELETE /subjects/{id}/` | Write disabled |
| Sources | `POST /sources/` | Write disabled |
| Sources | `PUT /sources/{id}/` | Write disabled |
| Sources | `DELETE /sources/{id}/` | Write disabled |
| Articles | `PUT /articles/{id}/` | Write disabled |
| Articles | `DELETE /articles/{id}/` | Write disabled |
| Trials | `POST /trials/` | Write disabled |
| Trials | `PUT /trials/{id}/` | Write disabled |
| Trials | `DELETE /trials/{id}/` | Write disabled |
| Teams | `POST /teams/` | Write disabled |
| Teams | `PUT /teams/{id}/` | Write disabled |
| Teams | `DELETE /teams/{id}/` | Write disabled |
| MLPredictions | All endpoints | Not implemented |
| ArticleSubjectRelevance | All endpoints | Not implemented |

</details>

---

## Removed team-based endpoints

The following URL patterns have been removed (they now return `404 Not Found`). Use the parameter-based equivalents shown instead:

| Removed | Replacement |
|:--------|:------------|
| `GET /teams/{id}/articles/` | `GET /articles/?team_id={id}` |
| `GET /teams/{id}/articles/subject/{subject_id}/` | `GET /articles/?team_id={id}&subject_id={subject_id}` |
| `GET /teams/{id}/articles/category/{category_slug}/` | `GET /articles/?team_id={id}&category_slug={slug}` |
| `GET /teams/{id}/articles/source/{source_id}/` | `GET /articles/?team_id={id}&source_id={source_id}` |
| `GET /teams/{id}/subjects/` | `GET /subjects/?team_id={id}` |

The parameter-based approach supports combining any set of filters in a single request.

Note: `GET /teams/{id}/subjects/{subject_id}/categories/` is unaffected and continues to work.

---

## Data formats

All endpoints support three formats. Specify via the `format` query parameter or `Accept` header.

| Format | `format=` value | `Accept` header |
|:-------|:----------------|:----------------|
| JSON (default) | `json` | `application/json` |
| Browsable API | `html` | `text/html` |
| CSV | `csv` | `text/csv` |

For CSV export details and streaming behaviour, see [csv-export.md](csv-export.md).
