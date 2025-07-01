# Enhanced Authors API Documentation

## Overview

The enhanced Authors API provides powerful filtering and sorting capabilities for querying authors based on their article contributions. The API supports filtering by teams, subjects, categories, and time periods, with optimized database queries for performance.

## Base Endpoint

```
GET /authors/
```

## Query Parameters

### Sorting
- `sort_by`: Field to sort by
  - `article_count` (default for article count sorting)
  - `author_id` (default for other cases)
  - `given_name`
  - `family_name`

- `order`: Sort direction
  - `desc` (default when sorting by article_count)
  - `asc` (default for other fields)

### Filtering

#### Team and Subject Filtering
- `team_id`: Filter authors by team ID (**required** when using `category_slug` or `subject_id`)
- `subject_id`: Filter authors by subject ID (must be used with `team_id`)
- `category_slug`: Filter authors by team category slug (must be used with `team_id`)

#### Time-based Filtering
- `date_from`: Start date for article filtering (YYYY-MM-DD format)
- `date_to`: End date for article filtering (YYYY-MM-DD format)
- `timeframe`: Relative time periods
  - `year`: Current year
  - `month`: Current month
  - `week`: Current week

## API Endpoints

### 1. List All Authors with Sorting and Filtering

```
GET /authors/?sort_by=article_count&order=desc
GET /authors/?sort_by=article_count&timeframe=year
GET /authors/?sort_by=article_count&date_from=2024-01-01&date_to=2024-12-31
GET /authors/?team_id=1&category_slug=neuroscience&sort_by=article_count&order=desc
GET /authors/?team_id=2&subject_id=3&sort_by=article_count&timeframe=month
```

**Important Note:** When filtering by `category_slug` or `subject_id`, you **must** also specify `team_id` because multiple teams may have categories or subjects with identical slugs/IDs.

**Example Response:**
```json
{
  "count": 150,
  "next": "http://localhost:8000/authors/?page=2",
  "previous": null,
  "results": [
    {
      "author_id": 123,
      "given_name": "Jane",
      "family_name": "Smith",
      "full_name": "Jane Smith",
      "ORCID": "0000-0000-0000-0000",
      "country": "US",
      "articles_count": 25,
      "articles_list": "https://api.example.com/articles/author/123"
    }
  ]
}
```

### 2. Authors by Team and Subject

```
GET /teams/{team_id}/subjects/{subject_id}/authors/
```

**Query Parameters:**
- All standard filtering parameters apply
- `team_id` and `subject_id` are extracted from URL path

**Example:**
```
GET /teams/1/subjects/5/authors/?sort_by=article_count&timeframe=month
```

### 3. Authors by Team and Category

```
GET /teams/{team_id}/categories/{category_slug}/authors/
```

**Query Parameters:**
- All standard filtering parameters apply
- `team_id` and `category_slug` are extracted from URL path

**Example:**
```
GET /teams/1/categories/neuroscience/authors/?sort_by=article_count&date_from=2024-01-01
```

## Advanced Filtering Examples

### 1. Top Authors by Article Count (Current Year)
```
GET /authors/?sort_by=article_count&order=desc&timeframe=year
```

### 2. Authors in Specific Team and Subject (Last Month)
```
GET /teams/1/subjects/3/authors/?sort_by=article_count&timeframe=month
```

### 3. Authors in Category with Custom Date Range
```
GET /teams/2/categories/cardiology/authors/?sort_by=article_count&date_from=2024-06-01&date_to=2024-12-01
```

### 4. Authors by Team with Multiple Filters
```
GET /authors/?team_id=1&subject_id=3&sort_by=article_count&timeframe=week
```

### 5. Authors by Category (with Required Team ID)
```
GET /authors/?team_id=1&category_slug=neuroscience&sort_by=article_count&order=desc
GET /authors/?team_id=2&category_slug=cardiology&timeframe=year&sort_by=article_count
```

## Performance Optimizations

1. **Database Query Optimization**: Uses `annotate()` with filtered `Count()` to efficiently calculate article counts
2. **Prefetch Related**: Minimizes database hits by prefetching related articles, teams, subjects, and categories
3. **Conditional Annotations**: Only adds expensive annotations when needed
4. **Distinct Results**: Prevents duplicate authors in results when joining across multiple tables

## Response Format

All endpoints return paginated results with:
- `count`: Total number of authors
- `next`: URL to next page (if available)
- `previous`: URL to previous page (if available)  
- `results`: Array of author objects

### Author Object Fields

- `author_id`: Unique identifier
- `given_name`: Author's first name
- `family_name`: Author's last name
- `full_name`: Complete name
- `ORCID`: ORCID identifier (if available)
- `country`: Country code
- `articles_count`: Number of articles (filtered based on query parameters)
- `articles_list`: URL to author's articles

## Error Handling

### 400 Bad Request
```json
{
  "error": "Both team_id and subject_id are required"
}
```

### Missing Team ID for Category/Subject Filtering
When using `category_slug` or `subject_id` without `team_id`, results may be ambiguous or incorrect since multiple teams can have identical category slugs or subject IDs.

### Invalid Date Format
Invalid date parameters are silently ignored to maintain API stability.

### Invalid Numeric Parameters
Invalid team_id or subject_id values are silently ignored.

## Semantic URL Structure

The API follows RESTful principles with intuitive URL structure:

- `/authors/` - All authors
- `/teams/{id}/subjects/{id}/authors/` - Authors filtered by team and subject
- `/teams/{id}/categories/{slug}/authors/` - Authors filtered by team and category

This structure makes it easy for clients to construct URLs and understand the resource hierarchy.
