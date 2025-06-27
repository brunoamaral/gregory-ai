# Articles API

The Articles API provides access to scientific publications collected and processed by Gregory.

## Endpoints

### List Articles

```
GET /api/v1/articles/
```

Returns a paginated list of all articles across all subjects and sources.

#### Query Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| page | integer | Page number for pagination |
| ordering | string | Field to order by (e.g., `-discovery_date`, `title`) |
| search | string | Full-text search across title and abstract |
| discovery_date_after | date | Filter articles discovered after this date |
| discovery_date_before | date | Filter articles discovered before this date |
| is_relevant | boolean | Filter by relevance (true/false) |

#### Response

```json
{
  "count": 1500,
  "next": "https://api.example.com/api/v1/articles/?page=2",
  "previous": null,
  "results": [
    {
      "id": 12345,
      "title": "Example Research Article",
      "abstract": "This is an example abstract for...",
      "discovery_date": "2023-05-15",
      "publication_date": "2023-05-10",
      "url": "https://journal.example.com/article/12345",
      "is_relevant": true,
      "doi": "10.1234/example.12345",
      "authors": [
        {
          "name": "Jane Smith",
          "orcid": "0000-0001-2345-6789"
        }
      ],
      "source": {
        "id": 42,
        "name": "Example Journal"
      },
      "subject": {
        "id": 5,
        "name": "Neurology"
      },
      "categories": [
        {
          "id": 12,
          "name": "Clinical Trials"
        }
      ],
      "ml_prediction": 0.89
    },
    // Additional articles...
  ]
}
```

### Get Article Detail

```
GET /api/v1/articles/{id}/
```

Returns detailed information about a specific article.

#### Response

Same format as individual article in list response, with additional fields:

```json
{
  "id": 12345,
  "title": "Example Research Article",
  "abstract": "This is an example abstract for...",
  "discovery_date": "2023-05-15",
  "publication_date": "2023-05-10",
  "url": "https://journal.example.com/article/12345",
  "is_relevant": true,
  "doi": "10.1234/example.12345",
  "authors": [...],
  "source": {...},
  "subject": {...},
  "categories": [...],
  "ml_prediction": 0.89,
  "full_text": "Complete article text if available...",
  "keywords": ["keyword1", "keyword2"],
  "citation_count": 15,
  "journal_impact_factor": 4.2
}
```

### Update Article Relevance

```
PATCH /api/v1/articles/{id}/
```

Update whether an article is considered relevant. Requires authentication.

#### Request Body

```json
{
  "is_relevant": true
}
```

#### Response

```json
{
  "id": 12345,
  "is_relevant": true,
  // Other article fields...
}
```

## Related Endpoints

For more specific article endpoints, see:

- [Articles by Subject](../subject-api.md)
- [Articles by Source](../source-api.md)
- [Articles by Team](../team-api.md)
- [Article Search](../article-search-api.md)
