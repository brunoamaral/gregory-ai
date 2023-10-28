# API and RSS feeds

## Authentication

- **API Authentication:** `/api-auth/`
- **Obtain Token:** `/api/token/`
- **Get Token:** `/api/token/get/`

## Articles

- **List All Articles (via router):** `/articles/`
- **Get Relevant Articles:** `/articles/relevant/`
- **Post an Article:** `/articles/post/`
- **Get Articles by an Author:** `/articles/author/{author_id}/`
- **Get Articles by a Category:** `/articles/category/{category_slug}/`
- **Get Articles by a Source:** `/articles/source/{source}/`
- **Get Articles by a Subject:** `/articles/subject/{subject}/`
- **Get Articles by a Journal:** `/articles/journal/{journal}/`
- **Get Open Access Articles:** `/articles/open-access/`
- **Get Unsent Articles:** `/articles/unsent/`
- **Get Relevant Articles by Week:** `/articles/relevant/week/{year}/{week}/`
- **Get Relevant Articles from the Last X Days:** `/articles/relevant/last/{days}/`

## Categories

- **List All Categories:** `/categories/`
- **Get Monthly Counts for a Category:** `/categories/{category_slug}/monthly-counts/`

## Trials

- **List All Trials (via router):** `/trials/`
- **Get Trials by a Category:** `/trials/category/{category_slug}/`
- **Get Trials by a Source:** `/trials/source/{source}/`

## RSS Feeds

- **Get Articles by an Author:** `/feed/articles/author/{author_id}/`
- **Get Articles by a Category:** `/feed/articles/category/{category}/`
- **Get Articles by a Subject:** `/feed/articles/subject/{subject}/`
- **Get Open Access Articles:** `/feed/articles/open-access/`
- **Get Latest Articles:** `/feed/latest/articles/`
- **Get Latest Trials:** `/feed/latest/trials/`
- **Get Relevant Articles by Machine Learning:** `/feed/machine-learning/`

You might want to use tools such as Postman or similar to test these endpoints, ensuring they provide the expected functionality.