# Clinical Trials Search API

## Overview
The API now includes a dedicated endpoint for searching clinical trials by title, summary, and recruitment status. This allows for both specific field searches and general searches across multiple fields.

## Endpoint

```
GET /api/trials/search/
```

## Query Parameters

| Parameter | Description |
|-----------|-------------|
| `title`   | Search for trials with titles containing this keyword (case-insensitive) |
| `summary` | Search for trials with summaries containing this keyword (case-insensitive) |
| `search`  | Search for trials where either the title OR the summary contains this keyword |
| `status`  | Filter trials by recruitment status (exact match, e.g., "Recruiting", "Completed", "Active, not recruiting") |

## Response Format
The response follows the standard DRF paginated format:

```json
{
  "count": 10,
  "next": "http://example.com/api/trials/search/?page=2",
  "previous": null,
  "results": [
    {
      "trial_id": 123,
      "title": "Example Trial Title",
      "summary": "Example trial summary...",
      "link": "https://example.com/trial",
      "published_date": "2025-06-20T12:00:00Z",
      "discovery_date": "2025-06-21T12:00:00Z",
      "recruitment_status": "Recruiting",
      "phase": "Phase 3",
      "sources": ["ClinicalTrials.gov"],
      "teams": [...],
      "subjects": [...],
      // ... other trial fields
    },
    // ... more trials
  ]
}
```

## Examples

1. Search for trials with "COVID" in the title:
```
GET /api/trials/search/?title=COVID
```

2. Search for trials with "treatment" in the summary:
```
GET /api/trials/search/?summary=treatment
```

3. Search for trials that mention "vaccine" in either title or summary:
```
GET /api/trials/search/?search=vaccine
```

4. Find only actively recruiting trials:
```
GET /api/trials/search/?status=Recruiting
```

5. Combined search and status filtering:
```
GET /api/trials/search/?search=COVID&status=Completed
```

## Notes
- The title and summary searches are case-insensitive and use partial matching
- The status filter requires an exact match to the recruitment_status field
- Results are ordered by discovery date (newest first)
- The endpoint is paginated (default 10 items per page)
