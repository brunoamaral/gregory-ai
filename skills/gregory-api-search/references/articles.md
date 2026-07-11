# Articles — `GET /articles/`

Search and filter scientific articles across the whole corpus (~49k). **No team_id or
subject_id is required** — query the entire set freely. Add `subject_id` only when you want
to scope to a specific research area (see [subjects-and-categories.md](subjects-and-categories.md)).

Single article: `GET /articles/{article_id}/`.

## Search & text filters

| Param | Meaning |
|---|---|
| `search` | Boolean search over **title + abstract**. AND/OR/NOT/`-`/`"phrase"`/`()`. See [search-syntax.md](search-syntax.md). |
| `title` | Substring match in title only (case-insensitive). |
| `summary` | Substring match in abstract only (case-insensitive). |
| `doi` | One DOI or a comma-separated list, case-insensitive: `?doi=10.1/a,10.2/b`. |

## Scope / relationship filters

| Param | Meaning |
|---|---|
| `subject_id` | Articles tagged with this research subject. **Best filter for a condition/topic.** |
| `subjects` | Comma-separated subject IDs, **AND** — tagged with *all* of them: `?subjects=1,13`. |
| `subjects_any` | Comma-separated subject IDs, **OR** — tagged with *any*: `?subjects_any=1,13`. |
| `team_id` | Articles belonging to a team. Optional; usually unnecessary for topic search. |
| `author_id` | Articles by a given author (see [authors.md](authors.md)). |
| `category_slug` / `category_id` | Articles in a team category. |
| `journal_slug` | Journal name with spaces as dashes, e.g. `?journal_slug=nature-medicine`. |
| `source_id` | Articles from a specific ingestion source. |

## Relevance / type filters

| Param | Meaning |
|---|---|
| `relevant` | `true`/`false`. AI- or manually-flagged relevance. Scope with `subject_id` for per-topic relevance. |
| `ml_threshold` | Float `0.0`–`1.0`. Minimum ML confidence, e.g. `?ml_threshold=0.75`. |
| `open_access` | `true`/`false`. |
| `has_clinical_trials` | `true`/`false` — article linked to at least one trial. |

## Date / recency filters

| Param | Meaning |
|---|---|
| `published_date_after` | `YYYY-MM-DD`, inclusive. |
| `published_date_before` | `YYYY-MM-DD`, inclusive (whole day). |
| `last_days` | Articles discovered in the last N days. |
| `week` + `year` | Articles from a specific ISO week (both required). |

## Ordering & pagination

- `ordering` — one of `discovery_date`, `published_date`, `title`, `article_id`, `ml_score`.
  Prefix `-` for descending: `?ordering=-ml_score`. Articles with no ML score sort last.
- `page`, `page_size` (≤100), `all_results=true`, `format=json|csv`.

## Response fields (per article)

`article_id`, `title`, `summary`, `link`, `links` (source→URL map), `doi`, `access`
(`open`/other), `published_date`, `discovery_date`, `publisher`, `container_title` (journal),
`authors` (list), `sources`, `teams`, `subjects`, `team_categories`,
`article_subject_relevances`, `ml_predictions`, `ml_score` (avg ML probability, `null` if none),
`clinical_trials` (linked trials).

## Examples

```bash
BASE="https://api.brain-regeneration.com"

# Boolean search across the whole corpus
curl -s "$BASE/articles/?search=(microglia%20OR%20astrocyte)%20AND%20inflammation&format=json"

# Multiple Sclerosis (subject 1), AI-relevant, most confident first
curl -s "$BASE/articles/?subject_id=1&relevant=true&ml_threshold=0.8&ordering=-ml_score&format=json"

# Open-access Parkinson's papers from the last 30 days
curl -s "$BASE/articles/?subject_id=14&open_access=true&last_days=30&format=json"

# Everything by one author, newest first
curl -s "$BASE/articles/?author_id=547318&ordering=-published_date&format=json"

# Look up a specific paper by DOI
curl -s "$BASE/articles/?doi=10.1007/s00296-026-06233-x&format=json"

# Export a year of Alzheimer's articles as CSV
curl -s "$BASE/articles/?subject_id=13&published_date_after=2024-01-01&published_date_before=2024-12-31&format=csv&all_results=true"
```
