# Plan: fix the P2 subscription bugs

Execution plan for the P2 findings in
[subscriptions-audit-2026-07.md](subscriptions-audit-2026-07.md) — robustness.

P0, its follow-up, and P1 are all deployed (`c0c1e80f`). Every P2 finding was
re-verified against current code on 2026-07-28 before this plan was written, and
most of P2 turned out to be already closed as a side effect of that work. What
remains is one substantial task and one decision.

## Preconditions

- Branch off `main` before touching anything.
- Tests from `django/`: `pytest subscriptions`. Baseline is 313 passing, 2,803 across the full suite.
- Lint and format on the host: `uvx ruff check django/` and `uvx ruff format django/`. Note `django/gregory/models.py` already fails `format --check` and is one of ~130 unformatted files repo-wide — do not sweep those in.
- No open decisions. Task 2's scope was settled on 2026-07-28 and is written out below. Task 1 has one small sub-question flagged inline (how a suppressed recipient affects final status); pick the simpler option and say so in the commit message.

---

## Already closed — verify, annotate, do not re-fix

Three of the five P2 findings are done. They carry no status annotation in the
audit, which is the only reason they still look open. Confirm each in one pass,
then annotate:

| Finding | State | Evidence |
|:--------|:------|:---------|
| 9. No exception handling around `send_email` | Closed in P0 | `except requests.RequestException` present in all three send commands |
| 10. Falsy-`Response` fix never backported | Closed in P0 | All three commands route through `subscriptions.utils.postmark.classify_postmark_response`; no truthiness check on a response survives outside comments explaining the trap |
| 11. `sent_at` is never refreshed | Closed in P1 | `send_weekly_summary.py:498` uses `threshold_date = now() - timedelta(days=max(30, days_to_look_back))`, so the exclusion window is always at least as wide as the content window |

Finding 11's underlying `get_or_create` still does not refresh `sent_at`. That is
now harmless, because the widened exclusion window means a record inside the
content window is always inside the exclusion window too. Do not "fix" the
`get_or_create` — the window is the invariant that matters, and task 2 below is
where it can be broken.

Update the audit with a status block for findings 9 and 10 in the same style as
the others. No code change.

---

## Task 1 — announcement sending never got the P0 treatment

This is the whole of the remaining P2 work. Audit finding 12 described it as
"synchronous send in an admin request", which is real but is not the worst of
it: the announcement path is the one send path that P0 did not touch, so every
robustness fix made elsewhere is missing here.

### What is wrong, in order of severity

1. It does not handle Postmark suppression. `admin.py:2050` checks `response.status_code == 200` and treats anything else as a generic failure — `ErrorCode: 406` is never recognised, the subscriber is never deactivated, and `classify_postmark_response` is not used.

2. A single suppressed recipient marks the whole announcement `failed`, because `status = "sent" if failure_count == 0 else "failed"`.

3. `failed` is re-sendable — the guard at `admin.py:1914` only blocks `("sent", "sending")` — and the send loop iterates every subscriber with no check for an existing successful `AnnouncementRecipient`. So retrying a partially-failed announcement re-mails everyone who already received it.

4. The send is synchronous inside the admin request, and the timeouts do not agree: nginx `proxy_read_timeout` is 60s (`nginx-example-configuration/nginx.conf:277`) while gunicorn's is 300s (`Dockerfile:62`).

5. `except Exception` around the send swallows programming errors as delivery failures.

### The live evidence

Two announcements are sitting in `failed` right now, both of them only because
of suppressed recipients:

| Announcement | Succeeded | Failed | Retry would duplicate |
|:-------------|:----------|:-------|:----------------------|
| #12 "How Brain-Regeneration.com can help you save…" | 177 | 5 | 177 emails |
| #9 "Gregory-MS just became part of something bigg…" | 176 | 12 | 176 emails |

Every recorded failure on both is `{"ErrorCode":406,...marked as inactive}`.
Anyone clicking Send on either row today sends ~177 duplicate emails to people
who already received it. Treat this as the reason to do points 1 to 3 first.

On point 4: the widest possible audience is 192 subscribers, which at 0.3 to 1.0
seconds per Postmark call is 58 to 192 seconds. Both outcomes are bad and the
operator cannot tell them apart, because both look like a 504 in the browser:

- under 300s — nginx cuts the connection at 60s, gunicorn keeps working and the send completes. Status ends correct; the operator does not know that.
- over 300s — gunicorn kills the worker mid-loop. Status is stuck at `sending`, which nothing can clear through the UI, with partial delivery and no record of where it stopped.

### The change

Do it in two commits. The first is small and removes the live duplicate risk;
the second is the structural fix.

#### Commit 1 — make the send idempotent, suppression-aware, and resumable

1. Route the response through `classify_postmark_response`, exactly as the three management commands do. On `POSTMARK_INACTIVE_RECIPIENT`, call `subscriptions.utils.suppression.deactivate_subscribers([subscriber.subscriber_id], reason=detail)` and record the recipient row as failed.

2. Skip subscribers that already have an `AnnouncementRecipient` for this announcement with `success=True`. This single change makes a retry safe, makes a timed-out send resumable by clicking Send again, and fixes the 177-duplicate trap without any new machinery.

3. Narrow `except Exception` to `requests.RequestException`, matching the commands. Let programming errors surface.

4. Count a suppressed recipient as suppressed rather than as a delivery failure when deciding final status, so one 406 no longer marks an otherwise-clean announcement `failed`. Decide with Bruno whether `sent`/`failed` needs a third state for "sent, some recipients suppressed" or whether suppressed simply does not count toward `failures_count` — the second is simpler and probably right.

5. Allow recovery from `sending`. A run killed mid-loop leaves that status permanently. Either allow a resume when `status == "sending"` and `sent_at` (or a new heartbeat field) is older than a threshold, or add an explicit admin action to reset a stuck announcement to `draft`. With step 2 in place, resuming is safe by construction.

Do not renumber or reset `recipients_count` / `failures_count` on resume —
recompute them from `AnnouncementRecipient` rows so they stay correct across
multiple partial runs.

#### Commit 2 — get the send out of the request

Without a task queue in the project (verified: no Celery, RQ, Huey, Dramatiq or
django-q in `requirements.txt`, `pyproject.toml` or `docker-compose.yaml`), do
not add one for this. Two workable options:

- Option A — a `send_announcement` management command plus a `queued` status. The admin action validates, sets `queued`, and returns immediately; a cron entry runs the command, which picks up queued announcements and sends them with the idempotent loop from commit 1. Progress is visible in the admin from `AnnouncementRecipient` counts. This matches how every other send in this app already works and reuses the existing cron pattern in `docs/cookbook.md`.
- Option B — Postmark's batch send endpoint, which accepts many fully-personalised messages in one HTTP call. Per-subscriber HTML (unsubscribe token, greeting) is preserved because each array element carries its own body. Check the current Postmark documentation for the per-batch message cap and total payload limit before committing to this, and size it against 192 recipients times the rendered body. If it fits, it removes the timeout problem rather than working around it.

Recommendation: option A. It is resumable by construction, it degrades safely if
Postmark is slow or down, and it does not depend on a payload-size limit staying
comfortable as announcements grow. Option B is worth measuring afterwards as an
optimisation inside the command, not as the fix.

### Tests

Add `django/subscriptions/tests/test_announcement_send_resume.py`:

- a subscriber with an existing successful `AnnouncementRecipient` is not sent to again on a second send — the regression for the 177-duplicate trap, and it must fail against current code
- a 422 with `ErrorCode: 406` deactivates the subscriber and records the recipient as failed
- one suppressed recipient does not mark an otherwise-successful announcement `failed`
- a `requests.ConnectionError` for one subscriber records that recipient and continues to the next
- `recipients_count` and `failures_count` after a resumed send match the `AnnouncementRecipient` rows rather than counting only the second run
- an announcement stuck in `sending` can be recovered, and recovering it does not re-mail anyone already recorded successful

Existing tests to re-check: `test_announcements.py`,
`test_announcement_send_validation.py`, `test_announcement_duplicate.py`.

### Docs

- `docs/subscriptions.md` — document the announcement send lifecycle including the new status, the resume semantics, and that suppressed recipients are handled the same way as in the digest commands.
- `docs/cookbook.md` — the cron entry, if option A is taken.

### Operational note, not a code change

Announcements #9 and #12 are in `failed` with 177 and 176 successful recipients
respectively. Until commit 1 ships, nobody should click Send on either. Worth
saying out loud to whoever has admin access rather than relying on the plan
being read.

---

## Task 2 — finding 13, `lookback_days` applies to only one email type

### Current state

Partially addressed by the P0 follow-up, which added a `days` parameter to
`get_trials_for_list` and `get_latest_research_by_category`. The weekly digest
passes its own `lookback_days` to both. The other two commands do not:

| Call site | Passes `days`? |
|:----------|:---------------|
| `send_weekly_summary.py:444` — `get_trials_for_list` | yes, `days=days_to_look_back` |
| `send_admin_summary.py:106` — `get_trials_for_list` | no, defaults to 30 |
| `send_trials_notification.py:93` — `get_trials_for_list` | no, defaults to 30 |
| `send_admin_summary.py:98` — `get_articles_for_list` | no `days` parameter exists at all |

### Decided: `lookback_days` applies to all three email types

Decided by Bruno on 2026-07-28. The field sits in the same "Content Settings"
box as `article_limit`, `trial_limit` and `trial_max_age_days`, all of which
apply to all three email types after P0 — a knob that silently ignores you is
worse than one that does not exist.

Note this changes no behaviour today: every list is set to `lookback_days = 30`
and the hardcoded value is also 30, so the two are identical until someone edits
the field. The point of the change is that editing it will then do what it says.

### The change

1. Add a `days` parameter to `get_articles_for_list(lst, days=30)` in `management/commands/utils/subscription.py`, mirroring the signature `get_trials_for_list` and `get_latest_research_by_category` already have.

2. Pass the list's own value at all three remaining call sites:
	- `send_admin_summary.py:98` — `get_articles_for_list(admin_list, days=admin_list.lookback_days)`
	- `send_admin_summary.py:106` — `get_trials_for_list(admin_list, days=admin_list.lookback_days)`
	- `send_trials_notification.py:93` — `get_trials_for_list(lst, days=lst.lookback_days)`

3. Reword `Lists.lookback_days.help_text` in `models.py:66` — it currently says "in the weekly digest", which will be wrong. Generate the migration (`AlterField`, help-text only) rather than editing an existing one.

4. Update the "Content Settings" fieldset description in `admin.py` to say the lookback window applies to all three email types, alongside the sentence already there about the limits.

### Do this in the same commit, not after

Widening the content window without widening the sent-record window reopens
finding 11, which P1 closed. `send_admin_summary.py:51` and
`send_trials_notification.py:51` both hardcode
`threshold_date = now() - timedelta(days=30)`, and are safe today only because
their content window is also 30. Step 2 above removes that guarantee.

Apply the same treatment the weekly digest uses at `send_weekly_summary.py:498`:

```python
threshold_date = now() - timedelta(days=max(30, lookback_days))
```

Without it, an article discovered 45 days ago and emailed 40 days ago falls
outside a 30-day dedup window while still inside a 60-day content window — so it
is treated as unsent and mailed again, and `get_or_create` does not refresh
`sent_at`, so it repeats on every run until it ages out. For the admin summary
that is every two days.

### Tests

- `get_articles_for_list(lst, days=N)` honours `N`, and defaults to 30 when omitted
- the admin summary surfaces an article discovered 45 days ago when `lookback_days = 60`, and does not when it is 30
- the trials notification honours `lookback_days` the same way
- for each of the two commands: with `lookback_days = 60`, an item recorded as sent 50 days ago is still excluded. Model these on `SentRecordLookbackWindowTest` in `test_latest_research_delta.py` — it already does exactly this for the weekly digest, including the guard that keeps the assertion from passing vacuously when everything gets skipped.

### Docs

- `docs/02.1-database-tables-and-fields.md` — update the `lookback_days` row to say all three email types.
- `docs/subscriptions.md` — same, next to the `article_limit` / `trial_limit` table that already lists which emails each field affects.

---

## Out of scope

- P3 performance findings.
- A Postmark bounce webhook and reactivation flow. Suppression handling stays reactive everywhere, including in announcements after task 1.
- The article-side staleness guard noted as a follow-up in the P0 plan.
- The smaller items in the audit's own "Smaller items" section, except where task 1 touches them incidentally (announcement recipients deduplicated by email and attributed to the first list; `privacy_policy_url` and `terms_url` hardcoded to `""` in `_render_announcement_email`). If task 1 makes either trivial to fix, do it and say so; otherwise leave them.

## Definition of done

- `pytest subscriptions` passes; each new test fails when its fix is reverted
- `pytest` full suite passes
- `uvx ruff check django/` passes, and `uvx ruff format --check django/` reports nothing that was not already failing
- announcements #9 and #12 can be safely retried, verified against a copy of the data rather than in production
- audit findings 9 and 10 carry status annotations; findings 12 and 13 carry ones describing what shipped
- every command that can now exceed a 30-day content window has a sent-record window test, and `lookback_days` no longer says "in the weekly digest" anywhere in the model, the admin, or the docs
