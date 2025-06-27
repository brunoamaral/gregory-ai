# Team API Endpoints

## Overview
These endpoints provide access to various resources associated with a specific team, including articles, trials, subjects, sources, and categories.

## Base URL

All team-specific endpoints follow this pattern:

```
/teams/{team_id}/...
```

Where `{team_id}` is the ID of the team you want to access.

## Endpoints

### 1. List All Articles for a Team

```
GET /teams/{team_id}/articles/
```

Returns all articles associated with the specified team, ordered by discovery date (newest first).

#### Response Format

```json
{
  "count": 42,
  "next": "http://example.com/teams/1/articles/?page=2",
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

- **404 Not Found**: If the team ID does not exist.

### 2. List All Clinical Trials for a Team

```
GET /teams/{team_id}/trials/
```

Returns all clinical trials associated with the specified team, ordered by discovery date (newest first).

#### Response Format

```json
{
  "count": 42,
  "next": "http://example.com/teams/1/trials/?page=2",
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

- **404 Not Found**: If the team ID does not exist.

### 3. List All Sources for a Team

```
GET /teams/{team_id}/sources/
```

Returns all sources associated with the specified team, ordered by source ID.

#### Response Format

```json
{
  "count": 42,
  "next": "http://example.com/teams/1/sources/?page=2",
  "previous": null,
  "results": [
    {
      "source_id": 123,
      "name": "Source Name",
      "url": "https://example.com/source",
      // ... other source fields
    },
    // ... more sources
  ]
}
```

#### Error Responses

- **404 Not Found**: If the team ID does not exist.

### 4. List All Subjects for a Team

```
GET /teams/{team_id}/subjects/
```

Returns all research subjects associated with the specified team.

#### Response Format

```json
{
  "count": 42,
  "next": "http://example.com/teams/1/subjects/?page=2",
  "previous": null,
  "results": [
    {
      "id": 123,
      "subject_name": "Subject Name",
      "subject_slug": "subject-slug",
      // ... other subject fields
    },
    // ... more subjects
  ]
}
```

#### Error Responses

- **404 Not Found**: If the team ID does not exist.

### 5. List All Categories for a Team

```
GET /teams/{team_id}/categories/
```

Returns all categories associated with the specified team.

#### Response Format

```json
{
  "count": 42,
  "next": "http://example.com/teams/1/categories/?page=2",
  "previous": null,
  "results": [
    {
      "id": 123,
      "category_name": "Category Name",
      "category_slug": "category-slug",
      // ... other category fields
    },
    // ... more categories
  ]
}
```

#### Error Responses

- **404 Not Found**: If the team ID does not exist.

## Notes

- All endpoints are paginated (default 10 items per page)
- Results are ordered by relevance, usually by discovery date (newest first)
- Authentication may be required for some endpoints
