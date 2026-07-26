# Plan: fix silently-ignored filters on POST search endpoints

## Context

`/articles/search/`, `/trials/search/` and `/authors/search/` accept both GET and
POST. On GET they mount the full `ArticleFilter` / `TrialFilter` / `AuthorFilter`
via `DjangoFilterBackend`, so every list-endpoint filter works. On POST, the
views read `team_id`, `subject_id`, `title`, `summary`, `search` (and `status`
for trials) manually out of `request.data` — but **`DjangoFilterBackend` and
`OrderingFilter` only ever read `request.query_params`**, so every other filter
sent in the JSON body is dropped without a word.

The result is a wrong `200`, not an error. Measured against production
(2026-07-26):

```
GET  /trials/search/?team_id=1&subject_id=1&date_registration_after=2024-01-01&date_registration_before=2024-01-31   ->    32 trials
POST /trials/search/  {"team_id":1,"subject_id":1,"date_registration_after":"2024-01-01","date_registration_before":"2024-01-31"}  ->  6249 trials
```

Same for `relevant`, `subjects`, `open_access`, `phase_normalized`, `country`,
`sponsor_id`, `has_results` — anything defined on the filterset.

This is a *class* of bug, not one filter: every filter added to those filtersets
in future silently fails on POST unless someone remembers to hand-plumb it.

The docs were corrected on branch `merge-authors-admin-action` (see
`docs/03-api-and-rss-feeds.md`, section "GET vs POST on search endpoints") to
warn about the behaviour. This plan fixes the behaviour itself.

## Design decision: keep both verbs, fix POST

GET is the better transport here — cacheable, loggable, linkable, and it is the
only path DRF's filter/ordering/pagination backends natively understand. POST is
worth keeping only for the one thing GET cannot do: `search` strings with long
boolean expressions that would blow past practical URL limits (~2000 chars in
browsers, `large_client_header_buffers` in nginx).

So: **do not deprecate POST, and do not remove it.** Make POST behave the way the
docs always implied — one code path, no silent drops. Deprecating it would be a
breaking change for unknown consumers and buys nothing once the divergence is
gone.

## Implementation

### 1. Add a mixin that exposes POST-body params to the filter backends

Add to `django/api/views.py` (near the other mixins at the top, alongside
`CSVStreamingMixin` / `BulkExportThrottleMixin`):

```python
class BodyParamsAsQueryParamsMixin:
	"""Make POST-body params visible to DRF's filter backends.

	DjangoFilterBackend and OrderingFilter read request.query_params only, so on
	a POST every filterset param used to be dropped silently and the response
	came back unfiltered — a wrong 200, not an error. Merging the body into
	query_params gives GET and POST a single code path.

	An explicit query-string value wins over the same key in the body, so the
	documented `POST /search/?filters...` workaround keeps working unchanged.
	"""

	def initial(self, request, *args, **kwargs):
		super().initial(request, *args, **kwargs)
		if request.method != "POST":
			return
		data = request.data
		if not hasattr(data, "items"):   # not a JSON object / form payload
			return
		merged = request.query_params.copy()   # a mutable QueryDict
		items = data.lists() if hasattr(data, "lists") else data.items()
		for key, value in items:
			if key in merged:
				continue                       # query string wins
			if isinstance(value, (list, tuple)):
				merged.setlist(key, [str(v) for v in value if v is not None])
			elif value is not None:
				merged[key] = str(value)
		merged._mutable = False
		request._request.GET = merged
```

Notes for whoever implements this:

- `request.query_params` *is* `request._request.GET`, so assigning
  `request._request.GET` updates both.
- Call `super().initial()` **first**. Content negotiation happens inside
  `initial()`, and we deliberately do not want a body `{"format": "csv"}` to
  retroactively swap the renderer — that is a separate change with its own
  blast radius. Verify this holds and note it in the docstring.
- A JSON list value (`{"subjects": [1, 3]}`) must become a repeated key so
  `BaseInFilter` sees it; a comma string (`"1,3"`) already works.
- Booleans stringify to `"True"`/`"False"` — confirm DRF's `BooleanFilter`
  accepts those (it accepts `true/True/1`). Add a test for
  `{"relevant": true}` and `{"has_results": false}` specifically.

### 2. Apply it to the three POST-enabled search views

- `ArticleSearchView` — `django/api/views.py` (`http_method_names` at ~:3004)
- `TrialSearchView` — ~:3251
- `AuthorSearchView` — ~:3448

Put the mixin first in the MRO, before `CSVStreamingMixin`:

```python
class ArticleSearchView(BodyParamsAsQueryParamsMixin, CSVStreamingMixin, BulkExportThrottleMixin, generics.ListAPIView):
```

### 3. Remove the now-redundant manual param handling

Once the merge is in place the filterset handles `title` / `summary` / `search` /
`status` on both verbs, so the hand-rolled blocks in `get_queryset` are dead
weight — and on GET they already double-apply the same conditions today.

- `ArticleSearchView.get_queryset` — drop the `title` / `summary` / `search`
  block (~:3037–3048). Keep `team_id`/`subject_id` extraction, the visibility
  check, and all the `prefetch_related` work untouched.
- `TrialSearchView.get_queryset` — drop the equivalent `title` / `summary` /
  `search` / `status` block.
- `filter_queryset`'s manual POST `ordering` handling (~:3094–3111 and the trials
  twin) also becomes redundant — `OrderingFilter` will see `ordering` in the
  merged query params. Remove it, but **only after** a test proves POST ordering
  still works, because `OrderingFilter` silently ignores unknown fields whereas
  the manual code validated against `ordering_fields`.

Do this step as its own commit so it can be reverted independently of the fix.

**Careful:** `get_queryset` still reads `team_id`/`subject_id` from
`request.data` for POST. Leave that alone — it feeds the 400/404 error contract
in `post()`, which is separately tested.

### 4. Tests

New file `django/api/tests/test_search_post_filters.py`. For articles and trials
(and `full_name` for authors), assert POST-body and GET produce **identical**
results:

- `published_date_after` / `published_date_before` in a POST body now filter,
  and match the GET count exactly.
- `date_registration_after` / `date_registration_before` likewise.
- A non-date filter — `relevant` (articles), `has_results` and
  `phase_normalized` (trials) — to prove the fix is general, not date-specific.
- List-valued body param: `{"subjects": [id1, id2]}` matches
  `?subjects=id1,id2`.
- Query string beats body when both set the same key.
- Regression: `title` / `summary` / `search` / `status` still work on POST after
  step 3 removes the manual handling.
- Regression: POST `ordering` still respects `ordering_fields` and rejects
  garbage by falling back to the default order.
- Regression: the existing 400/404 contract for missing/bad
  `team_id`/`subject_id` is unchanged.

Reuse the fixture style in `django/api/tests/test_date_range_filter_articles.py`
and `test_date_range_filter_trials.py`.

Full suite must stay green — the search views are covered by
`test_trial_search.py`, `test_author_search.py`, `test_search_ordering.py`,
`test_api_integration.py`, `test_boolean_search.py` and the CSV export tests.

```bash
docker exec gregory python manage.py test api
```

### 5. Docs

`docs/03-api-and-rss-feeds.md` currently documents the broken behaviour as a
warning. Once fixed:

- Endpoint table rows for `POST /articles/search/` and `POST /trials/search/`
  (~:163, ~:186) — drop "**Restricted subset**", restore "same fields as GET".
- The "GET vs POST on search endpoints" subsection (~:228) — rewrite from
  "filters are silently ignored" to a short note that both verbs accept the same
  fields, POST exists for long boolean `search` strings, and GET stays
  preferable because it is cacheable and linkable. Keep a one-line changelog
  mention that POST previously ignored filters, so anyone who worked around it
  knows it changed.
- The GET/POST columns in the search-parameters table (~:215) become all ✅.
- View docstrings `ArticleSearchView` (~:2957) and `TrialSearchView` (~:3196) —
  replace the "GET-only parameters" paragraphs with the corrected behaviour.

Per `CLAUDE.md`, docs ship in the same PR.

## Risk

Low but non-zero, and worth stating plainly: any client currently sending extra
keys in a POST body — a stray `relevant`, a leftover `page_size`, an unrelated
field that happens to collide with a filter name — gets *different results after
this change*, because those keys start being honoured. That is the intended fix,
but it is a behaviour change on a public endpoint, not a pure no-op. Call it out
in the PR description and the docs changelog.

Unknown collisions are the thing to watch. Before merging, grep the frontend and
any known API consumers for `POST` calls to the three search endpoints and check
what keys they send.

## Out of scope

- Whether `format=csv` should work from a POST body (content-negotiation
  ordering — see the note in step 1).
- Deprecating POST search.
- The `date_range` filter design question that started this thread — still
  unanswered, tracked separately.
