# Plan: subscriptions cleanup — the ready-to-go items

Seven items from [subscriptions-remaining-work.md](subscriptions-remaining-work.md)
that need no further decisions. Independent of each other; ship them separately
or together, but keep tasks 1 and 6 in their own commits — task 1 changes what
recipients see, task 6 changes what they receive.

Not in scope: the bounce webhook and the P3 selection loop stay parked.

## Preconditions

- Branch off `main` before touching anything — CI deploys everything merged there.
- Tests from `django/`: `pytest subscriptions`. Baseline is 2,836 across the full suite.
- Lint and format on the host: `uvx ruff check django/` and `uvx ruff format django/`.

---

## Task 1 — one unsubscribe link per list in announcements

### The problem

`subscriptions/utils/announcement_send.py` deduplicates recipients by email and
keeps the first list encountered (`all_subscribers[email] = (sub, lst)`), so the
footer renders a single "Unsubscribe from this list" link pointing at whichever
list happened to come first. For an announcement sent to several lists, "this
list" is ambiguous — the recipient cannot tell which subscription they are
leaving.

Decided by Bruno on 2026-07-29: render one link per list, so there is nothing to
guess.

### Current shape of the data

Every announcement sent so far targeted exactly one list — Project News or TEST
— so the ambiguity has not bitten yet. It would immediately: of the 6 active TEST
subscribers, 5 are also on Project News, so a two-list announcement gives those
5 an unlabelled link covering one of their two subscriptions.

### The change

1. Collect every list a subscriber matched, not just the first. Keep the deduplication — a subscriber on three of the announcement's lists must still receive one email, not three.

2. Pass those lists into the render context for the footer. `AnnouncementRecipient.list` stays a single FK recording which list they were attributed to; no migration is needed. The link set is a render concern, not a storage one.

3. Extend `templates/emails/components/footer.html` without breaking the digests. The footer is shared by every email type, and the three digest commands pass a single `list_id`. Add an optional `unsubscribe_lists` context variable holding `(list_id, list_name)` pairs:
	- when `unsubscribe_lists` is present, render one "Unsubscribe from <list name>" link per entry
	- otherwise fall back to the existing single-`list_id` behaviour unchanged

	Naming the list in the link text is the point of the change — "Unsubscribe from this list" repeated three times would be worse than one ambiguous link.

4. Leave the site-scope and global links exactly as they are. They already work and cover the "get me off everything" case.

### Tests

- an announcement to two lists renders two named unsubscribe links for a subscriber on both, and one for a subscriber on only one
- a subscriber on several of the announcement's lists still receives exactly one email
- a weekly digest, admin summary and trial notification render unchanged — one link, existing wording. This is the regression guard for the shared footer.
- each rendered link resolves to the right `list_id`

### Docs

- `docs/subscriptions.md` — note that announcement footers list every relevant subscription, unlike digests which are single-list by nature.

---

## Task 2 — pass `organization` in trial notifications

`send_trials_notification.py` calls `get_optimized_email_context` without
`organization=`, unlike the other two send commands, so `org_content_map` is
always empty. The missing-organization warning in `content_organizer.py` only
covers `weekly_summary` and `admin_summary`, so nothing reports it.

Decided by Bruno on 2026-07-29: trial notifications do not need Key Takeaways,
but pass the organization anyway so all three send paths resolve
organisation-scoped context identically, and a future template addition does not
silently render nothing.

- pass `organization=team.organization` — already in scope, used for `get_postmark_credentials` in the same block
- add `trial_notification` to `_ORG_EXPECTED_TYPES` so the warning covers all three

Tests: a trial notification context carries a populated `org_content_map`; the
warning fires when `organization` is omitted for a trial notification.

---

## Task 3 — restore the announcement footer's legal links

`announcement_send.py` hardcodes `privacy_policy_url` and `terms_url` to `""`,
so those two footer links disappear for announcements only. Every other email
type populates them from `CustomSetting`, and every neighbouring key in the same
dict already reads from `custom_settings`.

This reads as an oversight rather than a decision. Populate them the same way as
the surrounding keys. If there turns out to be a reason they were blanked,
write it down instead of restoring them.

Tests: an announcement rendered with a `CustomSetting` carrying both URLs
includes both links.

---

## Task 4 — make the staff preview resemble what gets sent

`templates/emails/views.py` builds preview context without `organization`, and
ignores `article_sort_order` — it takes the newest N by date regardless of the
list's configuration. A relevancy-mode digest therefore previews as something no
recipient will ever receive, which defeats the point of a preview.

Bring it in line with `send_weekly_summary`: pass `organization`, honour
`article_sort_order`, and apply the same `lookback_days`, limits, and staleness
filter the command uses.

The selection logic is now duplicated between the command and the preview, and
this drift is the direct result. Extract it into a shared helper rather than
copying the command's logic a second time — a third copy will drift again. If
extraction turns out to be large, say so and do the smaller fix, but do not
duplicate.

Tests: a preview for a relevancy-mode list returns the same article set the
command would select for the same list and window.

---

## Task 5 — make `mark_all_as_sent` safe or delete it

`management/commands/mark_all_as_sent.py` iterates
`subscribers × lists × every article and trial` with individual `get_or_create`
calls — against 49,533 articles that is millions of queries. It also iterates
`subscriber.subscriptions.all()`, which includes lists the subscriber has opted
out of, so it writes suppression records for lists they will never be mailed
from. There is no `--dry-run`, no confirmation, and no way to undo it.

Check first whether anything still calls it. The P0 work made its original use
case — writing off a backlog — obsolete, since content now rolls over instead of
failing the send.

If it stays:

- scope it with a required `--list`, so "suppress everything for everyone" stops being the default mode
- filter to `list_subscriptions__is_active=True`
- replace the per-row `get_or_create` with `bulk_create(..., ignore_conflicts=True)` in batches
- add `--dry-run` reporting counts, and require confirmation otherwise

If nothing calls it, delete it — that is the better outcome.

Tests: `--dry-run` writes nothing; `--list` scopes correctly; opted-out lists are
skipped.

---

## Task 6 — article staleness guard

### Why

`Articles.discovery_date` is `auto_now_add` — it records when the feedreaders
first saw the row, not when the paper was published. A bulk import stamps every
row with the same day, and the whole historical set becomes eligible for the
next digest. This is the exact mechanism behind the 2026-07-06 trial flood,
which P0 fixed for trials with `Lists.trial_max_age_days`; articles have no
equivalent guard.

It would be quieter than the trial version, and therefore worse: `article_limit`
now caps payloads, so nothing would error. Subscribers would simply receive
digests of decade-old papers with no signal that anything was wrong.

### Decided

`Lists.article_max_age_days`, default **90**, compared against
`published_date`, nulls kept, blank disables. Confirmed by Bruno 2026-07-29.

### The evidence behind the threshold

In normal operation `discovery_date` and `published_date` are effectively the
same day. Measured over 6,768 articles across 120 days, excluding bulk-import
days: median lag 0 days, p90 1 day, p95 3 days, p99 21 days.

The guard is therefore a no-op on normal days and only bites during an import.
Threshold choice barely matters for legitimate content — 30 days would drop
0.89% of normal-operation articles, 90 days 0.65%, 365 days 0.61%. There is a
floor of ~0.6% that is genuinely old whatever the threshold. 90 was chosen for
headroom over the p99 of 21 days and for consistency with the trials guard, not
because 30 would fail.

Validated retroactively against the real imports in the database:

| Import | Articles | Would pass a 90-day guard |
|:-------|:---------|:--------------------------|
| 2022-05-31 | 1,746 | 47 (2.7%) |
| 2022-06-09 | 1,531 | 21 (1.4%) |
| 2026-06-30 | 182 | 182 (100%) |

It blocks ~98% of both historical dumps while letting a legitimate 182-article
burst through untouched.

### The change

Mirror the trials implementation — see Task B0 of
[subscriptions-p0-fix-plan.md](subscriptions-p0-fix-plan.md) for the shape.

1. Add `Lists.article_max_age_days` (PositiveIntegerField, default 90, null/blank allowed, validators 1–3650) with help text explaining that age is measured on the article's own `published_date`, not `discovery_date`. Generate the migration; do not edit an existing one.

2. Apply it in **every** place articles are selected. This is the part to get right — the equivalent trials fix shipped incomplete because `send_weekly_summary` built its own query and was missed, costing a follow-up round. The four sites:
	- `send_weekly_summary`, all-articles mode
	- `send_weekly_summary`, date-sort mode
	- `send_weekly_summary`, relevancy mode
	- `get_articles_for_list` (admin summary)
	- and the Latest Research query in `get_latest_research_by_category`

	That is five, not four — confirm the list by grepping for the article
	querysets rather than trusting this one, and prefer applying the filter in
	one shared helper over five call sites.

3. Keep articles whose `published_date` is NULL — 46 of 49,533, and dropping unknown-age articles silently would hide genuinely new ones. `article_limit` bounds them regardless.

4. Add the field to the "Content Settings" fieldset in `ListsAdmin` and update the fieldset description.

### Tests

- an article published 200 days ago but discovered yesterday is excluded from every one of the selection paths above — parametrise across them rather than testing one
- an article published last week and discovered yesterday is included
- an article with `published_date` NULL is included
- `article_max_age_days` set to `None` disables the check
- regression reproducing the incident shape: 1,000 articles sharing today's `discovery_date` with publication dates spread over 20 years reduce to the handful inside the window

### Docs

- `docs/02.1-database-tables-and-fields.md` — add the column
- `docs/subscriptions.md` — document it alongside `trial_max_age_days`, including why age is measured on the article's own date

---

## Task 7 — annotate audit finding 13

Finding 13 (`lookback_days` only half-works) shipped in P2 but never got a status
annotation, so
[subscriptions-audit-2026-07.md](subscriptions-audit-2026-07.md) still reads as
though it is open. Add one in the same style as the others, recording that
`lookback_days` now applies to all three email types and that the sent-record
window was widened with it to keep audit finding 11 closed.

Docs only.

---

## Definition of done

- `pytest subscriptions` passes; each new test fails when its fix is reverted
- `pytest` full suite passes
- `uvx ruff check django/` passes, and `uvx ruff format --check django/` reports nothing that was not already failing
- the shared-footer regression test in task 1 covers all three digest types, not just announcements
- `docs/subscriptions.md` covers the announcement footer change
- audit findings 13 and the "Smaller items" entries for tasks 2 to 5 carry status annotations
