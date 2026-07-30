# Predictions written by the pipeline never updated the denormalized fields

`Articles.relevant` and `Articles.ml_score` are both maintained by a `post_save`
signal on `MLPredictions`. The ML pipeline creates predictions with
`bulk_create`, which does not fire `post_save`. Neither field was updated
by a pipeline run since the feature shipped — only by manual backfills.

Found 2026-07-30 while checking whether three implementations of the ML
consensus rule agree. They do. The denormalized columns they feed did not.

## Root cause

`gregory/management/commands/predict_articles.py` writes predictions with:

```python
MLPredictions.objects.bulk_create(prediction_instances, ignore_conflicts=True)
```

`bulk_create` bypasses `save()` and therefore `post_save`. The receiver at
`gregory/signals.py` — which calls both the ml_score recompute and
`recompute_article_relevance` — never ran for pipeline output.

The signal does fire for individual `.save()` calls, which is what admin edits
and the test suite do. That is why this looked correct everywhere except
production.

## The fix

- [gregory/relevance.py](../django/gregory/relevance.py) now has a scoped
  `recompute_article_ml_scores(article_ids=None)`, alongside
  `recompute_article_relevance`, both backed by a single SQL `UPDATE` guarded
  with `IS DISTINCT FROM` so the reported changed-row count is real and a
  full-table pass is cheap when nothing changed.
- [gregory/management/commands/backfill_ml_scores.py](../django/gregory/management/commands/backfill_ml_scores.py)
  is a thin wrapper over `recompute_article_ml_scores`, mirroring how
  `refresh_article_relevance` wraps `recompute_article_relevance`.
- [gregory/signals.py](../django/gregory/signals.py)'s `_recompute_article_ml_score`
  (the per-article signal path) now calls the same scoped function with a
  single-element list, so the per-article and bulk paths cannot drift apart.
- [gregory/management/commands/predict_articles.py](../django/gregory/management/commands/predict_articles.py)
  calls both recomputes for the batch's article IDs immediately after
  `bulk_create`, guarded by `not dry_run`.

## Ongoing health check

With the write-time fix in place, drift should not recur. Two things watch
for it anyway, on the assumption that some future write path will bypass the
recompute the same way `predict_articles` did:

1. **Weekly cron** — `backfill_ml_scores` and `refresh_article_relevance` run
   every Sunday at 04:30/04:40 and report a changed-row count. See
   [cookbook.md](cookbook.md#how-do-i-check-whether-the-denormalized-ml-fields-are-still-in-sync).
   Weekly rather than nightly is deliberate: a frequent sweep would mask this
   exact class of bug as a few hours of staleness instead of surfacing it.
   Zero rows changed is the passing result; non-zero is a bug report.

2. **Admin summary email** — computes the same drift live (not from the last
   cron run) and renders it as a line near the "Admin Actions" box on every
   send:
   - articles with predictions but a NULL `ml_score`
   - articles the live consensus query says are relevant but whose `relevant`
     flag disagrees, in both directions (the reverse direction should always
     be zero — a non-zero there means something different: a wrong rule, not
     staleness)

   These numbers are global, not scoped to a particular digest list; with more
   than one `admin_summary` list configured, the same figures would appear in
   each send.

   The email only sends when there are articles or trials to review, so this
   is a slow-moving signal, not a guaranteed heartbeat — acceptable given the
   weekly cron already covers that case.

## Out of scope

- The test binding the three consensus implementations together
  (`Articles.is_ml_relevant_for_subject`, `api.filters.ml_relevant_articles_q`,
  `gregory.relevance.recompute_article_relevance`). They agree today and
  were not the cause of this bug — see
  [subscriptions-remaining-work.md](subscriptions-remaining-work.md) Task 8.
- Any change to the consensus rule itself.
- The `ml_score` NULL-sorting behaviour in the API (`nulls_last`). It was
  correct; it was just being fed stale data.
