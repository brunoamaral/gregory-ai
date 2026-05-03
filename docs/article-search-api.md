# Article search API

> Audience: developers integrating with the GregoryAI REST API.

Dedicated endpoint for searching articles by title and abstract. Requires team and subject IDs to scope results.

## Endpoint

```text
POST /api/articles/search/
```

## Request body

```json
{
  "team_id": 1,
  "subject_id": 2,
  "title": "keyword",
  "summary": "keyword",
  "search": "keyword"
}
```

### Required parameters

| Parameter | Description |
|:----------|:------------|
| `team_id` | Team ID to filter articles by |
| `subject_id` | Subject ID to filter articles by |

### Optional search parameters

| Parameter | Description |
|:----------|:------------|
| `title` | Articles whose title contains this keyword (case-insensitive, partial match) |
| `summary` | Articles whose abstract/summary contains this keyword |
| `search` | Articles where title OR summary contains this keyword |

## Response format

### JSON format (default)

The response follows the standard DRF paginated format:

```json
{
  "count": 10,
  "next": "https://api.example.com/api/articles/search/?page=2",
  "previous": null,
  "results": [
    {
      "article_id": 123,
      "title": "Example article title",
      "summary": "Example article abstract...",
      "link": "https://example.com/article",
      "published_date": "2023-06-20T12:00:00Z",
      "discovery_date": "2023-06-21T12:00:00Z",
      "sources": ["Journal of Example Studies"],
      "teams": [],
      "subjects": [],
      "authors": []
    }
  ]
}
```

### CSV format

Add `format=csv` and optionally `all_results=true` to the query string. See [csv-export.md](csv-export.md) for full details.

### Error responses

```json
{"error": "Missing required parameters: team_id, subject_id"}
```

```json
{"error": "Invalid team_id or subject_id"}
```

## Examples

```bash
# Search for articles about COVID
POST /api/articles/search/
{"team_id": 1, "subject_id": 2, "search": "COVID"}

# Search by abstract keyword
POST /api/articles/search/
{"team_id": 1, "subject_id": 2, "summary": "treatment"}

# Search by title keyword
POST /api/articles/search/
{"team_id": 1, "subject_id": 2, "title": "vaccine"}

# Export all matching results as CSV
GET /api/articles/search/?team_id=1&subject_id=2&search=COVID&format=csv&all_results=true
```

## Notes

- Search is case-insensitive with partial matching (`treat` matches `treatment`).
- Results are ordered by discovery date, newest first.
- Default page size is 10 items.
- Both `team_id` and `subject_id` are required.
