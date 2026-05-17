# Implementation Plan — API Improvements P0

Branch: `api-improvements`
Spec: [API_IMPROVEMENTS_SPEC.md](API_IMPROVEMENTS_SPEC.md) — **read this first**, the plan only describes the *how*, not the *what* or the *why*.
Last updated: 2026-05-17

This plan is written for a coding agent. Each phase is a sensible commit boundary. Stick to the order — later phases depend on earlier ones.

---

## Conventions used by the existing codebase

Match these. Do **not** introduce new patterns.

- **Indentation**: tabs (per `CLAUDE.md`).
- **Tests**: Django `TestCase` + `Client` (not pytest). API-key auth via header `HTTP_AUTHORIZATION = <api_key>` (no `Bearer` prefix — `getAPIKey()` reads `request.headers.get('Authorization')` directly). See `django/api/tests/test_post_article_org_scoping.py` for the helper conventions (`_make_org`, `_make_team`, `_make_scheme`, etc.) — reuse the same shape.
- **Migrations**: schema migrations only. Data movement for legacy takeaways is a **management command**, not `RunPython` (spec §7).
- **Run inside Docker**: `docker exec gregory python manage.py …` for `makemigrations`, `migrate`, `test`.

---

## Phase 1 — Foundation (exceptions, responses, helpers, middleware)

**Files touched**: `django/api/utils/exceptions.py`, `django/api/utils/responses.py`, `django/api/utils/utils.py`, `django/gregory/middleware/visibility.py` (or new middleware file), `django/admin/settings.py` (MIDDLEWARE).

### 1.1 Add new exception classes — `django/api/utils/exceptions.py`

Append to the existing list (which already has `APIAccessDeniedError`, `CrossOrgPayloadError`, `FieldNotFoundError`, etc.):

- `ArticleNotFoundError(APIError)`
- `TrialNotFoundError(APIError)`
- `DuplicateArticleError(APIError)` — carries a list of conflicting `article_id`s; expose via `__init__(self, ids, *args)` so the view can pass them to the response.
- `DuplicateTrialError(APIError)` — same shape with `trial_id`s.

### 1.2 Add new response codes — `django/api/utils/responses.py`

Allocate new integer codes after the existing `CROSS_ORG_PAYLOAD = 10`:

- `ARTICLE_NOT_FOUND`
- `TRIAL_NOT_FOUND`
- `DUPLICATE_ARTICLE`
- `DUPLICATE_TRIAL`

Add a message per code to the `ERRORS` dict. The `returnError(code, extra_data, status_error_code)` signature already supports passing the conflicting ids via `extra_data` — use it.

### 1.3 Extract a trial-identifier lookup helper — `django/api/utils/utils.py`

Today's trial dedup lives inline in `post_article` ([django/api/views.py:254-267](django/api/views.py#L254)). It uses `.first()`, which hides duplicates. Extract to a helper:

```python
def find_trial_by_identifier(identifiers: dict | None):
    """Return queryset of Trials matching any identifier key (euct, nct, eudract).
    Caller decides .first() vs .count()/.all() based on whether dupes are allowed."""
```

Update `post_article` to call the helper (still `.first()` there — keeps current behaviour). `edit_trial` will call it and reject on `count() > 1`.

### 1.4 New middleware to attach the resolved API key to the request — `django/gregory/middleware/visibility.py` (extend) or a new file `django/api/middleware.py`

Spec §6.1 reads `request.api_key.organization`. That attribute doesn't exist yet. Implement either:

- **Recommended**: a thin middleware `ApiKeyMiddleware` that, when the `Authorization` header is present, calls `getAPIKey(request)` + `checkValidAccess(...)` and stashes the resolved `APIAccessScheme` on `request.api_access_scheme`. On failure (no key, invalid key, IP mismatch), it sets `request.api_access_scheme = None` and lets downstream views/serializers handle "no key". **Do not raise** in the middleware — `edit_article` needs to log its own failures.

Register in `MIDDLEWARE` in `django/admin/settings.py` **after** `VisibleOrgMiddleware`.

The serializer reads `request.api_access_scheme.organization_id` from `self.context['request']`.

---

## Phase 2 — Models + migration

**Files touched**: `django/gregory/models.py`, `django/gregory/migrations/000X_articleorgcontent_trialorgcontent.py`.

### 2.1 Add `ArticleOrgContent` and `TrialOrgContent` to `django/gregory/models.py`

Place near `Articles` / `Trials`. Spec §3.1 and §3.2 have the field list.

Required:
- FK to `Articles` / `Trials` with `on_delete=CASCADE`, `related_name='org_contents'`.
- FK to `Organization` with `on_delete=CASCADE`, `related_name='article_contents'` / `'trial_contents'`.
- `takeaways: TextField(blank=True, null=True)`.
- `summary_plain_english: TextField(blank=True, null=True)`.
- `created_at`, `updated_at` (`auto_now_add` / `auto_now`).
- `history = HistoricalRecords(...)` — see §2.3 for the API-key attribution requirement.
- `class Meta: constraints = [UniqueConstraint(fields=['article', 'organization'], name='unique_article_org_content')]` (and the trial variant).
- `__str__` returning `f"{self.article_id}/{self.organization_id}"` or similar.

### 2.2 Extend `Articles.history` and `Trials.history` to carry `api_access_scheme`

Per spec §3.4, the historical row for **every** model the edit endpoints write to needs an `api_access_scheme` FK populated at save time:

- `ArticleOrgContent.history`
- `TrialOrgContent.history`
- `Articles.history` (because `/articles/edit/` writes `access`, `retracted`, `kind` on the article row)
- `Trials.history` (no current writes from the edit endpoint, but add it for symmetry — see spec §3.4 last paragraph)

`django-simple-history==3.8.0` (confirmed at `django/requirements.txt:27`) supports adding extra fields to the historical model. The cleanest approach with this version:

1. Define a small abstract base or use `HistoricalRecords(bases=[...])`.
2. Or use the `register()` API (function-call form) with `extra_fields={'api_access_scheme': models.ForeignKey('api.APIAccessScheme', null=True, blank=True, on_delete=models.SET_NULL)}`.

Pick whichever is least invasive. **Required behaviour** (regardless of API choice):

- Every historical row for the four models above has an `api_access_scheme_id` column.
- Nullable (admin/shell saves leave it `NULL`).
- The edit view sets it on save (see Phase 3).

### 2.3 Migration

Run `docker exec gregory python manage.py makemigrations gregory`. Expect:

- Two `CreateModel` operations for the new content models.
- One `CreateModel` per new historical model.
- `AddField` on the historical models for `Articles` and `Trials` to carry the new FK.

Sanity-check the generated migration file by reading it — don't blindly trust the autogen for historical models with extra fields. Apply with `docker exec gregory python manage.py migrate`.

**Do not** drop `Articles.takeaways` in this migration. Removal is a follow-up branch (spec §3.5).

---

## Phase 3 — Edit endpoints

**Files touched**: `django/api/views.py`, `django/admin/urls.py`.

### 3.1 Helper for setting `api_access_scheme` on the most recent historical row

Add (private) helper in `django/api/views.py`:

```python
def _stamp_api_key_on_history(instance, access_scheme):
    """After instance.save(), attach the API access scheme to the most recent history row."""
    h = instance.history.first()
    if h is not None:
        h.api_access_scheme = access_scheme
        h.save(update_fields=['api_access_scheme'])
```

### 3.2 `edit_article` — `django/api/views.py`

Place immediately after `post_article` (after [line 381](django/api/views.py#L381)). Follow the exact structure of `post_article`:

1. Build `call_type`, `ip_addr`, parse `post_data`.
2. Resolve api key with `getAPIKey` / `checkValidAccess`; require `access_scheme.organization`.
3. Validate body:
   - `doi` required → `FieldNotFoundError` if missing/empty.
4. Look up the article: `Articles.objects.filter(doi__iexact=post_data['doi'])`.
   - `count() == 0` → `ArticleNotFoundError` (HTTP 404, code `ARTICLE_NOT_FOUND`).
   - `count() > 1` → `DuplicateArticleError(ids=[…])` (HTTP 409, code `DUPLICATE_ARTICLE`, response includes the ids in `extra_data`).
5. Cross-org check: `article.teams.filter(organization_id=access_scheme.organization_id).exists()` — if not → `CrossOrgPayloadError` (HTTP 403).
6. Validate per-article scalar fields if present (`access` in `Articles.ACCESS_OPTIONS`, `kind` in `Articles.KINDS`, `retracted` boolean) → `FieldNotFoundError` with a specific message on each.
7. Upsert `ArticleOrgContent` for `(article, access_scheme.organization)`:
   - `takeaways` and `summary_plain_english`: if key present in payload, set field. Empty string → `None`. Omitted → no change.
8. Update `Articles` scalars if any per-article fields were sent.
9. Save both (article first if changed, then content). Stamp `api_access_scheme` on the history of whichever rows were saved via `_stamp_api_key_on_history`.
10. Return `returnData({'article_id': …, 'doi': …, 'organization_id': …, 'updated_fields': [...]})` with HTTP 200.

Exception handling and `generateAccessSchemeLog(...)` calls in every branch (success + failure) match `post_article`'s pattern exactly. **Every** failure path must log to `APIAccessSchemeLog` with the right `http_code` (per spec §9.1).

### 3.3 `edit_trial` — `django/api/views.py`

Same shape. Differences:

1. Lookup key: payload field `identifier`. Build `{euct, nct, eudract}` mapping the same way `post_article` does (e.g., accept either a flat `identifier` value with `id_type` discriminator, or a nested dict — match what clients already send to `post_article`). Pass through `find_trial_by_identifier(...)` from §1.3.
2. `count() == 0` → `TrialNotFoundError`. `count() > 1` → `DuplicateTrialError(ids=[…])`.
3. Cross-org check via `trial.teams.filter(organization_id=...)`.
4. Editable fields: `takeaways`, `summary_plain_english` only (per spec §4.2). No per-trial scalars.
5. Upsert `TrialOrgContent`. Stamp history.

### 3.4 URL registration — `django/admin/urls.py`

- Add `edit_article, edit_trial` to the `from api.views import …` block ([line 23](django/admin/urls.py#L23)).
- Add `path('articles/edit/', edit_article)` and `path('trials/edit/', edit_trial)` immediately after [line 67](django/admin/urls.py#L67) (`articles/post/`).

---

## Phase 4 — Read serialization changes

**File touched**: `django/api/serializers/__init__.py`.

### 4.1 `ArticleSerializer` ([line 278](django/api/serializers/__init__.py#L278))

- Replace `'takeaways'` and `'summary_plain_english'` in `fields` with `SerializerMethodField` declarations.
- Drop `'takeaways'` from `read_only_fields` (it's now a method field — automatically read-only).
- Add resolvers `get_takeaways(self, obj)` and `get_summary_plain_english(self, obj)` implementing the decision tree from spec §6.2:
  1. If `request.api_access_scheme` is set and has an org → return that org's `ArticleOrgContent` field, else `None`.
  2. Else, if the request has filter param `team__organization=<id>` (or whichever filter the existing `ArticleFilter` exposes — verify in `django/api/filters.py`) AND that org has `OrganizationApiSettings.make_api_public=True` → return that org's content.
  3. Else → omit from response. To omit a field per-instance in DRF, return a sentinel and override `to_representation` to pop it, OR override `to_representation` directly. Use whichever produces cleaner code; the existing `OrgScopedSerializerMixin.to_representation` is a good model.

### 4.2 `TrialSerializer` ([line 305](django/api/serializers/__init__.py#L305))

Same pattern. `Trials.summary_plain_english` exists today; replace with the method field. Add `takeaways` as a brand-new method field (the underlying `Trials` model has no `takeaways` column — it lives only on `TrialOrgContent`).

### 4.3 N+1 mitigation

Add a `prefetch_related(Prefetch('org_contents', queryset=ArticleOrgContent.objects.filter(organization_id=…)))` on `ArticleViewSet.queryset` and `TrialViewSet.queryset` *only when* `request.api_access_scheme` resolves to an org. If not, skip (the field is omitted anyway).

This is the minimum required to avoid a per-row query in list responses. Full `select_related`/`prefetch_related` overhaul is P1 (spec §11).

---

## Phase 5 — Lock down ModelViewSets

**File touched**: `django/api/views.py`.

Add `http_method_names = ['get', 'head', 'options']` to each of these classes (spec §5):

| Viewset | Line |
|---|---|
| `ArticleViewSet` | 386 |
| `CategoryViewSet` | 498 |
| `TrialViewSet` | 755 |
| `SourceViewSet` | 847 |
| `AuthorsViewSet` | 870 |
| `TeamsViewSet` | 1147 |
| `SubjectsViewSet` | 1185 |

Leave `permission_classes = [permissions.IsAuthenticatedOrReadOnly]` unchanged — reads still work as today.

---

## Phase 6 — Remove broken DRF Token endpoint

**Files touched**: `django/admin/urls.py`, `django/admin/settings.py`, new migration.

Spec §8.

1. Delete [django/admin/urls.py:21](django/admin/urls.py#L21) — `from rest_framework.authtoken import views`.
2. Delete [django/admin/urls.py:64](django/admin/urls.py#L64) — `path('api/token/get/', views.obtain_auth_token),`.
3. Delete `'rest_framework.authtoken'` from `INSTALLED_APPS` in `django/admin/settings.py`.
4. Run `docker exec gregory python manage.py makemigrations` — Django should emit a migration removing the app's tables (`authtoken_token`, `authtoken_tokenproxy`). Verify the generated migration drops those tables and nothing else. If Django doesn't auto-detect (it usually does for app removal), write a manual migration with `migrations.DeleteModel` for `Token` and `TokenProxy`, or `migrations.RunSQL("DROP TABLE IF EXISTS authtoken_token CASCADE; DROP TABLE IF EXISTS authtoken_tokenproxy CASCADE;")`.

Sanity test: existing JWT flow at `/api/token/` still works (Phase 8 covers).

---

## Phase 7 — Management command for legacy takeaways

**File touched**: new file `django/gregory/management/commands/migrate_legacy_takeaways.py`.

Spec §7. Required CLI:

```
python manage.py migrate_legacy_takeaways [--org-id <id>] [--noinput] [--dry-run]
```

Behaviour:

- No args → list orgs (`id`, `name`, count of articles linked via any team) and prompt: `Enter target organisation id: `. Re-prompt on invalid input; allow Ctrl-C to exit.
- `--org-id <id>` → use it directly; still prompt for confirmation unless `--noinput` is also passed.
- `--dry-run` → print "Would create N rows" without writing. Compatible with any other flag.
- For each `Articles` row where `takeaways IS NOT NULL AND takeaways != ''`, `ArticleOrgContent.objects.get_or_create(article=a, organization_id=chosen, defaults={'takeaways': a.takeaways})`. Skip silently if exists. Report counts at the end: `Created: N. Skipped (already existed): M.`
- Idempotent: re-running should create zero new rows.
- **Does not** clear `Articles.takeaways` — leave the column populated for now.

Test this command (Phase 8).

---

## Phase 8 — Tests

**Files touched**: new files under `django/api/tests/`.

### 8.1 `tests/test_edit_article.py`

Follow the `_make_org`/`_make_team`/`_make_scheme` helpers from `test_post_article_org_scoping.py`. Cases (per spec §10):

- Successful upsert of `takeaways` for an article in the API key's org → 200, row created.
- Successful update of existing `ArticleOrgContent` → 200, row updated (not duplicated).
- Per-article scalar update (`access='restricted'`, `retracted=True`, `kind='news article'`) → fields persisted on `Articles`.
- DOI not found → 404, `ARTICLE_NOT_FOUND` code in response.
- DOI matches two articles → 409, `DUPLICATE_ARTICLE`, both ids returned.
- Cross-org article → 403, `CROSS_ORG_PAYLOAD`.
- Invalid `access`/`kind` value → 400, `FIELD_NOT_FOUND`-style error.
- No API key → 401 / configured "no key" response.
- API key without org → 403.
- Empty-string `takeaways` clears (`NULL` in DB).
- Historical row created with `api_access_scheme` = the calling key.
- Admin save (no API key, just `obj.save()` in the test) → historical row with `api_access_scheme = NULL`.
- Failed edit (403, 404, 409, 400) → no historical row created, but `APIAccessSchemeLog` row exists with right `http_code`.

### 8.2 `tests/test_edit_trial.py`

Same matrix with trial identifiers and `DUPLICATE_TRIAL`.

### 8.3 `tests/test_per_org_serialization.py`

- API key for org A → `takeaways`/`summary_plain_english` show org A's content.
- API key for org A on an article that has no org A `ArticleOrgContent` → fields are `null` in response.
- Anonymous request → fields **absent** (not just null) from JSON.
- Anonymous request filtered to a `make_api_public=True` org → fields present.
- Anonymous request filtered to a `make_api_public=False` org → fields absent.
- Two orgs editing the same article → each sees its own takeaways.

### 8.4 `tests/test_viewset_lockdown.py`

- `PATCH /articles/<id>/` → 405.
- `PUT /articles/<id>/` → 405.
- `DELETE /articles/<id>/` → 405.
- Same for trials, authors, sources, categories, subjects, teams.
- `GET /articles/<id>/` → 200 (sanity check that reads still work).

### 8.5 `tests/test_migrate_legacy_takeaways.py`

- Run `call_command('migrate_legacy_takeaways', org_id=…, noinput=True)` on a fixture with two articles → one row created.
- Re-run → zero new rows.
- `dry_run=True` → no rows written, output reports the count.
- Invalid `org_id` → command exits non-zero with a clear message.

### 8.6 Run the suite

```
docker exec gregory python manage.py test api
docker exec gregory python manage.py test gregory
```

Both must pass. Fix any regression — don't disable tests.

---

## Verification checklist before opening the PR

- [ ] All eight phases complete and committed.
- [ ] `docker exec gregory python manage.py test` passes.
- [ ] Spec §10 test matrix is covered.
- [ ] `Articles.takeaways` still exists in the DB (not dropped yet).
- [ ] `/api/token/` (JWT) still works.
- [ ] `/api/token/get/` (DRF Token) is gone.
- [ ] `/articles/post/` still works (no regression).
- [ ] Anonymous `GET /articles/` no longer returns `takeaways` or `summary_plain_english`.
- [ ] API-key `GET /articles/` returns the calling org's per-org content only.
- [ ] No new throttling, no relation-editing endpoints (out of scope).

---

## Out of scope reminders (do NOT do these)

- Don't drop `Articles.takeaways`.
- Don't add throttling.
- Don't expose write methods for relations (`teams`, `subjects`, etc.).
- Don't refactor `views.py` into per-resource modules (P2).
- Don't delete `optimized_views.py` (P1).
- Don't move URL config to `django/api/urls.py` (P1).
- Don't add OpenAPI docs (P1).
