# syntax=docker/dockerfile:1
FROM python:3
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /code
COPY requirements.txt /code/
# RUN pip install --upgrade pip
# RUN pip install Django>=4.0
# RUN pip install psycopg2-binary
# RUN pip install asgiref
# RUN pip install django-filter
# RUN pip install djangorestframework
# RUN pip install Markdown
# RUN pip install sqlparse
# RUN pip install pytz
# RUN pip install gunicorn
# RUN pip install requests
# RUN pip install django4-cron
# RUN pip install crossrefapi
# RUN pip install python-dotenv
# RUN pip install -U spacy
# RUN python -m spacy download en_core_web_sm
# RUN pip install feedparser
# RUN pip install pandas
# RUN pip install stopwords
# RUN pip install beautifulsoup4
# RUN pip install -U scikit-learn

RUN pip install -r requirements.txt
# COPY django /code/
# ADD django/ /code/
# RUN chmod +x /code/wait-for-postgres.sh