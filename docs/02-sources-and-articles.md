# Sources and Articles

Some core concepts before we start, we use the term **Article** to refer to both science papers published in journals and news articles in other sources.

A **Source** is any website from where we can extract articles.

A **Subject** is a group of Sources and their respective articles. 

A **Category** is a group of articles whose title matches at least one keyword in a list for terms for the category. Categories can include articles across subjects.

## Sources



1. source id
1. name
1. link (link to the RSS feed)
1. language
1. subject (A source is usually a search page for a given subject, like Multiple Sclerosis, ALS, or other conditions)
1. method (how we fetch information, should be "rss")
1. source for (will be used for the "kind" field in articles, can be one of "science paper", "news", "trials")
1. ignore ssl (whether we should bypass SSL certificate verification or not)

## Articles

1. article id

2. title

3. summary (abstract of the article)
4. link
5. published date

6. relevant

7. ml prediction gnb (Machine Learning prediction using the Gaussian Naive Bayes model)
8. ml prediction Ir, Machine Learning prediction using the Logarithmic Regression model
9. discovery date
10. noun phrases (chunks of the title that contain a noun. More details on the [Spacy documentation](https://spacy.io/usage/linguistic-features#noun-chunks).)

11. sent to admin (whether the article was sent in the admin digest or not)

12. sent to subscribers (whether the article was sent in the weekly digest or not)
13. source (where the article was found)
14. doi (digital object identification number, used to remove duplicates and fetch information from [crossref.org](https://crossref.org/))
15. kind (science paper, news, trial) 

## About Subjects and Categories

A **Subject** is a group of Sources and their respective articles. 

A **Category** is a group of articles whose title matches at least one keyword in list for that category. Categories can include articles across subjects.

There are API endpoints and RSS feeds to filter lists of articles by their category or subject in the format `articles/category/<category>` and `articles/subject/<subject>` where and is the lowercase name with spaces replaced by dashes.

### RSS feeds

1. Latest articles by subject, `https://DOMAIN.COM/feed/articles/subject/<subject>/`
2. Latest articles by category, `https://DOMAIN.COM/feed/articles/category/<category>/`

### API endpoints

1. Latest articles by subject, `https://DOMAIN.COM/articles/subject/<subject>/`
2. Latest articles by category, `https://DOMAIN.COM/articles/category/<category>/`

