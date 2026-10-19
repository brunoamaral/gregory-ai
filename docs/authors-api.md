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

#### Identity
- `author_id`: A specific author
- `full_name`, `given_name`, `family_name`: Case-insensitive substring of the name
- `orcid`: Case-insensitive substring of the ORCID
- `country`: Exact country code
- `search`: Substring of the full name or ORCID

#### Team, Subject and Category Filtering
- `team_id`: Filter authors by team ID (**required** with `category_slug` or `category_id`)
- `subject_id`: Authors with an article in this subject. Does **not** need `team_id`: a subject id belongs to exactly one team
- `subjects_any`: Comma-separated subject IDs, OR semantics (`?subjects_any=1,10`)
- `category_slug`, `category_id`: Filter authors by team category (must be used with `team_id`; without it the response is an empty list, not an error)

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
GET /authors/?subject_id=3&sort_by=article_count
```

**Important Note:** When filtering by `category_slug` or `category_id`, you **must** also specify `team_id`. It is not there to disambiguate the category (both are unique on their own): the count then covers articles that are both tagged to the team **and** in the category. `subject_id` and `subjects_any` need no `team_id`.

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
      "articles_list": "https://api.brain-regeneration.com/articles/?author_id=123"
    }
  ]
}
```

### 2. Authors by Team and Subject

```
GET /authors/by_team_subject/?team_id={team_id}&subject_id={subject_id}
```

The same as `GET /authors/` with both parameters made **required**: either one missing is `400` (`{"error": "Both team_id and subject_id are required"}`). Every other `/authors/` parameter applies.

**Example:**
```
GET /authors/by_team_subject/?team_id=1&subject_id=5&sort_by=article_count&timeframe=month
```

### 3. Authors by Team and Category

```
GET /authors/by_team_category/?team_id={team_id}&category_slug={category_slug}
GET /authors/by_team_category/?team_id={team_id}&category_id={category_id}
```

`team_id` and one of `category_slug` / `category_id` are **required**; otherwise `400` (`{"error": "team_id and either category_slug or category_id are required"}`). Every other `/authors/` parameter applies.

**Example:**
```
GET /authors/by_team_category/?team_id=1&category_slug=neuroscience&sort_by=article_count&date_from=2024-01-01
```

There are no `/teams/{id}/subjects/{id}/authors/` or `/teams/{id}/categories/{slug}/authors/` routes; they 404.

## Advanced Filtering Examples

### 1. Top Authors by Article Count (Current Year)
```
GET /authors/?sort_by=article_count&order=desc&timeframe=year
```

### 2. Authors in Specific Team and Subject (Last Month)
```
GET /authors/by_team_subject/?team_id=1&subject_id=3&sort_by=article_count&timeframe=month
```

### 3. Authors in Category with Custom Date Range
```
GET /authors/by_team_category/?team_id=2&category_slug=cardiology&sort_by=article_count&date_from=2024-06-01&date_to=2024-12-01
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

### Pagination limits

`GET /authors/` uses standard `page`/`page_size` pagination. Requests whose
offset (`page * page_size`) would exceed **10,000** return `400 Bad Request`
(same cap as `/articles/` and `/trials/`). Unlike those two endpoints,
`/authors/` does **not** support `all_results=true` as a bypass — a full-table
read of Authors combined with the per-author article-count annotation is too
expensive to serve inline. For a bulk or team/subject-scoped read, use
`GET /authors/search/` instead, which does support `all_results=true` and is
always scoped to a single team.

### Author Object Fields

- `author_id`: Unique identifier
- `given_name`: Author's first name
- `family_name`: Author's last name
- `full_name`: Complete name
- `ORCID`: ORCID identifier (if available)
- `country`: Country code
- `articles_count`: Number of articles (filtered based on query parameters)
- `articles_list`: URL to this author's articles, as
  `/articles/?author_id={id}`. Built from the host the request was made
  to, so one instance serving several frontends returns each caller its
  own API domain rather than the one configured in `settings.SITE_ID`.
  The scheme follows `X-Forwarded-Proto` when a proxy sets it.

## Error Handling

### 400 Bad Request
Returned by `/authors/by_team_subject/` and `/authors/by_team_category/` when a required parameter is missing:
```json
{
  "error": "Both team_id and subject_id are required"
}
```
Also returned by any list request whose offset (`page * page_size`) exceeds 10,000.

### Missing Team ID for Category Filtering
On `GET /authors/`, `category_slug` or `category_id` without `team_id` returns an empty list (`count: 0`), not an error.

### Invalid Date Format
Invalid date parameters are silently ignored to maintain API stability.

### Invalid Numeric Parameters
Invalid team_id or subject_id values are silently ignored.

## Endpoint summary

- `GET /authors/` - All authors in the caller's scope
- `GET /authors/{id}/` - One author
- `GET /authors/{id}/coauthors/` - Co-authors, ordered by number of shared articles
- `GET /authors/by_team_subject/` - Authors filtered by team and subject (both required)
- `GET /authors/by_team_category/` - Authors filtered by team and category (both required)
- `GET /authors/search/` - Team- and subject-scoped search with `all_results` and CSV; see [03-api-and-rss-feeds.md § Search endpoints](03-api-and-rss-feeds.md#search-endpoints)
