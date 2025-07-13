# Database Performance Optimization: Uppercase Search Columns

## Overview

This implementation adds persisted uppercase columns (`utitle`, `usummary`) to the `articles` and `trials` tables to dramatically improve case-insensitive search performance.

## Problem

The original search implementation used Django's `icontains` lookup, which translates to PostgreSQL queries like:

```sql
WHERE UPPER(title) LIKE UPPER('%search_term%')
   OR UPPER(summary) LIKE UPPER('%search_term%')
```

Since PostgreSQL cannot use indexes when functions are applied to columns at query time, every search resulted in a full table scan, causing performance issues on large datasets.

## Solution

### 1. Django-Native Implementation

Used Django 5.2.3's `GeneratedField` with `db_persist=True` to create computed stored columns:

```python
# In models.py
from django.db.models import GeneratedField
from django.db.models.functions import Upper

class Articles(models.Model):
    # ...existing fields...
    utitle = GeneratedField(
        expression=Upper('title'),
        output_field=models.TextField(),
        db_persist=True
    )
    usummary = GeneratedField(
        expression=Upper('summary'),
        output_field=models.TextField(),
        db_persist=True
    )
    
    class Meta:
        indexes = [
            GinIndex(fields=['utitle'], opclasses=['gin_trgm_ops'], name='articles_utitle_gin_idx'),
            GinIndex(fields=['usummary'], opclasses=['gin_trgm_ops'], name='articles_usummary_gin_idx'),
        ]
```

### 2. PostgreSQL Extensions and Indexes

Enabled `pg_trgm` extension and created GIN indexes using Django's native approach:

**Migration 0019: Enable pg_trgm extension**
```python
from django.contrib.postgres.operations import TrigramExtension

class Migration(migrations.Migration):
    operations = [
        TrigramExtension(),
    ]
```

**Migration 0020: Create GIN indexes**
```python
from django.contrib.postgres.indexes import GinIndex

class Migration(migrations.Migration):
    operations = [
        migrations.AddIndex(
            model_name='articles',
            index=GinIndex(fields=['utitle'], opclasses=['gin_trgm_ops'], name='articles_utitle_gin_idx'),
        ),
        # ...additional indexes...
    ]
```

### 3. Application Code Updates

Updated search queries to use Django ORM with the new uppercase columns:

**Before:**
```python
queryset.filter(Q(title__icontains=search) | Q(summary__icontains=search))
```

**After:**
```python
upper_search = search.upper()
queryset.filter(Q(utitle__contains=upper_search) | Q(usummary__contains=upper_search))
```

## Performance Results

Performance testing shows dramatic improvements:

- **Old approach**: 56.177 ms execution time (Sequential Scan)
- **New approach**: 6.085 ms execution time (Bitmap Index Scan using GIN indexes)
- **Improvement**: ~9x faster
- **Index Usage**: Confirmed using `articles_utitle_gin_idx` and `articles_usummary_gin_idx`

## Files Modified

### Database Migrations
- `django/gregory/migrations/0018_add_uppercase_helper_columns_to_trials_articles.py` - Creates GeneratedField columns
- `django/gregory/migrations/0019_enable_pg_trgm_extension.py` - Enables pg_trgm extension
- `django/gregory/migrations/0020_add_gin_indexes_for_text_search.py` - Creates GIN indexes

### Models
- `django/gregory/models.py` - Added GeneratedField definitions and GinIndex configurations

### API Filters
- `django/api/filters.py` - Updated `ArticleFilter` and `TrialFilter` to use Django ORM with uppercase columns

### Management Commands
- `django/gregory/management/commands/rebuild_categories.py` - Updated to use Django-native ORM throughout

### Tests
- `django/gregory/tests/test_uppercase_search_columns.py` - Comprehensive test suite including GIN index verification

## Benefits

1. **9x Performance Improvement**: Searches went from 56ms to 6ms execution time
2. **Django-Native Implementation**: Uses GeneratedField and GinIndex - no raw SQL required
3. **Index Usage**: PostgreSQL uses GIN indexes with trigram operators for fast text search
4. **Scalability**: Performance remains consistent as dataset grows
5. **Backward Compatibility**: Original columns remain unchanged
6. **Automatic Updates**: GeneratedField columns update automatically when source data changes
7. **Maintainable**: Standard Django patterns throughout the codebase

## Technical Notes

### Why GIN Indexes?

- **Trigram Support**: GIN indexes with `gin_trgm_ops` are optimized for substring searches
- **Large Text Handling**: Unlike B-tree indexes, GIN indexes can handle very long text values
- **Optimal for LIKE Queries**: Perfect for `LIKE '%search%'` patterns

### Generated Columns vs. Regular Columns

We used PostgreSQL's `GENERATED ALWAYS AS ... STORED` columns because:
- Automatically maintained by the database
- Zero application overhead
- Always consistent with source data
- No need for triggers or application logic

### Django ORM Integration

The GeneratedField columns are defined in Django models and fully integrated with the ORM:

```python
# Direct access through Django ORM
from gregory.models import Articles
from django.db.models import Q

# Search using the optimized approach
results = Articles.objects.filter(
    Q(utitle__contains='COVID') | Q(usummary__contains='COVID')
)
```

### Migration Dependencies

Important: The `pg_trgm` extension must be enabled before creating GIN indexes:

1. Migration 0019: Enables `TrigramExtension` 
2. Migration 0020: Creates GIN indexes (depends on 0019)

## Usage Examples

### API Search
```bash
# Search for COVID-related articles
GET /api/articles/?search=covid

# Search in title only
GET /api/articles/?title=vaccine

# Search in summary only  
GET /api/articles/?summary=treatment
```

### Programmatic Usage
```python
from gregory.models import Articles
from django.db.models import Q

# Search using the Django ORM with optimized columns
results = Articles.objects.filter(
    Q(utitle__contains='COVID') | Q(usummary__contains='COVID')
)

# Can also combine with other filters
recent_covid_articles = Articles.objects.filter(
    Q(utitle__contains='COVID') | Q(usummary__contains='COVID')
).filter(discovery_date__gte='2024-01-01')
```

## Monitoring

To verify the indexes are being used:

```sql
EXPLAIN ANALYZE 
SELECT COUNT(*) FROM articles 
WHERE utitle LIKE '%COVID%' OR usummary LIKE '%COVID%';
```

Look for "Bitmap Index Scan" using the GIN indexes (`articles_utitle_gin_idx`, `articles_usummary_gin_idx`) rather than "Seq Scan".

## Future Considerations

1. **Index Maintenance**: GIN indexes require more maintenance overhead than B-tree indexes
2. **Storage Space**: Computed columns use additional storage (estimated 20-30% increase)
3. **Monitoring**: Consider adding monitoring for index usage and query performance
4. **Additional Languages**: The current implementation is optimized for English text
