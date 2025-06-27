# Category API Endpoints

## Overview
These endpoints provide access to articles and clinical trials associated with a specific category within a team.

## Base URL

All category-specific endpoints follow this pattern:

```
/teams/{team_id}/articles/category/{category_slug}/
/teams/{team_id}/trials/category/{category_slug}/
```

Where:
- `{team_id}` is the ID of the team
- `{category_slug}` is the slug of the category within that team

## Endpoints

### 1. List Articles by Category

```
GET /teams/{team_id}/articles/category/{category_slug}/
```

Returns all articles associated with the specified category within the specified team, ordered by discovery date (newest first).

#### Response Format

```json
{
  "count": 42,
  "next": "http://example.com/teams/1/articles/category/category-slug/?page=2",
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

- **404 Not Found**: If the team ID or category slug does not exist, or if the category doesn't belong to the specified team.

### 2. List Clinical Trials by Category

```
GET /teams/{team_id}/trials/category/{category_slug}/
```

Returns all clinical trials associated with the specified category within the specified team, ordered by discovery date (newest first).

#### Response Format

```json
{
  "count": 42,
  "next": "http://example.com/teams/1/trials/category/category-slug/?page=2",
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

- **404 Not Found**: If the team ID or category slug does not exist, or if the category doesn't belong to the specified team.

### 3. Get Monthly Counts by Category

```
GET /teams/{team_id}/categories/{category_slug}/monthly-counts/
```

Returns monthly counts of articles and trials for the specified category and team.

#### Response Format

```json
{
  "category_name": "Category Name",
  "category_slug": "category-slug",
  "monthly_article_counts": [
    {
      "month": "2025-01-01T00:00:00Z",
      "count": 15
    },
    {
      "month": "2025-02-01T00:00:00Z",
      "count": 23
    }
  ],
  "monthly_trial_counts": [
    {
      "month": "2025-01-01T00:00:00Z",
      "count": 5
    },
    {
      "month": "2025-02-01T00:00:00Z",
      "count": 8
    }
  ]
}
```

#### Error Responses

- **404 Not Found**: If the team ID or category slug does not exist, or if the category doesn't belong to the specified team.

## Notes

- All endpoints are paginated (default 10 items per page)
- Results are ordered by discovery date (newest first)
- Authentication may be required for some endpoints
