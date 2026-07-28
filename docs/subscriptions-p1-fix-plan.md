# Plan: fix the P1 subscription bugs

Execution plan for the P1 findings in
[subscriptions-audit-2026-07.md](subscriptions-audit-2026-07.md) — correctness
of the shared email content organizer.

P0 and its follow-up are deployed (`f8cc70b4`). All five P1 findings were
re-verified against current code on 2026-07-28 and still stand:
`templates/emails/components/content_organizer.py` was untouched by the P0 work
apart from lint and format commits.

## Preconditions

- Branch off `main` before touching anything.
- Tests from `django/`: `pytest subscriptions`. Baseline is 284 passing.
- Lint and format on the host: `uvx ruff check django/` and `uvx ruff format django/`. Note `templates/emails/components/content_organizer.py` lives outside `django/subscriptions/`, so widen the path when checking.
- One design decision is needed before tasks 4 and 5 can be scoped. It is stated below; raise it, do not pick it.

---

## Read this first: the featured/regular split does nothing visible

Three of the five findings (5, 6, and the related P3 N+1) exist only to serve a
featured/regular article split. That split has no visual effect in either
template that uses it:

- `templates/emails/weekly_summary.html:31-42` loops `articles`, then loops `additional_articles`, including the identical `article_card_simple.html` both times.
- `templates/emails/admin_summary.html:29-44` loops `articles`, then `additional_articles`, including `article_card.html` with an identical `{% with %}` parameter set both times.

The HTML comments claim a distinction ("High-Confidence Articles (Featured)",
"Articles Needing Review (Additional)") but there is no heading, separator, or
styling difference between the two loops. The split's only observable effect is
ordering: high-confidence articles appear before the rest, each group sorted by
discovery date.

Every per-article ML query in `_filter_high_confidence` and `_get_max_ml_score`
is therefore paid to reorder a flat list.

### Decision required

Before tasks 4 and 5 are scoped, decide what the split is for:

- Option A — make it visible. Add section headings to both templates so "high confidence" and "needs review" are distinguishable, then fix the split's correctness (tasks 4 and 5 as written below). This is the larger change and the only one that justifies the query cost.
- Option B — delete it. Have the organizer return one list sorted by relevance then date, drop `_filter_high_confidence` and `_get_max_ml_score`, and let the commands' existing ranking decide order. This closes findings 5 and 6 and the P3 N+1 in a single simplification, and changes nothing a subscriber sees.

Recommendation: option B for the weekly digest, option A for the admin summary.
The admin summary exists so a human can triage, and "needs review" versus
"already high-confidence" is exactly the distinction that email is for — it is
worth surfacing there. The weekly digest is a reading list where the split is
invisible and the ordering barely matters.

Tasks 1 to 3 are independent of this decision. Start there.

---

## Task 1 — recruitment status is matched on the raw string

Highest-value fix in P1: mechanical, well-evidenced, and the normalized field it
needs already exists.

### The bug

`content_organizer.py:102-112` splits trials with
`"recruit" in str(t.recruitment_status).lower()`. That substring also matches
`Not Recruiting`, `NOT_YET_RECRUITING`, `ACTIVE_NOT_RECRUITING`,
`Ongoing, recruitment ended` and `Authorised, recruitment pending`.

Measured on 2026-07-28 across the trials that currently reach an email, after
the P0 staleness filter: **58 of 99 trials are misclassified** — the same ~59%
error rate the original audit found on the unfiltered set.

The consequences are `featured_trials` ordering,
`content_stats.recruiting_trials` (`content_organizer.py:356`) and
`has_recruiting_trials` (`content_organizer.py:542`).

### The change

`Trials.recruitment_status_normalized` is a canonical field maintained by
`gregory.utils.trial_field_normalizers` and recomputed on every save. Both trial
card templates already key their status colours off it
(`trial_card.html:46`, `trial_card_simple.html:47`), so the organizer is the
last place still parsing the raw string.

In `organize_trials` (`content_organizer.py:67`), split on
`recruitment_status_normalized == "recruiting"` instead. Do not fall back to a
substring match on the raw value when the normalized field is empty — a NULL
normalized status means the normalizer did not recognise the raw value, and
guessing is what produced this bug. Treat it as not-recruiting.

### Tests

Add `django/subscriptions/tests/test_content_organizer_trials.py`:

- each of `not_recruiting`, `not_yet_recruiting`, `active_not_recruiting` lands in `regular_trials`, not `featured_trials` — these are the regressions and must fail against current code
- `recruiting` lands in `featured_trials`
- a trial with `recruitment_status_normalized` unset is treated as not recruiting regardless of what the raw string says
- `content_stats.recruiting_trials` counts only genuinely recruiting trials

Use the raw/normalized pairs from the real data as fixtures: `RECRUITING`,
`Recruiting`, `Ongoing, recruiting`, `Authorised, recruiting` map to
`recruiting`; `Not Recruiting`, `NOT_YET_RECRUITING`, `ACTIVE_NOT_RECRUITING`,
`Ongoing, recruitment ended`, `Authorised, recruitment pending` do not.

### Docs

- `docs/subscriptions.md` — note that trial featuring keys off `recruitment_status_normalized`, cross-referencing `docs/trials-field-normalization.md`.

---

## Task 2 — the blanket except sends empty emails

### The bug

`content_organizer.py:637` wraps the whole of `prepare_optimized_context` in
`except Exception`. Any error inside returns `_get_fallback_context`, which has
`articles: []`, no `additional_articles`, no `latest_research` and no
`org_content_map`.

P0 fixed half of this by accident: because the commands now record only what the
rendered context actually contained, a fallback render records nothing, so the
old failure mode where `send_admin_summary` marked unsent articles as sent is
gone.

What remains is that the email is still sent, and it is empty. The weekly digest
renders its "No New Content This Week" block and delivers it. Only
`send_trials_notification.py:193` guards against empty rendered content;
`send_weekly_summary` and `send_admin_summary` do not.

### The change

Two parts, both needed:

1. Narrow the exception handling in `prepare_optimized_context`. Let programming errors propagate — the caller now handles exceptions and records a `FailedNotification`, which is strictly better than silently mailing an empty digest. If a fallback is kept at all, it must be for a specific, named, expected failure, not `Exception`.

2. Add the same empty-content guard the trials command already has to `send_weekly_summary` and `send_admin_summary`: if `articles_to_be_sent` and `trials_to_be_sent` are both empty after render, skip the send, log at ERROR, and write a `FailedNotification`. A digest with no content is never worth delivering, whatever the cause.

Note the interaction with `render_within_limit`: an exception raised inside
`_render` now propagates through the shrink loop to the command. Make sure the
commands' existing `try/except requests.RequestException` around `send_email`
is not mistaken for covering the render — it is not, and the render call needs
its own handling.

### Tests

Add to `django/subscriptions/tests/`:

- when `get_optimized_email_context` raises, the weekly digest does not send, records a `FailedNotification`, and records no `SentArticleNotification`
- same for the admin summary
- a context that legitimately organizes to zero articles and zero trials does not produce a send
- the existing "no content" early-skip before subscriber iteration still short-circuits, so this guard is a backstop and not the primary path

---

## Task 3 — Latest Research does not implement its own definition

### What the section is meant to be

Decided by Bruno on 2026-07-28: Latest Research means **new articles found since
the last email**, grouped by category. It is a delta, not a standing digest, and
it is deliberately articles-only.

Two things this settles:

- repetition is a bug, not intended behaviour. An earlier draft of this plan assumed a standing-digest reading and scoped the fix accordingly; that was wrong.
- trials are out of scope for this section by decision, not by oversight. `TeamCategory` does carry trials (`Trials.team_categories`, `related_name="trials"`), so the relation is there and a future reader will notice it is unused here. Do not "fix" that — record the decision in the docs so it stays settled.

### The bug

`content_organizer.py:548-562` builds the section by calling
`get_latest_research_by_category` (`utils/subscription.py:72`), which hardcodes
`days=30` and 20 articles per category. Measured against the definition above:

- it selects by a fixed 30-day window rather than by what the subscriber has already been sent, so the same articles reappear week after week. Verified on 2026-07-28: the single article currently in MS Weekly Digest's section has already been sent to at least one subscriber of that list.
- it ignores the list's `lookback_days`, unlike every other content query since the P0 follow-up
- it is not deduplicated against the main article list, so one article can appear twice in the same email
- its articles are excluded from `org_content_map` (built at `content_organizer.py:611` from the main lists only), so per-organisation takeaways never render for them
- its articles are excluded from `content_stats`
- it cannot be shrunk by `render_within_limit`, because the section is rebuilt from `list_obj` inside the organizer on every render rather than passed in as a list

That last point is new since P0 and is the reason this is worth doing now rather
than later: the size backstop halves `articles` and `trials`, re-renders, and if
an oversized `latest_research` section is what pushed the body over the limit,
the loop cannot converge and the send fails permanently on the give-up path.

Current exposure is small — only MS Weekly Digest has a category configured, and
it contributes 1 article — so this is a latent trap rather than a live failure.

### The change

The delta definition means Latest Research needs exactly the bookkeeping the
main content already has. Rather than inventing a parallel mechanism, move the
section onto the existing one.

1. Build the section in the command, not inside the organizer. `prepare_optimized_context` has no reason to run its own queries, and building it alongside `unsent_articles` gives it the subscriber context it needs, makes it shrinkable by `render_within_limit`, and lets its articles flow into `articles_to_be_sent` for recording. The organizer keeps `organize_latest_research_by_category` as pure formatting over data it is handed.

2. Filter by what the subscriber has already been sent, using the existing `SentArticleNotification` table keyed on `(article, list, subscriber)` — the same exclusion the main content applies. One table and one key for both sections means an article shown in either is suppressed from both next time, which is what "since the last email" requires.

3. Keep a lookback window as a floor, passed in rather than hardcoded, so a newly-subscribed person's first email is not unbounded. Use the list's `lookback_days`, mirroring the `days` parameter added to `get_trials_for_list` in the P0 follow-up. The primary filter is the sent-record exclusion; the window is a safety net.

4. Deduplicate against the main section within a single email: exclude articles already in `context["articles"]` or `context["additional_articles"]`. The main section wins, since it is the list's primary subject-matched content.

5. Include the section's articles in `org_content_map` so per-organisation takeaways render.

Leave `get_latest_research_by_category` returning articles only, and leave the
template's Latest Research block without a trial loop. That is the decision, not
an omission.

Leave `content_stats` alone unless the counts are shown somewhere — check the
templates before adding to it.

### Watch for this

Recording Latest Research items in the same tables as the main content changes
what the main content sees on the next run: an article surfaced only in Latest
Research this week will be excluded from the main section next week. That is
correct under the delta definition — the subscriber has already seen it — but it
is a behaviour change worth stating in the commit message, because it means the
two sections now compete for the same items rather than drawing independently.

Order the build so the main section is selected first and Latest Research fills
in around it, matching the dedup rule in step 5.

### Tests

- an article already recorded in `SentArticleNotification` for this subscriber and list does not appear in Latest Research — this is the regression for the verified case above and must fail against current code
- an article appearing in Latest Research is recorded as sent, and is absent from the next run's section
- a category whose only new content is trials contributes nothing to the section, and the section is omitted when no category has new articles
- an article in both the main list and a Latest Research category renders once, in the main section
- the section honours `lookback_days` rather than a fixed 30 days
- an oversized Latest Research section is shrunk by `render_within_limit` rather than failing the send
- `org_content_map` covers Latest Research articles

### Docs

- `docs/subscriptions.md` — state the definition plainly: Latest Research is new **articles** since the subscriber's last email for that list, grouped by category, tracked through the same `SentArticleNotification` table as the main content. The absence of this sentence is what allowed a 30-day window to stand in for it. Say explicitly that trials are excluded by design, so the unused `TeamCategory.trials` relation does not read as an oversight to the next person.
- `docs/02.1-database-tables-and-fields.md` — no schema change expected; confirm before assuming.

---

## Task 4 — the list's ml_threshold is ignored (gated on the decision)

`prepare_optimized_context` accepts `confidence_threshold`
(`content_organizer.py:435`, applied at 461-462) but no caller anywhere passes
it. Verified by grep across `django/subscriptions/` and `django/templates/`: the
only occurrences are the parameter itself and the hardcoded
`self.confidence_threshold = 0.8` at `content_organizer.py:26`.

So article selection honours each list's `ml_threshold` while the
featured/regular split always uses 0.8.

Current impact is zero: all nine lists are set to `ml_threshold = 0.8`, so the
hardcoded default coincides with every configured value. This is latent, and it
will surface the first time anyone tunes a list.

- under option A, pass `confidence_threshold=digest_list.ml_threshold` from `send_weekly_summary` and the admin summary equivalent, and add a test asserting a list at 0.6 features an article the 0.8 default would not.
- under option B, delete the parameter and the attribute along with the split.

Do not fix this by changing the default from 0.8 to something else. The bug is
that the configured value is not consulted, not that the constant is wrong.

---

## Task 5 — relevance checks are not scoped to the list's subjects (gated on the decision)

`_filter_high_confidence` (`content_organizer.py:247`) checks
`article_subject_relevances.filter(is_relevant=True)` across every subject, and
`_get_max_ml_score` (`content_organizer.py:289`) scans all of
`ml_predictions_detail` for any subject and any team. An article can therefore be
featured in one team's digest on the strength of a relevance judgement or ML
prediction belonging to a different team's subject.

The manual-review query in `send_weekly_summary` is scoped to
`digest_list.subjects`, so the selection and presentation halves of the same
pipeline disagree.

Current impact is small: only two subjects have `auto_predict=True` (Multiple
Sclerosis and Dihydroartemisinin) and both belong to Team Gregory, so no
cross-team leak is possible today. It grows with every subject that enables
auto-prediction.

- under option A, thread the list's subjects into both methods and filter on them. `send_admin_summary` already demonstrates the pattern — it prefetches `ml_predictions_detail` filtered to `subject__in=list_subjects` into `filtered_ml_predictions`, and `_filter_high_confidence` already prefers that attribute when present. Extending the same prefetch to the weekly digest would fix the scoping and the N+1 together.
- under option B, both methods are deleted and the finding closes with them.

---

## Out of scope

- P2 robustness and P3 performance findings from the audit, except where option B closes a P3 item as a side effect. If it does, say so in the commit message rather than expanding scope.
- the article-side staleness guard noted as a follow-up in the P0 plan.

## Definition of done

- `pytest subscriptions` passes; each new test fails when its fix is reverted
- `pytest` full suite passes
- `uvx ruff check django/` passes, and `uvx ruff format --check django/` reports nothing new
- the featured/regular decision is recorded in `docs/subscriptions.md`, whichever way it goes — the next reader should not have to re-derive that the split is invisible
- audit findings 4 to 8 are annotated with their fix status in `docs/subscriptions-audit-2026-07.md`
