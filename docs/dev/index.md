# Developer Guide

This guide provides information for developers working on the Gregory codebase.

## Project Structure

Gregory is organized into several components:

- **Django Backend** - Core application with API and data processing
- **Machine Learning** - ML models for article relevance prediction
- **RSS Processing** - Fetching and parsing RSS feeds
- **Email System** - Sending digests and notifications
- **Database** - PostgreSQL for data storage

## Local Development Setup

### Prerequisites

- Python 3.8+
- Docker and docker-compose
- PostgreSQL (optional, can use containerized version)

### Setup Steps

1. Clone the repository:
   ```bash
   git clone <repository_url>
   cd <repository_directory>
   ```

2. Create a virtual environment:
   ```bash
   python -m venv env
   source env/bin/activate  # On Windows: env\Scripts\activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Set up local environment:
   ```bash
   cp example.env .env
   # Edit .env with local configuration
   ```

5. Start dependencies with Docker:
   ```bash
   docker-compose up -d postgres
   ```

6. Run migrations:
   ```bash
   cd django
   python manage.py migrate
   ```

7. Create a superuser:
   ```bash
   python manage.py createsuperuser
   ```

8. Start the development server:
   ```bash
   python manage.py runserver
   ```

## Running Tests

Gregory includes a comprehensive test suite:

```bash
# Run all tests
cd django
python manage.py test

# Run specific test files
python manage.py test gregory.tests.test_filename

# Run with coverage
coverage run --source='.' manage.py test
coverage report
```

## Code Standards

- PEP 8 style guide for Python code
- Black for code formatting
- Flake8 for linting
- Type hints where appropriate
- Docstrings for all functions and classes

## Pull Request Process

1. Create a feature branch: `git checkout -b feature/your-feature-name`
2. Make your changes and add tests
3. Run the test suite to ensure everything passes
4. Update documentation if necessary
5. Submit a pull request to the main repository

## Common Development Tasks

### Adding a New API Endpoint

1. Create a new view in the appropriate file in `django/api/views.py`
2. Add serializer in `django/api/serializers.py` if needed
3. Add URL pattern in `django/api/urls.py`
4. Add tests in `django/api/tests/`
5. Update API documentation

### Adding a New Model

1. Define the model in `django/gregory/models.py`
2. Create migrations: `python manage.py makemigrations`
3. Add admin configuration in `django/gregory/admin.py`
4. Add serializer if needed
5. Add tests

### Working with ML Models

1. Machine learning code is in `django/gregory/ml/`
2. Use the `train_models` management command for training
3. Test models with `test_train_models_standalone.py`

## Troubleshooting

### Database Issues

If you encounter database errors:
1. Check PostgreSQL connection settings in `.env`
2. Ensure migrations are applied: `python manage.py migrate`
3. Check database logs: `docker logs gregory_postgres`

### Machine Learning Issues

1. Ensure required libraries are installed
2. Check for disk space if model training fails
3. Verify input data format
4. See detailed logs with `--verbose 3` flag

## Additional Resources

- [Django Documentation](https://docs.djangoproject.com/)
- [Django REST Framework](https://www.django-rest-framework.org/)
- [Scikit-learn Documentation](https://scikit-learn.org/stable/documentation.html)
- [PostgreSQL Documentation](https://www.postgresql.org/docs/)
