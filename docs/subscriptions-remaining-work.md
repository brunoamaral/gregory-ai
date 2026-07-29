# Subscriptions: remaining work after the July 2026 audit

Everything numbered in [subscriptions-audit-2026-07.md](subscriptions-audit-2026-07.md)
is fixed and deployed — CI ships everything merged to `main`. This file collects
what was deliberately left out of P0 to P3, so it does not get lost.

Nothing here is failing in production. These are latent traps, half-working
features, and known gaps. Sequence them against other priorities rather than
running them as one block.

## Preconditions

- Branch off `main` before touching anything.
- Tests from `django/`: `pytest subscriptions`. Baseline is 2,836 across the full suite.
- Lint and format on the host: `uvx ruff check django/` and `uvx ruff format django/`.
- Two decisions are flagged inline. Raise them, do not pick them.

---

## Task 1 — pass `organization` in trial notifications

`send_trials_notification.py:151` calls `get_optimized_email_context` without
`organization=`, unlike the weekly digest and admin summary. The
missing-organization warning at `content_organizer.py` only fires for
`weekly_summary` and `admin_summary`, so it is silent here.

Decided by Bruno on 2026-07-29: trial notifications do not need Key Takeaways,
but the organization scope should be passed anyway, so all three send paths
resolve organisation-scoped context the same way and a future template addition
does not silently render nothing.

Pass `organization=team.organization` — the value is already in scope at that
point, used for `get_postmark_credentials` at line 85. Extend the
`_ORG_EXPECTED_TYPES` set in `content_organizer.py` to include
`trial_notification` so the warning covers all three.

Tests: a trial notification context carries a populated `org_content_map`; the
warning fires when `organization` is omitted for a trial notification.

---

## Task 2 — announcement unsubscribe links only cover one list

`subscriptions/utils/announcement_send.py:149` deduplicates recipients by email
and attributes each to the first list encountered. The footer's "Unsubscribe
from this list" link therefore points at that one list, even when the
announcement went to three lists the subscriber belongs to.

The other two footer links work — the site scope was fixed in P0, and the global
scope always worked — so nobody is trapped. But the per-list link is misleading:
it says "this list" for an email that was not from one list.

### Decision required

- Option A — suppress the per-list link for announcements and lead with the site and global scopes. Announcements are not list-specific in the way a digest is, so the per-list link arguably should not be there at all. Smallest change.
- Option B — attribute recipients to every list they matched, and render one unsubscribe link per list. Most accurate, but it changes the `AnnouncementRecipient` model's single-`list` shape and the footer layout.

Recommendation: option A. The link's meaning is the problem, not its precision,
and the site-scope link now does what a recipient of a multi-list announcement
would expect.

Whichever is chosen, keep the deduplication by email — a subscriber on three
lists must still receive one copy, not three.

---

## Task 3 — the staff email preview does not resemble what gets sent

`templates/emails/views.py:160` builds preview context without `organization`,
so Key Takeaways never render, and it ignores `article_sort_order`, taking the
newest N by date regardless. A relevancy-mode digest previews as something no
recipient will ever receive.

Worth fixing because the preview is the tool used to check a digest before it
goes out, and it currently cannot answer the question it exists to answer.

Bring it in line with `send_weekly_summary`: pass `organization`, honour
`article_sort_order`, and apply the same `lookback_days` and limits the command
uses. The selection logic is worth extracting rather than duplicating for a
third time — the preview drifting from the command is the root cause here.

Tests: a preview for a relevancy-mode list returns the same article set the
command would select for the same list and window.

---

## Task 4 — announcement footer drops the legal links

`announcement_send.py:96` hardcodes `privacy_policy_url` and `terms_url` to
`""`, so those footer links disappear for announcements only. Every other email
type populates them from `CustomSetting`.

This looks like an oversight rather than a decision — the surrounding keys all
read from `custom_settings`. Populate them the same way, unless there is a
reason not to that is worth writing down.

---

## Task 5 — `mark_all_as_sent` is a footgun

`management/commands/mark_all_as_sent.py` iterates
`subscribers × lists × every article and trial` doing individual `get_or_create`
calls. On current data that is roughly 42,000 articles times the lists each
subscriber holds — millions of queries. It also iterates
`subscriber.subscriptions.all()`, which includes lists the subscriber has opted
out of, so it writes suppression records for lists they will never be mailed
from.

It has no guard, no `--dry-run`, and no confirmation, and its effect —
suppressing all existing content for everyone — is not reversible in any
convenient way.

Either fix it (bulk `bulk_create` with `ignore_conflicts`, filter to
`list_subscriptions__is_active=True`, add `--dry-run` and a confirmation prompt,
scope it to a single list with `--list`) or delete it. Check whether anything
still calls it first; the P0 work made the scenario it was written for —
writing off a backlog — obsolete, since content now rolls over instead of
failing.

Recommendation: add `--list` and `--dry-run` and keep it as a targeted tool,
rather than a command whose only mode is "suppress everything for everyone".

---

## Task 6 — no staleness guard on articles

The 2026-07-06 incident: a bulk import stamped 3,501 historical trials with a
same-day `discovery_date`, and they flooded a newsletter because `discovery_date`
records when GregoryAI first saw a row, not how old the content is. P0 fixed
that for trials with `Lists.trial_max_age_days`, checked against
`COALESCE(date_registration, published_date)`.

`Articles.discovery_date` is `auto_now_add`, so exactly the same thing happens on
an article re-import: every row gets today's date and the whole historical set
becomes eligible for the next digest. There is no equivalent guard.

The count caps mean it would no longer break a send — the failure would be
quieter than the trial one was, which arguably makes it worse: subscribers get a
digest of decade-old papers and nothing errors.

### Decision required

Articles have `published_date` and `discovery_date`, and the meaningful "how old
is this" field is `published_date`. Confirm that before building on it, and pick
a default threshold. Trials landed on 90 days, chosen because slow registries
lag; journal publication dates behave differently and may want a different
number.

Mirror the trial implementation: an `Lists.article_max_age_days` field, applied
in the article selection queries, nulls kept, blank disables. See Task B0 of
[subscriptions-p0-fix-plan.md](subscriptions-p0-fix-plan.md) for the shape.

---

## Task 7 — suppression is reactive everywhere

`classify_postmark_response` recognises `ErrorCode: 406` and deactivates the
subscriber, in all three digest commands and in announcements. But the system
only learns about a bounce by attempting a send: the first email after someone
hard-bounces is always sent and always fails.

A Postmark bounce webhook would suppress on Postmark's signal instead, before
the next attempt. It also opens the question of reactivation, which nothing
currently handles — a subscriber deactivated by a transient bounce has no path
back other than manual admin intervention.

This is the largest remaining item and the only one needing new surface area (an
endpoint, webhook authentication, and a decision about reactivation). Worth
doing when bounce volume justifies it; the reactive path is correct, just late.

---

## Task 8 — one deliberately open performance finding

The `is_ml_relevant_any_subject` selection loop in `send_weekly_summary` runs
~2,484 queries and ~2s per list per run. Left open in P3 on purpose — see the
status annotation in the audit and Task 4 of
[subscriptions-p3-fix-plan.md](subscriptions-p3-fix-plan.md), which sets the
precondition: a test pinning current selection for a fixture set, so any rewrite
is provably equivalent.

The reason to do it is convergence, not speed. The same consensus logic exists
in three places — this loop, `api.filters._get_ml_relevant_articles_query`, and
`gregory.relevance.recompute_article_relevance` — and three definitions of
"relevant" is a correctness risk regardless of how fast each one is.

---

## Also open, tracked elsewhere

- The incident record's open items —
  [incidents/2026-07-28-site-scope-unsubscribe-not-honoured.md](incidents/2026-07-28-site-scope-unsubscribe-not-honoured.md).
  Run `scripts/incident-2026-07-28-scope-check.sh` on production. The access-log
  item expires as logs rotate, so it is the only thing here with a deadline.
- Audit finding 13 shipped in P2 but never got a status annotation, so it still
  reads as open. One-line docs fix.
- Postmark batch send, noted in the P2 plan as worth measuring inside
  `send_announcement` once there is a reason to care about send duration.
