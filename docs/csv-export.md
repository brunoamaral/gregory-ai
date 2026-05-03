# CSV export

> Audience: developers and operators exporting GregoryAI data.

All list endpoints that accept `format=csv` stream their results as a CSV file. This works for `/articles/`, `/trials/`, and the dedicated search endpoints.

## Query parameters

| Parameter | Description |
|:----------|:------------|
| `format=csv` | Return CSV instead of JSON |
| `all_results=true` | Bypass pagination and return all matching rows (recommended for exports) |

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
curl "https://api.example.com/trials/?team_id=1&subject_id=2&status=Recruiting&format=csv&all_results=true"

# Export article search results (POST)
curl -X POST \
  "https://api.example.com/api/articles/search/?format=csv&all_results=true" \
  -H "Content-Type: application/json" \
  -d '{"team_id":1,"subject_id":2,"search":"COVID"}'

# Export trial search results (GET)
curl "https://api.example.com/api/trials/search/?team_id=1&subject_id=2&status=Recruiting&format=csv&all_results=true"
```

## Implementation notes

The API uses `DirectStreamingCSVRenderer`, which generates CSV rows one at a time via a generator to minimise server memory usage. Large datasets do not require loading all rows into memory before streaming begins. See [streaming-csv-response.md](streaming-csv-response.md) for developer-level implementation details.
