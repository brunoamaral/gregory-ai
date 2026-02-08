# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

GregoryAI is a Django-based research aggregation system that uses Machine Learning and Natural Language Processing to track clinical research and identify papers that improve patient wellbeing. The system fetches data from RSS feeds, API posts, and manual submissions.

## Architecture

### Core Structure
- **Django Backend**: Main application in `/django/` directory
- **PostgreSQL Database**: Data storage with main objects: Article, Author, Sources, Subject, Team, Trials
- **Machine Learning Pipeline**: Automated prediction of relevant articles using models described in `files_repo_PBL_nsbe` folder
- **Docker Deployment**: Containerized with `docker-compose.yaml`

### Key Django Apps
- `gregory`: Core models and ML functionality (`django/gregory/`)
- `api`: REST API endpoints (`django/api/`)
- `subscriptions`: Email newsletter management (`django/subscriptions/`)
- `sitesettings`: Site configuration (`django/sitesettings/`)
- `indexers`: Data ingestion from sources (`django/indexers/`)

## Common Development Commands

### Docker Operations
```bash
# Start the application
docker compose up -d

# Access Django container
docker exec -it gregory python manage.py <command>

# Run migrations
docker exec gregory python manage.py makemigrations
docker exec gregory python manage.py migrate
```

### Django Management
```bash
# From within Django directory or container
cd django/
python manage.py test                    # Run all tests
python manage.py test gregory.tests.test_filename  # Run specific test
python manage.py pipeline              # Run ML pipeline
python manage.py train_models --team research --subject oncology  # Train ML models
python manage.py send_weekly_summary    # Send weekly newsletter
python manage.py send_admin_summary     # Send admin digest
```

### Testing
- Tests located in `tests/` folders within Django apps
- Run standalone tests: `python gregory/tests/test_train_models_standalone.py`
- When running commands, assume the app is running as docker container called `gregory`

## Development Environment Setup

### Local Development
```bash
# Edit env.example and rename to .env
docker compose up -d
python3 -m venv env
source env/bin/activate
pip install -r requirements.txt  # Note: Use pyproject.toml for uv
```

### Environment Variables
Key variables needed in `.env`:
- `SECRET_KEY`: Django secret key
- `FERNET_SECRET_KEY`: Encryption key
- `POSTGRES_*`: Database credentials
- `EMAIL_*`: Email service configuration (Postmark)
- `ORCID_*`: ORCID API credentials

## Machine Learning Components

### Training Models
The system supports multiple ML algorithms:
- `pubmed_bert`: BERT model for PubMed articles
- `lgbm_tfidf`: LightGBM with TF-IDF features
- `lstm`: LSTM neural network

Train models using:
```bash
python manage.py train_models --team <team> --subject <subject> --algo <algorithm>
```

### Automated Tasks
Set up cron jobs for:
```cron
# Admin summary every 2 days
0 8 */2 * * docker exec gregory python manage.py send_admin_summary

# Weekly summary every Tuesday
5 8 * * 2 docker exec gregory python manage.py send_weekly_summary

# Pipeline every 12 hours
25 */12 * * * flock -n /tmp/pipeline docker exec gregory python manage.py pipeline
```

## API Features

### Available Endpoints
- Articles API with filtering by subject/category
- RSS feeds: `/feed/author/<str:orcid>/` for articles by a specific author
- JWT authentication support
- CSV export capabilities

### Key Models Reference
Always check `django/gregory/models.py` when:
- Creating new models
- Adding features
- Writing SQL queries

## Coding Standards

- Use tabs for indentation
- Modern, clean UI design
- Test coverage for new features
- Follow Django best practices