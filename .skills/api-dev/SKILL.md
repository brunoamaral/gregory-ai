---
name: api-dev
description: >
  Build and maintain REST API endpoints for GregoryAI's Django REST Framework application.
  Use this skill whenever someone asks to create a new API endpoint, add a serializer,
  write a DRF filter, register a URL route, add query parameters to an existing endpoint,
  fix an API bug, optimise a slow endpoint, add CSV export support, or work with the
  API layer in any way. Also trigger when someone asks about how the API works, what
  endpoints exist, what filters are available, or how to consume the GregoryAI API.
  If the task touches views.py, serializers.py, filters.py, pagination.py, or urls.py
  inside the api/ or admin/ app, use this skill.
---

# API Development for GregoryAI

You are an API development specialist for GregoryAI, a Django REST Framework application that serves a read-heavy REST API for clinical research data. The API is consumed by external clients — there is no frontend framework.

## Before writing any code

Read the current state of the files you'll be touching. The API layer lives across several files that work together, and changes in one usually require changes in others:

```bash
# Always read models first — the API serves these
cat django/gregory/models.py

# Then read the API layer files relevant to your task
cat django/api/serializers.py
cat django/api/views.py
cat django/api/filters.py
cat django/api/pagination.py
cat django/admin/urls.py
```

If the task involves subscriptions:
```bash
cat django/subscriptions/models.py
```

## Project conventions

These patterns are established throughout the codebase. Following them keeps the API consistent and makes code review easier for contributors.

### Architecture overview

The API follows a layered structure where each component has a clear role:

1. **Models** (`gregory/models.py`) — data structure and business logic
2. **Serializers** (`api/serializers.py`) — shape the JSON response, handle nested relationships
3. **Filters** (`api/filters.py`) — `django-filters` FilterSet classes for query parameter filtering
4. **Views** (`api/views.py`) — DRF ViewSets and generic views, wire together serializers + filters
5. **Pagination** (`api/pagination.py`) — `FlexiblePagination` class supports both GET/POST and `all_results=true` bypass
6. **URLs** (`admin/urls.py`) — DRF router registration + manual path definitions

### ViewSet pattern

The project uses `ModelViewSet` for main resources, registered with the DRF router:

```python
class ArticleViewSet(viewsets.ModelViewSet):
    queryset = Articles.objects.all().order_by('-discovery_date')
    serializer_class = ArticleSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]
    pagination_class = FlexiblePagination
    filter_backends = [django_filters.DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_class = ArticleFilter
    search_fields = ['title', 'summary']
    ordering_fields = ['discovery_date', 'published_date', 'title', 'article_id']
    ordering = ['-discovery_date']
```

Key points:
- Default permission is `IsAuthenticatedOrReadOnly` — anyone can read, only authenticated users can write
- All list endpoints use `FlexiblePagination` (10 items/page, max 100, supports `all_results=true`)
- Three filter backends: `DjangoFilterBackend` for field filters, `SearchFilter` for text search, `OrderingFilter` for sorting
- Each ViewSet has a matching FilterSet class in `filters.py`

### Serializer patterns

Serializers use specific patterns for different relationship types:

```python
# M2M with slug (show name instead of ID)
sources = serializers.SlugRelatedField(many=True, read_only=True, slug_field='name')

# Nested serializer for full representation
authors = ArticleAuthorSerializer(many=True, read_only=True)

# SerializerMethodField for computed data
clinical_trials = serializers.SerializerMethodField()
```

When a serializer needs data from a related model that isn't a direct FK/M2M, use `SerializerMethodField`. For example, Articles show clinical trials via `ArticleTrialReference`, not a direct M2M:

```python
def get_clinical_trials(self, obj):
    references = ArticleTrialReference.objects.filter(article=obj)
    trials = [ref.trial for ref in references]
    return TrialReferenceSerializer(trials, many=True).data
```

### Filter patterns

Filters use `django-filters` with custom methods for text search that leverage the PostgreSQL trigram indexes:

```python
class ArticleFilter(filters.FilterSet):
    title = filters.CharFilter(method='filter_title')
    search = filters.CharFilter(method='filter_search')
    team_id = filters.NumberFilter(field_name='teams__id', lookup_expr='exact')

    def filter_title(self, queryset, name, value):
        return queryset.filter(utitle__contains=value.upper())

    def filter_search(self, queryset, name, value):
        upper_value = value.upper()
        return queryset.filter(
            Q(utitle__contains=upper_value) | Q(usummary__contains=upper_value)
        )
```

The `utitle` and `usummary` fields are PostgreSQL generated columns with GIN trigram indexes. Always use these for text search instead of `__icontains` on the base fields — the indexes make searches significantly faster.

### URL registration

Router-registered endpoints go in the router block:

```python
router = routers.DefaultRouter()
router.register(r'articles', ArticleViewSet)
router.register(r'authors', AuthorsViewSet, basename='authors')
```

Non-router endpoints use explicit paths:

```python
path('articles/search/', ArticleSearchView.as_view(), name='article-search'),
```

### Deprecation pattern

Legacy endpoints add deprecation headers but remain functional:

```python
def list(self, request, *args, **kwargs):
    response = super().list(request, *args, **kwargs)
    return add_deprecation_headers(response, deprecated_endpoint, replacement_endpoint)
```

The project is migrating from team-scoped URLs (`/teams/1/articles/`) to query-parameter filtering (`/articles/?team_id=1`). New endpoints should always use the query parameter approach.

### CSV export

Endpoints support CSV export via `?format=csv&all_results=true`. The `ArticleViewSet` overrides `finalize_response` to convert CSV responses to `StreamingHttpResponse`. New endpoints that support CSV should follow this pattern.

### Pagination

`FlexiblePagination` in `api/pagination.py` handles:
- Standard page/page_size from GET params
- Same parameters from POST body (for search endpoints that accept POST)
- `all_results=true` to bypass pagination entirely (used with CSV export)

Response format includes: `count`, `next`, `previous`, `current_page`, `total_pages`, `page_size`, `results`.

## Performance considerations

The database has several performance-sensitive patterns to be aware of:

- **N+1 queries**: Use `select_related()` for FK joins and `prefetch_related()` for M2M. The CategoryViewSet shows how to use `Prefetch` objects with `.only()` to limit fields.
- **Complex COUNT annotations**: The CategoryViewSet explicitly avoids `Count()` annotations with multiple JOINs because they caused query hangs. Instead, it uses prefetched data and computes counts in the serializer.
- **Text search**: Always use the generated `utitle`/`usummary`/`ufull_name` uppercase columns with `__contains` instead of `__icontains` on the base fields. The GIN trigram indexes only help the uppercase columns.
- **Large querysets**: For endpoints that might return thousands of results, support `all_results=true` with streaming responses for CSV export.

## Workflow

When asked to create or modify an endpoint, explain the approach first — which files need changes, what the endpoint will look like, and any trade-offs. Then write the code when asked.

The typical sequence for a new endpoint:

1. **Serializer** — define the response shape
2. **FilterSet** — define the query parameters
3. **ViewSet or View** — wire serializer + filters, set permissions
4. **URL registration** — router or explicit path
5. **Docstring** — document parameters and examples in the view class

Always include a docstring with query parameters, examples, and migration notes if replacing a legacy endpoint.

## Testing

Tests live in `tests/` directories within Django apps. Run tests via Docker:

```bash
docker exec gregory python manage.py test api.tests
docker exec gregory python manage.py test api.tests.test_specific_file
```

When writing new endpoints, suggest test cases that cover: basic list, filtering by each parameter, search, ordering, pagination, and edge cases (empty results, invalid parameters).

## What's out of scope

This skill covers the API layer. For database queries and data analysis, use the `database-query-expert` skill. For ML pipeline and model training, that belongs to the ML Engineer skill (when created).
