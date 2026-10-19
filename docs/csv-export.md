# CSV export

> Audience: developers and operators exporting GregoryAI data.

All list endpoints that accept `format=csv` stream their results as a CSV file. This works for `/articles/`, `/trials/`, and the dedicated search endpoints (`/articles/search/`, `/trials/search/`, `/authors/search/`). `/patents/` has no CSV (`?format=csv` is `404`; see [Patents](03-api-and-rss-feeds.md#patents)), and `/authors/` and `/trials/sites/` don't accept `all_results`.

## Query parameters

| Parameter | Description |
|:----------|:------------|
| `format=csv` | Return CSV instead of JSON |
| `all_results=true` | Bypass pagination and return all matching rows (recommended for exports) |
| `site_id` | Filter to items carrying a subject in the given Django Site's scope (`CustomSetting.scope_subjects`); useful for exporting a single frontend's scope without listing every subject_id |

## What the export does

- Consolidates nested fields (authors, subjects, clinical trials) into single readable columns.
- Removes pagination metadata.
- Replaces line breaks in text fields (e.g., summaries) with spaces for spreadsheet compatibility.
- Sets the filename to `gregory-ai-{object_type}-{date}.csv` via `Content-Disposition`.

## Examples

```bash
# Export all articles matching a search
curl "https://api.example.com/articles/?team_id=1&subject_id=2&search=covid&format=csv&all_results=true"

# Export all recruiting trials
curl "https://api.example.com/trials/?team_id=1&subject_id=2&recruitment_status_normalized=recruiting&format=csv&all_results=true"

# Export article search results (POST)
curl -X POST \
  "https://api.example.com/articles/search/?format=csv&all_results=true" \
  -H "Content-Type: application/json" \
  -d '{"team_id":1,"subject_id":2,"search":"COVID"}'

# Export trial search results (GET)
curl "https://api.example.com/trials/search/?team_id=1&subject_id=2&recruitment_status_normalized=recruiting&format=csv&all_results=true"

# Export all articles for a site, across every subject in its scope
curl "https://api.example.com/articles/?site_id=3&format=csv&all_results=true"
```

## Rate limiting

`all_results=true` requests (whether CSV or JSON) are subject to the `bulk_export` throttle scope: 4 requests per hour per client. Paginated requests (no `all_results`) are unaffected. A build pipeline polling this endpoint repeatedly should cache the result rather than re-fetching on every run.

## Implementation notes

Endpoints stream CSV rows in bounded batches rather than buffering the full dataset in memory before responding — the first byte (the header row) is sent as soon as the query starts returning data. See [streaming-csv-response.md](streaming-csv-response.md) for developer-level implementation details.
