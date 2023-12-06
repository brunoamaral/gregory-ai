# API and RSS feeds

Gregory's API is open and doesn't require authentication unless you need to use it to add Articles or Clinical Trials.

1. **Admin Routes:**
   - `admin/`: Admin site routes.
2. **API Authentication:**
   - `api-auth/`: Default REST framework authentication routes.
3. **Article Routes:**
   - `articles/relevant/`: Access relevant articles.
   - `articles/post/`: Endpoint for posting an article.
4. **Feed Routes:**
   - `feed/articles/author/<int:author_id>/`: Feed for articles by a specific author.
   - `feed/articles/category/<str:category>/`: Feed for articles in a specific category.
   - `feed/articles/subject/<str:subject>/`: Feed for articles on a specific subject.
   - `feed/articles/open-access/`: Feed for open-access articles.
   - `feed/latest/articles/`: Feed for the latest articles.
   - `feed/latest/trials/`: Feed for the latest trials.
   - `feed/machine-learning/`: Feed for machine learning related articles.
5. **Subscriptions Route:**
   - `subscriptions/new/`: Endpoint for new subscriptions.
6. **More Articles Routes:**
   - `articles/author/<int:author_id>/`: List articles by a specific author.
   - `articles/category/<category_slug>/`: List articles in a specific category.
   - `articles/source/<int:source_id>`: List articles from a specific source.
   - `articles/subject/<subject>/`: List articles on a specific subject.
   - `articles/journal/<journal_slug>/`: List articles from a specific journal.
   - `articles/open-access/`: List open-access articles.
   - `articles/unsent/`: List unsent articles.
7. **Relevant Articles Routes:**
   - `articles/relevant/week/<int:year>/<int:week>/`: Articles relevant for a specific week.
   - `articles/relevant/last/<int:days>/`: Articles relevant in the last X days.
8. **Category Routes:**
   - `categories/`: List all categories.
   - `categories/<category_slug>/monthly-counts/`: Monthly counts for a specific category.
9. **Trial Routes:**
   - `trials/category/<category_slug>/`: List trials in a specific category.
   - `trials/source/<source>/`: List trials from a specific source.
10. **Token Routes:**
    - `api/token/`: Obtain a new token pair.
    - `api/token/get/`: Obtain an authentication token.
11. **Protected Endpoint Route:**
    - `protected_endpoint/`: A protected endpoint.
12. **Router Registered Routes:**
    - `articles/`: Routes for `ArticleViewSet`.
    - `authors/`: Routes for `AuthorsViewSet`.
    - `categories/`: Routes for `CategoryViewSet`.
    - `sources/`: Routes for `SourceViewSet`.
    - `trials/`: Routes for `TrialViewSet`.


You might want to use tools such as Postman or similar to test these endpoints, ensuring they provide the expected functionality.