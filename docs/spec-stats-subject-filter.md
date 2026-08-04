# Specification — subject filtering on `GET /stats/`

Status: plan, not implemented. Written 2026-08-04.

Adds a `?subject=` filter and a `by_subject` breakdown to the site-wide stats
endpoint (`StatsView`, [django/api/views.py:3715](../django/api/views.py)), so a
client can ask "how much data does team 1 have **for multiple sclerosis**" in one
call instead of scoping to the team and eyeballing the difference.

## 1 — What exists today

`GET /stats/` is a plain `APIView` (not a DRF filterset). It accepts two params,
both CSV-of-ints, both validated against `request.visible_org_ids`:

| Param | Behaviour |
|:------|:----------|
| `team` | Scope to one or more teams. Invisible team → 404. |
| `organization` (alias `org`) | Scope to one or more orgs. Invisible org → 404. Combined with `team`, the effective scope is the **intersection**. |

The flow is: parse → validate visibility → resolve a single `team_id_list`
(one `Team` VALUES query, reused everywhere) → cache lookup on
`stats:<sorted team ids>` → 4 `COUNT DISTINCT` queries + 1 `Sources` VALUES →
build payload → `cache.set(..., settings.STATS_CACHE_TTL)` (default 600s).

Cold-cache budget today: **7 queries** under LocMemCache, pinned by
`StatsQueryCountTest` with a ceiling of 15 (the slack covers production's
`DatabaseCache` GET/SET/cull overhead).

### 1.1 How each count reaches `Subject`

| Count | Relation | Note |
|:------|:---------|:-----|
| articles | `Articles.subjects` M2M (`related_name="articles"`) | independent of `Articles.teams` |
| trials | `Trials.subjects` M2M (`related_name="trials"`) | independent of `Trials.teams` |
| authors | `Authors → articles__subjects` | two-hop |
| subscribers | `Subscribers.subscriptions` → `Lists.subjects` M2M | `Lists` also has a `team` FK |
| sources | `Sources.subject` | **single, nullable FK** — not M2M |

`Subject.team` is a nullable FK, and `(team, subject_slug)` is unique — slugs are
**not** globally unique, which is why this filter takes IDs, not slugs.

### 1.2 Coverage on the dev database

Measured 2026-08-04, dev DB:

| | total | without a subject |
|:--|--:|--:|
| subjects | 7 | — |
| articles | 50,183 | 372 (0.7%) |
| trials | 30,169 | 12,829 (42.5%) |
| sources | 52 | 1 |
| lists | 9 | 2 |

The trials figure matters: any subject-filtered trial count drops ~42% relative
to the team-scoped one, and that is correct, not a bug. Worth a line in the docs
so nobody files it as one.

## 2 — Decisions taken

Confirmed with Bruno before drafting:

1. **Param shape** — `?subject=1,2`, CSV of ints, **OR** semantics (union), matching
   this endpoint's existing `?team=` / `?organization=` style rather than the list
   endpoints' `?subject_id=`. No `subjects_any` / AND variant: on an aggregate
   endpoint an AND across subjects has no obvious meaning.
2. **Sources** — filtered on the `subject` FK. Sources with `subject IS NULL` drop
   out of `sources.total`, `by_type` and `by_domain`.
3. **Subscribers** — filtered via `subscriptions__subjects__in`. Lists with no
   subjects tagged contribute nobody.
4. **`by_subject`** — add it, in the same PR, carrying per-subject `articles`,
   `trials`, `authors` and `sources`. **Not** per-subject subscribers.
5. **No slug form of the filter.** `(team, subject_slug)` is unique, not
   `subject_slug` alone, so a slug filter would only be safe alongside a mandatory
   `?team=`. IDs only; RSS routes stay the one place slugs are addressable.

Two more that follow from existing precedent, taken without asking:

6. **Invisible subject → 404**, judged against `visible_org_ids` only — same as
   `team` and `organization` today, so subject existence isn't leaked.
7. **Visible subject outside the requested team/org scope → zero counts, not 404** —
   mirrors `OrgAndTeamIntersectionTest.test_team_not_in_org_returns_zero_not_404`.
   `?team=1&subject=<subject of team 9>` returns a well-formed all-zero payload.

## 3 — Response shape

Additive. Every existing key keeps its meaning; `by_subject` is new.

```json
{
  "articles": 8420,
  "trials": 133,
  "subscribers": 78,
  "authors": 11902,
  "sources": { "total": 42, "by_type": {...}, "by_domain": [...] },
  "by_subject": [
    { "subject_id": 2, "subject_name": "Multiple Sclerosis", "articles": 8412, "trials": 133, "authors": 11837, "sources": 12 },
    { "subject_id": 5, "subject_name": "Rare Disease",       "articles": 0,    "trials": 0,   "authors": 0,     "sources": 0 }
  ]
}
```

- `by_subject` lists **every in-scope subject**, including those with zero
  articles and zero trials — the payload doubles as the list a UI needs to build
  a subject picker, so silently dropping empty subjects would be worse than the
  extra rows. (`/articles/stats/` and `/trials/stats/` aggregate straight off the
  through table and therefore omit empties; this endpoint deliberately differs.)
- Scope of the list: subjects whose `team_id` is in `team_id_list`, further
  narrowed to the requested `?subject=` set when one is given.
- Ordering: `subject_name` ascending. Stable across calls, which `-count` is not
  when two subjects tie.
- Counts inside `by_subject` are `articles`, `trials`, `authors` and `sources`.
  Per-subject subscribers is deliberately absent (decision 4) — with `Lists` as
  the only path to `Subject`, the number would say more about how lists are tagged
  than about the subject.
- `sources` is a count of **distinct domains**, matching what the top-level
  `sources.total` means. Not a count of feed rows: two RSS feeds on
  `pubmed.ncbi.nlm.nih.gov` are one source there and must be one here too.
- Neither `authors` nor `sources` sums to its top-level total, and that is
  correct, not a reconciliation bug. Both are distinct counts *within* a subject:
  an author writing under two subjects appears in both rows and once at the top,
  and a domain feeding two subjects likewise. Say this explicitly in the API docs
  — it is the first thing anyone will try to add up.

`docs/03-api-and-rss-feeds.md` currently claims the shape is "unchanged across all
filter combinations" — that sentence has to go.

## 4 — Implementation

All in `StatsView.get`, [django/api/views.py:3740](../django/api/views.py).

### 4.1 Parse `?subject=` (after the `?organization=` block, ~line 3774)

Same shape as the two existing parsers, same 400 on a non-integer:

```python
subject_param = request.query_params.get("subject")
subject_ids = None
if subject_param:
    try:
        subject_ids = [int(s.strip()) for s in subject_param.split(",") if s.strip()]
    except ValueError:
        return Response(
            {"error": "Invalid subject parameter. Expected integer or comma-separated integers."},
            status=status.HTTP_400_BAD_REQUEST,
        )
```

### 4.2 Resolve subjects — one query, three jobs

Placed **after** `team_id_list` is resolved (~line 3839) and **before** the cache
lookup. This single query serves visibility validation, count scoping, and the
`by_subject` enumeration:

```python
subj_qs = Subject.objects.all()
if visible_org_ids is not None:
    subj_qs = subj_qs.filter(team__organization_id__in=visible_org_ids)
if subject_ids is not None:
    subj_qs = subj_qs.filter(id__in=subject_ids)
elif team_id_list is not None:
    subj_qs = subj_qs.filter(team_id__in=team_id_list)
visible_subjects = list(subj_qs.values("id", "subject_name", "team_id"))
```

Then, in order:

1. **404 check** — only when `?subject=` was given and the middleware ran:
   `len(visible_subjects) != len(set(subject_ids))` → `raise Http404`. Note the
   predicate is org-visibility only; `team_id` is deliberately *not* in it.
2. **Effective scope** — `effective_subject_ids = [s["id"] for s in visible_subjects
   if team_id_list is None or s["team_id"] in team_id_list]`. When `?subject=` was
   given and this comes out empty, decision 6 applies: return the all-zero payload.
3. **`by_subject` roster** — the same list, filtered the same way, sorted by
   `subject_name`.

Note the `elif` in the query above: when `?subject=` is absent the roster is the
in-scope subjects; when it is present the requested IDs are looked up *without*
the team narrowing, because the team narrowing is what distinguishes 404 from
zero and it has to be applied in Python, after the visibility verdict.

### 4.3 Cache key

**Mandatory** — without it, a `?subject=`-filtered payload poisons the unfiltered
one for `STATS_CACHE_TTL` seconds. Extend line 3842:

```python
cache_key = (
    "stats:"
    + ("all" if team_id_list is None else ",".join(str(i) for i in sorted(team_id_list)))
    + ":subj:"
    + ("all" if subject_ids is None else ",".join(str(i) for i in sorted(set(subject_ids))))
)
```

Key off the **requested** `subject_ids`, not `effective_subject_ids` — two
different requests must not share a key just because both resolved to an empty
effective set.

The new format orphans every existing `stats:*` entry. They expire on their own
within `STATS_CACHE_TTL`; no manual flush, no migration.

### 4.4 Counts

The current code branches on `fully_unscoped` (no middleware, no `?team=`) to use
cheap non-distinct `.count()`. Adding an M2M subject join to that branch makes the
non-distinct count wrong. Rework so the distinct path is taken whenever *either*
a team scope or a subject scope is active:

```python
apply_subject = effective_subject_ids is not None

articles_qs = Articles.objects.all()
if team_id_list is not None:
    articles_qs = articles_qs.filter(teams__in=team_id_list)
if apply_subject:
    articles_qs = articles_qs.filter(subjects__in=effective_subject_ids)
articles_count = (
    articles_qs.count()
    if (team_id_list is None and not apply_subject)
    else articles_qs.values("article_id").distinct().count()
)
```

…and the same for trials (`trial_id`), authors
(`articles__subjects__in`), subscribers (`subscriptions__subjects__in` alongside
the existing `subscriptions__team__in`) and sources (`subject_id__in` — plain FK,
still no distinct needed).

**Keep the team join on every count even when `?subject=` is given.** Dropping it
is a visibility leak: an article belonging only to a non-visible team can be
tagged with a subject the caller *can* see, and `filter(subjects__in=[...])` alone
would count it.

Preserve the `.values(pk).distinct().count()` idiom and its comment (line 3859) —
`.distinct()` over full rows is ~5x slower at prod scale.

### 4.5 `by_subject`

Three grouped queries over the M2M through tables, following `_by_subject_counts`
([django/api/views.py:428](../django/api/views.py)). All three anchor on the
through table and differ only in what they count, so one helper covers them:

```python
def _subject_counts(model, subject_ids, team_id_list, count_field=None):
    through = model.subjects.through
    src = model._meta.model_name  # "articles" / "trials"
    qs = through.objects.filter(subject_id__in=subject_ids)
    if team_id_list is not None:
        qs = qs.filter(**{f"{src}__teams__in": team_id_list})
    return {
        r["subject_id"]: r["count"]
        for r in qs.values("subject_id").annotate(
            count=Count(count_field or f"{src}_id", distinct=True)
        )
    }
```

- articles — `_subject_counts(Articles, ...)`
- trials — `_subject_counts(Trials, ...)`
- authors — `_subject_counts(Articles, ..., count_field="articles__authors")`,
  which walks `articles_subjects → articles → articles_authors` and counts
  distinct author IDs per subject.

Per-subject `sources` needs **no query at all**. The existing domain aggregation
(views.py:3892-3918) already materialises every in-scope source row into Python
with `sources_qs.values("link", "source_for")`; add `"subject_id"` to that
`values()` and accumulate a second dict in the same loop:

```python
source_data = list(sources_qs.values("link", "source_for", "subject_id"))
subject_domains = {}          # subject_id -> set of netlocs
for s in source_data:
    d = extract_domain(s["link"])
    if d:
        ...                    # existing all_domains / type_domains / domain_feed_count
        if s["subject_id"] is not None:
            subject_domains.setdefault(s["subject_id"], set()).add(d)
```

`len(subject_domains.get(sid, ()))` is then the per-subject count, distinct by
domain and therefore consistent with `sources.total` by construction. Sources with
a NULL subject are skipped here, exactly as decision 2 already skips them from the
filtered totals.

Zip the four dicts against the roster, defaulting to 0. Skip the three group-by
queries when the roster is empty.

**Measured on dev** (50,183 articles, 428,022 article-author rows, all teams, all
7 subjects): articles group-by **0.04s**, authors group-by **0.31s**. The authors
query is by a wide margin the most expensive thing on this endpoint — acceptable
only because it sits behind the 600s cache. Re-measure it under `?team=`-scoped
conditions on prod-sized data before merging, and if it degrades, fall back to a
correlated subquery in the shape of `author_articles_count_subquery`
([django/api/views.py:2612](../django/api/views.py)) rather than widening the
group-by.

### 4.6 Query budget

| | queries |
|:--|--:|
| today, cold, LocMemCache | 7 |
| \+ subject resolution (§4.2) | 1 |
| \+ `by_subject` group-bys — articles, trials, authors (§4.5) | 3 |
| \+ `by_subject` sources — folded into the existing Python pass | 0 |
| **new total** | **11** |

Under the existing ceiling of 15, but that ceiling was sized for production's
`DatabaseCache` overhead on a 7-query baseline. Raise it to 18 and rewrite the
breakdown comment in `StatsQueryCountTest.test_scoped_call_query_budget`
(test_visibility_stats.py:440) — leaving a stale comment there is worse than the
number itself.

Query *count* is not the concern here — query *cost* is. The authors group-by
alone is 0.31s on dev (§4.5), roughly as much wall time as the other ten put
together. `EXPLAIN ANALYZE` it on a prod-sized copy before merging (§6).

## 5 — Files to change

| File | Change |
|:-----|:-------|
| `django/api/views.py` | `StatsView.get` — §4.1 to §4.5; extend the class docstring's Filters section with `?subject=` and the 404-vs-zero rule |
| `django/api/tests/test_visibility_stats.py` | new test classes (§6); raise the query ceiling and fix its breakdown comment |
| `docs/03-api-and-rss-feeds.md` | endpoint table row (line 194) → add `subject`; Stats section (lines 322-360) → new example URLs, `by_subject` in the sample payload, `subject` row in the filter-parameter table, drop the "unchanged across all filter combinations" claim, note the 42%-of-trials-have-no-subject caveat |

No migration. No model change. No new dependency.

## 6 — Tests

New in `test_visibility_stats.py` (its `_make_subject` / `_make_article` /
`_make_trial` helpers already exist; `_make_article` needs a `subjects=` kwarg,
`_make_trial` already takes one).

**`SubjectFilterStatsTest`**
- `?subject=<id>` scopes articles, trials, authors, subscribers and sources
- `?subject=1,2` unions rather than intersects
- `?subject=abc` → 400, message matches the team/org wording
- an article tagged with the subject but assigned to no team is excluded when
  `?team=` is given — the §4.4 leak guard
- a source with `subject IS NULL` drops out of `sources.total` (decision 2)
- a list with no subjects contributes no subscribers (decision 3)

**`SubjectVisibilityStatsTest`**
- subject in a hidden org → 404, anonymous and API-key callers
- subject in a public org → 404 without `include_public`, 200 with it
- `?subject=<visible>,<hidden>` → 404 (mixed request fails whole, as with orgs)

**`SubjectTeamIntersectionStatsTest`**
- `?team=1&subject=<subject of team 9>` → 200, all counts zero, `by_subject: []`
- `?team=1&subject=<subject of team 1>` → the team's numbers

**`SubjectStatsCacheTest`**
- `/stats/?team=1` and `/stats/?team=1&subject=2` do not share a cache entry —
  the regression this whole section exists to catch
- two different `?subject=` values do not share one
- `?subject=2,1` and `?subject=1,2` **do** share one (sorted key)

**`BySubjectFacetTest`**
- lists every in-scope subject including zero-count ones
- ordered by `subject_name`
- respects `?subject=`
- excludes subjects of non-visible orgs even when a visible article is tagged with
  one — the leak `_by_subject_counts` already guards against upstream
- per-subject `authors` counts each author once per subject: an author with two
  articles under the same subject counts 1, and an author writing under two
  subjects appears in both rows while counting once in the top-level `authors`
- per-subject `sources` counts distinct **domains**: two feeds on one domain under
  the same subject count 1, and a domain feeding two subjects appears in both rows
  while counting once in `sources.total`
- a source with `subject IS NULL` appears in no `by_subject` row
- no `subscribers` key inside `by_subject` rows (decision 4) — pin it so it
  doesn't get added by accident

Plus: extend `StatsQueryCountTest` with a `?subject=`-scoped budget case.

Run:

```bash
docker exec gregory python manage.py test api.tests.test_visibility_stats
```

Then the full suite before committing, per repo practice on broad changes.

Manual check against prod-sized data, on dev:

```bash
docker exec gregory python manage.py shell -c "from django.test import Client; c=Client(); print(c.get('/stats/', {'team':1,'subject':1}).json())"
```

## 7 — Open questions

1. Should `by_subject` be suppressible (`?by_subject=false`) for callers that only
   want the totals? At 3 queries — one of them the 0.31s authors group-by — this
   is more defensible than it was at 2. Decide after the prod-scale measurement in
   §4.5: if the authors query holds under half a second, leave it always-on.

Everything else is settled; §2 has the decisions and their reasons.
