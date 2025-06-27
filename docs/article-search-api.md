# Article Search API

## Overview
The API now includes a dedicated endpoint for searching articles by title and abstract (summary). This allows for both specific field searches and general searches across both fields. Access to article search requires providing team and subject IDs, which are used to filter the results.

## Endpoint

```
POST /api/articles/search/
```

## Request Body

The request must be a POST request with a JSON body containing at least the following required fields:

```json
{
  "team_id": 1,
  "subject_id": 2,
  
  // Optional search parameters:
  "title": "keyword",
  "summary": "keyword",
  "search": "keyword"
}
```

### Required Parameters

| Parameter | Description |
|-----------|-------------|
| `team_id` | ID of the team to filter articles by (required) |
| `subject_id` | ID of the subject to filter articles by (required) |

### Optional Search Parameters

| Parameter | Description |
|-----------|-------------|
| `title`   | Search for articles with titles containing this keyword (case-insensitive) |
| `summary` | Search for articles with abstracts/summaries containing this keyword (case-insensitive) |
| `search`  | Search for articles where either the title OR the abstract contains this keyword |

## Response Format

### Success Response (200 OK)
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

### Error Responses

#### 400 Bad Request
Returned when required parameters are missing or invalid.

```json
{
  "error": "Missing required parameters: team_id, subject_id"
}
```

or

```json
{
  "error": "Invalid team_id or subject_id"
}
```

## Examples

### Search for articles related to "COVID" for a specific team and subject

```
POST /api/articles/search/
Content-Type: application/json

{
  "team_id": 1,
  "subject_id": 2,
  "search": "COVID"
}
```

### Search for articles containing "treatment" in the abstract

```
POST /api/articles/search/
Content-Type: application/json

{
  "team_id": 1,
  "subject_id": 2,
  "summary": "treatment"
}
```

### Search for articles with "vaccine" in the title

```
POST /api/articles/search/
Content-Type: application/json

{
  "team_id": 1,
  "subject_id": 2,
  "title": "vaccine"
}
```

## Notes
- The search is case-insensitive
- Results are ordered by discovery date (newest first)
- The search uses partial matching, so searching for "treat" will match "treatment", "treatments", etc.
- The endpoint is paginated (default 10 items per page)
- Both team_id and subject_id are required parameters
