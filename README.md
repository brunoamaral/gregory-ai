[TOC]

# GregoryAI

Gregory is an AI system that uses Machine Learning and Natural Language Processing to track
clinical research and identify papers which improves the wellbeing of patients.

Sources for research can be added by RSS feed or manually.

The output can be seen in a static site, using `build.py` or via the api provided by the Django Rest Framework.

The docker compose file also includes a Metabase container which is used to build dashboards and manage notifications.

Sources can also be added to monitor Clinical Trials, in which case Gregory can notify a list of email subscribers.

For other integrations, the Django app provides RSS feeds with a live update of relevant research and newly posted clinical trials.

## Features

1. Machine Learning to identify relevant research
2. Configure RSS feeds to gather search results from PubMed and other websites
3. Configure searches on any public website
4. Integration with mailgun.com to send emails
5. Automatic emails to the admin team with results in the last 48hours
6. Subscriber management
7. Configure email lists for different stakeholders
8. Public and Private API to integrate with other software solutions and websites
9. Configure categories to organize search results based on keywords in title
10. Configure different “subjects” to have keep different research areas segmented
11. Identify authors and their ORCID
12. Generate different RSS feeds

### Current Use Case for Multiple Sclerosis

<https://gregory-ms.com>

#### Rest API: <https://api.gregory-ms.com>

## Running in Production

### Server Requirements

- [ ] [Docker](https://www.docker.com/) and [docker-compose](https://docs.docker.com/compose/) with 2GB of swap memory to be able to build the MachineLearning Models. ([Adding swap for Ubuntu](https://www.digitalocean.com/community/tutorials/how-to-add-swap-space-on-ubuntu-20-04))
- [ ] [Mailgun](https://www.mailgun.com/) (optional)

### Installing Gregory

#### 1. Clone and Install
1. Clone the repository:
	```bash
	git clone <repository_url>
	cd <repository_directory>
	docker compose up -d 
	docker exec admin python manage.py makemigrations
	docker exec admin python manage.py migrate
	```
#### 2. Setup DNS for `api.domain.etc`

1. Log in to your DNS provider.
2. Add a new A record for `api.domain.etc` pointing to your server's IP address.

#### 3. Setup DNS for Mailgun `mg.domain.etc`
1. Log in to your DNS provider.
2. Add the following DNS records provided by Mailgun for `mg.domain.etc`:
	- TXT record
	- MX record
	- CNAME record

#### 4. Get Mailgun API Keys and Add to `.env`
1. Log in to your Mailgun account.
2. Navigate to `API Keys`.
3. Copy the private API key.
4. Add the key to your `.env` file.

#### 5. Get ORCID API Keys and Add to `.env`
1. Log in to your ORCID account.
2. Navigate to `Developer Tools` and create an API client.
3. Copy the client ID and client secret.
4. Add the following to your `.env` file:
	```env
	ORCID_CLIENT_ID=your_orcid_client_id
	ORCID_CLIENT_SECRET=your_orcid_client_secret
	```

##### 5.1 make sure your .env file is complete
```bash
DOMAIN_NAME=DOMAIN.COM
# Set this to the subdomain you configured with Mailgun. Example: mg.domain.com
EMAIL_DOMAIN=
# The SMTP server and credentials you are using. For example: smtp.eu.mailgun.org
# These variables are only needed if you plan to send notification emails
EMAIL_HOST=
EMAIL_HOST_PASSWORD=
EMAIL_HOST_PASSWORD=
EMAIL_HOST_USER=
# We use Mailgun by default on the newsletters, input your API key here
EMAIL_MAILGUN_API_URL=
EMAIL_PORT=587
EMAIL_USE_TLS='True'
# Where you cloned the repository
GREGORY_DIR=
# Set your postgres DB and credentials
POSTGRES_DB=
POSTGRES_PASSWORD=
POSTGRES_USER=
SECRET_KEY='Yeah well, you know, that is just, like, your DJANGO SECRET_KEY, man' # you should set this manually https://docs.djangoproject.com/en/4.0/ref/settings/#secret-key
```

#### 6. Configure Server

##### 6.1. Nginx
1. Install Nginx:
	```bash
	sudo apt-get update
	sudo apt-get install nginx
	```
2. Configure Nginx for your application:
	```bash
	sudo nano /etc/nginx/sites-available/default
	```
	- Add your server block configuration.
3. Test and restart Nginx:
	```bash
	sudo nginx -t
	sudo systemctl restart nginx
	```

##### 6.2. Certbot
1. Install Certbot:
	```bash
	sudo apt-get install certbot python3-certbot-nginx
	```
2. Obtain and install SSL certificate:
	```bash
	sudo certbot --nginx -d domain.etc -d www.domain.etc
	```

##### 6.3. Firewall
1. Allow necessary ports:
	```bash
	sudo ufw allow 'Nginx Full'
	sudo ufw enable
	```

#### 7. Configure Gregory

##### 7.1. Create a Site
1. Log in to the Gregory dashboard.
2. Navigate to `Sites` and click `Create Site`.

##### 7.2. Create a Team
1. Navigate to `Teams` and click `Create Team`.

##### 7.3. Add a User to the Team
1. Navigate to `Teams`, select the team, and click `Add User`.
2. Enter the user's email and assign a role.

##### 7.4. Add a Source, such as PubMed

1. Navigate to `Sources` and click `Add Source`.
2. Select `RSS` method and provide the necessary configuration.

#### 8. Add cronjobs to run the pipeline and send emails

```cron
# Every 2 days at 8:00
0 8 */2 * * /usr/bin/docker exec admin python manage.py send_admin_summary

# Every Tuesday at 8:05
5 8 * * 2 docker exec admin python manage.py send_weekly_summary

# Run ML predictions on new articles daily at 7:00
0 7 * * * /usr/bin/docker exec admin python manage.py predict_articles --all-teams

# Every 12 hours, at minute 25, run the pipeline
25 */12 * * * /usr/bin/flock -n /tmp/pipeline /usr/bin/docker exec admin ./manage.py pipeline
```



1. **Execute** `python3 setup.py`.

The script checks if you have all the requirements and run to help you setup the containers.

Once finished, login at <https://api.DOMAIN.TLD/admin> or wherever your reverse proxy is listening on.

4. Go to the admin dashboard and change the example.com site to match your domain
5. Go to custom settings and set the Site and Title fields.
6. **Configure** your RSS Sources in the Django admin page.
7. **Setup** database maintenance tasks.
Gregory needs to run a series of tasks to fetch missing information before applying the machine learning algorithm. For that, we are using [Django-Con](https://github.com/Tivix/django-cron). Add the following to your crontab:

```cron
# Run django cron jobs every 3 minutes
*/3 * * * * /usr/bin/docker exec -t admin ./manage.py runcrons

# Get article takeaways every 5 minutes
*/5 * * * * /usr/bin/flock -n /tmp/get_takeaways /usr/bin/docker exec admin ./manage.py get_takeaways

# Run ML predictions on new articles every 6 hours
0 */6 * * * /usr/bin/docker exec admin python manage.py predict_articles --all-teams --verbose 1

# Train ML models monthly (first day of each month)
0 3 1 * * /usr/bin/docker exec admin python manage.py train_models --all-teams
```

## How everything fits together

### Django

Most of the logic is inside Django, the **admin** container provides the [Django Rest Framework](https://www.django-rest-framework.org/), manages subscriptions, and sends emails.

The following subscriptions are available:

**Admin digest**

This is sent every 48 hours with the latest articles and their machine learning prediction. Allows the admin access to an Edit link where the article can be edited and tagged as relevant.

**Weekly digest**

This is sent every Tuesday, it lists the relevant articles discovered in the last week.

**Clinical Trials**

This is sent every 12 hours if a new clinical trial was posted.

The title of the email footer for these emails needs to be set in the Custom Settings section of the admin backoffice.

Django also allows you to add new sources from where to fetch articles. Take a look at `/admin/gregory/sources/ `

![image-20220619195841565](images/image-20220619195841565.png)

### Mailgun

Emails are sent from the `admin` container using Mailgun.

To enable them, you will need a mailgun account, or you can replace them with another way to send emails.

You need to configure the relevant variables for this to work:

```bash
EMAIL_USE_TLS=true
EMAIL_MAILGUN_API='YOUR API KEY'
EMAIL_DOMAIN='YOURDOMAIN'
EMAIL_MAILGUN_API_URL="https://api.eu.mailgun.net/v3/YOURDOMAIN/messages"
```

As an alternative, you can configure Django to use any other email server.

### RSS feeds and API

Gregory has the concept of 'subject'. In this case, Multiple Sclerosis is the only subject configured. A Subject is a group of Sources and their respective articles. There are also categories that can be created. A category is a group of articles whose title matches at least one keyword in list for that category. Categories can include articles across subjects.

There are options to filter lists of articles by their category or subject in the format `articles/category/<category>` and `articles/subject/<subject>` where <category> and <subject> is the lowercase name with spaces replaced by dashes.

#### Available RSS feeds

1. Latest articles, `/feed/latest/articles/`
2. Latest articles by subject, `/feed/articles/subject/<subject>/`
3. Latest articles by category, `/feed/articles/category/<category>/`
4. Latest clinical trials, `/feed/latest/trials/`
5. Latest relevant articles by Machine Learning, `/feed/machine-learning/`
6. Twitter feed, `/feed/twitter/`. This includes all relevant articles by manual selection and machine learning prediction. It's read by [Zapier](https://zapier.com/) so that we can post on twitter automatically.

## How to update the Machine Learning Algorithms

It's useful to re-train the machine learning models once you have a good number of articles flagged as relevant.

### Training Models with the Django Management Command

Gregory AI now includes a powerful Django management command for training ML models, with support for different algorithms, verbosity levels, and more.

#### Basic Usage

```bash
# Train all algorithms for a specific team and subject
python manage.py train_models --team research --subject oncology

# Train all models for the 'clinical' team with maximum verbosity
python manage.py train_models --team clinical --verbose 3

# Train only LGBM model for a specific team and subject
python manage.py train_models --team research --subject cardiology --algo lgbm_tfidf
```

#### Command Options

| Option | Description |
|--------|-------------|
| `--team TEAM_SLUG` | Team slug to train models for |
| `--all-teams` | Train models for all teams |
| `--subject SUBJECT_SLUG` | Subject slug within the chosen team (if not specified, train for all subjects) |
| `--all-articles` | Use all labeled articles (ignores 90-day window) |
| `--lookback-days DAYS` | Override the default 90-day window for article discovery |
| `--algo ALGORITHMS` | Comma-separated list of algorithms to train (pubmed_bert,lgbm_tfidf,lstm) |
| `--prob-threshold THRESHOLD` | Probability threshold for classification (default: 0.8) |
| `--version VERSION` | Manual version tag (default: auto-generated YYYYMMDD with optional _n suffix) |
| `--pseudo-label` | Run BERT self-training loop before final training |
| `--verbose LEVEL` | Verbosity level (0: quiet, 1: progress, 2: +warnings, 3: +summary) |

#### Production Use

In production, it's recommended to run the command on a scheduled basis (e.g., monthly) with the appropriate verbosity level:

```bash
# Example for production cron job
docker exec -it gregory-django python manage.py train_models --all-teams --verbose 1
```

#### Development Use

For development and testing, you may want to see detailed training information:

```bash
# Example for development
python manage.py train_models --team research --subject test --verbose 3
```

### Running Article Predictions with the Django Management Command

Once you have trained ML models, you can use the `predict_articles` command to automatically classify new articles with your trained models.

#### Basic Usage

```bash
# Run predictions for all algorithms on a specific team
python manage.py predict_articles --team research

# Run predictions only for LSTM model with verbose output
python manage.py predict_articles --team clinical --algo lstm --verbose 2

# Run predictions for all teams
python manage.py predict_articles --all-teams
```

#### Command Options

| Option | Description |
|--------|-------------|
| `--team TEAM_SLUG` | Team slug to run predictions for |
| `--all-teams` | Run predictions for all teams |
| `--subject SUBJECT_SLUG` | Subject slug within the chosen team (requires --team) |
| `--lookback-days DAYS` | Select articles discovered within last N days (default: 90) |
| `--algo ALGORITHMS` | Comma-separated list of algorithms to use (default: all available) |
| `--model-version VERSION` | Force a specific model version (default: latest available) |
| `--prob-threshold VALUE` | Probability threshold for classification (default: 0.8) |
| `--verbose LEVEL` | Verbosity level (0-3, where 3=detailed summary) |
| `--dry-run` | Run everything except database writes |

#### Production Use

For production environments, add the prediction command to your scheduled tasks:

```bash
# Example cron job to run predictions daily
0 2 * * * docker exec gregory-django python manage.py predict_articles --all-teams
```

#### Development Use

For testing and development:

```bash
# Test predictions without making database changes
python manage.py predict_articles --team research --subject test --dry-run --verbose 3

# Run with a specific model version
python manage.py predict_articles --team research --algo pubmed_bert --model-version 20250430_1
```

## Testing

### Running Tests

Gregory AI includes a comprehensive test suite. To run tests:

```bash
# Run all tests
cd django
python manage.py test

# Run specific test files or modules
python manage.py test gregory.tests.test_filename

# Run standalone test files (for tests that avoid Django dependencies)
cd django
python gregory/tests/test_train_models_standalone.py
```

## Running for local development

Edit the env.example file to fit your configuration and rename to .env

```bash
sudo docker-compose up -d
python3 -m venv env
source env/bin/activate
pip install -r requirements.txt
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
