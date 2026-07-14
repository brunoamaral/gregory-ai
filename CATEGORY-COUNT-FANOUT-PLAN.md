# Category count fan-out — validation & fix plan

**Date:** 2026-07-14
**Symptom:** prod stuck, `BuffileRead` waits, `/categories/` and `CategoriesByTeamAndSubject`
calls taking 3.5+ min; unrelated `/articles/` queries hung too (same Postgres, saturated disk I/O).

## 1. Root cause — VALIDATED

The team's diagnosis is correct. Confirmed in code:

- `CategoryViewSet.get_queryset` — [django/api/views.py:1454](django/api/views.py:1454)
- `CategoriesByTeamAndSubject.get_queryset` — [django/api/views.py:2366](django/api/views.py:2366)

Both annotate `TeamCategory` with **two joined `Count(..., distinct=True)` in one query**:

```python
.annotate(
    article_count_annotated=Count("articles", distinct=True),
    trials_count_annotated=Count("trials", distinct=True),
)
```

### Why it fans out

`articles` and `trials` are both **independent M2M relations** on `TeamCategory`:

- `Articles.team_categories` → through `articles_team_categories` ([models.py:559](django/gregory/models.py:559))
- `Trials.team_categories` → through `trials_team_categories` ([models.py:771](django/gregory/models.py:771))

Annotating both in one query makes Postgres JOIN *both* through tables to `teamcategory`
simultaneously. There is no join condition between articles and trials, so the intermediate
result is the **Cartesian product** `articles × trials` per category. `COUNT(DISTINCT ...)`
then de-dupes back down — but only *after* the product has been materialised.

For a category like "Myelin" (~7,300 articles) with even a few hundred trials, the
intermediate is millions of rows. The hash-aggregate for the two `DISTINCT` counts spills to
disk → the `BuffileRead` wait event, and minutes per call.

### Why it runs twice per request

Both viewsets are paginated (`CategoryViewSet` uses the default `PageNumberPagination`,
`PAGE_SIZE=10`, [admin/settings.py:248](django/admin/settings.py:248)). DRF pagination calls
`.count()`, which — because the queryset carries GROUP BY annotations plus `.distinct()` —
wraps the whole thing in `SELECT COUNT(*) FROM (<fan-out query>)`. So the expensive
aggregation executes **once for the count and once for the page**.

### Why unrelated endpoints hung

Two concurrent fan-out requests saturate disk I/O on the shared Postgres instance. Every other
query (including `/articles/`) then queues on the same starved disk. This is consistent with
the reported system-wide stall.

### The handover.md contradiction

`handover.md:174` ("annotation with `Count(..., distinct=True)` is fine now", from #747/#749) is
**only half true**. A single `Count(distinct=True)` over one relation does not fan out. The
regression at #747/#749 was the org-visibility DISTINCT-count pagination bug — a different code
path. Two joined M2M `Count(distinct=True)` in the same query was never actually load-tested
against a large category. The in-code comment at [views.py:1446-1453](django/api/views.py:1446)
repeats this incorrect reassurance and should be corrected.

## 2. Fix hypothesis

**Replace the two joined `Count(distinct=True)` annotations with two independent correlated
scalar subqueries over the through tables.** Each subquery counts rows in one through table for
the current category — no cross-join, no product, no DISTINCT needed (the through tables have
`unique_together (articles/trials, teamcategory)`, so a plain `COUNT(*)` per category already
equals the distinct count).

```python
from django.db.models import Count, IntegerField, OuterRef, Subquery
from django.db.models.functions import Coalesce
from gregory.models import ArticleCategoryAssignment, TrialCategoryAssignment

def _count_subquery(through_model):
    return Coalesce(
        Subquery(
            through_model.objects
            .filter(teamcategory=OuterRef("pk"))
            .order_by()
            .values("teamcategory")
            .annotate(c=Count("*"))
            .values("c"),
            output_field=IntegerField(),
        ),
        0,
    )

queryset = queryset.annotate(
    article_count_annotated=_count_subquery(ArticleCategoryAssignment),
    trials_count_annotated=_count_subquery(TrialCategoryAssignment),
)
```

### Why this fixes both problems

1. **No fan-out.** Each subquery touches exactly one through table and returns a scalar.
   Postgres runs them as two independent correlated index scans on the `teamcategory_id` FK —
   O(rows in that category), not O(articles × trials). No hash-aggregate spill → no
   `BuffileRead`.
2. **Cheap pagination count.** The subqueries are select-only (not referenced in any
   `filter`/`order_by` for the count query). Django's `get_count`/`get_aggregation` strips
   select-only annotations, and the outer query no longer has a GROUP BY, so pagination's
   `.count()` collapses to a plain `SELECT COUNT(*) FROM teamcategory WHERE ...`. The
   fan-out no longer runs twice — it doesn't run at all in the count path.
3. **Contract preserved.** The serializer reads `obj.article_count_annotated` /
   `trials_count_annotated` ([serializers/__init__.py:155-167](django/api/serializers/__init__.py:155)),
   with a `.count()` fallback for un-annotated instances. Field names are unchanged, so the
   serializer, the `ordering_fields = [... "article_count_annotated" ...]`
   ([views.py:1400](django/api/views.py:1400)) ordering, and the API JSON shape all stay the same.
   Ordering by a scalar subquery annotation works in Postgres.

### `.distinct()`

`CategoryViewSet` ends with `.distinct()` ([views.py:1459](django/api/views.py:1459)) because the
optional `subjects__id=` filter joins the subjects M2M and can duplicate category rows. Keep
`.distinct()` — but it now de-dupes only the small outer category set, not a fan-out. (Optionally
tighten to `.distinct("id")` / restructure the subjects filter as an `Exists`, but that is not
required for the fix.)

## 3. Implementation steps

1. Add the `_count_subquery` helper (module-level in `api/views.py`, or a small shared util).
2. Swap the annotation block in **both** `CategoryViewSet.get_queryset` (views.py:1454) and
   `CategoriesByTeamAndSubject.get_queryset` (views.py:2366).
3. Rewrite the stale comment at views.py:1446-1453 to describe the fan-out and why subqueries
   are used.
4. Import `ArticleCategoryAssignment`, `TrialCategoryAssignment` in views.py (verify not
   already imported).

## 4. Verification

- **Existing regression test** `api/tests/test_category_count_annotations.py` must still pass
  (counts = 5 articles / 3 trials; query count < 20). Update its annotated-queryset mirror
  (test lines 96-101) to use the same subquery form so the test reflects production.
- **New load-shape assertion:** add a test with a category holding many articles *and* several
  trials, and assert the response returns without materialising the product. Best signal is an
  `EXPLAIN` check (no `Seq Scan` hash-agg over the join) or a `CaptureQueriesContext` assertion
  that no query contains a join of both `articles_team_categories` and `trials_team_categories`.
- **Manual EXPLAIN on prod-like data:** run `EXPLAIN (ANALYZE, BUFFERS)` on the old vs. new
  queryset for the "Myelin" category. Confirm the `BuffileRead` / `temp read` disk spill is gone
  and cost drops from minutes to milliseconds.
- Smoke-test `/categories/?category_id=<myelin>` and
  `/categories/<team>/<subject>/` end-to-end; confirm counts match the old values.

## 5. Follow-ups (out of scope for the hotfix)

- Correct `handover.md:174`.
- Audit for any other multi-relation `Count(distinct=True)` in one `.annotate()` across the
  codebase (`grep -n "distinct=True" django/api/views.py`).
- Consider a short statement-level `statement_timeout` on read endpoints so a single pathological
  query can't saturate disk for the whole instance again.
