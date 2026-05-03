# Clinical trials search API

> Audience: developers integrating with the GregoryAI REST API.

Dedicated endpoint for searching clinical trials by title, summary, and recruitment status. Requires team and subject IDs to scope results.

## Endpoint

```text
POST /api/trials/search/
```

## Request body

```json
{
  "team_id": 1,
  "subject_id": 2,
  "title": "keyword",
  "summary": "keyword",
  "search": "keyword",
  "status": "Recruiting"
}
```

### Required parameters

| Parameter | Description |
|:----------|:------------|
| `team_id` | Team ID to filter trials by |
| `subject_id` | Subject ID to filter trials by |

### Optional search parameters

| Parameter | Description |
|:----------|:------------|
| `title` | Trials whose title contains this keyword (case-insensitive, partial match) |
| `summary` | Trials whose summary contains this keyword |
| `search` | Trials where title OR summary contains this keyword |
| `status` | Exact match on `recruitment_status` (e.g., `Recruiting`, `Completed`) |

## Response format

### JSON format (default)

The response follows the standard DRF paginated format:

```json
{
  "count": 10,
  "next": "https://api.example.com/api/trials/search/?page=2",
  "previous": null,
  "results": [
    {
      "id": 123,
      "title": "Example clinical trial",
      "summary": "Trial summary...",
      "recruitment_status": "Recruiting",
      "source_id": 42,
      "team_id": 1,
      "subject_id": 2
    }
  ]
}
```

### CSV format

Add `format=csv` to the query parameters. See [csv-export.md](csv-export.md) for full details on CSV export options.

### Error responses

```json
{"error": "Missing required parameters: team_id, subject_id"}
```

```json
{"error": "Invalid team_id or subject_id"}
```

## Examples

```bash
# Search by keyword
POST /api/trials/search/
{"team_id": 1, "subject_id": 2, "search": "diabetes"}

# Filter recruiting trials by title keyword
POST /api/trials/search/
{"team_id": 1, "subject_id": 2, "title": "cancer", "status": "Recruiting"}

# Export all results as CSV
GET /api/trials/search/?team_id=1&subject_id=2&search=diabetes&format=csv&all_results=true
```

## Notes

- Results are ordered by discovery date, newest first.
- Default page size is 10 items.
- `status` requires an exact match to the `recruitment_status` field value.
