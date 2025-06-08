# Gregory API EndPoints

| Model                   | API Endpoint                             | Description                                         | Status                                                   |
| ----------------------- | ---------------------------------------- | --------------------------------------------------- | -------------------------------------------------------- |
| Authors                 | GET /authors/                            | List all authors                                    | :white_check_mark:                                       |
| Authors                 | POST /authors/                           | Create a new author                                 | :stop_sign:                                              |
| Authors                 | GET /authors/{id}/                       | Retrieve a specific author by ID                    | :white_check_mark:                                       |
| Authors                 | PUT /authors/{id}/                       | Update a specific author by ID                      | :stop_sign:                                              |
| Authors                 | DELETE /authors/{id}/                    | Delete a specific author by ID                      | :stop_sign:                                              |
| Categories              | GET /categories/                         | List all categories                                 |                                                          |
| Categories              | POST /categories/                        | Create a new category                               | :stop_sign:                                              |
| Categories              | GET /categories/{id}/                    | Retrieve a specific category by ID                  |                                                          |
| Categories              | PUT /categories/{id}/                    | Update a specific category by ID                    | :stop_sign:                                              |
| Categories              | DELETE /categories/{id}/                 | Delete a specific category by ID                    | :stop_sign:                                              |
| Team Categories         | GET /team-categories/                    | List all team categories                            |                                                          |
| Team Categories         | POST /team-categories/                   | Create a new team category                          | :stop_sign:                                              |
| Team Categories         | GET /team-categories/{id}/               | Retrieve a specific team category by ID             |                                                          |
| Team Categories         | PUT /team-categories/{id}/               | Update a specific team category by ID               | :stop_sign:                                              |
| Team Categories         | DELETE /team-categories/{id}/            | Delete a specific team category by ID               | :stop_sign:                                              |
| Entities                | GET /entities/                           | List all entities                                   | :stop_sign:                                              |
| Entities                | POST /entities/                          | Create a new entity                                 | :stop_sign:                                              |
| Entities                | GET /entities/{id}/                      | Retrieve a specific entity by ID                    | :stop_sign:                                              |
| Entities                | PUT /entities/{id}/                      | Update a specific entity by ID                      | :stop_sign:                                              |
| Entities                | DELETE /entities/{id}/                   | Delete a specific entity by ID                      | :stop_sign:                                              |
| Subjects                | GET /subjects/                           | List all subjects                                   | :white_check_mark:                                       |
| Subjects                | POST /subjects/                          | Create a new subject                                | :stop_sign:                                              |
| Subjects                | GET /subjects/{id}/                      | Retrieve a specific subject by ID                   | :white_check_mark:                                       |
| Subjects                | PUT /subjects/{id}/                      | Update a specific subject by ID                     | :stop_sign:                                              |
| Subjects                | DELETE /subjects/{id}/                   | Delete a specific subject by ID                     | :stop_sign:                                              |
| Sources                 | GET /sources/                            | List all sources                                    | :white_check_mark: needs to migrate to new sources model |
| Sources                 | POST /sources/                           | Create a new source                                 | :stop_sign:                                              |
| Sources                 | GET /sources/{id}/                       | Retrieve a specific source by ID                    | :white_check_mark: needs to migrate to new sources model |
| Sources                 | PUT /sources/{id}/                       | Update a specific source by ID                      | :stop_sign:                                              |
| Sources                 | DELETE /sources/{id}/                    | Delete a specific source by ID                      | :stop_sign:                                              |
| Articles                | GET /articles/                           | List all articles                                   | :white_check_mark:                                       |
| Articles                | POST /articles/                          | Create a new article                                | :white_check_mark:                                       |
| Articles                | GET /articles/{id}/                      | Retrieve a specific article by ID                   | :white_check_mark:                                       |
| Articles                | PUT /articles/{id}/                      | Update a specific article by ID                     | :stop_sign:                                              |
| Articles                | DELETE /articles/{id}/                   | Delete a specific article by ID                     | :stop_sign:                                              |
| Trials                  | GET /trials/                             | List all trials                                     | :white_check_mark:                                       |
| Trials                  | POST /trials/                            | Create a new trial                                  | :stop_sign:                                              |
| Trials                  | GET /trials/{id}/                        | Retrieve a specific trial by ID                     | :white_check_mark:                                       |
| Trials                  | PUT /trials/{id}/                        | Update a specific trial by ID                       | :stop_sign:                                              |
| Trials                  | DELETE /trials/{id}/                     | Delete a specific trial by ID                       | :stop_sign:                                              |
| Teams                   | GET /teams/                              | List all teams                                      | :white_check_mark:                                       |
| Teams                   | POST /teams/                             | Create a new team                                   |                                                          |
| Teams                   | GET /teams/{id}/                         | Retrieve a specific team by ID                      | :white_check_mark:                                       |
| Teams                   | PUT /teams/{id}/                         | Update a specific team by ID                        |                                                          |
| Teams                   | DELETE /teams/{id}/                      | Delete a specific team by ID                        |                                                          |
| Teams                   | GET /teams/{id}/articles                 | List all articles for a specific team by ID         | :white_check_mark:                                       |
| Teams                   | GET /teams/{id}/trials                   | List all clinical trials for a specific team by ID  | :white_check_mark:                                       |
| Teams                   | GET /teams/{id}/subjects                 | List all subjects for specific team by ID           | :white_check_mark:                                       |
| Teams                   | GET /teams/{id}/sources                  | List all sources for specific team by ID            | :white_check_mark:                                       |
| Teams                   | GET /teams/{id}/categories               | List all categories for specific team by ID         | :white_check_mark:                                       |
| Teams                   | GET /teams/{id}/articles/subject/{subject_id}/     | List all articles for a team filtered by subject    | :white_check_mark:              |
| Teams                   | GET /teams/{id}/articles/category/{category_slug}/ | List all articles for a team filtered by category   | :white_check_mark:              |
| Teams                   | GET /teams/{id}/articles/source/{source_id}/       | List all articles for a team filtered by source     | :white_check_mark:              |
| Teams                   | GET /teams/{id}/trials/category/{category_slug}/   | List clinical trials for a team filtered by category| :white_check_mark:              |
| Teams                   | GET /teams/{id}/trials/subject/{subject_id}/       | List clinical trials for a team filtered by subject | :white_check_mark:              |
| Teams                   | GET /teams/{id}/trials/source/{source_id}/         | List clinical trials for a team filtered by source  | :white_check_mark:              |
| Teams                   | GET /teams/{id}/categories/{category_slug}/monthly-counts/ | Monthly article and trial counts for a team category | :white_check_mark:              |
| MLPredictions           | GET /ml-predictions/                     | List all ML predictions                             | :stop_sign:                                              |
| MLPredictions           | POST /ml-predictions/                    | Create a new ML prediction                          | :stop_sign:                                              |
| MLPredictions           | GET /ml-predictions/{id}/                | Retrieve a specific ML prediction by ID             | :stop_sign:                                              |
| MLPredictions           | PUT /ml-predictions/{id}/                | Update a specific ML prediction by ID               | :stop_sign:                                              |
| MLPredictions           | DELETE /ml-predictions/{id}/             | Delete a specific ML prediction by ID               | :stop_sign:                                              |
| ArticleSubjectRelevance | GET /article-subject-relevances/         | List all article subject relevances                 | :stop_sign:                                              |
| ArticleSubjectRelevance | POST /article-subject-relevances/        | Create a new article subject relevance              | :stop_sign:                                              |
| ArticleSubjectRelevance | GET /article-subject-relevances/{id}/    | Retrieve a specific article subject relevance by ID | :stop_sign:                                              |
| ArticleSubjectRelevance | PUT /article-subject-relevances/{id}/    | Update a specific article subject relevance by ID   | :stop_sign:                                              |
| ArticleSubjectRelevance | DELETE /article-subject-relevances/{id}/ | Delete a specific article subject relevance by ID   | :stop_sign:                                              |