# Local development setup

> Audience: developers setting up GregoryAI on a local machine.

For server and production installation, see the project [README](../README.md).

---

## Requirements

- Python 3.12 (the project uses `pyproject.toml` with `uv`)
- Docker Desktop (or Docker Engine with the Compose plugin)
- 2 GB of available RAM minimum — 16 GB recommended if you plan to train BERT locally (see [05-training-models.md](05-training-models.md))
- Optional: Postmark account if you want to test outgoing emails locally

---

## Setup

### 1. Clone and configure the environment

```bash
git clone https://github.com/brunoamaral/gregory-ai.git
cd gregory-ai
cp example.env .env
```

Edit `.env` with your local settings. Minimum required variables:

```bash
SECRET_KEY='your-django-secret-key'
FERNET_SECRET_KEY='your-fernet-key'
POSTGRES_DB=gregory
POSTGRES_USER=gregory
POSTGRES_PASSWORD=gregory
DOMAIN_NAME=localhost
```

Optional — add Postmark credentials if you want to test email sending:

```bash
EMAIL_POSTMARK_API_KEY=your-postmark-server-token
EMAIL_POSTMARK_API_URL=https://api.postmarkapp.com/email
```

Optional — add ORCID credentials if you want to test author enrichment:

```bash
ORCID_CLIENT_ID=your-orcid-client-id
ORCID_CLIENT_SECRET=your-orcid-client-secret
```

### 2. Start the containers

```bash
docker compose up -d
```

This starts the Django application, PostgreSQL, and any supporting services defined in `docker-compose.yaml`. The container is named `gregory`.

### 3. Run migrations and create a superuser

```bash
docker exec gregory python manage.py migrate
docker exec gregory python manage.py createcachetable
docker exec gregory python manage.py createsuperuser
```

### 4. Set up a Python virtual environment (for running management commands locally)

```bash
cd django
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt
```

### 5. Verify the installation

Open the Django admin at `http://localhost/admin/` and log in with the superuser credentials you created.

---

## Seeding test data

To add a source and run the ingestion pipeline for the first time:

1. Log into the admin at `/admin/`.
2. Go to **Gregory > Sources** and add a PubMed RSS feed.
3. Run the pipeline to fetch articles:

```bash
docker exec gregory python manage.py pipeline
```

---

## Running tests

```bash
docker exec gregory python manage.py test
```

To run a specific test file:

```bash
docker exec gregory python manage.py test gregory.tests.test_filename
```

---

## Scheduled tasks

GregoryAI relies on cron jobs to keep the data fresh. For local development you can run commands manually. For production setup see the [README](../README.md#automated-tasks).

```bash
# Admin digest email (every 2 days in production)
docker exec gregory python manage.py send_admin_summary

# Weekly subscriber digest (every Tuesday in production)
docker exec gregory python manage.py send_weekly_summary

# Full ingestion and ML pipeline (every 12 hours in production)
docker exec gregory python manage.py pipeline
```
