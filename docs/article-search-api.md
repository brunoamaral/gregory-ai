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

The API supports multiple response formats:

### JSON Format (default)

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
      "published_date": "2023-06-20T12:00:00Z",
      "discovery_date": "2023-06-21T12:00:00Z",
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

### CSV Format

To get results in CSV format, add `format=csv` to the query parameters. When using CSV format:

- Nested objects (authors, subjects, etc.) are consolidated into single comma-separated string columns
- Pagination metadata is removed
- All values are properly converted to strings
- A standardized filename is provided (e.g., gregory-ai-articles-2023-07-15.csv)

Add `all_results=true` to bypass pagination and get all matching results (especially useful for CSV export).

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
- The endpoint supports CSV export using `format=csv` parameter
- Use `all_results=true` to bypass pagination and get all matching results (especially useful for CSV export)

## CSV Export

To export search results as CSV, add `format=csv` to your query parameters. This is particularly useful for data analysis or reporting purposes.

### Example: Export all search results as CSV

```
GET /api/articles/search/?team_id=1&subject_id=2&search=COVID&format=csv&all_results=true
```

This will return a CSV file containing all articles matching the search criteria, with no pagination limit. The CSV export:

1. Consolidates nested fields (like authors, subjects) into readable columns
2. Removes pagination metadata
3. Uses a standardized filename format (e.g., gregory-ai-articles-2023-07-15.csv)
4. Properly formats and escapes text fields to handle special characters
5. Ensures all values are properly converted to strings to avoid type errors

### CSV Export Options

| Parameter | Description |
|-----------|-------------|
| `format=csv` | Specifies CSV output format instead of JSON |
| `all_results=true` | Bypasses pagination to include all matching records in the CSV |

You can use both GET and POST methods with CSV export:

```bash
# Using GET with CSV export
curl "https://api.example.com/api/articles/search/?team_id=1&subject_id=2&search=COVID&format=csv&all_results=true"

# Using POST with CSV export (Content-Type still application/json)
curl -X POST \
  "https://api.example.com/api/articles/search/?format=csv&all_results=true" \
  -H "Content-Type: application/json" \
  -d '{"team_id":1,"subject_id":2,"search":"COVID"}'
```
