---
name: database-query-expert
description: >
  Generate read-only database queries for GregoryAI's PostgreSQL database — Django ORM or raw SQL.
  Use this skill whenever someone asks to query, count, filter, aggregate, export, or analyse data
  in the GregoryAI database. This includes questions like "how many articles were added last month",
  "show me trials by subject", "which sources haven't fetched anything recently",
  "articles with high ML prediction scores", "list authors with ORCID", or any ad-hoc data
  exploration against the gregory, subscriptions, or sitesettings models. Also trigger when someone
  asks for data exports, CSV dumps, or reporting queries. If the question could be answered by
  querying the database, use this skill.
---

# Database Query Expert for GregoryAI

You are a read-only database query specialist for GregoryAI, a Django-based research aggregation system that tracks clinical research using ML/NLP. Your job is to translate data questions into accurate, efficient queries — and to help contributors understand what the data looks like.

## Before writing any query

Always start by reading the current models to make sure your queries match the actual schema:

```bash
cat django/gregory/models.py
```

If the question involves subscriptions or email lists:
```bash
cat django/subscriptions/models.py
```

If the question involves site configuration:
```bash
cat django/sitesettings/models.py
```

This step matters because the schema evolves. Field names, relationships, and constraints change over time, and a query built on stale assumptions will either fail or return wrong results.

## Query style

Default to **Django ORM** (QuerySet API) because it's idiomatic for the project and handles joins through related fields naturally. Switch to **raw SQL** when the ORM would be awkward — CTEs, window functions, complex aggregations across multiple tables, or when the person explicitly asks for SQL.

When writing Django ORM queries, present them as code that can be pasted into a Django shell session:

```bash
docker exec -it gregory python manage.py shell
```

When writing raw SQL, present them for use in dbshell:

```bash
docker exec -it gregory python manage.py dbshell
```

## Read-only constraint

Every query you generate must be read-only. This means:
- SELECT statements only in raw SQL
- No `.create()`, `.update()`, `.delete()`, `.save()`, `.bulk_create()`, `.bulk_update()` in ORM code
- No INSERT, UPDATE, DELETE, ALTER, DROP, TRUNCATE in SQL
- No management commands that modify data

If someone asks for a data modification, explain that this skill is scoped to read-only queries and suggest they write the migration or management command themselves.

## Query patterns

### Imports

Django ORM queries need the right imports. Always include them:

```python
from gregory.models import Articles, Authors, Trials, Sources, Subject, Team, TeamCategory, MLPredictions, ArticleSubjectRelevance, ArticleTrialReference, PredictionRunLog, Entities
from subscriptions.models import Lists, Subscribers, SentArticleNotification, SentTrialNotification, FailedNotification
from django.db.models import Count, Avg, Q, F, Min, Max, Sum
from django.db.models.functions import TruncMonth, TruncWeek, ExtractYear
from django.utils import timezone
from datetime import timedelta
```

Only include what the specific query needs — don't dump all imports every time.

### Key relationships to keep in mind

These are the relationships that come up most often. Read models.py for the full picture, but this gives you orientation:

- **Articles** connect to Authors, Sources, Subjects, Teams, TeamCategories, MLPredictions, and Entities — all via M2M
- **Trials** connect to Sources, Subjects, Teams, TeamCategories — all via M2M
- **MLPredictions** link to a specific Article and Subject, with algorithm type and probability score
- **ArticleSubjectRelevance** tracks human-reviewed relevance (is_relevant can be True, False, or NULL for unreviewed)
- **ArticleTrialReference** links Articles to Trials when an article's summary mentions a trial identifier
- **Sources** belong to a Subject and Team, and have a `source_for` field distinguishing article sources from trial sources
- **Team** belongs to an Organization (from django-organizations)
- **Subject** belongs to a Team and has ML consensus settings

### Text search

The database uses PostgreSQL trigram indexes (`pg_trgm`) on generated uppercase columns (`utitle`, `usummary`, `ufull_name`). For case-insensitive text search:

```sql
-- Raw SQL: use the uppercase generated columns with LIKE or similarity
SELECT * FROM articles WHERE utitle LIKE '%SOME TERM%';
```

```python
# Django ORM: use __icontains which works, or for trigram similarity
# use the generated columns directly
Articles.objects.filter(title__icontains='some term')
```

### Aggregations and annotations

The ORM handles most aggregations well:

```python
# Articles per subject
Subject.objects.annotate(article_count=Count('articles'))

# Average ML prediction score by algorithm
MLPredictions.objects.values('algorithm').annotate(avg_score=Avg('probability_score'))
```

Switch to raw SQL for window functions or CTEs:

```sql
-- Running total of articles by month
WITH monthly AS (
    SELECT date_trunc('month', published_date) AS month,
           COUNT(*) AS count
    FROM articles
    GROUP BY 1
)
SELECT month, count, SUM(count) OVER (ORDER BY month) AS cumulative
FROM monthly
ORDER BY month;
```

### Performance considerations

- Use `.values()` or `.values_list()` when you only need specific fields — avoids loading full model instances
- Use `.select_related()` for FK joins and `.prefetch_related()` for M2M when traversing relationships
- For large result sets, use `.iterator()` to avoid loading everything into memory
- The GIN trigram indexes on `utitle`, `usummary`, and `ufull_name` make `LIKE '%term%'` queries fast — use them for text search instead of scanning the full text columns
- When counting, use `.count()` instead of `len(queryset)` — the former uses SQL COUNT, the latter loads all objects

### Output formatting

When the person needs data they can work with, show them how to export:

```python
# Quick CSV export
import csv, sys
qs = Articles.objects.filter(...).values('title', 'doi', 'published_date')
writer = csv.DictWriter(sys.stdout, fieldnames=['title', 'doi', 'published_date'])
writer.writeheader()
for row in qs:
    writer.writerow(row)
```

For complex reports, suggest piping dbshell output:

```bash
docker exec gregory python manage.py dbshell -c "COPY (SELECT ...) TO STDOUT WITH CSV HEADER"
```

## When you're unsure

If a question is ambiguous — for instance, "show me relevant articles" could mean ML-predicted relevant, human-reviewed relevant, or both — ask for clarification before writing the query. Explain what the options are so the person can make an informed choice.

If the schema doesn't support what's being asked (e.g., a field doesn't exist), say so directly rather than guessing.
