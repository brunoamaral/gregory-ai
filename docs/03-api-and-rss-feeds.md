# RSS feeds

Gregory's API is open and doesn't require authentication unless you need to use it to add Articles or Clinical Trials.

1. **Admin Routes:**
   - `admin/`: Admin site routes.
2. **API Authentication:**
   - `api-auth/`: Default REST framework authentication routes.
3. **Article Routes:**
   - `articles/relevant/`: Access relevant articles.
   - `articles/post/`: Endpoint for posting an article.
4. **Feed Routes:**
   - `feed/author/<str:orcid>/`: Feed for articles by a specific author using their ORCID identifier. Ordered by `-published_date` (newest first), limited to 50 items.
   - `feed/trials/subject/<str:subject_slug>/`: Feed for clinical trials filtered by subject slug. Ordered by `-discovery_date` (newest first), limited to 50 items.
5. **Subscriptions Route:**
   - `subscriptions/new/`: Endpoint for new subscriptions.

## Subscription form endpoint

`POST /subscriptions/new/` accepts HTML form submissions (no CSRF token required). Fields:

| Field | Required | Description |
|---|---|---|
| `first_name` | yes | Subscriber first name |
| `last_name` | no | Subscriber last name |
| `email` | yes | Subscriber email address |
| `profile` | yes | One of: `patient`, `caregiver`, `doctor`, `clinical centre`, `researcher` |
| `list` | no | List ID(s) to subscribe to; may be repeated for multiple selections |

On success the subscriber is created or updated, subscribed to the selected lists, and the browser is redirected to `/thank-you/`. On failure it is redirected to `/error/`.

### Redirect domain and allowed domains

The redirect base URL is **not** hardcoded. Instead, the view reads the `Origin` header (falling back to `Referer`) from the request and checks whether that domain appears in the **Allowed Domains** field of at least one of the selected lists. If it matches, the subscriber is redirected back to that origin domain. If no match is found, the redirect falls back to the current Django `Site` domain.

Configure allowed origins per list in the Django admin under **Subscriptions → Lists → Allowed Domains** as a comma-separated list of hostnames:

```
example.com, staging.example.com
```

This prevents open-redirect attacks: only explicitly whitelisted domains are used for redirects.

## Articles Query Parameters

The `/articles/` endpoint supports the following query parameters for filtering:

| Parameter | Type | Description |
|---|---|---|
| `team_id` | integer | Filter by team ID |
| `subject_id` | integer | Filter by subject ID |
| `author_id` | integer | Filter by author ID |
| `doi` | string | Filter by exact DOI (case-insensitive) |
| `category_slug` | string | Filter by category slug |
| `category_id` | integer | Filter by category ID |
| `journal_slug` | string | Filter by journal (convert spaces to dashes) |
| `source_id` | integer | Filter by source ID |
| `search` | string | Search in title and summary |
| `relevant` | boolean | Filter for relevant articles. When combined with `subject_id`, relevance is scoped to that specific subject — only articles relevant *for that subject* (via ML predictions or manual marking) are returned. Without `subject_id`, relevance is checked across all subjects. |
| `ml_threshold` | float (0.0–1.0) | Minimum ML prediction confidence score. Also scoped to `subject_id` when provided. |
| `open_access` | boolean | Filter for open access articles |
| `last_days` | integer | Filter for articles from the last N days |
| `week` | integer (1–52) | Filter by week number (requires `year`) |
| `year` | integer | Year for week filtering (used with `week`) |
| `ordering` | string | Order results (e.g., `-published_date`, `title`) |
| `page` | integer | Page number for pagination |
| `page_size` | integer | Items per page (max 100) |
| `all_results` | boolean | Bypass pagination and return all results (useful for CSV export) |
| `format` | string | Response format: `json` (default) or `csv` |

## Examples

```
GET /articles/?team_id=1&subject_id=4&relevant=true
GET /articles/?relevant=true&ml_threshold=0.75
GET /articles/?relevant=true&last_days=15
GET /articles/?team_id=1&search=stem+cells
GET /articles/?format=csv&all_results=true
```

## API EndPoints

| Model                   | API Endpoint                             | Description                                         | Status                                                   |
| ----------------------- | ---------------------------------------- | --------------------------------------------------- | -------------------------------------------------------- |
| Authors                 | GET /authors/                            | List all authors                                    | ✅                                       |
| Authors                 | POST /authors/                           | Create a new author                                 | 🛑                                              |
| Authors                 | GET /authors/{id}/                       | Retrieve a specific author by ID                    | ✅                                       |
| Authors                 | PUT /authors/{id}/                       | Update a specific author by ID                      | 🛑                                              |
| Authors                 | DELETE /authors/{id}/                    | Delete a specific author by ID                      | 🛑                                              |
| Categories              | GET /categories/                         | List all categories                                 | ✅                                                        |
| Categories              | POST /categories/                        | Create a new category                               | 🛑                                              |
| Categories              | GET /categories/{id}/                    | Retrieve a specific category by ID                  | ✅                                                        |
| Categories              | PUT /categories/{id}/                    | Update a specific category by ID                    | 🛑                                              |
| Categories              | DELETE /categories/{id}/                 | Delete a specific category by ID                    | 🛑                                              |
| Categories              | GET /categories/<str:category_slug>/monthly-counts/ | Get monthly counts of articles and trials for a specific category by slug | |
| Team Categories         | GET /team-categories/                    | List all team categories                            |                                                          |
| Team Categories         | POST /team-categories/                   | Create a new team category                          | 🛑                                              |
| Team Categories         | GET /team-categories/{id}/               | Retrieve a specific team category by ID             |                                                          |
| Team Categories         | PUT /team-categories/{id}/               | Update a specific team category by ID               | 🛑                                              |
| Team Categories         | DELETE /team-categories/{id}/            | Delete a specific team category by ID               | 🛑                                              |
| Entities                | GET /entities/                           | List all entities                                   | 🛑                                              |
| Entities                | POST /entities/                          | Create a new entity                                 | 🛑                                              |
| Entities                | GET /entities/{id}/                      | Retrieve a specific entity by ID                    | 🛑                                              |
| Entities                | PUT /entities/{id}/                      | Update a specific entity by ID                      | 🛑                                              |
| Entities                | DELETE /entities/{id}/                   | Delete a specific entity by ID                      | 🛑                                              |
| Subjects                | GET /subjects/                           | List all subjects                                   | ✅                                       |
| Subjects                | POST /subjects/                          | Create a new subject                                | 🛑                                              |
| Subjects                | GET /subjects/{id}/                      | Retrieve a specific subject by ID                   | ✅                                       |
| Subjects                | PUT /subjects/{id}/                      | Update a specific subject by ID                     | 🛑                                              |
| Subjects                | DELETE /subjects/{id}/                   | Delete a specific subject by ID                     | 🛑                                              |
| Sources                 | GET /sources/                            | List all sources                                    | ✅ needs to migrate to new sources model |
| Sources                 | POST /sources/                           | Create a new source                                 | 🛑                                              |
| Sources                 | GET /sources/{id}/                       | Retrieve a specific source by ID                    | ✅ needs to migrate to new sources model |
| Sources                 | PUT /sources/{id}/                       | Update a specific source by ID                      | 🛑                                              |
| Sources                 | DELETE /sources/{id}/                    | Delete a specific source by ID                      | 🛑                                              |
| Articles                | GET /articles/                           | List all articles                                   | ✅                                       |
| Articles                | POST /articles/                          | Create a new article                                | ✅                                       |
| Articles                | GET /articles/{id}/                      | Retrieve a specific article by ID                   | ✅                                       |
| Articles                | PUT /articles/{id}/                      | Update a specific article by ID                     | 🛑                                              |
| Articles                | DELETE /articles/{id}/                   | Delete a specific article by ID                     | 🛑                                              |
| Articles                | GET /articles/relevant/                  | List relevant articles                              |                                                          |
| Articles                | POST /articles/post/                     | Post a new article                                  |                                                          |
| Articles                | GET /articles/author/{author_id}/        | List articles by author                             |                                                          |
| Articles                | GET /articles/category/{category_slug}/  | List articles by category                           |                                                          |
| Articles                | GET /articles/source/{source_id}/        | List articles by source                             |                                                          |
| Articles                | GET /articles/journal/{journal_slug}/    | List articles by journal                            |                                                          |
| Articles                | GET /articles/open-access/               | List open access articles                           |                                                          |
| Articles                | GET /articles/unsent/                    | List unsent articles                                |                                                          |
| Articles                | GET /articles/relevant/week/{year}/{week}/| List relevant articles for a specific week          |                                                          |
| Articles                | GET /articles/relevant/last/{days}/      | List relevant articles for the last number of days  |                                                          |
| Trials                  | GET /trials/                             | List all trials                                     | ✅                                       |
| Trials                  | POST /trials/                            | Create a new trial                                  | 🛑                                              |
| Trials                  | GET /trials/{id}/                        | Retrieve a specific trial by ID                     | ✅                                       |
| Trials                  | PUT /trials/{id}/                        | Update a specific trial by ID                       | 🛑                                              |
| Trials                  | DELETE /trials/{id}/                     | Delete a specific trial by ID                       | 🛑                                              |
| Trials                  | GET /trials/category/{category_slug}/    | List trials by category                             |                                                          |
| Trials                  | GET /trials/source/{source}/             | List trials by source                               |                                                          |
| Teams                   | GET /teams/                              | List all teams                                      | ✅                                       |
| Teams                   | POST /teams/                             | Create a new team                                   |                                                          |
| Teams                   | GET /teams/{id}/                         | Retrieve a specific team by ID                      | ✅                                       |
| Teams                   | PUT /teams/{id}/                         | Update a specific team by ID                        |                                                          |
| Teams                   | DELETE /teams/{id}/                      | Delete a specific team by ID                        |                                                          |
| Teams                   | GET /teams/{id}/articles                 | List all articles for a specific team by ID         | ✅                                       |
| Teams                   | GET /teams/{id}/trials                   | List all clinical trials for a specific team by ID  | ✅                                       |
| Teams                   | GET /teams/{id}/subjects                 | List all subjects for specific team by ID           | ✅                                       |
| Teams                   | GET /teams/{id}/sources                  | List all sources for specific team by ID            | ✅                                       |
| Teams                   | GET /teams/{id}/categories               | List all categories for specific team by ID         | ✅                                       |
| Teams                   | GET /teams/{id}/articles/subject/{subject_id}/     | List all articles for a team filtered by subject    | ✅              |
| Teams                   | GET /teams/{id}/articles/category/{category_slug}/ | List all articles for a team filtered by category   | ✅              |
| Teams                   | GET /teams/{id}/articles/source/{source_id}/       | List all articles for a team filtered by source     | ✅              |
| Teams                   | GET /teams/{id}/trials/category/{category_slug}/   | List clinical trials for a team filtered by category | ✅              |
| Teams                   | GET /teams/{id}/trials/subject/{subject_id}/       | List clinical trials for a team filtered by subject | ✅              |
| Teams                   | GET /teams/{id}/trials/source/{source_id}/         | List clinical trials for a team filtered by source  | ✅              |
| Teams                   | GET /teams/{id}/categories/{category_slug}/monthly-counts/ | Monthly article and trial counts for a team category | ✅              |
| MLPredictions           | GET /ml-predictions/                     | List all ML predictions                             | 🛑                                              |
| MLPredictions           | POST /ml-predictions/                    | Create a new ML prediction                          | 🛑                                              |
| MLPredictions           | GET /ml-predictions/{id}/                | Retrieve a specific ML prediction by ID             | 🛑                                              |
| MLPredictions           | PUT /ml-predictions/{id}/                | Update a specific ML prediction by ID               | 🛑                                              |
| MLPredictions           | DELETE /ml-predictions/{id}/             | Delete a specific ML prediction by ID               | 🛑                                              |
| ArticleSubjectRelevance | GET /article-subject-relevances/         | List all article subject relevances                 | 🛑                                              |
| ArticleSubjectRelevance | POST /article-subject-relevances/        | Create a new article subject relevance              | 🛑                                              |
| ArticleSubjectRelevance | GET /article-subject-relevances/{id}/    | Retrieve a specific article subject relevance by ID | 🛑                                              |
| ArticleSubjectRelevance | PUT /article-subject-relevances/{id}/    | Update a specific article subject relevance by ID   | 🛑                                              |
| ArticleSubjectRelevance | DELETE /article-subject-relevances/{id}/ | Delete a specific article subject relevance by ID   | 🛑                                              |
