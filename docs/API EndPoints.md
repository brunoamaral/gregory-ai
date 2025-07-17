# Gregory API EndPoints

## Data Formats

All API endpoints support three formats:

1. **JSON** (default): Standard JSON format for API responses
2. **HTML** (browsable API): A user-friendly HTML interface for browsing the API
3. **CSV**: Data in comma-separated values format for spreadsheet applications

To get the response in CSV format, you can either:

1. Add the format parameter: `?format=csv` to the URL
2. Set the Accept header to `text/csv`

Example:
```bash
# Using format parameter
curl https://api.example.com/articles/?format=csv

# Using Accept header
curl -H "Accept: text/csv" https://api.example.com/articles/
```

### CSV Export Features

When exporting data as CSV, the following features are available:

1. **Pagination Bypass**: Add `all_results=true` to retrieve all data without pagination (useful for downloading complete datasets)
2. **Clean Data Format**: The CSV export automatically:
   - Consolidates nested fields (authors, subjects, clinical_trials, etc.) into single, readable columns
   - Properly formats and escapes text fields to handle special characters
   - Sets meaningful column names
   - Uses a standardized filename format (e.g., gregory-ai-articles-2023-07-15.csv)
3. **Type Safety**: All values are properly converted to strings to ensure compatibility with CSV format

Example for exporting all search results as CSV:
```bash
# Export all articles matching a search query as CSV
curl https://api.example.com/articles/search/?team_id=1&subject_id=1&search=covid&format=csv&all_results=true

# Export all clinical trials matching a search query as CSV
curl https://api.example.com/trials/search/?team_id=1&subject_id=1&status=Recruiting&format=csv&all_results=true
```

## Endpoints

| Model                   | API Endpoint                             | Description                                         | Parameters                                              | Status                                                   |
| ----------------------- | ---------------------------------------- | --------------------------------------------------- | ------------------------------------------------------- | -------------------------------------------------------- |
| Authors                 | GET /authors/                            | List all authors                                    | `sort_by`, `order`, `team_id`, `subject_id`, `category_slug`, `date_from`, `date_to`, `timeframe` | ✅ **Available**                                       |
| Authors                 | POST /authors/                           | Create a new author                                 | N/A                                                     | ❌ **Not Available**                                  |
| Authors                 | GET /authors/{id}/                       | Retrieve a specific author by ID                    | `id` (path)                                             | ✅ **Available**                                       |
| Authors                 | PUT /authors/{id}/                       | Update a specific author by ID                      | N/A                                                     | ❌ **Not Available**                                  |
| Authors                 | DELETE /authors/{id}/                    | Delete a specific author by ID                      | N/A                                                     | ❌ **Not Available**                                  |
| Authors                 | GET /authors/search/                     | Search authors by full name with filters & optional CSV export | `team_id` *(req)*, `subject_id` *(req)*, `full_name`, `format`, `all_results` | ✅ **Available**                                       |
| Categories              | GET /categories/                         | List all categories                                 | Standard pagination params                               | ✅ **Available**                                       |
| Categories              | POST /categories/                        | Create a new category                               | N/A                                                     | ❌ **Not Available**                                  |
| Categories              | GET /categories/{id}/                    | Retrieve a specific category by ID                  | `id` (path)                                             | ✅ **Available**                                       |
| Categories              | PUT /categories/{id}/                    | Update a specific category by ID                    | N/A                                                     | ❌ **Not Available**                                  |
| Categories              | DELETE /categories/{id}/                 | Delete a specific category by ID                    | N/A                                                     | ❌ **Not Available**                                  |
| Team Categories         | GET /team-categories/                    | List all team categories                            | Standard pagination params                               | ✅ **Available** (via /categories/)                   |
| Team Categories         | POST /team-categories/                   | Create a new team category                          | N/A                                                     | ❌ **Not Available**                                  |
| Team Categories         | GET /team-categories/{id}/               | Retrieve a specific team category by ID             | `id` (path)                                             | ✅ **Available** (via /categories/{id}/)              |
| Team Categories         | PUT /team-categories/{id}/               | Update a specific team category by ID               | N/A                                                     | ❌ **Not Available**                                  |
| Team Categories         | DELETE /team-categories/{id}/            | Delete a specific team category by ID               | N/A                                                     | ❌ **Not Available**                                  |
| Entities                | GET /entities/                           | List all entities                                   | N/A                                                     | ❌ **Not Available**                                  |
| Entities                | POST /entities/                          | Create a new entity                                 | N/A                                                     | ❌ **Not Available**                                  |
| Entities                | GET /entities/{id}/                      | Retrieve a specific entity by ID                    | N/A                                                     | ❌ **Not Available**                                  |
| Entities                | PUT /entities/{id}/                      | Update a specific entity by ID                      | N/A                                                     | ❌ **Not Available**                                  |
| Entities                | DELETE /entities/{id}/                   | Delete a specific entity by ID                      | N/A                                                     | ❌ **Not Available**                                  |
| Subjects                | GET /subjects/                           | List all subjects                                   | Standard pagination params                               | ✅ **Available**                                       |
| Subjects                | POST /subjects/                          | Create a new subject                                | N/A                                                     | ❌ **Not Available**                                  |
| Subjects                | GET /subjects/{id}/                      | Retrieve a specific subject by ID                   | `id` (path)                                             | ✅ **Available**                                       |
| Subjects                | PUT /subjects/{id}/                      | Update a specific subject by ID                     | N/A                                                     | ❌ **Not Available**                                  |
| Subjects                | DELETE /subjects/{id}/                   | Delete a specific subject by ID                     | N/A                                                     | ❌ **Not Available**                                  |
| Sources                 | GET /sources/                            | List all sources with optional filters              | `team_id`, `subject_id`, `search`, `ordering`, pagination | ✅ **Available**                                       |
| Sources                 | POST /sources/                           | Create a new source                                 | N/A                                                     | ❌ **Not Available**                                  |
| Sources                 | GET /sources/{id}/                       | Retrieve a specific source by ID                    | `id` (path)                                             | ✅ **Available**                                       |
| Sources                 | PUT /sources/{id}/                       | Update a specific source by ID                      | N/A                                                     | ❌ **Not Available**                                  |
| Sources                 | DELETE /sources/{id}/                    | Delete a specific source by ID                      | N/A                                                     | ❌ **Not Available**                                  |
| Articles                | GET /articles/                           | List all articles                                   | `search`, `title`, `summary`, filtering & pagination    | ✅ **Available**                                       |
| Articles                | POST /articles/                          | Create a new article                                | `title`, `link`, `doi`, `summary`, `source_id`, etc.   | ✅ **Available** (via /articles/post/)                |
| Articles                | GET /articles/{id}/                      | Retrieve a specific article by ID                   | `id` (path)                                             | ✅ **Available**                                       |
| Articles                | PUT /articles/{id}/                      | Update a specific article by ID                     | N/A                                                     | ❌ **Not Available**                                  |
| Articles                | DELETE /articles/{id}/                   | Delete a specific article by ID                     | N/A                                                     | ❌ **Not Available**                                  |
| Articles                | GET /articles/search/                    | Search articles with filters & optional CSV export  | `team_id` *(req)*, `subject_id` *(req)*, `title`, `summary`, `search`, `format`, `all_results` | ✅ **Available**                                       |
| Articles                | POST /articles/search/                   | Search articles (POST method)                       | Same as GET but in request body                         | ✅ **Available**                                       |
| Articles                | GET /articles/relevant/                  | List relevant articles (ML + manual)                | Standard pagination params                               | ✅ **Available**                                       |
| Articles                | GET /articles/relevant/last/{days}/      | List relevant articles from last N days             | `days` (path)                                           | ✅ **Available**                                       |
| Articles                | GET /articles/relevant/week/{year}/{week}/ | List relevant articles from specific week         | `year` (path), `week` (path)                           | ✅ **Available**                                       |
| Articles                | GET /articles/open-access/               | List open access articles                           | Standard pagination params                               | ✅ **Available**                                       |
| Articles                | GET /articles/unsent/                    | List articles not sent to subscribers               | Standard pagination params                               | ✅ **Available**                                       |
| Articles                | GET /articles/author/{author_id}/        | List articles by specific author                    | `author_id` (path)                                      | ✅ **Available**                                       |
| Articles                | GET /articles/category/{category_slug}/  | List articles by category                           | `category_slug` (path)                                  | ✅ **Available**                                       |
| Articles                | GET /articles/journal/{journal_slug}/    | List articles by journal                            | `journal_slug` (path)                                   | ✅ **Available**                                       |
| Trials                  | GET /trials/                             | List all trials                                     | `search`, `title`, `summary`, filtering & pagination    | ✅ **Available**                                       |
| Trials                  | POST /trials/                            | Create a new trial                                  | N/A                                                     | ❌ **Not Available**                                  |
| Trials                  | GET /trials/{id}/                        | Retrieve a specific trial by ID                     | `id` (path)                                             | ✅ **Available**                                       |
| Trials                  | PUT /trials/{id}/                        | Update a specific trial by ID                       | N/A                                                     | ❌ **Not Available**                                  |
| Trials                  | DELETE /trials/{id}/                     | Delete a specific trial by ID                       | N/A                                                     | ❌ **Not Available**                                  |
| Trials                  | GET /trials/search/                      | Search trials with filters & optional CSV export    | `team_id` *(req)*, `subject_id` *(req)*, `title`, `summary`, `search`, `status`, `format`, `all_results` | ✅ **Available**                                       |
| Trials                  | POST /trials/search/                     | Search trials (POST method)                         | Same as GET but in request body                         | ✅ **Available**                                       |
| Teams                   | GET /teams/                              | List all teams                                      | Standard pagination params                               | ✅ **Available**                                       |
| Teams                   | POST /teams/                             | Create a new team                                   | N/A                                                     | ❌ **Not Available**                                  |
| Teams                   | GET /teams/{id}/                         | Retrieve a specific team by ID                      | `id` (path)                                             | ✅ **Available**                                       |
| Teams                   | PUT /teams/{id}/                         | Update a specific team by ID                        | N/A                                                     | ❌ **Not Available**                                  |
| Teams                   | DELETE /teams/{id}/                      | Delete a specific team by ID                        | N/A                                                     | ❌ **Not Available**                                  |
| Teams                   | GET /teams/{id}/articles/                | List all articles for a specific team by ID         | `id` (path), pagination params                         | ✅ **Available**                                       |
| Teams                   | GET /teams/{id}/trials/                  | List all clinical trials for a specific team by ID  | `id` (path), pagination params                         | ✅ **Available**                                       |
| Teams                   | GET /teams/{id}/subjects/                | List all subjects for specific team by ID           | `id` (path), pagination params                         | ✅ **Available**                                       |
| Teams                   | GET /teams/{id}/categories/              | List all categories for specific team by ID         | `id` (path), pagination params                         | ✅ **Available**                                       |
| Teams                   | GET /teams/{id}/subjects/{subject_id}/categories/ | List all categories for a team filtered by subject | `id` (path), `subject_id` (path), pagination params | ✅ **Available**                                       |
| Teams                   | GET /teams/{id}/articles/subject/{subject_id}/     | List all articles for a team filtered by subject    | `id` (path), `subject_id` (path)              | ✅ **Available**              |
| Teams                   | GET /teams/{id}/articles/category/{category_slug}/ | List all articles for a team filtered by category   | `id` (path), `category_slug` (path)           | ✅ **Available**              |
| Teams                   | GET /teams/{id}/articles/source/{source_id}/       | List all articles for a team filtered by source     | `id` (path), `source_id` (path)               | ✅ **Available**              |
| Teams                   | GET /teams/{id}/trials/category/{category_slug}/   | List clinical trials for a team filtered by category| `id` (path), `category_slug` (path)           | ✅ **Available**              |
| Teams                   | GET /teams/{id}/trials/subject/{subject_id}/       | List clinical trials for a team filtered by subject | `id` (path), `subject_id` (path)              | ✅ **Available**              |
| Teams                   | GET /teams/{id}/trials/source/{source_id}/         | List clinical trials for a team filtered by source  | `id` (path), `source_id` (path)               | ✅ **Available**              |
| Teams                   | GET /teams/{id}/categories/{category_slug}/monthly-counts/ | Monthly article and trial counts for a team category | `id` (path), `category_slug` (path)    | ✅ **Available**              |
| Teams                   | GET /teams/{id}/subjects/{subject_id}/authors/     | List authors by team and subject                     | `id` (path), `subject_id` (path), author filters | ✅ **Available**              |
| Teams                   | GET /teams/{id}/categories/{category_slug}/authors/ | List authors by team and category                   | `id` (path), `category_slug` (path), author filters | ✅ **Available**              |
| MLPredictions           | GET /ml-predictions/                     | List all ML predictions                             | N/A                                                     | ❌ **Not Available**                                  |
| MLPredictions           | POST /ml-predictions/                    | Create a new ML prediction                          | N/A                                                     | ❌ **Not Available**                                  |
| MLPredictions           | GET /ml-predictions/{id}/                | Retrieve a specific ML prediction by ID             | N/A                                                     | ❌ **Not Available**                                  |
| MLPredictions           | PUT /ml-predictions/{id}/                | Update a specific ML prediction by ID               | N/A                                                     | ❌ **Not Available**                                  |
| MLPredictions           | DELETE /ml-predictions/{id}/             | Delete a specific ML prediction by ID               | N/A                                                     | ❌ **Not Available**                                  |
| ArticleSubjectRelevance | GET /article-subject-relevances/         | List all article subject relevances                 | N/A                                                     | ❌ **Not Available**                                  |
| ArticleSubjectRelevance | POST /article-subject-relevances/        | Create a new article subject relevance              | N/A                                                     | ❌ **Not Available**                                  |
| ArticleSubjectRelevance | GET /article-subject-relevances/{id}/    | Retrieve a specific article subject relevance by ID | N/A                                                     | ❌ **Not Available**                                  |
| ArticleSubjectRelevance | PUT /article-subject-relevances/{id}/    | Update a specific article subject relevance by ID   | N/A                                                     | ❌ **Not Available**                                  |
| ArticleSubjectRelevance | DELETE /article-subject-relevances/{id}/ | Delete a specific article subject relevance by ID   | N/A                                                     | ❌ **Not Available**                                  |

## Additional Endpoints

| Category                | API Endpoint                             | Description                                         | Parameters                                              | Status                                                   |
| ----------------------- | ---------------------------------------- | --------------------------------------------------- | ------------------------------------------------------- | -------------------------------------------------------- |
| **Authentication**      | POST /api/token/                         | Obtain JWT authentication token                     | `username`, `password` (body)                          | ✅ **Available**                                       |
| **Authentication**      | POST /api/token/get/                     | Obtain auth token (alternative)                     | `username`, `password` (body)                          | ✅ **Available**                                       |
| **Authentication**      | GET /protected_endpoint/                 | Test protected endpoint                             | Requires authentication header                          | ✅ **Available**                                       |
| **Subscriptions**       | POST /subscriptions/new/                 | Subscribe to email lists                            | `first_name`, `last_name`, `email`, `profile`, `list` (body) | ✅ **Available**                                       |
| **Email Templates**     | GET /emails/                             | Email template preview dashboard                    | None                                                    | ✅ **Available**                                       |
| **Email Templates**     | GET /emails/preview/{template_name}/     | Preview specific email template                     | `template_name` (path)                                  | ✅ **Available**                                       |
| **Email Templates**     | GET /emails/context/{template_name}/     | Get template context as JSON                        | `template_name` (path)                                  | ✅ **Available**                                       |
| **RSS Feeds**           | GET /feed/latest/articles/               | RSS feed for latest articles                        | None                                                    | ✅ **Available**                                       |
| **RSS Feeds**           | GET /feed/latest/trials/                 | RSS feed for latest trials                          | None                                                    | ✅ **Available**                                       |
| **RSS Feeds**           | GET /feed/articles/author/{author_id}/   | RSS feed for articles by author                     | `author_id` (path)                                      | ✅ **Available**                                       |
| **RSS Feeds**           | GET /feed/articles/subject/{subject}/    | RSS feed for articles by subject                    | `subject` (path)                                        | ✅ **Available**                                       |
| **RSS Feeds**           | GET /feed/articles/open-access/          | RSS feed for open access articles                   | None                                                    | ✅ **Available**                                       |
| **RSS Feeds**           | GET /feed/machine-learning/              | RSS feed for ML predictions                         | None                                                    | ✅ **Available**                                       |
| **RSS Feeds**           | GET /feed/teams/{team_id}/categories/{category_slug}/ | RSS feed for team category articles        | `team_id` (path), `category_slug` (path)               | ✅ **Available**                                       |

## Parameter Details

### Common Parameters
- **Standard pagination params**: `page`, `page_size`
- **Format params**: `format` (json, csv, html), `all_results` (true/false for CSV export)
- **Author filter params**: `sort_by`, `order`, `team_id`, `subject_id`, `category_slug`, `date_from`, `date_to`, `timeframe`
- **Sources filter params**: `team_id`, `subject_id`, `search` (name/description), `ordering` (name, source_id)

### Sources Endpoint Filtering

The `/sources/` endpoint supports comprehensive filtering and searching:

**Filtering:**
- `?team_id=X` - Filter sources by team ID
- `?subject_id=Y` - Filter sources by subject ID
- `?team_id=X&subject_id=Y` - Filter by both team and subject

**Searching:**
- `?search=keyword` - Search in source name and description fields

**Ordering:**
- `?ordering=name` - Order by name (default)
- `?ordering=source_id` - Order by source ID
- `?ordering=-name` - Reverse order (add `-` prefix)

**Example Usage:**
```bash
# Filter sources by team
GET /sources/?team_id=1

# Filter sources by team and subject
GET /sources/?team_id=1&subject_id=2

# Search and filter combined
GET /sources/?team_id=1&search=pubmed&ordering=name
```

### Search Endpoint Requirements
- **Required params** *(marked with req)*: Must be provided for the endpoint to function
- **Optional params**: Can be omitted, will use default values
- **Path params**: Part of the URL path, must be provided when using the endpoint

## Legend

- ✅ **Available**: Endpoint is implemented and functional
- ❌ **Not Available**: Endpoint is not implemented (write operations are generally disabled for security)
- ⚠️ **Limited**: Endpoint exists but has restrictions or limitations