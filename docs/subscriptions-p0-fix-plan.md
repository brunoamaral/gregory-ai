# Plan: fix the P0 subscription bugs

Execution plan for the three P0 findings in
[subscriptions-audit-2026-07.md](subscriptions-audit-2026-07.md). Each task is
independently shippable and independently revertable — commit them separately,
in the order below.

Read the audit first for the evidence behind each finding.

## Preconditions

- Branch off `main` before touching anything (`main` tracks production).
- Suggested branch: `fix/subscriptions-p0`.
- Tests run from `django/`: `pytest subscriptions` (config in `django/pytest.ini`; note `--nomigrations`, so a new migration will not be exercised by the suite — apply it locally with `manage.py migrate` to check it).
- The `gregory` container bind-mounts `~/Labs/gregory/django` over `/code` and runs live `main` code. Do not `docker exec` into it to test branch work; use a throwaway `docker run` with the worktree mounted.
- One step needs a decision from Bruno before it runs, flagged inline as "decision required". Do not act on it unprompted.

---

## Task A — site-scope unsubscribe reads the wrong field

Smallest change, highest user-facing impact. Do this first.

### The bug

`templates/emails/components/footer.html:92` builds the link from the site id
resolved for the list, which comes from `Lists.site`.
`subscriptions/views.py:341` filters on `list__team__site_id` — a different,
nullable FK. In the dev DB every list has `list.site = 3` while `team.site` is
`1` or `None`, so the filter matches nothing, no subscription is deactivated,
and the subscriber is still shown the "unsubscribed" confirmation page.

### Changes

1. `django/subscriptions/views.py`, in `_unsubscribe_confirm`, the `scope == "site"` branch:

```python
elif scope == "site":
	ListSubscription.objects.filter(
		subscriber=subscriber,
		list__site_id=extra_id,
		is_active=True,
	).update(is_active=False, unsubscribed_at=tz_now())
```

2. Capture the number of rows updated in all three branches and pass it to the done template, so a scope that matches nothing can never again report success silently:

```python
updated = 0
if scope == "list":
	updated = ListSubscription.objects.filter(...).update(...)
elif scope == "site":
	updated = ListSubscription.objects.filter(...).update(...)
elif scope == "all":
	...
	updated = ListSubscription.objects.filter(...).update(...)

return render(
	request,
	"subscriptions/unsubscribe_done.html",
	{"subscriber": subscriber, "scope": scope, "updated_count": updated},
)
```

Note that `scope == "all"` must keep setting `subscriber.active = False` before
the subscription update, and should still report success when `updated == 0` —
the account flag is the meaningful action there.

3. `django/templates/subscriptions/unsubscribe_done.html` — for `list` and `site` scopes, render a "you were not subscribed to anything on this site" variant when `updated_count == 0` instead of the success copy.

### Tests

New file `django/subscriptions/tests/test_unsubscribe_scopes.py`:

- site scope deactivates every `ListSubscription` whose `list.site` matches, across teams
- site scope leaves lists on other sites untouched
- regression: a list whose `team.site` is `None` (or differs from `list.site`) is still deactivated — this is the exact production shape and must fail against the old code
- site scope stamps `unsubscribed_at` and does not touch `Subscribers.active`
- list scope and all scope keep their current behaviour
- `updated_count == 0` renders the "nothing to unsubscribe" variant

Follow the existing patterns in `subscriptions/tests/test_views.py`.

### Docs

- `docs/subscriptions.md`, "Unsubscribe Endpoints" — state explicitly that the site scope matches `Lists.site`, not `Team.site`.

### People who already used the broken link — decided

Anyone who clicked "Unsubscribe from all lists on <site>", confirmed, and saw the
success page is still subscribed and still receiving email. The view writes
nothing when the filter matches zero rows, so the database holds no record of who
clicked.

Decision taken 2026-07-28: fix forward, no proactive contact, document the
incident for audit. Recorded in
[incidents/2026-07-28-site-scope-unsubscribe-not-honoured.md](incidents/2026-07-28-site-scope-unsubscribe-not-honoured.md).

Two things that record depends on, to be done as part of this task:

- confirm the scope figures against production and replace the indicative development figures in the record
- establish whether production web server access logs cover 2026-04-16 onward. The unsubscribe token is in the URL path, so `POST /subscriptions/unsubscribe/<token>/site/<id>/` entries map back to individual subscribers. If those logs exist, honour the requests retroactively in preference to fixing forward alone, and update the record accordingly.

Once the fix is deployed, add the deployment date to the record and close it.

---

## Task B — cap email payloads and make oversized sends self-healing

### The bug

Nothing caps trials on the way into an email, so the Alzheimer Disease trial
notification renders 3,570 trial cards and Postmark rejects the body
(`ErrorCode: 300`, 5,242,880 character limit). Because the send fails, no
`SentTrialNotification` rows are written, so the next run rebuilds the identical
payload — 413 identical failures over 15 days.

`article_limit` exists but is only honoured by the weekly digest. Weekly digest
trials are uncapped too (`send_weekly_summary.py:425`), as are admin summary
articles and trials.

### The trigger, and why age must be measured on the trial's own date

Confirmed against the dev DB: 3,501 of those 3,570 trials share
`discovery_date = 2026-07-06`, and the first oversized-body failure is
2026-07-06 17:18. A bulk import that day stamped thousands of historical trials
with a same-day discovery date and pushed them all into the newsletter queue.

Their registration dates span 1999 to 2026. Only 11 of the 3,570 were registered
in the last 30 days:

| Window on `date_registration` | Alzheimer Disease | Clinical Trials for MS |
|:------------------------------|:------------------|:-----------------------|
| Total in the discovery window | 3,570 | 366 |
| Registered within 30 days | 11 | 4 |
| Registered within 90 days | 22 | 15 |
| Registered within 365 days | 135 | — |
| `date_registration` is NULL | 34 | 4 |

So `discovery_date` measures when *we* first saw the row, not how old the trial
is, and every selection query currently windows on it
(`utils/subscription.py:12`). Filtering on `discovery_date` older than 30 days
would change nothing here — the whole batch is 22 days old by that measure.
Age has to be read from the trial's own date.

### B0. Staleness filter

Add a per-list maximum content age, applied to the trial's own dates rather than
to `discovery_date`.

On `Lists`:

```python
trial_max_age_days = models.PositiveIntegerField(
	default=90,
	null=True,
	blank=True,
	validators=[MinValueValidator(1), MaxValueValidator(3650)],
	help_text=(
		"Skip trials whose own registration or publication date is older than this. "
		"Guards against bulk imports of historical trials flooding a newsletter, "
		"because discovery_date only records when GregoryAI first saw the row. "
		"Leave blank to disable the check."
	),
	verbose_name="Maximum trial age (days)",
)
```

In `get_trials_for_list` (`management/commands/utils/subscription.py:9`), add the
filter after the existing discovery window:

- compare against `COALESCE(date_registration, published_date)`
- keep rows where **both** dates are NULL — 34 trials on the Alzheimer list have no usable date, and silently dropping unknown-age trials would hide genuinely new ones. The per-email cap in B1 bounds them regardless.
- skip the filter entirely when `trial_max_age_days` is NULL

Why 90 and not 30: WHO ICTRP and CTIS feeds lag, so a trial registered 45 days
ago may only reach us today. At 30 days it would be dropped silently and would
then age out of the discovery window before it ever qualified. The table above
shows 90 costs almost nothing in payload (22 vs 11 trials) and buys real headroom.
Make it configurable per list so the threshold can be tuned without a deploy.

Applied today this takes the Alzheimer payload from 3,570 to roughly 22 and the
MS trials list from 366 to 15 — under the per-email cap in both cases, so no
backlog drains and no one-off backfill is needed. It is also a permanent fix for
the root cause: the next bulk import cannot flood a newsletter.

The same exposure exists for articles — `Articles.discovery_date` is
`auto_now_add`, so a re-import of historical articles would behave identically.
Out of scope here; note it as a follow-up.

### B1. Model

`django/subscriptions/models.py`, on `Lists`:

- add `trial_limit`, mirroring `article_limit`:

```python
trial_limit = models.PositiveIntegerField(
	default=15,
	null=True,
	blank=True,
	help_text="Maximum number of clinical trials to include in a single email (default: 15). Trials that do not fit roll over to the next send.",
	verbose_name="Trial limit per email",
)
```

- widen `article_limit`'s `verbose_name` to "Article limit per email" and update its `help_text` — it now applies to weekly digests, admin summaries and trial notifications, not just weekly digests.

Generate the migration with `makemigrations subscriptions` (next number is
`0030`). Do not edit any existing migration — Bruno applies branch migrations to
his local database.

Add both fields to the "Content Settings" fieldset in `ListsAdmin.fieldsets`
(`django/subscriptions/admin.py:1176`) and update that fieldset's `description`.

### B2. Shared limit helper

New file `django/subscriptions/utils/email_limits.py`:

```python
# Postmark rejects an HtmlBody over 5,242,880 characters (ErrorCode 300).
POSTMARK_MAX_BODY_CHARS = 5_242_880
# Headroom: the JSON payload also carries TextBody, and character count is not
# byte count. Shrink well before the hard limit.
SAFE_BODY_CHARS = 4_000_000

DEFAULT_ARTICLE_LIMIT = 15
DEFAULT_TRIAL_LIMIT = 15


def resolve_limits(list_obj):
	"""Return (article_limit, trial_limit), substituting defaults for None/0."""
	article_limit = getattr(list_obj, "article_limit", None) or DEFAULT_ARTICLE_LIMIT
	trial_limit = getattr(list_obj, "trial_limit", None) or DEFAULT_TRIAL_LIMIT
	return article_limit, trial_limit


def render_within_limit(render, articles, trials, *, max_attempts=5):
	"""
	Render an email, shrinking its content until the HTML fits SAFE_BODY_CHARS.

	`render(articles, trials)` must return (html, used_articles, used_trials) —
	the caller owns context building, so the returned "used" lists reflect what
	the content organizer actually placed in the template.

	Returns (html, used_articles, used_trials), or (None, [], []) when even a
	single article and a single trial will not fit.
	"""
```

Implementation notes:

- On each attempt, call `render`, and return immediately when `len(html) <= SAFE_BODY_CHARS`.
- Otherwise halve both list lengths (floor, minimum 1) and retry.
- Give up when both lists are already at length 1 (or empty) and the body is still too large, or when `max_attempts` is exhausted.
- Return the "used" lists from the successful attempt, never the pre-shrink input — this is what makes the fix self-healing.

### B3. Wire into the three commands

The invariant to preserve everywhere: record as sent only what the rendered
context actually contained. `send_weekly_summary.py:649` already does this via
`articles_to_be_sent` / `trials_to_be_sent`; the other two commands do not.

`django/subscriptions/management/commands/send_trials_notification.py`:

- after `new_trials = list_trials.exclude(...)` (line 120), order and truncate:
  `new_trials = list(new_trials.order_by("-discovery_date")[:trial_limit])`
- route the render through `render_within_limit`
- replace the record loop at line 195 so it iterates the trials in the rendered context (`summary_context["trials"] + summary_context["additional_trials"]`), not `new_trials`

`django/subscriptions/management/commands/send_admin_summary.py`:

- `get_articles_for_list` returns an unordered queryset (`utils/subscription.py:38`). Add `.order_by("-discovery_date")` before slicing, otherwise the truncated subset is non-deterministic and the same arbitrary articles can be resent run after run.
- truncate `new_articles` and `new_trials` to the configured limits
- route the render through `render_within_limit`
- replace the record loops at lines 197 and 202 with the rendered sets

`django/subscriptions/management/commands/send_weekly_summary.py`:

- truncate `unsent_trials` to `trial_limit`, ordered `-discovery_date`, alongside the existing article truncation at line 535
- route the render at line 717 through `render_within_limit`
- the existing `articles_to_be_sent` / `trials_to_be_sent` logic already reads from the context; make sure it reads from the *final* rendered context after any shrink

### B4. Give-up path

When `render_within_limit` returns `None`, do not send. Write a
`FailedNotification` with a reason naming the list and the rendered size, log at
ERROR, and continue to the next subscriber. With the count caps in place this
should be unreachable; it exists so the failure mode is loud rather than a
silent retry loop.

### Tests

New file `django/subscriptions/tests/test_email_payload_limits.py`:

- staleness: a trial registered 200 days ago but discovered yesterday is excluded at `trial_max_age_days = 90`, and included when the field is NULL
- staleness: a trial with both `date_registration` and `published_date` NULL is included
- staleness: `published_date` is used when `date_registration` is NULL
- regression for the July-6 incident: 3,000 trials with today's `discovery_date` and registration dates spread over 20 years reduce to the handful inside the age window
- unit: `resolve_limits` substitutes defaults for `None` and `0`
- unit: `render_within_limit` returns the first fitting render; halves on overflow; returns `(None, [], [])` when one item of each still overflows
- regression for the production failure: a trial-notification list with 400 trials in the window renders at most `trial_limit` cards and the HTML stays under `SAFE_BODY_CHARS`
- only the rendered trials get `SentTrialNotification` rows
- rollover: run the command twice and assert the second run's trials are disjoint from the first — this is the property that makes the backlog drain
- weekly digest truncates trials, and records only rendered ones
- admin summary truncates articles and trials, orders newest first, and records only rendered ones
- a failed send (non-200) writes no `SentArticleNotification` / `SentTrialNotification` rows

Existing tests to re-check for breakage: `test_weekly_summary_volume.py`,
`test_weekly_summary_date_sort.py`, `test_weekly_summary_relevancy_sort.py`.

### Docs

- `docs/subscriptions.md` — document `article_limit` and `trial_limit` applying to all three email types, the rollover behaviour (content that does not fit is not marked sent and appears in the next email), and `trial_max_age_days` including why age is measured on the trial's own date rather than `discovery_date`.
- `docs/02.1-database-tables-and-fields.md` — add the `trial_limit` and `trial_max_age_days` columns.

### No backlog write-off needed

The staleness filter in B0 removes the need for a one-off backfill: it takes the
stuck list from 3,570 trials to roughly 22 on the first run after deploy, which
is inside the per-email cap. Nothing has to drain.

Two things to verify rather than assume after deploy:

- the July-6 batch also ages out of the 30-day discovery window on 2026-08-05 on its own, so the numbers above will shift downward again around that date
- run the trial notification with `--dry-run` (add the flag if it does not exist on this command) against the Alzheimer list before the first live send, and confirm the trial count and rendered body size

---

## Task C — stop retrying suppressed recipients

### The bug

Postmark returns HTTP 422 with `ErrorCode: 406` for hard-bounced, spam-complained
or manually suppressed addresses. Nothing consumes that signal: 440 such rows in
`FailedNotification`, one address retried 210 times. The failure also skips the
sent-record write, so the same recipient is retried on every run.

### C1. Response classification helper

New file `django/subscriptions/utils/postmark.py`:

```python
POSTMARK_INACTIVE_RECIPIENT = 406


def classify_postmark_response(result):
	"""
	Normalise a Postmark send result into (delivered, error_code, detail).

	Accepts a requests.Response, a plain dict (test doubles), or None.
	"""
```

This centralises logic that currently exists in three divergent copies. It must
handle the trap already documented at `send_weekly_summary.py:895`:
`requests.Response.__bool__` is `self.ok`, so a 422 response is falsy. Truthiness
checks on the response must not survive anywhere — `send_admin_summary.py:185`
still has one, which is why its 422 branch reports "HTTP Status No Response" and
its 422-detail extraction at line 222 is dead code.

Behaviour:

- `None` → `(False, None, "No response from Postmark")`
- HTTP 200 and `ErrorCode == 0` → `(True, 0, message)`
- HTTP 200 and `ErrorCode != 0` → `(False, error_code, message)`
- non-200 → `(False, error_code_from_body_or_None, "HTTP {status} - ErrorCode: {code}, Message: {message}")`
- unparseable body → `(False, None, "HTTP {status} - unable to parse error details")`

### C2. Suppression helper

New file `django/subscriptions/utils/suppression.py`:

```python
def deactivate_subscribers(subscriber_ids, *, reason=""):
	"""
	Global opt-out: clear Subscribers.active and deactivate every active
	ListSubscription, stamping unsubscribed_at where it is not already set.

	Mirrors the "Disable all emails" admin action. Returns
	(subscribers_updated, subscriptions_updated).
	"""
```

Copy the semantics from `admin.py:940` (`make_inactive`) exactly, including the
`transaction.atomic()` block and the "preserve existing `unsubscribed_at`" rule,
then refactor `make_inactive` to call this helper so the two paths cannot drift.
Keep the admin action's bulk behaviour — the helper takes a list of ids, so the
admin passes the whole selection and the commands pass a single id.

### C3. Wire into the three commands

In each of `send_weekly_summary.py`, `send_admin_summary.py` and
`send_trials_notification.py`, replace the bespoke response handling with:

```python
delivered, error_code, detail = classify_postmark_response(result)
if delivered:
	# record sent notifications for the rendered content
elif error_code == POSTMARK_INACTIVE_RECIPIENT:
	deactivate_subscribers([subscriber.subscriber_id], reason=detail)
	FailedNotification.objects.create(subscriber=subscriber, list=<list>, reason=detail)
	# log loudly: this subscriber will receive nothing further
else:
	FailedNotification.objects.create(subscriber=subscriber, list=<list>, reason=detail)
```

Deactivating globally is the right response to all three causes of a 406 — a
hard bounce, a spam complaint and a manual suppression each mean this address
must not be mailed again from any list.

### C4. Also wrap the send call

While in these branches, wrap each `send_email` call in `try/except
requests.RequestException` (finding 9 in the audit). Today a single connection
reset aborts the whole cron run, skipping every remaining subscriber and every
remaining list with no record. Catch it, write a `FailedNotification`, continue.
This is small, it belongs in the same edit, and without it Task C's error path is
still bypassable.

### Tests

New file `django/subscriptions/tests/test_postmark_suppression.py`:

- unit tests for `classify_postmark_response`: 200/ErrorCode 0; 200/ErrorCode non-zero; a falsy 422 `Response` (build a real `requests.Response` with `status_code = 422` so the truthiness trap is genuinely exercised); 500; `None`; dict form
- 422 + ErrorCode 406 deactivates the subscriber, deactivates every `ListSubscription` with `unsubscribed_at` stamped, and writes exactly one `FailedNotification`
- a second command run does not attempt to send to that subscriber at all
- 422 + a different ErrorCode leaves the subscriber active and records the detailed reason — this is the regression test for the `send_admin_summary` truthiness bug, and it must fail against the old code
- a `requests.ConnectionError` from `send_email` is recorded and the loop continues to the next subscriber
- `deactivate_subscribers` preserves an existing `unsubscribed_at`

### Docs

- `docs/subscriptions.md` — new section on bounce and suppression handling: what a 406 means, that it triggers a global opt-out, and where to see it (`FailedNotification` plus the `Subscribers` history).
- Cross-reference the `Subscribers.active` help text, which already describes the global-switch semantics this reuses.

### Optional cleanup — decision required

Roughly eight addresses are already suppressed at Postmark and still active in
the database. A one-off pass could deactivate any subscriber with a recent 406 in
`FailedNotification`. Offer it with a dry-run; it is not required for the fix,
since the next send will suppress them anyway on first 406.

---

## Out of scope for P0

Track these separately; do not fold them into this branch:

- Postmark bounce webhook for real-time suppression, and a reactivation flow (Task C is reactive only — it learns about a bounce by attempting one send)
- an equivalent staleness guard for articles — `Articles.discovery_date` is `auto_now_add`, so a re-import of historical articles would flood a digest the same way the July-6 trial import did
- moving announcement sending out of the admin request (audit finding 12)
- the content organizer correctness work (audit findings 4 to 8)
- the N+1 work (audit findings in Priority 3)

## Definition of done

- `pytest subscriptions` passes, and each new test fails when its fix is reverted
- `pytest` (full suite) passes — Task C touches shared admin behaviour
- migration `0030` applies cleanly and `makemigrations --check` is clean
- `docs/subscriptions.md`, `docs/02.1-database-tables-and-fields.md` and this plan's parent audit are updated, per the docs rule in `CLAUDE.md`
- the audit doc's P0 section is annotated with the fix status
- the "decision required" item in Task A is raised with Bruno rather than decided
