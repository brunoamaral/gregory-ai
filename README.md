# Gregory

Gregory is an AI system that uses machine learning and natural language processing to track
clinical research and identify papers which bring improvements for patients.

Sources for research can be added by RSS feed or manually. 

The output can be seen in a static site, using `build.py` or via the api provided by the Django Rest Framework.

The docker compose file also includes a Metabase container to build dashboards and manage notifications. 

Sources can also be added to monitor Clinical Trials, in which case Gregory can notify a list of email subscribers.

For other integrations, the Django app provides RSS feeds with a live update of relevant research and new clinical trials posted.

# Sources for searches

- APTA
- BioMedCentral
- FASEB
- JNeuroSci
- MS & Rel. Disorders
- PEDro
- pubmed
- Sage Pub
- Scielo
- The Lancet

# Live Version

<https://gregory-ms.com>

<https://api.gregory-ms.com>

# Running Gregory for Production

## Server Requirements

- [ ] [Docker](https://www.docker.com/) and [docker-compose](https://docs.docker.com/compose/) with 2GB of swap memory to be able to build the MachineLearning Models. [Adding swap for Ubuntu](https://www.digitalocean.com/community/tutorials/how-to-add-swap-space-on-ubuntu-20-04)
- [ ] [Hugo](https://gohugo.io/)
- [ ] [Mailgun](https://www.mailgun.com/)

## Install

1. Edit the .env file to reflect your settins and credentials.
2. Execute `python3 setup.py`. The script will check if you have all the requirements and run the Node-RED container.
3. Run `sudo docker-compose up -d` to fetch the required images and run

The final website is built by running `python3 ./build.py`.

4. Install django

Once the db container is running, start the admin container and run the following:

```bash
sudo docker exec -it admin /bin/bash
python manage.py makemigrations
python manage.py migrate
python manage.py createsuperuser
```

Now you can login at <https://YOUR-SUB.DOMAIN/admin> or wherever your reverse proxy is listening on.

# Running Gregory for Frontend Development

## MacOS

```bash
brew install hugo;
git clone git@github.com:brunoamaral/gregory.git;
cd gregory/hugo
hugo server 
```

## Running with Hugo, Django, NodeRed, and Metabase

Edit the env.example file to fit your configuration and rename to .env

```bash
sudo docker compose up -d
python3 -m venv env
source env/bin/activate
pip install -r requirements.txt
./build.py
hugo server
```

# How everything fits together

## Node-RED

We are using [Node-RED](https://nodered.org/) to collect articles from sources without an RSS.

## Django and Postgres

The database and the API are managed using the `admin` container that comes with [Django](https://www.djangoproject.com/) and the [Django Rest Framework](https://www.django-rest-framework.org/) installed.

The database is running on a postgres container called `db`.

## Metabase

A database is only as good as it's ability to answer questions. We have a separate `metabase` container in the docker-compose.yaml file that connects directly to Gregory's database.

It's available at <http://localhost:3000/>

The current website is also using some embeded dashboards whose keys are produced each time you run `build.py`. An example can be found in the [MS Observatory Page](https://gregory-ms.com/observatory/)

## Mailgun

Email are sent from the `admin`  container using Mailgun.

To enable them, you will need a mailgun account, or you can replace them with another way to send emails.

You need to configure the relevant variables for this to work:

```bash
EMAIL_USE_TLS=true
EMAIL_MAILGUN_API='YOUR API KEY'
EMAIL_DOMAIN='YOURDOMAIN'
EMAIL_MAILGUN_API_URL="https://api.eu.mailgun.net/v3/YOURDOMAIN/messages"
```

# Update the Machine Learning Algorithms

This is not working right now  and there is a [pull request to setup an automatic process to keep the machine learning models up to date](https://github.com/brunoamaral/gregory/pull/110).

1. `cd docker-python; source .venv/bin/activate`
2. `python3 1_data_processor.py`
3. `python3 2_train_models.py`

# Thank you to

@[Antoniolopes](https://github.com/antoniolopes) for helping with the Machine Learning script.
@[Chbm](https://github.com/chbm) for help in keeping the code secure.
@[Malduarte](https://github.com/malduarte) for help with the migration from sqlite to postgres.
@[Jneves](https://github.com/jneves) for help with the build script
@[Melo](https://github.com/melo) for showing me [Hugo](https://github.com/gohugoio/hugo)
@[Nurv](https://github.com/nurv) for the suggestion in using Spacy.io
@[Rcarmo](https://github.com/rcarmo) for showing me [Node-RED](https://github.com/node-red/node-red)

And the **Lobsters** at [One Over Zero](https://github.com/oneoverzero)
