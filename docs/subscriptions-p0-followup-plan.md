# Plan: P0 follow-up — weekly digest staleness, and format cleanup

Two items left open after the P0 fixes landed in `653b7f62`. Task 1 is a
correctness gap found while reviewing that work; task 2 is housekeeping the same
work introduced.

Read [subscriptions-p0-fix-plan.md](subscriptions-p0-fix-plan.md) first for
context on `trial_max_age_days` and why staleness is measured on the trial's own
date.

## Preconditions

- Branch off `main` before touching anything.
- Tests run from `django/`: `pytest subscriptions`.
- Lint and format run on the host, not in the container: `uvx ruff check django/subscriptions/` and `uvx ruff format django/subscriptions/`. Config is `ruff.toml` at the repository root.

---

## Task 1 — the weekly digest bypasses the staleness filter

### The gap

`trial_max_age_days` lives inside `get_trials_for_list`
(`management/commands/utils/subscription.py`), but `send_weekly_summary` never
calls that helper — it builds its own trials query inline at
`send_weekly_summary.py:434`, so the filter never applies to weekly digests.

`docs/subscriptions.md` currently records this as intentional: the check is
skipped for the weekly digest "where the trial count cap alone is enough to
bound the payload". That rationale covers payload size, but the filter does two
jobs and only one of them is payload size. The other is keeping decades-old
trials from being presented as new, and the count cap cannot do that.

### Evidence

These are the 15 trials the MS Weekly Digest would currently send to its 88
subscribers, under the heading "New Clinical Trials":

```
31239  discovered 2026-07-19  registered None        Effects of GLP-1 analogue in multiple sclerosis
31237  discovered 2026-07-17  registered 2026-07-02  Investigation of the Effects of Motor Imagery…
31236  discovered 2026-07-17  registered 2026-07-13  Ocrevus Zunovo for Patients With Multiple Scl…
…
27110  discovered 2026-07-06  registered 2003-09-17  A Phase III, Multi-Center, Double-Blind, Plac…
27109  discovered 2026-07-06  registered 2007-04-06  Rapamycin Therapy of Angiomyolipomas…
27108  discovered 2026-07-06  registered 1999-11-03  Lymphangioleiomyomatosis (LAM) Registry
27107  discovered 2026-07-06  registered 2007-06-21  A Trial of the Efficacy and Safety of Sirolim…
27106  discovered 2026-07-06  registered 2007-09-18  AV650-018: A Two-Part…
```

Five of fifteen come from the 2026-07-06 bulk import, registered between 1999 and
2007. Two are not multiple sclerosis trials at all — a LAM registry and an
angiomyolipoma/TSC study that matched the subject.

### The change

`get_trials_for_list` hardcodes a 30-day discovery window, but the weekly digest
uses a per-list `lookback_days` (overridable by `--days`). Swapping the call in
naively would silently change that behaviour, so parameterise the helper first.

1. `management/commands/utils/subscription.py` — add a `days` parameter, defaulting to the current value so the two existing callers are unaffected:

```python
def get_trials_for_list(lst, days=30):
	"""
	Returns trials discovered in the last `days` days for the given list.
	...
	"""
	qs = Trials.objects.filter(
		subjects__in=lst.subjects.all(),
		discovery_date__gte=now() - timedelta(days=days),
	)
	# staleness filter unchanged
```

2. `send_weekly_summary.py` — replace the inline query at line 434 with the helper, passing the resolved lookback:

```python
trials = get_trials_for_list(digest_list, days=days_to_look_back)
```

Add the import, and delete the comment above the old query — it reads "Use the
helper function to get trials, but pass the days_to_look_back parameter" while
describing a call the code does not make. That stale comment is what made this
gap easy to miss; do not leave it in place.

3. Leave `send_admin_summary` and `send_trials_notification` alone. They call `get_trials_for_list(lst)` and keep the 30-day default.

### Things already established, so do not re-litigate them

- `trials` is used downstream as `trials.count()`, `trials.exists()`, `SentTrialNotification.objects.filter(trial__in=trials, ...)` and `trials.exclude(pk__in=...)`. The annotated queryset the staleness filter produces already flows through exactly these shapes in `send_admin_summary` and `send_trials_notification`, and their tests pass, so the `__in` subquery and the `Coalesce` annotation are known to work together. No extra defensive change is needed.
- The annotation is a `Coalesce`, not an aggregate, so it introduces no `GROUP BY` and does not interact with `.distinct()`.

### Expected impact

Measured against the dev database on 2026-07-28, at the default
`trial_max_age_days = 90`:

| List | Trials before | Trials after |
|:-----|:--------------|:-------------|
| MS Weekly Digest | 366 | 18 |
| Cell Reprogramming Digest | 94 | 5 |
| Neuroimmune Interactions Digest | 79 | 3 |
| Neuroinflammation Digest | 97 | 22 |

No list drops to zero, so no digest starts skipping for lack of content. Confirm
these numbers still hold before deploying — the 2026-07-06 import batch ages out
of the 30-day discovery window around 2026-08-05, which moves the "before"
column down on its own.

### Tests

Add to `django/subscriptions/tests/test_email_payload_limits.py`, or a new
`test_weekly_digest_staleness.py` if that file is already large:

- a trial registered 200 days ago but discovered yesterday is excluded from the weekly digest — this is the regression for the evidence above and must fail against current code
- a trial registered last week and discovered yesterday is included
- a trial with both `date_registration` and `published_date` unset is included
- setting `trial_max_age_days` to `None` on the list disables the filter for the weekly digest
- `lookback_days` is still honoured: on a list with `lookback_days = 60`, a recently-registered trial discovered 45 days ago is included. This is the regression guarding against the helper's 30-day default leaking in — it must fail if `days=days_to_look_back` is omitted at the call site.
- `--days` on the command still overrides `lookback_days`

### Docs

- `docs/subscriptions.md` — in the field table, change `trial_max_age_days` from "`get_trials_for_list` (admin summary, trial notification)" to all three email types; delete the paragraph explaining why the weekly digest is exempt.
- `docs/subscriptions.md` — note that `get_trials_for_list` now takes `days`, and that the weekly digest passes its own `lookback_days`. This closes part of audit finding 13 (`lookback_days` was previously honoured only for articles); say so explicitly rather than leaving it implied.
- `docs/subscriptions-audit-2026-07.md` — update the P0 finding 1 status annotation to record that the weekly digest is now covered too.

---

## Task 2 — format the files the P0 work touched

`uvx ruff check django/subscriptions/` passes. `uvx ruff format --check
django/subscriptions/` does not: seven files would be reformatted, and six of
them were introduced or modified by the P0 commits.

Introduced by the P0 work, fix these:

- `management/commands/send_admin_summary.py`
- `management/commands/send_trials_notification.py`
- `management/commands/send_weekly_summary.py`
- `management/commands/utils/subscription.py`
- `tests/test_email_payload_limits.py`
- `tests/test_postmark_suppression.py`

Pre-existing, untouched by the P0 range:

- `tests/test_announcement_organization.py`

Run `uvx ruff format` over the six, in the same commit as task 1 if that commit
already touches them, otherwise as a separate `chore(format)` commit. The
formatter is configured to preserve tabs, so indentation style is unaffected —
the diffs are line-wrapping only.

Leave the pre-existing file alone unless Bruno wants it swept in; it is unrelated
to this work and mixing it in makes the diff harder to read.

Re-run `pytest subscriptions` afterwards. Formatting should not change
behaviour, but the two test files are in the list.

---

## Out of scope

- an equivalent staleness guard for articles. `Articles.discovery_date` is `auto_now_add`, so a re-import of historical articles would flood a digest the same way the 2026-07-06 trial import did. It needs its own field and its own decision about the threshold — track it separately.
- the remaining audit findings (P1 content organizer, P2 robustness, P3 performance).

## Definition of done

- `pytest subscriptions` passes, and each new test fails when its half of the fix is reverted
- `pytest` full suite passes
- `uvx ruff check django/subscriptions/` passes
- `uvx ruff format --check django/subscriptions/` reports only the one pre-existing file
- the impact table above is re-measured against production before deploy
- `docs/subscriptions.md` no longer claims the weekly digest is exempt from the staleness check
