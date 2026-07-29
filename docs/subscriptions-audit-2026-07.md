# Subscriptions audit — July 2026

Review of the `subscriptions` app, focused on newsletter sending and the shared
email content organizer. No code was changed; this is a planning document.

Scope of the read:

- `django/subscriptions/` — models, admin, views, forms, management commands
- `django/templates/emails/components/content_organizer.py` — the shared organizer
- `django/templates/emails/` — templates and the staff preview views

Findings marked "confirmed against the dev DB" were verified with read-only
queries on 2026-07-28. Counts are point-in-time.

---

## Priority 0 — failing in production today

Neither of these self-heals, and both are silent from the outside.

### 1. Trial notification emails exceed Postmark's 5 MB body limit

**Status: fixed in code, pending deploy.** `Lists.trial_max_age_days` (default
90) filters `get_trials_for_list` against the trial's own registration/
publication date; `Lists.trial_limit` mirrors `article_limit` and now applies
to all three email types; `subscriptions.utils.email_limits.render_within_limit`
shrinks and re-renders as a backstop below Postmark's hard limit, recording
only what was actually rendered as sent. See Task B in
[subscriptions-p0-fix-plan.md](subscriptions-p0-fix-plan.md) and
[subscriptions.md](subscriptions.md#content-limits-and-staleness-filtering).

Follow-up closed 2026-07-28: `send_weekly_summary` built its own inline trials
query and never called `get_trials_for_list`, so `trial_max_age_days` did not
apply to weekly digests — the count cap alone bounded payload size but could
not stop a bulk import of historical trials from being presented as new
content (15 trials from the 2026-07-06 import, some registered as early as
1999, reached the MS Weekly Digest's 88 subscribers under "New Clinical
Trials"). `get_trials_for_list` now takes a `days` parameter and the weekly
digest calls it with its own `lookback_days`, so the staleness filter and the
per-list lookback window both apply to all three email types.

`FailedNotification` holds 413 rows of `ErrorCode: 300, Invalid 'HtmlBody' value.
It should be up to 5242880 characters in length` — all on the Alzheimer Disease
list, 28 per day, every day from 2026-07-06 to 2026-07-21 (confirmed against the
dev DB).

Nothing caps the number of trials on the way into the email:

- `management/commands/utils/subscription.py:9` — `get_trials_for_list` returns every trial in a 30-day window. For that list: 3,570 trials.
- `management/commands/send_trials_notification.py:120` — `new_trials` is passed through untruncated.
- `templates/emails/components/content_organizer.py:114` — `# Include ALL trials - don't limit content for subscribers`. The `max_trials_per_email = 999` at line 29 is never read.

The failure is self-perpetuating: the send fails, so no `SentTrialNotification`
rows are written, so the next run rebuilds the identical 3,570-trial payload and
fails again. It cannot recover without manual intervention.

The trigger was a bulk import: 3,501 of those trials share
`discovery_date = 2026-07-06`, and the first failure is 2026-07-06 17:18. Their
registration dates span 1999 to 2026, so `discovery_date` records when GregoryAI
first saw the row, not how old the trial is — see
[subscriptions-p0-fix-plan.md](subscriptions-p0-fix-plan.md) for the numbers and
the staleness filter that follows from them.

`article_limit` is only honoured by the weekly digest, so `send_admin_summary`
has the same exposure — MS Admin currently renders 1,218 articles plus 366 trials
into a single email.

### 2. "Unsubscribe from all lists on <site>" is a silent no-op

**Status: fixed in code, pending deploy.** The `scope == "site"` filter now
matches `list__site_id`; the view reports how many rows were actually
deactivated so a no-op request can no longer render success. Incident record
in
[incidents/2026-07-28-site-scope-unsubscribe-not-honoured.md](incidents/2026-07-28-site-scope-unsubscribe-not-honoured.md)
(open — see that record's open items for what's still outstanding before it
can be closed). See Task A in
[subscriptions-p0-fix-plan.md](subscriptions-p0-fix-plan.md).

`templates/emails/components/footer.html:92` builds the link from the resolved
site id, which comes from `Lists.site`. `subscriptions/views.py:341` filters on
`list__team__site_id` — a different, nullable FK.

Confirmed against the dev DB: all 9 lists have `list.site = 3`
(brain-regeneration.com), while `team.site` is `1` or `None`. The filter matches
zero rows, no subscription is deactivated, and the subscriber is still shown the
"you have been unsubscribed" confirmation page.

`docs/subscriptions.md` documents the intent as "Remove from all lists on a
site", so the implementation is simply reading the wrong field. It should be
`list__site_id`.

### 3. Hard-bounced and suppressed recipients are retried indefinitely

**Status: fixed in code, pending deploy.**
`subscriptions.utils.postmark.classify_postmark_response` centralises response
parsing (and fixes the falsy-`Response` truthiness bug in `send_admin_summary`
along the way); a 406 now deactivates the subscriber globally via
`subscriptions.utils.suppression.deactivate_subscribers`, the same helper the
admin "Disable all emails" action uses. A `requests.RequestException` from
`send_email` no longer aborts the rest of the run. Still reactive-only — a
bounce webhook and reactivation flow remain out of scope. See Task C in
[subscriptions-p0-fix-plan.md](subscriptions-p0-fix-plan.md) and
[subscriptions.md](subscriptions.md#bounce-and-suppression-handling).

440 rows of `ErrorCode: 406 … marked as inactive`; one address has been retried
210 times, several others 20–60 times (confirmed against the dev DB, spanning
2026-05-08 to 2026-07-21).

Nothing consumes Postmark's suppression state — there is no bounce webhook, no
`List-Unsubscribe` handling, and no path that flips `Subscribers.active` or a
`ListSubscription` on a hard bounce or spam complaint. Because a failed send also
skips the sent-record write, the same recipient is retried on every subsequent
run. Repeated sends to suppressed addresses damage sender reputation, and they
are the bulk of the 853 `FailedNotification` rows.

---

## Priority 1 — content organizer correctness

### 4. The recruiting/other trial split is wrong for most trials

`content_organizer.py:102-112` matches the substring `"recruit"` against the raw
`recruitment_status`. That also matches `Not Recruiting`, `NOT_YET_RECRUITING`,
`ACTIVE_NOT_RECRUITING`, `Ongoing, recruitment ended` and
`Authorised, recruitment pending`.

Confirmed against the dev DB:

| Bucket | Trials |
|:-------|:-------|
| Genuinely recruiting | 2,739 |
| Wrongly classified as recruiting | 3,882 |

So roughly 59% of the "recruiting" bucket is wrong. It drives `featured_trials`
ordering, `content_stats.recruiting_trials` and `has_recruiting_trials`.

`Trials.recruitment_status_normalized` already exists and the trial card
templates already key off it — the organizer was never updated when the field
landed. The fix is `recruitment_status_normalized == "recruiting"`.

**Status: fixed 2026-07-28.** `organize_trials` now splits on
`recruitment_status_normalized == "recruiting"`; a `NULL` normalized status
(the normalizer didn't recognise the raw value) is treated as not-recruiting
rather than guessed from the raw string. See
[subscriptions.md](subscriptions.md#content-limits-and-staleness-filtering)
and `subscriptions/tests/test_content_organizer_trials.py` (9 regression
cases covering every misclassified raw string above).

### 5. Per-list `ml_threshold` is ignored by the organizer

`prepare_optimized_context` accepts a `confidence_threshold` argument
(`content_organizer.py:435`), but `send_weekly_summary.py:627` never passes it.
The featured/regular split therefore always uses the hardcoded `0.8` from
`content_organizer.py:26`.

Article *selection* honours the list's `ml_threshold`; article *presentation*
does not. A list configured at 0.6 or 0.9 silently gets 0.8 ordering.

**Status: closed 2026-07-28, two different ways per email type** — see the
design decision recorded in
[subscriptions.md](subscriptions.md#featuredregular-article-split-design-decision-2026-07-28).
The weekly digest's featured/regular split was invisible in the template (no
heading or styling distinguished the two loops), so it was **deleted**
outright rather than fixed: `_organize_weekly_articles` now always returns a
single flat list, and `confidence_threshold` is moot for that email type. The
admin summary's split drives real triage, so it was **fixed**:
`send_admin_summary` now passes `confidence_threshold=admin_list.ml_threshold`.
See `subscriptions/tests/test_admin_summary_relevance_scoping.py`.

### 6. Relevance checks are not scoped to the list's subjects

- `content_organizer.py:247` — `_filter_high_confidence` checks `article_subject_relevances.filter(is_relevant=True)` across every subject.
- `content_organizer.py:289` — `_get_max_ml_score` scans all of `ml_predictions_detail`, any subject, any team.
- `send_weekly_summary.py:318` — `is_ml_relevant_any_subject` iterates every `auto_predict` subject on the article rather than the list's subjects.

The manual-review query immediately above (`send_weekly_summary.py:303`) *is*
scoped to `digest_list.subjects`, so the two halves of the same selection
disagree with each other.

Confirmed against the dev DB: only 1 article is currently affected, because just
two subjects have `auto_predict=True` and both belong to the same team. The blast
radius grows with every subject that enables auto-prediction.

**Status: partially closed 2026-07-28.** The two organizer methods are moot
for the weekly digest (deleted along with the split, finding 5). For the
admin summary, `_get_max_ml_score` was already correctly scoped —
`send_admin_summary` prefetches `ml_predictions_detail` filtered to
`subject__in=list_subjects` into `filtered_ml_predictions`, which the method
prefers when present — confirmed by regression test rather than changed.

`Articles.is_ml_relevant_any_subject` was the one part left open when this
annotation was first written, and it closed shortly afterwards in `41f15c2a`,
`9ca78c98` and `c0c1e80f`: the method now takes an optional `subjects`
argument, and `send_weekly_summary` passes the digest list's own subjects so
an article can no longer qualify on the strength of an unrelated team's
`auto_predict` subject. Regression test in
`gregory/tests/test_is_ml_relevant_any_subject_scoping.py`. Finding 6 is
therefore fully closed, not partial — the heading above is kept as written for
history.

### 7. A blanket `except` turns any bug into a silently empty email

`content_organizer.py:637` wraps the whole of `prepare_optimized_context`. On any
exception it returns `_get_fallback_context`, which has `articles: []` and no
`additional_articles`, `latest_research` or `org_content_map`.

- The weekly digest then renders the "No New Content This Week" block (`weekly_summary.html:91`) and sends it.
- `send_admin_summary.py:197` records every article in `new_articles` as sent regardless of what actually rendered, so one transient error permanently suppresses those articles for that subscriber.

**Status: fixed 2026-07-28.** The blanket `except`/`_get_fallback_context` was
deleted; `prepare_optimized_context` now lets exceptions propagate.
`send_weekly_summary` and `send_admin_summary` each wrap their render call in
their own `try/except`, recording a `FailedNotification` and skipping the
send rather than mailing an empty digest or crashing the whole run. Both
commands also gained a post-render guard: organizing to zero articles, zero
trials (and, for the weekly digest, zero Latest Research items) skips the
send with a logged `FailedNotification` instead of delivering nothing. See
`subscriptions/tests/test_render_error_handling.py`.

### 8. The Latest Research section sits outside all the bookkeeping

`content_organizer.py:550` calls `get_latest_research_by_category`
(`management/commands/utils/subscription.py:49`), which hardcodes 30 days and 20
articles per category, ignoring `lookback_days`. Those articles:

- are not deduplicated against the main article list, so one article can appear twice in the same email
- are never recorded in `SentArticleNotification`, so they repeat every week. Confirmed a defect: the section means "new articles since the last email", grouped by category (Bruno, 2026-07-28), so repetition is not intended behaviour.
- the section is articles-only by decision (Bruno, 2026-07-28). `TeamCategory` also carries trials via `Trials.team_categories`, and that relation is deliberately unused here — it is not an oversight to fix.
- are excluded from `org_content_map` (built at `content_organizer.py:611` from the main lists only)
- are excluded from `content_stats`
- add unbounded weight to the payload, feeding finding 1

**Status: fixed 2026-07-28.** Latest Research now implements the definition
recorded in
[subscriptions.md](subscriptions.md#latest-research-section-weekly-digest):
new articles since the subscriber's last email, grouped by category, tracked
through the same `SentArticleNotification` table as the main content. The
section is built in `send_weekly_summary` (not inside the organizer, which
now only formats a pre-filtered `{TeamCategory: [Articles]}` map), honours
`lookback_days` as a floor, deduplicates against the main section per render
attempt, flows through `render_within_limit`'s shrink loop, and is included in
`org_content_map`. `content_stats` was deliberately left alone — no template
displays a Latest Research count. See
`subscriptions/tests/test_latest_research_delta.py`.

---

## Priority 2 — robustness

### 9. No exception handling around any `send_email` call

`send_weekly_summary.py:872`, `send_admin_summary.py:173`,
`send_trials_notification.py:169`. A single connection reset or timeout aborts
the whole cron run: remaining subscribers and remaining lists get nothing, and
no `FailedNotification` is written, so the failure is invisible.

**Status: closed in P0, confirmed still closed 2026-07-28.** All three send
commands wrap their `send_email` call in `except requests.RequestException`
and write a `FailedNotification` before continuing to the next subscriber. No
code change needed here — annotating only, since this carried no status block
before and so still read as open.

### 10. The falsy-`Response` fix was never backported

`send_weekly_summary.py:895` carries an explicit comment about
`requests.Response.__bool__` being `self.ok`, and normalises around it.
`send_admin_summary.py:185` still does `if result and result.status_code == 200`,
so every 4xx/5xx falls to the else branch and logs `HTTP Status No Response`,
and the 422-detail extraction at line 222 is unreachable.

Latent rather than live: that list has one subscriber and no failures recorded
yet.

**Status: closed in P0, confirmed still closed 2026-07-28.** All three
commands route the Postmark response through
`subscriptions.utils.postmark.classify_postmark_response`, which never tests
the response for truthiness (see the docstring warning about
`requests.Response.__bool__`). No remaining `if result and result.status_code`
truthiness check exists outside the comments explaining the trap. Annotating
only, same reason as finding 9.

### 11. `sent_at` is never refreshed

`send_weekly_summary.py:915` uses `get_or_create`, which returns the existing row
untouched when one exists. Deduplication looks back 30 days
(`send_weekly_summary.py:456`) but `lookback_days` allows up to 365. Set any list
above 30 and articles in the overlap are re-sent on every run indefinitely.

Currently masked — all lists are at `lookback_days = 30` — so this is a
configuration landmine rather than a live bug.

**Status: closed 2026-07-28, as a side effect of the Latest Research fix
(finding 8).** Flagged by PR review on the Latest Research change: reusing
the same 30-day-capped sent-record exclusion for Latest Research made this
landmine reachable through a second path. `send_weekly_summary`'s
`threshold_date` is now `max(30, days_to_look_back)`, so the sent-record
lookback is always at least as wide as the content lookback window, for both
the main section and Latest Research. See
`subscriptions/tests/test_latest_research_delta.py::SentRecordLookbackWindowTest`.

### 12. Announcements send synchronously inside the admin request

`admin.py:1913`. Two problems:

- A list large enough to exceed the HTTP timeout leaves `status = "sending"`, and `send_view` refuses anything in `("sent", "sending")` — unrecoverable from the UI.
- A single failure sets `status = "failed"`, which *is* re-sendable, and the retry loops over all subscribers again without filtering on existing successful `AnnouncementRecipient` rows. Everyone who already received it gets a duplicate.

**Status: fixed 2026-07-28**, in two commits — see
[subscriptions-p2-fix-plan.md](subscriptions-p2-fix-plan.md#task-1--announcement-sending-never-got-the-p0-treatment).

1. `subscriptions.utils.announcement_send.send_announcement` now routes the
   Postmark response through `classify_postmark_response` (a 406 deactivates
   the subscriber and is recorded as `suppressed=True`, not a plain failure,
   so it no longer flips the whole send to `failed`), skips any subscriber
   who already has a successful `AnnouncementRecipient` row (the fix for the
   duplicate-retry trap), narrows the blanket `except` to
   `requests.RequestException`, and recomputes `recipients_count` /
   `failures_count` from `AnnouncementRecipient` rows on every run instead of
   incrementing counters. A new "Reset stuck 'Sending' announcements back to
   Draft" admin action recovers an announcement left in `sending` by a
   crashed run.
2. The admin "Send" action no longer calls Postmark itself: it validates and
   sets `status = "queued"`, returning immediately. A new
   `send_announcement` management command (cron-driven, see
   `docs/cookbook.md#how-do-i-send-queued-announcements`) picks up queued
   announcements and performs the actual send, so a large announcement can no
   longer be killed mid-flight by nginx's 60s or gunicorn's 300s request
   timeouts.

Two prod announcements (#9, #12) were sitting in `failed` purely because of
suppressed recipients; both can now be safely retried without duplicating any
of the deliveries that already succeeded. See
[subscriptions.md](subscriptions.md#announcement-send-lifecycle) for the full
status lifecycle.

**Follow-up fixed 2026-07-28:** the `send_view` confirmation page reported
`len(all_subscribers)` — the full audience, computed before the skip logic
above runs — as both the displayed count and the button's send count.
Against live data this overstated the actual send: announcement #12 said 181
but would send to 6 (177 already delivered), #9 said 181 but would send to
44 (176 already delivered). Nothing unsafe happened — it overstates rather
than understates — but it's the one number an operator needs to judge
whether a retry is worth doing. Fixed by subtracting subscribers with an
existing successful `AnnouncementRecipient` row from the confirm count and
showing both figures — new recipients and already-received/will-be-skipped —
separately. See [subscriptions.md](subscriptions.md#announcement-send-lifecycle)
for the resume-semantics note this surfaced (resuming mails everyone
currently subscribed who hasn't received it, including subscribers who
joined after the original send) and
`subscriptions/tests/test_announcement_confirm_count.py`.

### 13. `lookback_days` only half-works

`send_admin_summary` and `send_trials_notification` go through
`get_articles_for_list` / `get_trials_for_list`, which hardcode 30 days. Only the
weekly digest reads the field.

**Fully closed 2026-07-28.** `get_articles_for_list` now takes a `days`
parameter (mirroring `get_trials_for_list` and `get_latest_research_by_category`),
and all three send commands pass the list's own `lookback_days` at every
remaining call site: `send_admin_summary.py` for both its articles and trials
queries, and `send_trials_notification.py` for its trials query. Decided by
Bruno on 2026-07-28: `lookback_days` applies to all three email types — it
sits in the same "Content Settings" admin fieldset as `article_limit` and
`trial_limit`, both of which already apply everywhere, so a knob that
silently ignores you there is worse than one that doesn't exist. This changes
no behaviour today (every list is at `lookback_days = 30`, matching the old
hardcoded value) — it only takes effect once someone edits the field.

Widening the content window this way reopens finding 11 unless the
sent-record exclusion window widens with it.
`send_admin_summary`/`send_trials_notification` now compute
`threshold_date = now() - timedelta(days=max(30, lst.lookback_days))`, the
same guard the weekly digest already uses, so an item inside a widened
content window can never fall outside a narrower exclusion window. See
`subscriptions/tests/test_lookback_days_all_email_types.py`.

---

## Priority 3 — performance

**Re-measured against current code on 2026-07-28** before work started, since
P1 deleted the featured/regular split and changed which code is still on the
hot path. The re-measurement found a different shape than this section
originally described (a genuine N+1, but on the *authors* relation, not via
`_filter_high_confidence`, which P1 already made unreachable — see finding 5
above) and a much larger cost for the per-subscriber priority loop than
first estimated. Findings below are annotated individually.

- `content_organizer.py:468` prefetches `ml_predictions__subject` (the M2M), but every consumer reads `ml_predictions_detail` (the reverse FK). The prefetch is wasted and `_filter_high_confidence` runs roughly 3 queries per article per subscriber. It is skipped entirely once `unsent_articles` becomes a list after truncation.

  **Status: fixed 2026-07-28, but not the way originally described.**
  `_filter_high_confidence` was already unreachable by the time this was
  re-measured — it was only ever called from `_organize_trial_notification_articles`,
  and trial notifications never pass `articles=` to the organizer, so both
  methods were dead code and were deleted outright (see
  `subscriptions/tests/test_trial_notification_articles_unreachable.py`,
  which pins the unreachability before the deletion). The wasted
  `ml_predictions__subject` prefetch was deleted too, along with the
  `try/except Exception: pass` blocks that had wrapped both prefetch
  attempts — with the fix below, the callers into `prepare_optimized_context`
  now only ever pass an unsliced QuerySet or an already-materialized list,
  never a sliced-but-still-QuerySet, so the exception path was unreachable as
  well.

  The real, still-live N+1 turned out to be on `authors`, not
  `ml_predictions`: `content_organizer.py:437`'s prefetch guard
  (`hasattr(articles, "prefetch_related")`) is skipped once
  `send_weekly_summary`/`send_admin_summary` truncate `articles` to a plain
  Python list via `article_limit`, so `article_card.html`'s
  `article.authors.exists()` / `.all()` cost two queries per article, per
  subscriber, on exactly the truncated-list path that matters. Fixed by
  moving the `authors` prefetch into both commands, applied to the queryset
  *before* truncation, so the prefetch cache survives slicing and
  materialization. Measured: MS Weekly Digest (88 subscribers, 15-article
  `article_limit`) drops from ~32 queries to ~4 per subscriber render, ~2,816
  → ~350 queries across the list. See
  `subscriptions/tests/test_weekly_summary_authors_prefetch.py` and
  `subscriptions/tests/test_admin_summary_authors_prefetch.py`.

- `send_weekly_summary.py:317` calls `is_ml_relevant_any_subject` across the whole window — 1,242 articles for MS Weekly Digest — at about 2 queries each.

  **Status: left open, deliberately.** Re-measured at ~2,484 queries and ~2s,
  once per list per run (not per subscriber, so it doesn't scale with
  audience). Two seconds doesn't justify a rewrite on its own, and the
  candidate replacements (`api.filters._get_ml_relevant_articles_query`, the
  denormalized `Articles.relevant` flag, `gregory.relevance.recompute_article_relevance`)
  aren't proven to select the identical article set as this loop — swapping
  in one of them without a test pinning the current selection risks a silent
  change in which articles a subscriber sees. No such pinning test exists
  yet, so this was left alone rather than guessed at.

- `send_weekly_summary.py:564` recomputes ML priority scores inside the per-subscriber loop (88 subscribers on that list).

  **Status: fixed 2026-07-28 — and larger than this line suggested.** This
  cost was never included in the original per-subscriber measurement above
  (that measurement started from an already-built article list); measured on
  its own against MS Weekly Digest's 1,219-article candidate pool, it came to
  ~2,440 queries and ~0.82s **per subscriber**, none of which varies by
  subscriber — manual-review status and ML consensus count depend only on
  the article and the list's threshold. Across 88 subscribers that's roughly
  158,000 queries and ~72 seconds of identical, repeated work every run.
  Fixed by computing `article_priority_scores` once per list (a handful of
  batched queries) and having the per-subscriber truncation step look up
  precomputed scores instead of recomputing them. Re-measured after the fix:
  4 queries / ~0.28s once per list, then 1 query / ~0.05s per subscriber —
  roughly 92 queries and ~4.5s total for the same 88-subscriber list, versus
  ~158,000 queries and ~72s before. See
  `subscriptions/tests/test_weekly_summary_priority_scores_shared.py`.

Worth knowing before optimising: the featured/regular split buys nothing
visually. `weekly_summary.html:31-42` renders both groups with the identical card
component, back to back. The split only affects ordering.

**Not attempted:** batching or parallelising the Postmark HTTP calls
themselves. At 0.3–1.0s each, sequential sends remain the dominant cost even
with every finding above fixed (26–88s for MS Weekly Digest's 88
subscribers) — but changing send concurrency interacts with suppression
handling, per-recipient error attribution, and Postmark rate limits, and
deserves its own change with its own testing rather than folding into a
performance clean-up.

---

## Smaller items

- `send_trials_notification.py:132` never passes `organization=`, so `org_content_map` is empty and Key Takeaways never render in trial emails. The warning at `content_organizer.py:626` only fires for `weekly_summary` and `admin_summary`, so it is silent.
- `templates/emails/views.py:160` (staff preview) also omits `organization`, and ignores `article_sort_order` — it takes the newest N by date, so a relevancy-mode digest previews as something no recipient will ever receive.
- `admin.py:1680` hardcodes `privacy_policy_url` and `terms_url` to `""`, so those footer links disappear for announcements only.
- `admin.py:1932` deduplicates announcement recipients by email and attributes each to the first list encountered, so the footer unsubscribe link covers only that one list.
- `management/commands/mark_all_as_sent.py` is `subscribers × lists × all articles` individual `get_or_create` calls, and iterates `subscriber.subscriptions.all()`, which includes lists the subscriber has opted out of.

---

## Suggested order of work

1. Findings 1 and 2 — both are failing silently today and neither recovers on its own. Finding 1 needs a hard cap on trials (and on admin-summary articles) plus a size guard before the Postmark call; finding 2 is a one-field fix plus a regression test.
2. Finding 3 — decide on a suppression strategy (Postmark bounce webhook, or deactivate after N consecutive 406s) so the retry loop closes.
3. Finding 4 — mechanical, well covered by the existing normalization work.
4. Findings 7 and 9 — replace the blanket `except` with a narrow one and stop recording sends that did not happen. These change failure behaviour, so they want tests first.
5. Findings 5, 6, 8, 13 — consistency work on the organizer; group into one pass.
6. Finding 12 — needs a design decision (move announcement sending to a management command or a task queue) rather than a patch.
7. Priority 3 — after the correctness work, since fixing 6 changes which queries are needed.
