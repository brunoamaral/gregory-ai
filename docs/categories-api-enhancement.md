# Enhanced Categories API with Author Statistics

This document describes the enhancements made to the `/categories/` endpoint to include author statistics.

## What's New

The existing `/categories/` endpoint has been enhanced to include author information for each category, making it the primary endpoint for category-focused analysis with author context.

### New Response Fields

Each category now includes:
- `authors_count`: Total number of unique authors in this category
- `top_authors`: List of top authors by article count in this category

### New Query Parameters

- `include_authors` (default: `true`): Include top authors data in response
- `max_authors` (default: `10`, max: `50`): Maximum number of top authors to return per category
- `date_from` (YYYY-MM-DD): Filter articles from this date
- `date_to` (YYYY-MM-DD): Filter articles to this date  
- `timeframe`: 'year', 'month', 'week' (relative to current date)

### New Action Endpoint

- `/categories/{id}/authors/`: Get detailed author statistics for a specific category

## Usage Examples

### Basic Usage (Backward Compatible)
```
GET /categories/?team_id=1
```
Returns categories with basic info plus author statistics.

### Without Author Data (Performance Optimization)
```
GET /categories/?team_id=1&include_authors=false
```
Returns only basic category information, excluding author data for better performance.

### More Authors per Category
```
GET /categories/?team_id=1&max_authors=20
```
Returns up to 20 top authors per category instead of the default 10.

### Date-Filtered Results
```
GET /categories/?team_id=1&timeframe=year
```
Shows author statistics based only on articles published in the current year.

### Detailed Authors for Specific Category
```
GET /categories/5/authors/?min_articles=2&sort_by=articles_count&order=desc
```
Returns paginated list of all authors in category 5 who have at least 2 articles, sorted by article count.

## Response Format

```json
{
  "results": [
    {
      "id": 1,
      "category_name": "Natalizumab",
      "category_description": "Articles about Natalizumab treatment",
      "category_slug": "natalizumab",
      "category_terms": ["natalizumab", "tysabri"],
      "article_count_total": 245,
      "trials_count_total": 12,
      "authors_count": 89,
      "top_authors": [
        {
          "author_id": 12345,
          "given_name": "Jane",
          "family_name": "Smith",
          "full_name": "Jane Smith",
          "ORCID": "0000-0000-0000-0000",
          "country": "US",
          "articles_count": 25
        }
      ]
    }
  ]
}
```

## Backward Compatibility

All existing functionality remains unchanged. The new fields are added to the response, and new query parameters are optional with sensible defaults.

## Performance Considerations

- Author data is computed efficiently using database annotations
- The `include_authors=false` parameter can be used to exclude author data when not needed
- Date filtering is applied at the database level for optimal performance
- Results are paginated for the detailed authors endpoint