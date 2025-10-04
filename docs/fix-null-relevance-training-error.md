# Fix: Training Error with Null Relevance Values

## Problem

When running `train_models` command in production, the training was failing with the error:

```
Training failed for team-gregory/multiple-sclerosis/lstm: int() argument must be a string, a bytes-like object or a real number, not 'NoneType'
```

This error occurred for all three model types (LSTM, LGBM, and BERT).

## Root Cause

The `ArticleSubjectRelevance.is_relevant` field is a `BooleanField` with `null=True, blank=True, default=None`. This means it can have three states:
- `True` - Article is relevant (manually reviewed)
- `False` - Article is not relevant (manually reviewed)  
- `None` - Article has not been manually reviewed yet

The training pipeline was attempting to convert `is_relevant` to an integer with `int(relevance.is_relevant)` without checking if the value was `None`. When an unreviewed article (with `is_relevant=None`) was included in the dataset, the conversion failed.

## Solution

Fixed in two places:

### 1. Database Query Level (`django/gregory/utils/dataset.py` - `collect_articles` function)

Added filter to exclude articles where `is_relevant` is `None` at the database query level:

```python
# After window filter, require at least one relevance entry with a non-null is_relevant value
# This ensures we only get manually reviewed articles (is_relevant is True or False, not None)
queryset = queryset.filter(
    article_subject_relevances__subject=subject,
    article_subject_relevances__is_relevant__isnull=False
).distinct()
```

This is the most efficient approach as it filters at the SQL level.

### 2. Data Processing Level (`django/gregory/utils/dataset.py` - `build_dataset` function)

Added defensive check to skip articles with `is_relevant=None` during dataset building:

```python
# Skip articles where relevance has not been manually reviewed (is_relevant is None)
if relevance.is_relevant is None:
    continue
```

This provides a safety net in case articles with null relevance somehow make it through the query.

## Testing

Added two new test cases to `django/gregory/tests/test_dataset.py`:

1. `test_collect_articles_skips_null_relevance` - Verifies that the `collect_articles` function excludes articles with `is_relevant=None`
2. `test_build_dataset_handles_null_relevance` - Verifies that `build_dataset` handles null values gracefully without crashing

All tests pass successfully.

## Impact

- Training commands will now only use articles that have been manually reviewed (where `is_relevant` is explicitly `True` or `False`)
- Articles with `is_relevant=None` (awaiting manual review) are excluded from training data
- This is the correct behavior since only reviewed articles should be used for supervised learning
- No impact on pseudo-labeling or prediction functionality

## How to Deploy

1. Pull the latest changes from the repository
2. No database migration required (field schema unchanged)
3. Run the training command again:
   ```bash
   docker exec gregory python manage.py train_models --team team-gregory --lookback-days 1650
   ```

## Future Considerations

The training dataset now depends on having manually reviewed articles. To ensure successful training:

1. Regularly review article relevance through the admin interface
2. Monitor the count of labeled articles before training (visible in training output)
3. Ensure at least 2 examples per class (relevant/not relevant) for training to succeed
