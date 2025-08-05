# ML Consensus Enhancement for Article Relevance

## Overview

This enhancement adds granular control over how ML models determine article relevance. Instead of requiring just one ML model to predict relevance, administrators can now configure subjects to require different levels of consensus among the 3 ML models (BERT, LGBM, LSTM).

## New Features

### 1. ML Consensus Types

Each subject now has a `ml_consensus_type` field with three options:

- **Any Model (default)**: Article is relevant if at least 1 ML model predicts relevance
- **Majority Vote**: Article is relevant if at least 2 out of 3 ML models agree
- **Unanimous**: Article is relevant if all 3 ML models predict relevance

### 2. Enhanced API Endpoints

#### Existing `/articles/?relevant=true` Endpoint
Now respects the subject-specific ML consensus settings when filtering articles.

**Example API Calls:**
```bash
# Get all relevant articles (manual + ML with consensus)
GET /api/articles/?relevant=true

# Get relevant articles for specific team and subject
GET /api/articles/?relevant=true&team_id=1&subject_id=4

# Get relevant articles from last 30 days
GET /api/articles/?relevant=true&last_days=30

# Get counts only (no article data)
GET /api/articles/?relevant=true&page_size=0
```

#### New `/articles/relevance_counts/` Endpoint
Provides detailed breakdown of article relevance by identification method.

**Example API Call:**
```bash
GET /api/articles/relevance_counts/?team_id=1&subject_id=4
```

**Example Response:**
```json
{
    "manual_relevant": 45,
    "ml_relevant": 67,
    "both_relevant": 12,
    "total_unique_relevant": 100,
    "breakdown": {
        "manual_only": 33,
        "ml_only": 55,
        "both": 12
    }
}
```

### 3. Database Changes

#### New Field: `Subject.ml_consensus_type`
- **Type**: CharField with choices
- **Default**: 'any'
- **Options**: 'any', 'majority', 'all'
- **Help Text**: "How ML models should agree for an article to be considered relevant"

## Configuration Guide

### Setting ML Consensus for Subjects

1. **Django Admin Interface**:
   - Navigate to Subjects in the admin
   - Edit a subject
   - Set the "ML consensus type" field to desired option
   - Save changes

2. **API Configuration** (if implemented):
   ```bash
   PATCH /api/subjects/4/
   {
       "ml_consensus_type": "majority"
   }
   ```

### Recommended Settings

- **High Precision Needed**: Use "all" (unanimous) - fewer false positives
- **Balanced Approach**: Use "majority" - good balance of precision and recall
- **High Recall Needed**: Use "any" - captures more potentially relevant articles

## Impact on Existing Functionality

### What Changes
- Article relevance filtering now respects subject-specific consensus settings
- More granular control over ML prediction sensitivity

### What Stays the Same
- Manual relevance markings work exactly as before
- Existing API endpoints maintain backward compatibility
- Newsletter and admin email logic automatically adapts

## Implementation Details

### New Model Methods

#### `Articles.is_ml_relevant_for_subject(subject)`
Checks if an article meets the ML consensus criteria for a specific subject.

#### `Articles.is_ml_relevant_any_subject()`
Checks if an article meets ML consensus criteria for any of its associated subjects.

### Filter Logic Update
The `filter_relevant` method now:
1. Gets manually relevant articles (unchanged)
2. Gets ML-relevant articles using new consensus logic
3. Combines both using OR logic

## Migration

A Django migration `0025_add_ml_consensus_type_to_subject.py` adds the new field with a default value of 'any', ensuring no existing functionality is disrupted.

## Testing the Feature

### Test Different Consensus Levels
1. Create test subjects with different consensus types
2. Ensure articles have predictions from multiple ML models
3. Verify filtering behavior matches expected consensus rules

### Example Test Scenarios
```python
# Article with BERT=relevant, LGBM=not_relevant, LSTM=relevant
# Should be included for subjects with consensus_type='any' or 'majority'
# Should be excluded for subjects with consensus_type='all'
```

## Monitoring and Analytics

Use the new `/articles/relevance_counts/` endpoint to:
- Monitor the effectiveness of different consensus settings
- Compare manual vs ML identification rates
- Identify subjects that may need consensus adjustment

## Future Enhancements

Potential future improvements:
- Weighted consensus (give more weight to certain models)
- Confidence threshold settings
- Per-model enable/disable options
- Historical consensus effectiveness tracking
