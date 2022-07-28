# Gregory MS

Gregory is an AI system that uses machine learning and natural language processing to track
clinical research and identify papers which bring improvements for patients.

Sources for research can be added by RSS feed or manually. 

The output can be seen in a static site, using `build.py` or via the api provided by the Django Rest Framework.

The docker compose file also includes a Metabase container to build dashboards and manage notifications. 

Sources can also be added to monitor Clinical Trials, in which case Gregory can notify a list of email subscribers.

For other integrations, the Django app provides RSS feeds with a live update of relevant research and new clinical trials posted.

Website: <https://gregory-ms.com>

Rest API: <https://api.gregory-ms.com> 

## Current sources for searches

1. APTA
2. BioMedCentral
3. FASEB
4. JNeuroSci
5. MS & Rel. Disorders
6. PEDro
7. pubmed
8. Sage Pub
9. Scielo
10. The Lancet

## Running in Production

### Server Requirements

- [ ] Python 3.9
- [ ] [Docker](https://www.docker.com/) and [docker-compose](https://docs.docker.com/compose/) with 2GB of swap memory to be able to build the MachineLearning Models. ([Adding swap for Ubuntu](https://www.digitalocean.com/community/tutorials/how-to-add-swap-space-on-ubuntu-20-04))
- [ ] [Hugo](https://gohugo.io/)
- [ ] [Mailgun](https://www.mailgun.com/) (optional)

### Install

1. **Install python dependencies locally**
2. **Edit the .env file** to reflect your settins and credentials.
3. **Execute** `python3 setup.py`. The script will check if you have all the requirements and run the Node-RED container.
4. **Run the containers** `sudo docker-compose up -d` 
5. **Create the database** for the metabase module (optional)

6. **Install** django

Run the following inside the **admin** container:

```bash
python manage.py makemigrations
python manage.py migrate
python manage.py createsuperuser
```

Now you can login at <https://YOUR-SUB.DOMAIN/admin> or wherever your reverse proxy is listening on.

7. **Configure** database maintenance tasks

Gregory needs to run a series of tasks to fetch missing information and apply the machine learning algorithm. For that, we are using [Django-Con](https://github.com/Tivix/django-cron). Add the following to your crontab:

```cron
*/5 * * * * /usr/bin/docker exec admin ./manage.py runcrons > /root/log
```

8. **Setup NodeRED** by installing the required nodes

You can visit the NodeRED editor and install the modules with the graphic interface to manage the pallete, or you can login to the container and run the following:

```
npm install node-red-contrib-cheerio && \
npm install node-red-contrib-moment && \
npm install node-red-contrib-sqlstring && \
npm install node-red-dashboard && \
npm install node-red-node-feedparser && \
npm install node-red-node-sqlite && \
npm install node-red-node-ui-list && \
npm install node-red-contrib-persist && \
npm install node-red-contrib-rss && \
npm install node-red-contrib-meta \
npm install node-red-contrib-join-wait \
npm install node-red-contrib-postgresql \ 
npm install node-red-contrib-re-postgres \
npm install node-red-contrib-string 
```

9.  **Configure** hugo

You need to install some node modules for hugo to build and process the css. Simply run this.

```bash
cd hugo && npm i && cd ..;
```

In the `hugo` dir you will find a `config.toml` file that needs to be configured with your domain.


10. **Build** by running `python3 ./build.py`.

## How everything fits together

### Node-RED

We use [Node-RED](https://nodered.org/) to collect articles from sources without an RSS. These flows need to be added manually and configured to write to the postres database. If your node-red container does not show a series of flows, import the `flows.json` file from this repository.

### Django and Postgres

Most of the logic is inside Django, the **admin** container provides the [Django Rest Framework](https://www.django-rest-framework.org/), manages subscriptions, and sends emails.

The following subscriptions are available:

**Admin digest**

Sent every 48 hours with the latest articles and their machine learning prediction. Allows the admin access to an Edit link where the article can be edited and tagged as relevant.

**Weekly digest**

Sent every Tuesday, lists the relevant articles discovered in the last week.

**Clinical Trials**

Sent every 12 hours if a new clinical trial was posted.

The title email footer for these emails needs to be set in the Custom Settings section of the admin backoffice.

Django also allows you to add new sources from where to fetch articles. Take a look at `/admin/gregory/sources/ `

![image-20220619195841565](images/image-20220619195841565.png)

### Metabase

A database is only as good as it's ability to answer questions. We have a separate `metabase` container in the docker-compose.yaml file that connects directly to Gregory's database.

It's available at <http://localhost:3000/>

The current website is also using some embeded dashboards whose keys are produced each time you run `build.py`. An example can be found in the [MS Observatory Page](https://gregory-ms.com/observatory/)

<img src="images/image-20220619200017849.png" alt="image-20220619200017849" style="zoom:33%;" />

Including dashboards in your content:

1. Add the dashboard ID to `data/dashboards.json`
2. In your content, use the shortcode `{{ metabase-embed dashboard="10" width="1300" height="1250" }}`
3. Run `build.py`

### Mailgun

Email are sent from the `admin`  container using Mailgun.

To enable them, you will need a mailgun account, or you can replace them with another way to send emails.

You need to configure the relevant variables for this to work:

```bash
EMAIL_USE_TLS=true
EMAIL_MAILGUN_API='YOUR API KEY'
EMAIL_DOMAIN='YOURDOMAIN'
EMAIL_MAILGUN_API_URL="https://api.eu.mailgun.net/v3/YOURDOMAIN/messages"
```

As an alternative, you can configure Django to use any other email server.

### RSS feeds

The following RSS feeds are configured in Django:

1. Latest articles, `/feed/latest/articles/`
2. Latest clinical trials, `/feed/latest/trials/`
3. Latest relevant articles by Machine Learning, `/feed/machine-learning/`
4. Twitter feed,  `/feed/twitter/`. This includes all relevant articles by manual selection and machine learning prediction. It's read by [Zapier](https://zapier.com/) so that we can post on twitter automatically. 

## How to update the Machine Learning Algorithms

This is not working right now  and there is a [pull request to setup an automatic process to keep the machine learning models up to date](https://github.com/brunoamaral/gregory/pull/110).

It's useful to re-train the machine learning models once you have a good number of articles flagged as relevant.

1. `cd docker-python; source .venv/bin/activate`
2. `python3 1_data_processor.py`
3. `python3 2_train_models.py`

## Running for local development

### Frontend

**MacOS**

```bash
brew install hugo;
git clone git@github.com:brunoamaral/gregory.git;
cd gregory/hugo
hugo server 
```

### Backend

Edit the env.example file to fit your configuration and rename to .env

```bash
sudo docker compose up -d
python3 -m venv env
source env/bin/activate
pip install -r requirements.txt
./build.py
hugo server
```



## Thank you to

- @[Antoniolopes](https://github.com/antoniolopes) for helping with the Machine Learning script.
- @[Chbm](https://github.com/chbm) for help in keeping the code secure.
- @[Jneves](https://github.com/jneves) for help with the build script
- @[Malduarte](https://github.com/malduarte) for help with the migration from sqlite to postgres.
- @[Melo](https://github.com/melo) for showing me [Hugo](https://github.com/gohugoio/hugo)
- @[Nurv](https://github.com/nurv) for the suggestion in using Spacy.io
- @[Rcarmo](https://github.com/rcarmo) for showing me [Node-RED](https://github.com/node-red/node-red)

And the **Lobsters** at [One Over Zero](https://github.com/oneoverzero)
