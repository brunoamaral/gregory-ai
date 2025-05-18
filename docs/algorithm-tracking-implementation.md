# Algorithm Tracking Feature Implementation

This document outlines the changes made to add ML algorithm tracking capabilities to the Gregory AI project.

## 1. Changes Made

### 1.1 Modified PredictionRunLog Model (models.py)

Added an `algorithm` field to the PredictionRunLog model with the following choices:
- pubmed_bert (PubMed BERT)
- lgbm_tfidf (LGBM TF-IDF)
- lstm (LSTM)
- unknown (Unknown) - default value

The field has a default value of "unknown" to handle existing records.

### 1.2 Updated Admin Interface (admin.py)

- Added the algorithm field to the list display
- Added algorithm to the list filter
- Updated the fieldset to include algorithm in the Run Information group
- Updated the CSV export functionality to include the algorithm field

### 1.3 Created Migration Files

Created two migration files:
1. `0039_add_algorithm_field.py`: Adds the algorithm field to the PredictionRunLog model
2. `0040_add_algorithm_field_and_default.py`: Sets the default value "unknown" for all existing records

## 2. Apply Changes

To apply these changes in your environment, follow these steps:

### 2.1 Docker Environment (Recommended)

```bash
# Run the migration
docker exec admin python manage.py migrate gregory

# Check if the migration was successful
docker exec admin python manage.py showmigrations gregory
```

### 2.2 Local Development Environment

```bash
cd /path/to/gregory-ai/django
python manage.py migrate gregory

# Or if using UV (as seen in the commands)
cd /path/to/gregory-ai/django
uv run python manage.py migrate gregory
```

## 3. Verification

After applying the changes, verify that:
1. The admin interface shows the algorithm column in the PredictionRunLog list
2. You can filter records by algorithm type
3. When creating a new PredictionRunLog record, you can select the algorithm
4. All existing records have "unknown" as their algorithm value

## 4. Usage in Code

When creating new PredictionRunLog entries in code, you can now specify the algorithm:

```python
PredictionRunLog.objects.create(
    team=team,
    subject=subject,
    model_version='1.0.0',
    algorithm='pubmed_bert',  # New field
    run_type='train',
    # ...other fields
)
```
