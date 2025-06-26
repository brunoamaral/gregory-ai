# Article Search API

## Overview
The API now includes a dedicated endpoint for searching articles by title and abstract (summary). This allows for both specific field searches and general searches across both fields.

## Endpoint

```
GET /api/articles/search/
```

## Query Parameters

| Parameter | Description |
|-----------|-------------|
| `title`   | Search for articles with titles containing this keyword (case-insensitive) |
| `summary` | Search for articles with abstracts/summaries containing this keyword (case-insensitive) |
| `search`  | Search for articles where either the title OR the abstract contains this keyword |

## Response Format
The response follows the standard DRF paginated format:

```json
{
  "count": 10,
  "next": "http://example.com/api/articles/search/?page=2",
  "previous": null,
  "results": [
    {
      "article_id": 123,
      "title": "Example Article Title",
      "summary": "Example article abstract...",
      "link": "https://example.com/article",
      "published_date": "2025-06-20T12:00:00Z",
      "discovery_date": "2025-06-21T12:00:00Z",
      "sources": ["Journal of Example Studies"],
      "teams": [...],
      "subjects": [...],
      "authors": [...],
      // ... other article fields
    },
    // ... more articles
  ]
}
```

## Examples

1. Search for articles with "COVID" in the title:
```
GET /api/articles/search/?title=COVID
```

2. Search for articles with "treatment" in the abstract:
```
GET /api/articles/search/?summary=treatment
```

3. Search for articles that mention "vaccine" in either title or abstract:
```
GET /api/articles/search/?search=vaccine
```

## Notes
- The search is case-insensitive
- Results are ordered by discovery date (newest first)
- The search uses partial matching, so searching for "treat" will match "treatment", "treatments", etc.
- The endpoint is paginated (default 10 items per page)
