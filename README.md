# Gregory

Gregory aggregates searches in JSON and outputs to a Hugo static site

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

# Install

## Server Requirements

- [ ] [Docker](https://www.docker.com/) and [docker-compose](https://docs.docker.com/compose/) with 2GB of swap memory to be able to build the MachineLearning Models. [Adding swap for Ubuntu](https://www.digitalocean.com/community/tutorials/how-to-add-swap-space-on-ubuntu-20-04)
- [ ] [Hugo](https://gohugo.io/)
- [ ] [Mailgun](https://www.mailgun.com/)

## Setup the environment

1. Edit the .env file to reflect your settins and credentials.
2. Execute `python3 setup.py`. The script will check if you have all the requirements and run the Node-RED container.
3. Run `sudo docker-compose up -d` to fetch the required images and run the

The final website is built by running `python3 ./build.py`.

## Running django

Once the db container is running, start the admin container and run the following:

```bash
sudo docker exec -it admin /bin/bash
python manage.py makemigrations
python manage.py migrate
python manage.py createsuperuser
```

Now you can login at <https://YOUR-SUB.DOMAIN/admin> or wherever your reverse proxy is listening on.

# How everything fits together

## Node-RED

We are using [Node-RED](https://nodered.org/) to collect articles and clinical trials.

Node-RED is installed with a custom dockerfile that includes some Machine Learning and Artificial Intelligence python modules, in case we need them. It is derived from the [Node-Red repository](https://github.com/node-red/node-red-docker/tree/master/docker-custom).  

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
