# Gregory
Gregory aggregates searches in JSON and outputs to a Hugo static site

[TOC]

# Live Version

https://gregory-ms.com

# Install

## Server Requirements

- [ ] [Docker](https://www.docker.com/) and [docker-compose](https://docs.docker.com/compose/) with 2GB of swap memory. [Adding swap for Ubuntu](https://www.digitalocean.com/community/tutorials/how-to-add-swap-space-on-ubuntu-20-04) 
- [ ] [Hugo](https://gohugo.io/) 
- [ ] [Mailgun](https://www.mailgun.com/)
- [ ] [Node JS v14.18.1](https://nodejs.org/en/)
- [ ] [SQLite](https://www.sqlite.org/index.html)

## Setup the environment

Run `pip3 install -r python-ml/requirements.txt`

Execute `python3 setup.py`. The script will check if you have all the requirements and run the Node-RED container.

Edit the .env file's variables to reflect your environment.

Finally, build the site with `python3 ./build.py`.

# Node-RED

This is where all of the processing of information happens. Node-RED provides a set of API endpoints to access the information it collects.

The following tabs have been configured to divide the flows:

1. Research (Collect data from different sources)
2. Email Digest (Sent to the admin and to subscribers)
3. API
4. Tests

`data/articles.json` and `data/trials.json` are generated from a Node-Red flow available in the `flows.json` file.

Node-RED is installed with a custom dockerfile from their official repository. https://github.com/node-red/node-red-docker/tree/master/docker-custom 

# Mailgun

Currently, we are using Mailgun to send emails to the admin and subscribers. These nodes can be found on the Email Digest tab of Node-RED and have been disabled.

To enable them, you will need a mailgun account, or you can replace them with another way to send emails.

# Database

The path https://api.gregory-ms.com/articles/all and https://api.gregory-ms.com/trials/all includes the full database export.

The same information is available in excel and json format: https://gregory-ms.com/downloads/

# Update the Machine Learning Algorithms

1. `cd docker-python; source .venv/bin/activate`
2. `python3 1_data_processor.py`
3. `python3 2_train_models.py`
4. Login to sqlite3: `sqlite3 gregory/docker-data/gregory.db`
5. Reset the Machine Learning records with `UPDATE articles SET ml_prediction_gnb ='', ml_prediction_lr='' WHERE article_id > 0;`
6. The Node-Red flow to review the articles runs every 10 minutes.


# Updating to version 7

1. update the code and start the containers leaving out node-red
2. migrate your data from sqlite to the postgres
3. start the admin service (django) and run `python manage.py migrate`
4. start node-red and update your flows

## Migrating from SQLite to Postgres

1. delete the column `table_constrains` and `temp` from all tables
2. dump the sqlite database, values only, table by table, and delete lines that PG will not recognize. `sqlite3 gregory.db .dump > migrate.sql`

Example:

```sql
PRAGMA foreign keys=OFF;
BEGIN TRANSACTION;
```

3. populate the `sources` and `categories` tables with your data since these are mostly static 
4. change '' to NULL in the SQL export of articles and trials
5. there may be other details to sanitize in your database. [Check this issue on github for some I had to deal with](https://github.com/brunoamaral/gregory/issues/62). 
6. migrate your data to pg `psql -d ${POSTGRES_DB} -f ./docker-data/gregory.db --username=${POSTGRES_USER} --password=${POSTGRES_PASSWORD} `

## Running django

Once the db container is running, start the admin container and run the following:

```bash
sudo docker exec -it admin /bin/bash
python manage.py migrate
python manage.py createsuperuser
```

Now you can login at https://YOUR-SUB.DOMAIN/admin or wherever your reverse proxy is listening on. 


# Thank you to

@[Antoniolopes](https://github.com/antoniolopes) for helping with the Machine Learning script.
@[Chbm](https://github.com/chbm) for help in keeping the code secure.    
@[Malduarte](https://github.com/malduarte) for help with the migration from sqlite to postgres.    
@[Jneves](https://github.com/jneves) for help with the build script    
@[Melo](https://github.com/melo) for showing me [Hugo](https://github.com/gohugoio/hugo)    
@[Nurv](https://github.com/nurv) for the suggestion in using Spacy.io    
@[Rcarmo](https://github.com/rcarmo) for showing me [Node-RED](https://github.com/node-red/node-red)       

And the **Lobsters** at [One Over Zero](https://github.com/oneoverzero)

# Sources

- APTA
- BioMedCentral
- FASEB
- JNeuroSci
- MS & Rel. Disorders
- Nature.com
- PEDro
- pubmed
- Sage Pub
- Scielo
- The Lancet