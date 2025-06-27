# Subject API Endpoints

## Overview
These endpoints provide access to articles and clinical trials associated with a specific subject within a team.

## Base URL

All subject-specific endpoints follow this pattern:

```
/teams/{team_id}/articles/subject/{subject_id}/
/teams/{team_id}/trials/subject/{subject_id}/
```

Where:
- `{team_id}` is the ID of the team
- `{subject_id}` is the ID of the subject within that team

## Endpoints

### 1. List Articles by Subject

```
GET /teams/{team_id}/articles/subject/{subject_id}/
```

Returns all articles associated with the specified subject within the specified team, ordered by discovery date (newest first).

#### Response Format

```json
{
  "count": 42,
  "next": "http://example.com/teams/1/articles/subject/2/?page=2",
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

- **404 Not Found**: If the team ID or subject ID does not exist, or if the subject doesn't belong to the specified team.

### 2. List Clinical Trials by Subject

```
GET /teams/{team_id}/trials/subject/{subject_id}/
```

Returns all clinical trials associated with the specified subject within the specified team, ordered by discovery date (newest first).

#### Response Format

```json
{
  "count": 42,
  "next": "http://example.com/teams/1/trials/subject/2/?page=2",
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

- **404 Not Found**: If the team ID or subject ID does not exist, or if the subject doesn't belong to the specified team.

## Advanced Searching

For more advanced search capabilities, including filtering by title, summary content, or (for trials) recruitment status, please use the dedicated search endpoints:

- `/api/articles/search/` - See [Article Search API](article-search-api.md)
- `/api/trials/search/` - See [Trial Search API](trial-search-api.md)

These endpoints require a POST request with team_id and subject_id in the request body, along with optional search parameters.

## Notes

- All endpoints are paginated (default 10 items per page)
- Results are ordered by discovery date (newest first)
- Authentication may be required for some endpoints
