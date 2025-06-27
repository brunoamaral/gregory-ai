# Source API Endpoints

## Overview
These endpoints provide access to articles and clinical trials associated with a specific source within a team.

## Base URL

All source-specific endpoints follow this pattern:

```
/teams/{team_id}/articles/source/{source_id}/
/teams/{team_id}/trials/source/{source_id}/
```

Where:
- `{team_id}` is the ID of the team
- `{source_id}` is the ID of the source within that team

## Endpoints

### 1. List Articles by Source

```
GET /teams/{team_id}/articles/source/{source_id}/
```

Returns all articles associated with the specified source within the specified team, ordered by discovery date (newest first).

#### Response Format

```json
{
  "count": 42,
  "next": "http://example.com/teams/1/articles/source/2/?page=2",
  "previous": null,
  "results": [
    {
      "article_id": 123,
      "title": "Example Article Title",
      "summary": "Example article abstract...",
      "link": "https://example.com/article",
      "published_date": "2025-06-20T12:00:00Z",
      "discovery_date": "2025-06-21T12:00:00Z",
      // ... other article fields
    },
    // ... more articles
  ]
}
```

#### Error Responses

- **404 Not Found**: If the team ID or source ID does not exist, or if the source doesn't belong to the specified team.

### 2. List Clinical Trials by Source

```
GET /teams/{team_id}/trials/source/{source_id}/
```

Returns all clinical trials associated with the specified source within the specified team, ordered by discovery date (newest first).

#### Response Format

```json
{
  "count": 42,
  "next": "http://example.com/teams/1/trials/source/2/?page=2",
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
      // ... other trial fields
    },
    // ... more trials
  ]
}
```

#### Error Responses

- **404 Not Found**: If the team ID or source ID does not exist, or if the source doesn't belong to the specified team.

## Notes

- All endpoints are paginated (default 10 items per page)
- Results are ordered by discovery date (newest first)
- Authentication may be required for some endpoints
