[TOC]

# GregoryAI

Gregory is an AI system that uses Machine Learning and Natural Language Processing to track
clinical research and identify papers which improves the wellbeing of patients.

Sources for research can be added by RSS feed or manually.

The output can be seen in a static site, using `build.py` or via the api provided by the Django Rest Framework.

The docker compose file also includes a Metabase container which is used to build dashboards and manage notifications.

Sources can also be added to monitor Clinical Trials, in which case Gregory can notify a list of email subscribers.


## Features

1. Machine Learning to identify relevant research
2. Configure RSS feeds to gather search results from PubMed and other websites
3. Configure searches on any public website
4. Integration with Postmark for transactional emails
5. Automatic emails to the admin team with results in the last 48hours
6. Subscriber management
7. Configure email lists for different stakeholders
8. Public and Private API to integrate with other software solutions and websites
9. Configure categories to organize search results based on keywords in title
10. Configure different “subjects” to have keep different research areas segmented
11. Identify authors and their ORCID
12. Generate RSS feeds for articles by author

### Current Use Case for Multiple Sclerosis

<https://gregory-ms.com>

#### Rest API: <https://api.gregory-ms.com>

## Codex Automation

Issues labeled `codex` are automatically assigned to the **openai-codex** user.
The workflow then invokes the Codex GitHub App, which proposes a pull request
with changes that address the issue.

## Running in Production

### Server Requirements

- [ ] [Docker](https://www.docker.com/) and the [Docker Compose plugin](https://docs.docker.com/compose/) with 2GB of swap memory to be able to build the Machine Learning models. ([Adding swap for Ubuntu](https://www.digitalocean.com/community/tutorials/how-to-add-swap-space-on-ubuntu-20-04))
- [ ] [Postmark](https://postmarkapp.com/) account (optional, for transactional emails)

### Installing Gregory

#### 1. Clone and Install
1. Clone the repository:
	```bash
	git clone <repository_url>
	cd <repository_directory>
	docker compose up -d
	docker exec gregory python manage.py makemigrations
	docker exec gregory python manage.py migrate
	```
#### 2. Setup DNS for `api.domain.etc`

1. Log in to your DNS provider.
2. Add a new A record for `api.domain.etc` pointing to your server's IP address.

#### 3. Configure Postmark (optional)

GregoryAI uses [Postmark](https://postmarkapp.com/) for transactional emails
(newsletters, admin digests, clinical trial notifications).

The preferred approach is to configure Postmark credentials **per-team** in
the Django admin (Team → `postmark_api_token` / `postmark_api_url`). You can
also set global fallback values via environment variables — see step 5.1.

1. Create a Postmark account and server at <https://postmarkapp.com/>.
2. Copy your Server API Token.
3. Set the token either per-team in Django admin or in your `.env` file.

#### 4. Get ORCID API Keys and Add to `.env`
1. Log in to your ORCID account.
2. Navigate to `Developer Tools` and create an API client.
3. Copy the client ID and client secret.
4. Add the following to your `.env` file:
	```env
	ORCID_ClientID=your_orcid_client_id
	ORCID_ClientSecret=your_orcid_client_secret
	```

##### 4.1 Make sure your .env file is complete
```bash
DOMAIN_NAME=DOMAIN.COM

# --- PostgreSQL ---
POSTGRES_DB=
POSTGRES_PASSWORD=
POSTGRES_USER=

# --- Django ---
SECRET_KEY='' # Generate a unique key — https://docs.djangoproject.com/en/4.0/ref/settings/#secret-key
GREGORY_DIR=

# Encryption key for sensitive DB fields.
# Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
FERNET_SECRET_KEY=

# --- Email / SMTP ---
# These variables are only needed if you plan to send notification emails.
EMAIL_DOMAIN=
EMAIL_HOST=
EMAIL_HOST_PASSWORD=
EMAIL_HOST_USER=
EMAIL_PORT=587
EMAIL_USE_TLS='True'

# Postmark (fallback; per-team config in Django admin is preferred)
EMAIL_POSTMARK_API_KEY=
EMAIL_POSTMARK_API_URL=

# --- ORCID API ---
ORCID_ClientID=
ORCID_ClientSecret=
```

#### 5. Configure Server

##### 5.1. Nginx
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

##### 5.2. Certbot
1. Install Certbot:
	```bash
	sudo apt-get install certbot python3-certbot-nginx
	```
2. Obtain and install SSL certificate:
	```bash
	sudo certbot --nginx -d domain.etc -d www.domain.etc
	```

##### 5.3. Firewall
1. Allow necessary ports:
	```bash
	sudo ufw allow 'Nginx Full'
	sudo ufw enable
	```

#### 6. Configure Gregory

##### 6.1. Create a Site
1. Log in to the Gregory dashboard.
2. Navigate to `Sites` and click `Create Site`.

##### 6.2. Create a Team
1. Navigate to `Teams` and click `Create Team`.

##### 6.3. Add a User to the Team
1. Navigate to `Teams`, select the team, and click `Add User`.
2. Enter the user's email and assign a role.

##### 6.4. Add a Source, such as PubMed

1. Navigate to `Sources` and click `Add Source`.
2. Select `RSS` method and provide the necessary configuration.

#### 7. Add cron jobs to run the pipeline and send emails

```cron
# Every 2 days at 8:00
0 8 */2 * * /usr/bin/docker exec gregory python manage.py send_admin_summary

# Every Tuesday at 8:05
5 8 * * 2 docker exec gregory python manage.py send_weekly_summary

# Every 12 hours, at minute 25
25 */12 * * * /usr/bin/flock -n /tmp/pipeline /usr/bin/docker exec gregory python manage.py pipeline
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
*/3 * * * * /usr/bin/docker exec -t gregory python manage.py runcrons
*/5 * * * * /usr/bin/flock -n /tmp/get_takeaways /usr/bin/docker exec gregory python manage.py get_takeaways
```

## How everything fits together

### Django

Most of the logic is inside Django, the **gregory** container provides the [Django Rest Framework](https://www.django-rest-framework.org/), manages subscriptions, and sends emails.

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

### Postmark

Emails are sent from the `gregory` container using [Postmark](https://postmarkapp.com/).

The preferred approach is to configure Postmark credentials **per-team** in the
Django admin (Team → `postmark_api_token` / `postmark_api_url`). This allows
different teams to use separate Postmark servers.

Global fallback environment variables can be set in `.env`:

```bash
EMAIL_USE_TLS=true
EMAIL_POSTMARK_API_KEY='YOUR SERVER API TOKEN'
EMAIL_POSTMARK_API_URL='https://api.postmarkapp.com/email'
EMAIL_DOMAIN='YOURDOMAIN'
```

As an alternative, you can configure Django to use any other SMTP email server
via the `EMAIL_HOST*` variables.

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

### Training Models with the Django Management Command

Gregory AI includes a powerful Django management command for training ML models, with support for different algorithms, verbosity levels, and more.

#### Basic Usage

```bash
# Train all algorithms for a specific team and subject
python manage.py train_models --team research --subject oncology

# Train only BERT for all subjects in the clinical team
python manage.py train_models --team clinical --algo pubmed_bert

# Train all models for all teams with verbose output
python manage.py train_models --all-teams --verbose 3

# Run with pseudo-labeling and custom threshold
python manage.py train_models --team research --subject cardiology --pseudo-label --prob-threshold 0.75
```

#### Command Options

| Option | Description |
|--------|-------------|
| `--team TEAM_SLUG` | Team slug to train models for |
| `--all-teams` | Train models for all teams |
| `--subject SUBJECT_SLUG` | Subject slug within the chosen team (if not specified, train for all subjects) |
| `--all-articles` | Use all labeled articles (ignores 90-day window) |
| `--lookback-days DAYS` | Override the default 90-day window for article discovery |
| `--algo ALGORITHMS` | Comma-separated list of algorithms to train (`pubmed_bert`, `lgbm_tfidf`, `lstm`) |
| `--prob-threshold VALUE` | Probability threshold for classification (default: 0.8) |
| `--version TAG` | Manual version tag (default: auto-generated YYYYMMDD) |
| `--pseudo-label` | Run BERT self-training loop before final training |
| `--verbose LEVEL` | Verbosity level (0-3, where 0=quiet, 3=summary) |

#### Development Use

When developing or testing, consider:

```bash
# Use minimal data for quick testing
python manage.py train_models --team test-team --subject test-subject --lookback-days 30 --algo pubmed_bert --verbose 3

# Skip pseudo-labeling in development to speed up training
python manage.py train_models --team research --subject oncology --algo pubmed_bert --verbose 2
```

#### Production Use

In production environments:

```bash
# Train all algorithms with default settings
python manage.py train_models --team production --subject main

# Use custom version tag for tracking specific runs
python manage.py train_models --team clinical --subject cardiology --version v1.2.3_special

# Use pseudo-labeling for improved performance with unlabeled data
python manage.py train_models --team research --all-articles --pseudo-label
```

## Running for local development

Edit the env.example file to fit your configuration and rename to .env

```bash
docker compose up -d
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
