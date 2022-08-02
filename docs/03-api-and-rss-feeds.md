

# RSS feeds and API


#### Available RSS feeds

1. Latest articles, `/feed/latest/articles/`
2. Latest articles by subject, `/feed/articles/subject/<subject>/`
3. Latest articles by category, `/feed/articles/category/<category>/`
4. Latest clinical trials, `/feed/latest/trials/`
5. Latest relevant articles by Machine Learning, `/feed/machine-learning/`
6. Twitter feed,  `/feed/twitter/`. This includes all relevant articles by manual selection and machine learning prediction. It's read by [Zapier](https://zapier.com/) so that we can post on twitter automatically.



### Available API endpoints

- Articles By Author https://api.gregory-ms.com/articles/author/{{author_id}}/
- Articles By Category https://api.gregory-ms.com/articles/category/{{category}}/
- Articles By Source https://api.gregory-ms.com/articles/articles/source/{{source}}/
- Articles By Subject https://api.gregory-ms.com/articles/subject/{{subject}}/
- Articles http://api.gregory-ms.com/articles/
- Authors http://api.gregory-ms.com/authors/
- Related Articles  https://api.gregory-ms.com/articles/related/
- Relevant List https://api.gregory-ms.com/articles/relevant/
- Sources http://api.gregory-ms.com/sources/
- Trials By Source List https://api.gregory-ms.com/trials/source/{{source}}/
- Trials http://api.gregory-ms.com/trials/
- Unsent List https://api.gregory-ms.com/articles/unsent/
