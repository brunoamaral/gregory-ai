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
   - `feed/author/<str:orcid>/`: Feed for articles by a specific author using their ORCID identifier.
   - `feed/trials/subject/<str:subject_slug>/`: Feed for clinical trials filtered by subject slug.
5. **Subscriptions Route:**
   - `subscriptions/new/`: Endpoint for new subscriptions.

# API EndPoints

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
