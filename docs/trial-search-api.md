# Clinical Trials Search API

## Overview
The API now includes a dedicated endpoint for searching clinical trials by title, summary, and recruitment status. This allows for both specific field searches and general searches across multiple fields. Access to trial search requires providing team and subject IDs, which are used to filter the results.

## Endpoint

```
POST /api/trials/search/
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
  "search": "keyword",
  "status": "Recruiting"
}
```

### Required Parameters

| Parameter | Description |
|-----------|-------------|
| `team_id` | ID of the team to filter trials by (required) |
| `subject_id` | ID of the subject to filter trials by (required) |

### Optional Search Parameters

| Parameter | Description |
|-----------|-------------|
| `title`   | Search for trials with titles containing this keyword (case-insensitive) |
| `summary` | Search for trials with summaries containing this keyword (case-insensitive) |
| `search`  | Search for trials where either the title OR the summary contains this keyword |
| `status`  | Filter trials by recruitment status (exact match, e.g., "Recruiting", "Completed", "Active, not recruiting") |

## Response Format

### Success Response (200 OK)
Returns a JSON array of trial objects matching the search criteria.

```json
[
  {
    "id": 123,
    "title": "Example Clinical Trial",
    "summary": "This is a summary of the trial...",
    "status": "Recruiting",
    "source_id": 42,
    "team_id": 1,
    "subject_id": 2,
    ...
  },
  ...
]
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

### Search for trials related to "diabetes" for a specific team and subject

```
POST /api/trials/search/
Content-Type: application/json

{
  "team_id": 1,
  "subject_id": 2,
  "search": "diabetes"
}
```

### Search for recruiting trials containing "cancer" in the title

```
POST /api/trials/search/
Content-Type: application/json

{
  "team_id": 1,
  "subject_id": 2,
  "title": "cancer",
  "status": "Recruiting"
}
```

## Notes
- The title and summary searches are case-insensitive and use partial matching
- The status filter requires an exact match to the recruitment_status field
- Results are ordered by discovery date (newest first)
- The endpoint is paginated (default 10 items per page)
- Both team_id and subject_id are required parameters
