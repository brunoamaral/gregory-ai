# Plan: P3 performance, plus the announcement confirm-count fix

Execution plan for the P3 findings in
[subscriptions-audit-2026-07.md](subscriptions-audit-2026-07.md), plus one
follow-up from the P2 review that belongs here rather than in its own change.

P0, its follow-up, P1 and P2 are all deployed (`e37cee38`). Every P3 finding was
re-measured against current code on 2026-07-28 before this plan was written,
because P1 deleted the featured/regular split and that changed which code is
still on the hot path.

## Read this first: the measurements, and what they mean

The audit described P3 as "N+1 queries". That is true, but the shape is
different from what the audit assumed, and the size is smaller. Measured on the
dev database:

| Measurement | Result |
|:------------|:-------|
| `send_weekly_summary --dry-run`, all 4 digest lists, 129 subscribers | 8.2s total |
| Per-subscriber context build + template render (15 articles, 15 trials) | 32 queries, 209ms |
| Of those 32 queries | 15 `articles_authors` + 15 `authors`, 1 `organizations_organization`, 1 `gregory_articleorgcontent` |
| Extrapolated to MS Weekly Digest's 88 subscribers | ~2,816 queries, ~18.4s |
| Postmark HTTP for those same 88 sends, at 0.3–1.0s each | 26–88s |
| `is_ml_relevant_any_subject` selection loop, 1,242 articles | ~2,484 queries, ~2s, once per list per run |

Two conclusions follow, and they should shape how much effort this gets:

- **The network dominates.** Even with every N+1 fixed, a weekly digest run stays bounded by 26–88s of sequential Postmark calls. This work buys scaling headroom, not a faster send today.
- **The dry-run total is misleadingly low.** 8.2s covers 129 subscribers because most of them were skipped for having no new content — the per-subscriber cost is only paid for subscribers who actually receive an email. The worst case is a list where everyone has content: a new list, a resumed backlog, or a list whose `lookback_days` was just widened.

So: do task 1, which is most of the cost and a genuine scaling risk. Do tasks 2
and 3, which are deletions. Treat tasks 4 and 5 as measure-first. Task 6 is
unrelated to performance and is folded in because it is small.

## Preconditions

- Branch off `main` before touching anything.
- Tests from `django/`: `pytest subscriptions`. Baseline is 2,828 across the full suite.
- Lint and format on the host: `uvx ruff check django/` and `uvx ruff format django/`.
- Measure before and after with `CaptureQueriesContext`, not by intuition. Every claim in the table above is reproducible; add the same measurement to the PR description.

---

## Task 1 — the authors N+1 is 30 of the 32 per-subscriber queries

### What is actually happening

`content_organizer.py:437` guards its prefetch with
`hasattr(articles, "prefetch_related")`. After `article_limit` truncation the
commands pass a **list**, not a queryset, so that check is False and the prefetch
block is skipped entirely. The article card template then does
`{% if article.authors.exists %}` and `{% for author in article.authors.all %}`
per card, which is two queries per article, per subscriber.

This is not the "wrong relation name" the audit described — that is task 2. This
is the prefetch not running at all on the path that matters.

### The change

Apply `prefetch_related("authors")` to the article queryset **before** truncation
in `send_weekly_summary`, so the resulting list of model instances carries a
populated `_prefetched_objects_cache`. Slicing a prefetched queryset and
evaluating it preserves the cache, and later slicing of the Python list by
`render_within_limit` preserves it too.

Do the same wherever `send_admin_summary` builds its article list. Note it
already prefetches `ml_predictions_detail` into `filtered_ml_predictions` and
must keep doing so — add `authors` alongside, do not replace it.

Verify the fix by asserting query counts rather than by eyeballing:
`assertNumQueries` around a single-subscriber render, before and after.

### Expected result

Per-subscriber render drops from 32 queries to roughly 4. At 88 subscribers that
is ~2,816 queries down to ~350.

### Tests

- `assertNumQueries` on a single-subscriber weekly digest render with N articles, asserting the count does not scale with N — this is the regression, and it must fail against current code
- the same for the admin summary
- authors still render correctly in the email body, including the ORCID link branch

---

## Task 2 — delete the prefetch that nothing reads

`content_organizer.py:440` prefetches `"ml_predictions__subject"`. `ml_predictions`
is the M2M on `Articles`; every consumer in the organizer reads
`ml_predictions_detail`, the reverse FK from `MLPredictions.article`. Nothing
reads the prefetched relation.

On the queryset path it costs two wasted queries per render. On the list path it
never runs at all. Either way it is dead.

Delete it. Keep `"authors"` — task 1 depends on that prefetch existing somewhere,
though after task 1 the organizer's own attempt becomes a no-op safety net rather
than the primary mechanism.

While there: the surrounding `try/except Exception: pass` blocks
(`content_organizer.py:443` and `:451`, both carrying `# noqa: S110`) exist to
swallow prefetch failures on sliced querysets. Once the prefetch happens in the
command, check whether these are still needed at all. Prefer deleting them over
keeping a silenced bare except.

---

## Task 3 — delete `_filter_high_confidence`, now unreachable

P1 removed the featured/regular split from the weekly digest, leaving
`_filter_high_confidence` called from exactly one place:
`_organize_trial_notification_articles` (`content_organizer.py:186`).

That method is unreachable. `send_trials_notification` calls
`get_optimized_email_context` with `trials=` and no `articles=`
(`send_trials_notification.py:151`), so `organize_articles` receives
`Articles.objects.none()`, `has_articles` is False, and it returns the empty
early-exit dict at `content_organizer.py:49` before any email-type dispatch
happens.

Confirm that reading, then delete `_filter_high_confidence` and
`_organize_trial_notification_articles` together. If deleting them feels
risky, that is a signal the reachability argument needs a test rather than a
comment — add one asserting a trial notification produces no articles, then
delete.

`_get_max_ml_score` must stay. It is still live for the admin summary via
`_organize_admin_articles` and `_sort_by_ml_score`, and it is already efficient
there because `send_admin_summary` prefetches `filtered_ml_predictions`, which
the method prefers when present.

---

## Task 4 — the selection loop, measure before optimising

`send_weekly_summary.py:335` calls `is_ml_relevant_any_subject` once per article
across the whole lookback window: ~2,484 queries and ~2s for MS Weekly Digest's
1,242 articles. It runs once per list, not per subscriber, so it does not scale
with audience.

Two seconds is not worth a risky rewrite. But the same consensus logic already
exists as a set-based query in `api.filters._get_ml_relevant_articles_query` and
in `gregory.relevance.recompute_article_relevance`, and `Articles.relevant` is a
denormalized flag maintained by signals for exactly this. If one of those can
replace the loop without changing which articles qualify, take it — the win is
correctness convergence (one definition of relevance instead of three) more than
speed.

Precondition for touching this: a test that pins the current selection for a
fixture set, so any rewrite is provably equivalent. Without that, leave it alone
and say so in the PR.

---

## Task 5 — the per-subscriber priority loop, unmeasured

`send_weekly_summary.py:626` ranks articles inside the per-subscriber loop, doing
`article.subjects.filter(auto_predict=True)` and
`article.ml_predictions_detail.filter(...)` per article. It only runs when
`articles_count > article_limit`, which is the normal case for MS Weekly Digest.

This was **not** included in the 32-query measurement above — that measurement
started from a pre-built list. So the real per-subscriber cost is 32 plus
whatever this adds, and nobody has measured it.

Measure it first. If it is small, leave it. If it is comparable to task 1, the
fix is the same shape: the ranking inputs do not vary by subscriber, only the
candidate set does, so the ML scores can be computed once per list and reused
across subscribers.

---

## Task 6 — the announcement confirm page overstates the audience

Not a performance item. Folded in from the P2 review because it is small and
touches the same area.

### The bug

`admin.py:2007` sets `"total_subscribers": len(all_subscribers)` — the full
audience, computed before the idempotent skip logic in
`subscriptions.utils.announcement_send.send_announcement` runs. The confirm page
and button therefore report the wrong number on exactly the case P2's resume
feature was built for.

Measured against current data:

| Announcement | Button says | Would actually send to | Already received it |
|:-------------|:------------|:-----------------------|:--------------------|
| #12 | 181 | 6 | 177 |
| #9 | 181 | 44 | 176 |

It overstates rather than understates, so nothing unsafe happens — but it is the
one number an operator needs to decide whether a retry is sane.

### The second, subtler part

#9 would send to 44 people, but only 12 of those were delivery failures. The
other ~32 subscribed *after* the original April send, and would receive a
three-month-old announcement. That follows correctly from the resume rule
("skip anyone already successfully sent to", not "retry only failures"), but it
is surprising and is documented nowhere.

### The change

1. Subtract subscribers with a successful `AnnouncementRecipient` from the confirm count, so the button reflects what will actually be sent.
2. Split the figure on the confirm page — new recipients, and already-received-and-will-be-skipped — so the fan-out to newer subscribers is visible before queueing rather than discovered afterwards.
3. Document the resume semantics in `docs/subscriptions.md`: re-queueing an announcement sends to everyone currently subscribed who has not already received it, which includes people who joined since the original send.

### Tests

- the confirm context for an announcement with existing successful recipients reports the post-skip count, not the full audience
- an announcement with no prior recipients reports the full audience unchanged
- the skipped count is reported separately and matches the successful `AnnouncementRecipient` rows

---

## Do not do these

- Do not batch or parallelise the Postmark calls as part of this work. It is the dominant cost, but changing send concurrency interacts with suppression handling, per-recipient error attribution, and rate limits — it deserves its own change with its own testing, not a performance clean-up.
- Do not add caching to the organizer. The per-subscriber variation is real (unsubscribe tokens, greeting, org content), and a cache keyed correctly would save little once task 1 lands.
- Do not sweep the ~130 repo-wide `ruff format` failures into this branch.

## Definition of done

- `pytest subscriptions` passes; each new test fails when its fix is reverted
- `pytest` full suite passes
- `uvx ruff check django/` passes, and `uvx ruff format --check django/` reports nothing that was not already failing
- the PR description carries before/after query counts from `CaptureQueriesContext` for tasks 1 and 5, using the same method as the table at the top of this plan
- audit findings in the P3 section carry status annotations, including any deliberately left open with the reason
- `docs/subscriptions.md` documents announcement resume semantics
