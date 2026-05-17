# API Improvements — P0 Spec

Branch: `api-improvements`
Status: Draft
Last updated: 2026-05-15

This document specifies the ship-blocking ("P0") improvements to the Django `api` app. P1/P2 items are listed at the end as a roadmap for follow-up branches; they are explicitly **out of scope** for the work this spec covers.

---

## 1. Goals

1. Let API clients **edit** existing articles and trials they own, via the same API-key flow used today for creating them (`/articles/post/`).
2. Make article/trial **editorial fields per-organisation**, so two orgs sharing the same upstream paper do not overwrite each other's takeaways/plain-English summaries.
3. **Close the cross-org write hole** on the existing DRF `ModelViewSet`s, where any authenticated user can currently PATCH any article/trial/author/source/etc.
4. Remove dead auth code (broken DRF Token endpoint).

## 2. Non-goals

- No throttling/rate limiting beyond what the API-key model already enforces (`max_calls_minute/hour/day`). Public data, polite scraping is welcome.
- No editing of relations (`teams`, `subjects`, `authors`, `sources`, `team_categories`) via the API. Those stay managed in the admin.
- No new permissions framework for read endpoints — anonymous reads behave as today, with the per-org fields hidden as described in §6.
- No backfill that splits the legacy `Articles.takeaways` across multiple orgs. We migrate it to a single designated org and deprecate the column.

---

## 3. Data model changes

### 3.1 New model: `ArticleOrgContent`

Per-organisation editorial content for an article.

| Field | Type | Notes |
|---|---|---|
| `article` | FK → `Articles`, `on_delete=CASCADE`, `related_name='org_contents'` | |
| `organization` | FK → `Organization`, `on_delete=CASCADE`, `related_name='article_contents'` | |
| `takeaways` | `TextField(blank=True, null=True)` | Editorial bullet list per org. |
| `summary_plain_english` | `TextField(blank=True, null=True)` | Plain-English rewrite per org. |
| `created_at` | `DateTimeField(auto_now_add=True)` | |
| `updated_at` | `DateTimeField(auto_now=True)` | |
| `history` | `HistoricalRecords(...)` | Audit trail with API-key attribution (see §3.4). |

Constraints:
- `UniqueConstraint(fields=['article', 'organization'], name='unique_article_org_content')`

Indexes:
- Implicit indexes from the FKs are sufficient.

### 3.2 New model: `TrialOrgContent`

Same shape as `ArticleOrgContent`, for `Trials`.

| Field | Type | Notes |
|---|---|---|
| `trial` | FK → `Trials`, `on_delete=CASCADE`, `related_name='org_contents'` | |
| `organization` | FK → `Organization`, `on_delete=CASCADE`, `related_name='trial_contents'` | |
| `takeaways` | `TextField(blank=True, null=True)` | New — `Trials` has no global takeaways field today. |
| `summary_plain_english` | `TextField(blank=True, null=True)` | Mirrors `Trials.summary_plain_english`. |
| `created_at` | `DateTimeField(auto_now_add=True)` | |
| `updated_at` | `DateTimeField(auto_now=True)` | |
| `history` | `HistoricalRecords(...)` | Audit trail with API-key attribution (see §3.4). |

Constraints:
- `UniqueConstraint(fields=['trial', 'organization'], name='unique_trial_org_content')`

### 3.3 Lifecycle / cascade behaviour

- **Article (or Trial) deleted** → all `ArticleOrgContent` (resp. `TrialOrgContent`) rows cascade away.
- **Organization deleted** → its `*OrgContent` rows cascade away. The article/trial itself is **not** affected.
- **Orphan articles/trials**: if an article ends up with no teams associated (and therefore no orgs that reference it), it should be deletable as part of routine cleanup. A management command `cleanup_orphan_articles` is added that deletes articles with `teams=None`. **Not run automatically** — operator-triggered for now. Same for trials.

### 3.4 Historical records and API-key attribution

`ArticleOrgContent` and `TrialOrgContent` use `HistoricalRecords` for field-level audit, with an explicit `api_access_scheme` FK on the historical model so it's clear which API key made the change.

Implementation pattern (in `django/gregory/models.py`):

```python
from simple_history.models import HistoricalRecords

class ArticleOrgContent(models.Model):
    # …regular fields…
    history = HistoricalRecords(
        excluded_fields=['updated_at'],
        cascade_delete_history=True,
    )
```

To attribute changes to an `APIAccessScheme` rather than to a Django auth user, add an extra field to the *historical* model only, via simple-history's `inherit` / `additional_fields` pattern. The cleanest expression is to define a custom historical model:

```python
from simple_history.models import HistoricalRecords

class ArticleOrgContent(models.Model):
    # …regular fields…
    history = HistoricalRecords(
        excluded_fields=['updated_at'],
        cascade_delete_history=True,
        history_change_reason_field=models.TextField(null=True, blank=True),
        bases=[models.Model],
    )
```

…and populate `api_access_scheme` through a thin abstraction the edit views use when they save:

```python
def _save_with_api_audit(instance, api_access_scheme):
    instance._history_user = None  # no Django user
    instance.save()
    # Attach the API key to the most recent history row
    instance.history.first().api_access_scheme = api_access_scheme
    instance.history.first().save(update_fields=['api_access_scheme'])
```

The simplest concrete shape: include an explicit FK on the historical model by subclassing `HistoricalRecords` or using `history.register(... extra_fields={...})`. The implementation may pick whichever simple-history API surface is least invasive; the requirement is just that **every historical row for `ArticleOrgContent` and `TrialOrgContent` carries an `api_access_scheme_id` populated at save time**. Nullable, because admin edits or fixture loads have no API key.

Required behaviour:
- Every save through the API edit endpoints sets the historical row's `api_access_scheme_id`.
- Admin/shell saves leave it `NULL` — those are attributed via `history_user` instead (Django's auth user).
- The historical model is readable from the Django admin's "History" button on each object.

`Articles` already has `HistoricalRecords` ([django/gregory/models.py:274](django/gregory/models.py#L274)). When the edit endpoint updates `access`/`retracted`/`kind` on `Articles` directly, the same attribution mechanism applies: stamp the API key onto that historical row too. This requires extending the existing `Articles.history` definition with the same `api_access_scheme` field. Same for `Trials.history` if `/trials/edit/` ever writes back to per-trial fields (currently it doesn't — only per-org content — so this is a no-op for trials today, but the field gets added for symmetry and future-proofing).

### 3.5 Field deprecation: `Articles.takeaways`

The existing `Articles.takeaways` column stays in place for the duration of this spec's PR, but:
- Read endpoints stop returning it directly (see §6).
- Migration moves its contents into `ArticleOrgContent` for a single designated organisation (see §7).
- The column will be removed in a follow-up PR after a release of co-existence. Removal is **not** part of this spec.

`Trials` has no `takeaways` field — no deprecation needed there.

---

## 4. Edit endpoints

Design follows `post_article` in [django/api/views.py:106](django/api/views.py#L106) — same auth, error model, and logging.

### 4.1 `POST /articles/edit/`

Auth: API key via `getAPIKey(request)` + `checkValidAccess(api_key, ip_addr)`. Key must have an `organization` (same constraint as `post_article`).

**Request body** (JSON):
```json
{
  "doi": "10.1056/NEJMoa2034577",
  "takeaways": "…",
  "summary_plain_english": "…",
  "access": "open",
  "retracted": false,
  "kind": "science paper"
}
```

Lookup rules:
- `doi` is **required** and is the canonical lookup field. `article_id` is **not** accepted (clients should not need to know the internal id).
- Article must already exist. If not found by DOI → `404` with error code `ArticleNotFoundError`.
- If more than one article matches the DOI (data quality issue, should be rare given the unique constraint on `(title, link)` but DOI is not unique) → `409 Conflict` with error code `DuplicateArticleError`. Response includes the matching `article_id`s so the operator can clean up the duplicates in the admin.
- Article must be associated with at least one team that belongs to the API key's organisation. If not → `403` with error code `CrossOrgPayloadError`. Phrase: *"this article is not visible to your organisation."*

Field handling:
- **Per-org fields** (`takeaways`, `summary_plain_english`): upserted into `ArticleOrgContent` for `(article, api_key.organization)`. Empty string is treated as "clear this field" (saved as `NULL`). Field omitted → not changed.
- **Per-article fields** (`access`, `retracted`, `kind`): updated on the `Articles` row directly. Allowed because they describe the underlying paper, not editorial content.
  - `access` must be one of `Articles.ACCESS_OPTIONS`.
  - `kind` must be one of `Articles.KINDS`.
  - `retracted` must be a boolean.
  - Invalid value → `400` with `FieldNotFoundError`-style payload.

Not editable through this endpoint: `title`, `link`, `doi`, `summary`, `published_date`, `discovery_date`, `authors`, `sources`, `teams`, `subjects`, `team_categories`, `entities`, `ml_predictions`, `noun_phrases`, `publisher`, `container_title`, `crossref_check`, `history`, `usummary`, `utitle`.

**Response** (200 OK):
```json
{
  "article_id": 12345,
  "doi": "10.1056/NEJMoa2034577",
  "organization_id": 7,
  "updated_fields": ["takeaways", "access"]
}
```

Errors mirror `post_article`'s exception model (`APIAccessDeniedError`, `FieldNotFoundError`, etc.) and log the same way (see §8).

### 4.2 `POST /trials/edit/`

Same pattern as articles. Lookup field is **`identifier`** — the canonical id of the trial in the source registry (e.g., NCT number, EUCTR id). Trials don't have a DOI.

Rationale: `Trials.identifiers` is a `JSONField`. The endpoint matches against the same key(s) `post_article`'s trial branch uses for dedup. The implementation must reuse the same lookup helper from the existing trial ingestion code; do not re-invent.

If trial dedup currently uses a different/additional field (e.g., `internal_number`, `secondary_id`), the endpoint accepts whatever lookup key the ingestion path accepts.

**Duplicate handling**: trial dedup in the existing ingestion path is best-effort, and duplicate trial rows can exist in the database today. If the lookup matches more than one trial → `409 Conflict` with error code `DuplicateTrialError`. Response includes the matching `trial_id`s so the operator can resolve the duplicates in the admin. The edit is **not** applied — clients must resolve the ambiguity first.

Editable fields:
- Per-org (into `TrialOrgContent`): `takeaways`, `summary_plain_english`.
- Per-trial (on `Trials`): none in this spec. (Trials carry registry-sourced data; orgs should not be editing global fields.)

Response and errors: same shape as `/articles/edit/`.

### 4.3 URL registration

Register both new endpoints in [django/admin/urls.py:67](django/admin/urls.py#L67) alongside `articles/post/`:

```python
path('articles/post/', post_article),
path('articles/edit/', edit_article),
path('trials/edit/', edit_trial),
```

A follow-up branch will move all of this into a dedicated `django/api/urls.py` (see roadmap §11).

---

## 5. Lock down existing write paths

The following viewsets currently accept PUT/PATCH/DELETE for any authenticated user, despite all editing being intended to go through the API-key flow. Restrict them to **read-only** by setting `http_method_names = ['get', 'head', 'options']`:

| Viewset | File | Line |
|---|---|---|
| `ArticleViewSet` | `django/api/views.py` | 386 |
| `TrialViewSet` | `django/api/views.py` | 755 |
| `AuthorsViewSet` | `django/api/views.py` | 870 |
| `SourceViewSet` | `django/api/views.py` | 847 |
| `CategoryViewSet` | `django/api/views.py` | 498 |
| `SubjectsViewSet` | `django/api/views.py` | 1185 |
| `TeamsViewSet` | `django/api/views.py` | 1147 |

The existing `permission_classes = [IsAuthenticatedOrReadOnly]` stays — anonymous reads are still allowed.

`post_article`, `edit_article`, `edit_trial` are not viewsets; they remain the only write paths in the API.

---

## 6. Read behaviour for per-org fields

Anonymous reads continue to work for everything that's currently public. The new rules apply only to the two new per-org fields, plus `Articles.takeaways` once it stops being read directly.

### 6.1 Authenticated with API key

`ArticleSerializer` and `TrialSerializer` expose `takeaways` and `summary_plain_english` resolved against the API key's organisation:

```
takeaways → ArticleOrgContent.takeaways where organization = api_key.organization
summary_plain_english → ArticleOrgContent.summary_plain_english where organization = api_key.organization
```

If no `ArticleOrgContent` row exists for that `(article, organization)` pair → both fields are `null` in the response.

### 6.2 Anonymous or JWT-only (no API key)

`takeaways` and `summary_plain_english` are **omitted from the serializer output entirely** (not just `null`). Exception: if the request targets a specific organisation (e.g., via existing filter like `?team__organization=<id>`) AND that organisation has `OrganizationApiSettings.make_api_public = True`, then that org's `ArticleOrgContent` is exposed.

The decision tree, per request:
1. Is there a valid API key? → return that org's content. Done.
2. Is the request filtered to a single org that has `make_api_public=True`? → return that org's content. Done.
3. Otherwise → omit per-org fields from the response.

### 6.3 Browsable API (DRF web UI)

Follows the same rules as §6.1/§6.2. The browsable API authenticates with session/JWT in dev, neither of which carries an organisation, so per-org fields are hidden unless §6.2 case 2 applies.

### 6.4 Serializer implementation notes

- Use `SerializerMethodField` for `takeaways` and `summary_plain_english` on `ArticleSerializer` and `TrialSerializer`.
- Pass `request` via context (already standard) and resolve the org from `request.api_key.organization` if present.
- For list endpoints, **prefetch** the `org_contents` relation filtered to the request's org to avoid N+1 (see P1 roadmap — partial fix in this spec).

---

## 7. Data migration for legacy `Articles.takeaways`

A one-shot management command (not a Django data migration) moves existing `Articles.takeaways` values into `ArticleOrgContent` rows for a single designated organisation. Using a management command instead of `migrations.RunPython` lets us prompt the operator interactively.

- **Command**: `python manage.py migrate_legacy_takeaways`
- **Interactive prompt**: lists existing organisations (`id`, `name`, article count) and asks the operator to type the target organisation id. Refuses to proceed if the id is invalid.
- **Non-interactive mode**: `--org-id <id>` flag for scripted runs (CI, container start). `--noinput` is required alongside `--org-id` to suppress the confirmation prompt.
- **Dry run**: `--dry-run` reports how many rows would be created without writing anything.
- **Behaviour**: for every `Articles` row where `takeaways IS NOT NULL AND takeaways != ''`, create an `ArticleOrgContent(article=…, organization_id=<chosen>, takeaways=<value>)`. Skip if a row for that pair already exists (idempotent — safe to re-run).
- **Reversal**: not built. If we need to roll back, the source data is still in `Articles.takeaways` until the follow-up migration drops the column.
- The legacy column is **not dropped** in this work. Removal is a follow-up branch.

No data migration needed for trials — they have no legacy per-org content.

---

## 8. Auth cleanup: remove broken DRF Token endpoint

The endpoint `POST /api/token/get/` ([django/admin/urls.py:64](django/admin/urls.py#L64)) issues DRF tokens via `obtain_auth_token`, but `TokenAuthentication` is missing from `DEFAULT_AUTHENTICATION_CLASSES` ([django/admin/settings.py:183](django/admin/settings.py#L183)). Tokens it issues are unusable on every protected endpoint. Dead code.

Changes:
1. Remove the URL pattern at [django/admin/urls.py:64](django/admin/urls.py#L64).
2. Remove the import at [django/admin/urls.py:21](django/admin/urls.py#L21) (`from rest_framework.authtoken import views`).
3. Remove `'rest_framework.authtoken'` from `INSTALLED_APPS` at [django/admin/settings.py:72](django/admin/settings.py#L72).
4. Add a migration that drops the `authtoken_token` table. Use `migrations.RunPython` with `connection.schema_editor()` only if Django doesn't auto-detect the app removal. Verify in dev first.

Out of scope: any change to JWT auth. `POST /api/token/` (SimpleJWT) continues to work as today and remains the user-credential path. API-key auth (the `APIAccessScheme` model) is unchanged and remains the client-integration path.

---

## 9. Logging and audit

Two complementary systems are used: `APIAccessSchemeLog` for the **call**, and `HistoricalRecords` for the **data**. They are not substitutes — the former records that a request happened (including failures and security-relevant attempts), the latter records what a field used to say.

### 9.1 Call log — `APIAccessSchemeLog`

Every call to `edit_article` and `edit_trial` writes one row to `APIAccessSchemeLog` ([django/api/models.py:67](django/api/models.py#L67)), matching `post_article`'s pattern:

- `call_type`: `"POST /articles/edit/"` or `"POST /trials/edit/"`
- `ip_addr`: from `getIPAddress(request)`
- `api_access_scheme`: the resolved `APIAccessScheme`
- `http_code`: the response status
- `error_message`: error class name + message on failure
- `payload_received`: truncated request body (existing `post_article` truncation rules apply)

Rejected calls (404, 403, 409, 400) **must** still log here. This is the audit trail for security investigations.

### 9.2 Data audit — `HistoricalRecords`

Every successful save on `ArticleOrgContent`, `TrialOrgContent`, and edits to `Articles` per-article fields (`access`, `retracted`, `kind`) creates a historical row. Each historical row carries:

- The full snapshot of the post-save fields (simple-history default).
- `history_date`, `history_type` (`+`/`~`/`-`) (simple-history default).
- `history_user` — the Django auth user if the change came from the admin/shell; `NULL` if from an API-key call.
- `api_access_scheme` — FK to `APIAccessScheme`, populated when the change came from `/articles/edit/` or `/trials/edit/`; `NULL` for admin/shell edits.

Failed edits do **not** create historical rows. To investigate a rejected attempt, use the `APIAccessSchemeLog` from §9.1.

### 9.3 Reading the audit

- "What did this article's takeaways say a week ago, for org X?" → `ArticleOrgContent.history.filter(organization_id=X, history_date__lt=…)`.
- "Who edited this article last?" → check both `history_user` (Django admin) and `api_access_scheme` (API call) on the most recent historical row.
- "Did anyone try to edit articles outside their org last month?" → `APIAccessSchemeLog.objects.filter(call_type__startswith='POST /articles/edit/', http_code=403)`.

---

## 10. Test plan

Add tests under `django/api/tests/`. Minimum coverage:

1. **`/articles/edit/`**
   - Successful upsert (no prior `ArticleOrgContent` row exists).
   - Successful update of existing `ArticleOrgContent`.
   - Update of per-article fields (`access`, `retracted`, `kind`) persists on `Articles`.
   - Article not found by DOI → 404.
   - Multiple articles match the DOI → 409 `DuplicateArticleError` with the conflicting `article_id`s in the response; no fields are written.
   - Article exists but belongs to a different org → 403 `CrossOrgPayloadError`.
   - Invalid `access` / `kind` values → 400.
   - Missing API key → 401.
   - API key without org → 403.
   - `HistoricalRecords` row is created on update with `api_access_scheme` populated to the calling key.
   - Admin/shell save creates a `HistoricalRecords` row with `api_access_scheme = NULL` and `history_user` populated.
   - Failed edits (403/404/409/400) do **not** create historical rows but **do** create an `APIAccessSchemeLog` row with the right `http_code`.
   - Empty string `takeaways` clears the field (saved as `NULL`).
2. **`/trials/edit/`**: same matrix, with `DuplicateTrialError` in place of `DuplicateArticleError` and identifier-based lookup.
3. **Read serialization**
   - With API key for Org A: takeaways resolves to Org A's content.
   - With API key for Org A on an article that has no Org A content: takeaways is `null`.
   - Anonymous: takeaways field absent from response.
   - Anonymous + filter to a `make_api_public=True` org: takeaways present.
   - Two orgs editing the same article: each sees its own takeaways.
4. **Lock-down**
   - `PATCH /articles/<id>/` returns 405 Method Not Allowed.
   - Same for trials/authors/sources/subjects/categories/teams.
5. **`migrate_legacy_takeaways` management command**
   - Run on a fixture with two articles (one with takeaways, one without) → exactly one `ArticleOrgContent` row created.
   - Re-running is idempotent (no duplicate rows).
   - `--dry-run` reports counts and writes nothing.
   - `--org-id <bad-id>` exits non-zero with a clear error.
   - `--org-id <id> --noinput` runs without prompting.
   - Interactive run with invalid id at the prompt re-prompts (or exits cleanly, TBD by implementation).

---

## 11. Roadmap — P1 / P2 (out of scope for this spec)

These will be addressed in follow-up branches, each one referencing this spec:

**P1**
- Move URL config from `django/admin/urls.py` into `django/api/urls.py`; include it from the admin urlconf.
- Fix N+1 queries: add `select_related`/`prefetch_related` to `ArticleViewSet.queryset` and `TrialViewSet.queryset`; replace `ArticleSerializer.get_clinical_trials` and `TrialSerializer.get_articles` per-row queries with prefetched relations.
- Move the aggregate query out of `TrialViewSet.list` into a separate `/trials/stats/` endpoint.
- Add `drf-spectacular` and serve `/schema/` and `/docs/` so the in-code docstrings become usable.
- Delete dead code: `django/api/optimized_views.py`, unregistered `OrganizationsViewSet`, unrouted `ArticlesByKeyword`, the typo'd `permissions_classes` on the latter.
- Drop the legacy `Articles.takeaways` column once a release cycle has passed.

**P2**
- Apply `FlexiblePagination` globally so pagination is consistent across all list endpoints.
- Remove deprecated `ArticlesByTeam` / `ArticlesBySubject` / `SubjectsByTeam` / `CategoriesByTeamAndSubject` / `ArticlesByCategoryAndTeam` viewsets.
- Split `django/api/views.py` (2004 lines) into per-resource modules.
- Optional very-high anonymous throttle (e.g., 1000/hour) as a circuit-breaker, not a paywall.

---

## 12. Resolved questions

All three §7-era questions resolved:

1. **Legacy takeaways org choice** — decided at deploy time, via the interactive `migrate_legacy_takeaways` management command (see §7). No env var needed.
2. **Trial lookup key** — confirmed to be the same key(s) used by `post_article`'s trial branch. Implementation reuses the existing helper. Duplicate matches return `409 DuplicateTrialError` (§4.2).
3. **Browsable API + `make_api_public`** — explicit-filter-only. Per-org fields are exposed to anonymous callers only when the request explicitly filters to one organisation that has `make_api_public=True`. List endpoints that span multiple orgs never expose per-org fields anonymously.
