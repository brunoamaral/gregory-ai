

# RSS feeds and API

Gregory has the concept of 'subject'. In this case, Multiple Sclerosis is the only subject configured. A Subject is a group of Sources and their respective articles. There are also categories that can be created. A category is a group of articles whose title matches at least one keyword in list for that category. Categories can include articles across subjects.

There are options to filter lists of articles by their category or subject in the format `articles/category/<category>` and `articles/subject/<subject>` where <category> and <subject> is the lowercase name with spaces replaced by dashes.


#### Available RSS feeds

1. Latest articles, `/feed/latest/articles/`
2. Latest articles by subject, `/feed/articles/subject/<subject>/`
3. Latest articles by category, `/feed/articles/category/<category>/`
4. Latest clinical trials, `/feed/latest/trials/`
5. Latest relevant articles by Machine Learning, `/feed/machine-learning/`
6. Twitter feed,  `/feed/twitter/`. This includes all relevant articles by manual selection and machine learning prediction. It's read by [Zapier](https://zapier.com/) so that we can post on twitter automatically.

## How to update the Machine Learning Algorithms

This is not working right now  and there is a [pull request to setup an automatic process to keep the machine learning models up to date](https://github.com/brunoamaral/gregory/pull/110).

It's useful to re-train the machine learning models once you have a good number of articles flagged as relevant.

1. `cd docker-python; source .venv/bin/activate`
2. `python3 1_data_processor.py`
3. `python3 2_train_models.py`
