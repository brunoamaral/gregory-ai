# Weekly Summary Email - ML Consensus + Threshold Integration

## Overview

The weekly summary email command has been updated to use the new **dual-filter approach**: ML consensus combined with probability thresholds. This ensures both quality (high confidence) and customizable sensitivity (consensus requirements) for article selection.

## Dual Filter Logic

### 1. Probability Threshold
Each ML prediction must have a `probability_score >= threshold` to be considered.

### 2. Consensus Requirements  
Among predictions above the threshold, the consensus rules apply:
- **Any**: ≥1 model above threshold
- **Majority**: ≥2 models above threshold  
- **All**: 3 models above threshold

### Example Scenario
Article with ML predictions:
- BERT: 0.92 (relevant) ✓ above 0.8 threshold
- LGBM: 0.75 (relevant) ✗ below 0.8 threshold
- LSTM: 0.88 (relevant) ✓ above 0.8 threshold

**Result**: 2 models above threshold
- `any` consensus → **INCLUDED**
- `majority` consensus → **INCLUDED**  
- `all` consensus → **EXCLUDED** (need 3 models)

## Changes Made

### 1. Replaced Threshold Logic with Consensus Logic

**Before:**
```python
# Articles with ML prediction scores above threshold
ml_predicted = Articles.objects.filter(
    Exists(ml_pred_subquery)  # probability_score >= threshold
).distinct()
```

**After:**
```python
# Articles that meet ML consensus criteria for any subject
ml_relevant_articles = []
for article in subject_articles:
    if article.is_ml_relevant_any_subject():
        ml_relevant_articles.append(article.article_id)

ml_predicted = Articles.objects.filter(pk__in=ml_relevant_articles)
```

### 2. Enhanced Debug Output

The debug output now shows:
- Which consensus type each subject uses (`any`, `majority`, `all`)
- How many models predicted relevant for each article/subject pair
- Whether each article is included or excluded based on consensus

**Example Debug Output:**
```
ML consensus evaluation for recent articles:
  Article 123: New treatment for multiple sclerosis...
    - Subject: Multiple Sclerosis (consensus: majority)
      * 2/3 models predict relevant → INCLUDED
    - Subject: Neurology (consensus: all)
      * 2/3 models predict relevant → EXCLUDED
```

### 3. Updated Article Prioritization

**Before:** Ordered by highest ML prediction score
**After:** Ordered by priority system:
1. Manually relevant articles (1000 points)
2. ML consensus strength (100 points per agreeing model)
3. Discovery date (tie-breaker)

### 4. Deprecated Threshold Parameter

The `--threshold` parameter is now deprecated but maintained for backward compatibility:
```bash
# Old usage (still works but deprecated)
python manage.py send_weekly_summary --threshold 0.8

# New usage (recommended)
python manage.py send_weekly_summary
```

## Command Usage

### Basic Usage
```bash
python manage.py send_weekly_summary
```

### With Debug Output
```bash
python manage.py send_weekly_summary --debug
```

### Dry Run (Testing)
```bash
python manage.py send_weekly_summary --dry-run --debug
```

### All Articles Mode (Bypass Filtering)
```bash
python manage.py send_weekly_summary --all-articles
```

## Configuration

### Subject-Level Consensus Settings

Each subject can be configured with different consensus requirements:

1. **Any Model** (`ml_consensus_type = 'any'`):
   - Article included if ≥1 ML model predicts relevant
   - Best for high recall (capture more articles)

2. **Majority Vote** (`ml_consensus_type = 'majority'`):
   - Article included if ≥2 ML models predict relevant
   - Balanced approach (default recommendation)

3. **Unanimous** (`ml_consensus_type = 'all'`):
   - Article included if all 3 ML models predict relevant
   - Best for high precision (fewer false positives)

### Setting Consensus in Django Admin

1. Navigate to **Subjects** in Django admin
2. Edit the desired subject
3. Set **ML consensus type** field
4. Save changes

## Impact on Email Content

### Article Selection
- Articles are selected based on consensus rules per subject
- Manual relevance markings always override ML predictions
- Articles belonging to multiple subjects use the most permissive rule

### Email Ordering
- Manually relevant articles appear first
- ML-relevant articles ordered by consensus strength
- Most recent articles appear first within each category

### Content Consistency
- Email content now matches API `/articles/?relevant=true` results
- No discrepancy between web interface and email digests

## Monitoring and Troubleshooting

### Debug Mode Benefits
- Shows exact consensus evaluation for sample articles
- Displays priority scoring for article ordering
- Reveals which subjects have `auto_predict=True`
- Identifies articles missing ML predictions

### Common Issues

1. **No Articles in Email**: Check if subjects have `auto_predict=True` and appropriate consensus settings
2. **Too Many Articles**: Consider stricter consensus (`majority` or `all`)
3. **Too Few Articles**: Consider looser consensus (`any`)

### Example Debug Session
```bash
python manage.py send_weekly_summary --debug --dry-run

# Output shows:
# - How many articles found per subject
# - Consensus evaluation for sample articles  
# - Final article counts and priorities
# - What would be sent to each subscriber
```

## Migration Path

### For Administrators
1. Review current subject consensus settings in Django admin
2. Test with `--dry-run --debug` to see impact
3. Adjust consensus settings as needed
4. Remove any custom threshold values from scripts

### For Developers
1. Update any custom email logic to use `article.is_ml_relevant_any_subject()`
2. Remove references to probability score thresholds
3. Use new priority-based ordering system

## Future Enhancements

Potential improvements:
- Per-subscriber consensus preferences
- Dynamic consensus based on article volume
- Historical performance tracking per consensus type
- A/B testing different consensus strategies
