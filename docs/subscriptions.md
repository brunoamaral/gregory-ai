# Subscription System

## Overview

GregoryAI supports multi-site, multi-list subscriptions. A subscriber can:

- Subscribe to one or more **lists** across different **sites** with consent recorded per list.
- Be unsubscribed at three levels: a single list, all lists on a site, or everything.

The system stores GDPR consent data (IP address, source site, method) for every list subscription and generates a unique token per subscriber for token-based unsubscribe links that require no login.

---

## Migration of Existing Subscriptions

When the database migrations run (`0009`), existing data is automatically carried over:

| What | How |
|---|---|
| Existing `Subscribers` rows | `unsubscribe_token` backfilled with a unique UUID per row |
| Old M2M subscriptions | Copied from the auto-generated `subscriptions_subscribers_subscriptions` table into `ListSubscription` with `consent_method='import'` and `is_active=True` |
| Per-site profiles | `SubscriberSiteProfile` created for each subscriber, site inferred from `list → team → site`; skipped if a team has no site assigned |

No manual data work is needed. The migration is idempotent — it uses `get_or_create` throughout and checks for table existence before reading from the old M2M table, so it is safe on fresh installs.

> **Consent note:** migrated rows have `consent_ip = NULL` and `consent_method = 'import'` because the original M2M table did not store consent metadata. This accurately reflects the historical data state.

---

## Data Models

### `Subscribers`

Core subscriber record. One row per unique email address.

| Field | Type | Notes |
|---|---|---|
| `subscriber_id` | AutoField PK | |
| `first_name` | CharField | Required |
| `last_name` | CharField | Optional |
| `email` | EmailField | Unique (case-insensitive) |
| `profile` | CharField | Global profile; per-site override in `SubscriberSiteProfile` |
| `active` | BooleanField | `False` = global opt-out |
| `unsubscribe_token` | UUIDField | Auto-generated; used in all unsubscribe links |
| `subscriptions` | M2M → `Lists` | Via `ListSubscription` through-model |

### `ListSubscription` (through-model)

One row per subscriber/list pair. Stores consent data and per-list opt-out state.

| Field | Type | Notes |
|---|---|---|
| `subscriber` | FK → Subscribers | |
| `list` | FK → Lists | |
| `subscribed_at` | DateTimeField | Auto-set on creation |
| `consent_ip` | GenericIPAddressField | Visitor IP at subscription time |
| `consent_source_site` | FK → Site | Which site the form was submitted from |
| `consent_method` | CharField | `web_form` / `admin` / `api` / `import` |
| `is_active` | BooleanField | `False` = unsubscribed from this list only |
| `unsubscribed_at` | DateTimeField | Set when `is_active` → `False` |

Changes are tracked via `django-simple-history` (`HistoricalListSubscription`).

### `SubscriberSiteProfile`

Per-site profile override. Allows a subscriber to have a different role on each site.

| Field | Type | Notes |
|---|---|---|
| `subscriber` | FK → Subscribers | |
| `site` | FK → Site | |
| `profile` | CharField | Same choices as `Subscribers.profile` |
| `created_at` / `updated_at` | DateTimeField | Auto-managed |

Unique together: `(subscriber, site)`.

---

## API Endpoint: Subscribe

### `POST /subscriptions/new/`

Subscribes a visitor to one or more lists. POST-only — a `GET` (crawler, link
preview fetcher, someone pasting the URL into a browser) gets `405 Method Not
Allowed` with an `Allow: POST` header rather than reaching the form-handling
code.

**Request body** (`application/x-www-form-urlencoded`):

| Field | Required | Description |
|---|---|---|
| `first_name` | Yes | Subscriber's first name |
| `last_name` | No | Subscriber's last name |
| `email` | Yes | Valid email address |
| `profile` | No | One of: `patient`, `caregiver`, `doctor`, `clinical centre`, `researcher` |
| `list` | Yes (≥1) | ID of a list to subscribe to. **Repeat this field** for multiple lists. |

**Behaviour:**
- If the email already exists, the record is updated with the submitted name and profile.
- If the subscriber was previously unsubscribed from a list, they are reactivated and consent is refreshed.
- A `SubscriberSiteProfile` is created or updated for the site the form was submitted from.
- On success: redirect to `{origin}/thank-you/`
- On error: redirect to `{origin}/error/`

The request origin is validated against the `allowed_domains` field on the current site's `CustomSetting`. The site's own `Site.domain` is always accepted; additional domains can be added (comma-separated) in the **Sites → [site] → Custom Setting** inline. If the origin doesn't match, the request is rejected. For standard non-AJAX browser form submissions, any redirect fallback uses the current site's domain. AJAX or JSON-oriented clients may instead receive a `403` JSON response and should not assume the request will be redirected.

---

## Unsubscribe Endpoints

All three endpoints accept `GET` (confirmation page) and `POST` (execute). Token-based authentication — no login required.

| URL | Scope |
|---|---|
| `/subscriptions/unsubscribe/<token>/list/<list_id>/` | Remove from one list only |
| `/subscriptions/unsubscribe/<token>/site/<site_id>/` | Remove from all lists on a site |
| `/subscriptions/unsubscribe/<token>/all/` | Global opt-out (deactivates account + all lists) |

`<token>` is the `unsubscribe_token` UUID from the `Subscribers` record. It is included in every email sent by the system.

The site scope matches `Lists.site` — the field the email footer link is generated
from — not `Team.site`. A list's team and its site are independent: `Team.site`
can be `NULL` or point at a different site than the lists it owns, so filtering
on `list__team__site_id` would silently match nothing. The response reports how
many `ListSubscription` rows were actually deactivated (`updated_count`); the
`list` and `site` scopes render a distinct "nothing to unsubscribe from" page
when that count is zero, so a request that changes nothing can never look like a
successful unsubscribe.

---

## Content Limits and Staleness Filtering

`Lists` has five fields that bound how much content a single email can carry.
They exist because nothing capped the number of trials on the way into an
email: a bulk import once stamped thousands of historical trials with a
same-day `discovery_date`, one notification list tried to render 3,570 trial
cards in a single send, and Postmark rejected the body outright
(`ErrorCode: 300`). Because the send failed, no `SentTrialNotification` rows
were written, so the next run rebuilt the identical oversized payload and
failed again — 413 times over 15 days before anyone noticed.

| Field | Default | Applies to |
|---|---|---|
| `lookback_days` | 30 | Weekly digest, admin summary, and trial notification emails |
| `article_limit` | 15 | Weekly digest, admin summary, and trial notification emails |
| `trial_limit` | 15 | Weekly digest, admin summary, and trial notification emails |
| `trial_max_age_days` | 90 | Weekly digest, admin summary, and trial notification emails |
| `article_max_age_days` | 90 | Weekly digest, admin summary, and the Latest Research section |

**Rollover:** content that does not fit in one email is not marked as sent —
only what actually gets rendered into the template is recorded in
`SentArticleNotification` / `SentTrialNotification`. Whatever was truncated
appears again on the next run, ordered newest-first, until it either gets
sent or ages out of the lookback window. This is what makes the fix
self-healing: a stuck backlog drains on its own instead of failing on repeat.

**Size guard:** even after truncating to `article_limit` / `trial_limit`, the
rendered HTML is checked against Postmark's hard limit
(`subscriptions.utils.email_limits.SAFE_BODY_CHARS`, comfortably under the
5,242,880-character `ErrorCode: 300` ceiling). If it's still too large the
content is halved and re-rendered, down to a single article and a single
trial. If even that overflows, nothing is sent — a `FailedNotification` is
written naming the list, and the failure is logged at `ERROR` so it's visible
rather than silently retried forever. With the counts above in place this
path should be unreachable in practice.

**Why staleness is measured on the trial's own date, not `discovery_date`:**
`discovery_date` only records when GregoryAI first saw a row — it says
nothing about how old the trial actually is. A bulk import can stamp
thousands of trials registered anywhere from last month to twenty years ago
with the *same* fresh `discovery_date`, and a window on `discovery_date`
alone would let all of them through. `trial_max_age_days` instead compares
against `COALESCE(date_registration, published_date)`: trials whose own date
is older than the threshold are skipped, regardless of when they were
discovered. Trials with neither date set are always kept — the per-email
`trial_limit` bounds them regardless — so an unusual-but-genuinely-new trial
is never silently dropped just because its registry didn't supply a date.
`get_trials_for_list` applies the check for all three email types — weekly
digest, admin summary, and trial notification — since it is the only one of
the two jobs the query performs that a count cap cannot substitute for: the
cap bounds payload size, but only the date check stops a bulk import from
being presented as new content. Set `trial_max_age_days` to blank on a list
to disable the check entirely.

The default of 90 days (rather than 30) exists because WHO ICTRP and CTIS
feeds lag: a trial registered 45 days ago may only reach GregoryAI today. At
30 days it would be dropped by the age check and would then also age out of
the 30-day discovery window before ever qualifying for an email.

**Why the same guard exists for articles, measured on `published_date`:**
`Articles.discovery_date` is `auto_now_add`, exactly like trials before
`trial_max_age_days` — it records when the feedreaders first saw the row,
not when the paper was published. A bulk import stamps every row with the
same day, and without a guard the whole historical set becomes eligible for
the next digest. This is the same mechanism behind the 2026-07-06 trial
flood, just not yet triggered for articles when it was found — quieter, and
therefore worse: `article_limit` already caps payloads, so nothing would
error. Subscribers would simply receive digests of decade-old papers with no
signal anything was wrong. `article_max_age_days` compares against the
article's own `published_date`; articles with no `published_date` are always
kept (46 of 49,533 in production), and `article_limit` bounds them
regardless. The check applies at every place articles are selected: all
three `send_weekly_summary` modes (all-articles, date-sort, relevancy),
`get_articles_for_list` (admin summary), and `get_latest_research_by_category`
(Latest Research) — all routed through the shared
`apply_article_max_age_filter` helper in
`subscriptions/management/commands/utils/subscription.py` so a future call
site can't be missed the way one weekly-digest query site was on the first
trials pass.

In normal operation `discovery_date` and `published_date` are effectively
the same day — measured over 6,768 articles across 120 days, excluding bulk
import days: median lag 0 days, p90 1 day, p95 3 days, p99 21 days. The
guard is therefore a no-op on normal days and only bites during an import.
90 was chosen for headroom over that p99 and for consistency with
`trial_max_age_days`, not because a shorter window would fail: 30 days would
drop 0.89% of normal-operation articles, 90 days 0.65%, 365 days 0.61% —
there's a floor of ~0.6% that is genuinely old regardless of threshold.
Validated retroactively against real imports, a 90-day guard blocks ~98% of
both historical dumps checked while letting a legitimate 182-article burst
through untouched. Set `article_max_age_days` to blank on a list to disable
the check entirely.

`get_trials_for_list` and `get_articles_for_list` both take a `days`
parameter controlling the discovery-date window (default 30). All three send
commands now pass the list's own `lookback_days` at every call site — the
weekly digest passes its resolved value (or the `--days` CLI override when
set); `send_admin_summary` and `send_trials_notification` pass
`lst.lookback_days` directly. Previously only the weekly digest read the
field at all, and even there it governed articles but not trials, so editing
`lookback_days` on an admin-summary-only or trial-notification-only list did
nothing.

Widening the content window without widening the sent-record window would
reopen audit finding 11 (an item still inside the content window but outside
a fixed 30-day exclusion window gets treated as unsent and re-mailed every
run). `send_admin_summary` and `send_trials_notification` compute their
`threshold_date` the same way the weekly digest already does:
`now() - timedelta(days=max(30, lst.lookback_days))`, so the exclusion window
is always at least as wide as the content window.

**Featuring trials by recruitment status:** `EmailContentOrganizer.organize_trials`
splits trials into `featured_trials` (recruiting) and `regular_trials`
(everything else) by checking `Trials.recruitment_status_normalized ==
"recruiting"`. It does not fall back to a substring match on the raw
`recruitment_status` string, and does not guess when the normalized field is
`NULL` (the normalizer didn't recognise the raw value) — a `NULL` normalized
status is always treated as not-recruiting. This mirrors how `trial_card.html`
and `trial_card_simple.html` already key their status colours off the same
field. See [`docs/trials-field-normalization.md`](trials-field-normalization.md)
for how raw registry strings map onto `recruitment_status_normalized`.

---

## Featured/Regular Article Split (Design Decision, 2026-07-28)

Both `weekly_summary.html` and `admin_summary.html` used to receive articles
pre-split into `articles` ("featured", high-confidence) and
`additional_articles` ("regular", needs review), each rendered through the
same component with the same parameters. In the weekly digest, that split had
**no visual effect** — no heading, no styling difference distinguished the two
loops — so it existed only to reorder articles, at the cost of a per-article
manual-review/ML query (`EmailContentOrganizer._filter_high_confidence` /
`_get_max_ml_score`) with N+1 characteristics.

The two emails were resolved differently, because they serve different
purposes:

- **Weekly digest — split removed.** `EmailContentOrganizer._organize_weekly_articles`
  now always returns a single flat list (`featured_articles` is always
  empty), ordered by discovery date, matching what `article_sort_order='date'`
  already did. Selection (which articles qualify) still happens in
  `send_weekly_summary` before the organizer runs; its own priority ranking
  (manual review + ML consensus, then date) still decides order when
  `article_limit` truncation applies. This is a reading list, where the split
  was invisible and the ordering barely mattered — subscribers see no change.
- **Admin summary — split kept, made visible and correct.** The admin summary
  exists so a human can triage, and "already high-confidence" versus "needs
  review" is exactly the distinction that email is for. `admin_summary.html`
  now renders separate "High-Confidence" and "Needs Review" section headings
  so the split is no longer silent, and the ML threshold + subject-scoping
  bugs below were fixed since the split is now load-bearing.

---

## Latest Research Section (Weekly Digest)

Latest Research is **new articles since the subscriber's last email for that
list, grouped by team category** — a delta, not a standing digest, and
deliberately **articles-only**.

- It shares the exact same bookkeeping as the main article section:
  `SentArticleNotification` keyed on `(article, list, subscriber)`. An article
  shown in either section is suppressed from both on the next run — the two
  sections compete for the same items rather than drawing independently.
- The candidate pool for a category is `TeamCategory.articles` (populated by
  `rebuild_categories`), bounded by a lookback floor of the list's
  `lookback_days` (or the `--days` CLI override), not a fixed 30 days.
- Within a single email, an article that also matches the list's subjects
  renders once, in the main section — the main section is selected first and
  Latest Research is deduplicated against it.
- The section is built in `send_weekly_summary`, not inside
  `EmailContentOrganizer`: `organize_latest_research_by_category` is pure
  formatting over a `{TeamCategory: [Articles]}` map the command hands it
  (via `prepare_optimized_context(..., latest_research_category_map=...)`),
  so it flows through `render_within_limit`'s shrink loop like the main
  articles and trials, and its articles are included in `org_content_map`.
- A digest list with no `latest_research_categories` configured, or a
  category with no qualifying articles, simply omits the section
  (`has_latest_research=False`).

**Trials are excluded from this section by decision, not oversight.**
`TeamCategory.trials` (`Trials.team_categories`, `related_name="trials"`) is a
real, populated relation, and a future reader will notice it is unused here —
that is intentional. A category whose only new content is trials contributes
nothing to Latest Research.

---

## Bounce and Suppression Handling

Postmark returns HTTP 422 with `ErrorCode: 406` when the recipient is on its
suppression list — a hard bounce, a spam complaint, or a manual suppression.
Left unhandled, nothing stops the same address from being retried on every
subsequent run: one address in production was retried 210 times before this
was noticed.

All three send commands (`send_weekly_summary`, `send_admin_summary`,
`send_trials_notification`) now route every Postmark response through
`subscriptions.utils.postmark.classify_postmark_response`, which normalises a
`requests.Response`, a plain dict, or `None` into `(delivered, error_code,
detail)`. This exists because `requests.Response.__bool__` is `self.ok`, so a
422/500 response is falsy — a bare `if result:` check silently treats a real
error response as "no response" and loses the actual status and error code.
`classify_postmark_response` never tests the response for truthiness, so this
class of bug can't recur.

When `error_code == 406` (`subscriptions.utils.postmark.POSTMARK_INACTIVE_RECIPIENT`),
the subscriber is deactivated globally via
`subscriptions.utils.suppression.deactivate_subscribers` — the same helper the
admin "Disable all emails" action uses (see the `Subscribers.active` field
above: it's a global switch, not a per-list one), so the two paths can't
drift. A hard bounce, a spam complaint, and a manual suppression are all
treated the same way: the address must not be mailed again from *any* list,
not just the one that triggered the 406. Any other non-200 response is
recorded as a normal failure without touching the subscriber's active state.

Every outcome is visible in `FailedNotification` (`reason` holds the detail
string from `classify_postmark_response`), and a 406 additionally shows up in
`Subscribers.active` going to `False` — check the model's change history for
when and why.

A connection-level failure (timeout, DNS, reset) from the `send_email` call
itself is also caught (`requests.RequestException`) and recorded as a
`FailedNotification` rather than aborting the rest of the run — previously a
single dropped connection could skip every remaining subscriber and list for
that cron invocation with no record of it happening.

This reactive path never goes away: it is the only thing that catches a
suppression whose webhook (below) was lost. Both paths converge on
`subscriptions.utils.suppression.deactivate_subscribers` so they cannot
drift.

---

## Suppression and Reactivation Webhook

**`POST /webhooks/`** — Postmark's webhook, configured on the **broadcast**
message stream (the only stream `send_email` sends on today). The path is
deliberately provider-agnostic (`/webhooks/`, not `/webhooks/postmark/`):
dispatch happens on the payload's `RecordType`, and anything that doesn't
look like a Postmark event is logged and ignored rather than acted on.

Postmark is configured to send: **Delivery, Bounce, Open (first open only),
Subscription Change** (Spam Complaint and Link Click join this list once
the author outreach webhook config is applied — see
[author-outreach.md § Postmark setup](author-outreach.md#postmark-setup)).
Delivery, Bounce, Spam Complaint, Open, and Click never change suppression
state — Bounce is supplementary detail (hard vs soft; a soft bounce does
not suppress), Delivery/Open/Click are irrelevant to suppression.
**Subscription Change drives everything** here: it is the
superset event for suppression state and fires in both directions
(`SuppressSending: true` / `false`). Spam complaints reach us through
`SuppressionReason: "SpamComplaint"` on this event even with the Spam
Complaint event type disabled.

**Every recognised event except Subscription Change is separately logged
to `EmailEvent`**, an append-only record kept alongside (not instead of)
the `SuppressionEvent` handling above. Subscription Change is deliberately
excluded from this second write: `SuppressionEvent`, above, is its sole
record. The two were dual-written for a short time; that duplicated the
event in the admin and, worse, collided, since Postmark sends an all-zero
placeholder MessageID on a Subscription Change with no originating message
and `EmailEvent`'s dedup key (before it grew a `recipient` component — see
below) had no way to tell two different people apart in the same second.
See
[Email Message and Event Log](#email-message-and-event-log) below: this is
new behaviour — before it existed, Delivery/Bounce/Open were accepted and
silently discarded.

### Authentication

HTTP Basic Auth, credentials embedded in the URL Postmark posts to:
`https://<user>:<pass>@api.brain-regeneration.com/webhooks/`. Postmark does
**not** support HMAC webhook signatures — despite a TypeScript sample in
their docs implying otherwise, there is no signature header to verify, so
none is implemented. Credentials live in `POSTMARK_WEBHOOK_USERNAME` /
`POSTMARK_WEBHOOK_PASSWORD` (environment only, never in the repo), compared
with `hmac.compare_digest`. A missing/wrong credential returns **403**, not
401 — Postmark stops retrying on 403 and keeps retrying everything else, so a
misconfigured credential fails once and loudly instead of retrying for hours.

### Idempotency and ordering

Deduplication key is **`(RecordType, Recipient, ChangedAt)`**, enforced by a
DB constraint on `SuppressionEvent` — **not** `MessageID` as Postmark's own
docs suggest. Postmark sends an all-zero placeholder `MessageID`
(`00000000-0000-0000-0000-000000000000`) for Subscription Change events with
no originating message (e.g. a manual suppression made in the Postmark UI),
so deduping on `MessageID` alone would collapse every such event into one and
silently drop the rest. `MessageID` is still recorded as data.

Events can arrive out of order. Each incoming event's `ChangedAt` is compared
against the most recent `ChangedAt` already recorded for that recipient;
anything older is recorded (`action_taken="record_only"`) but never changes
subscription state, so a stale suppress landing after a newer unsuppress (or
the reverse) can't win.

An unrecognised `Recipient` (test send, old address, different system on the
same host) is recorded with `subscriber=NULL` — never an error, never a
created `Subscribers` row.

### The `SuppressionEvent` model

One row per suppress/unsuppress event — the audit trail, and the prerequisite
for reactivation. See
[02.1-database-tables-and-fields.md](02.1-database-tables-and-fields.md) for
the full field list. The field that matters most is
`deactivated_list_subscription_ids`: the exact `ListSubscription` IDs a
suppression turned off, captured by `deactivate_subscribers` at suppression
time. Before this model existed, deactivation used `queryset.update()`
exclusively, which bypasses simple-history's `post_save` hook — measured on
production data, roughly 82% of past deactivations left no reconstructable
trace of what they had changed. Reactivation restores exactly this recorded
set, never a guess at "everything the subscriber probably still wants."

`record_type` distinguishes the three paths that can deactivate a
subscriber, all funnelled through `deactivate_subscribers`:
`SubscriptionChange` (this webhook), `ReactiveSendFailure` (the 406 path
above), `AdminManual` (the admin "Disable all emails" bulk action). Only
`SubscriptionChange` events carry a Postmark `SuppressionReason`
(`HardBounce` / `SpamComplaint` / `ManualSuppression`) — the other two are
never auto-restored by an incoming unsuppress, because their cause is
unrelated to Postmark's own suppression list.

### Reactivation policy

| `SuppressionReason` on the unsuppress | Behaviour |
|:--|:--|
| `HardBounce` | auto-restore (subject to the staleness cap below) |
| `ManualSuppression` | auto-restore (subject to the staleness cap below) |
| `SpamComplaint` | **never** — sticky, see below |
| anything else, or missing | record only, flagged for review |

"Restore" means both the `Subscribers.active` flag **and** the exact
`ListSubscription` rows named in the original suppression's
`deactivated_list_subscription_ids` — restoring only the global flag is a
no-op in practice, since the subscriber would still hold no active list
subscriptions. Restoring exactly that recorded set (and nothing else) is what
prevents re-subscribing someone to a list they had already left on their own
before the suppression happened.

**Spam complaints are sticky.** A complaint is a recorded objection to
processing and outranks a later unsuppress, including one performed by staff
in the Postmark UI. There is no automatic path back from `SpamComplaint`; a
human override would need to be an explicit, recorded admin action, not a
side effect of a webhook.

The semantics of the `Origin` field (`Recipient` / `Customer`) on an
*unsuppress* event are not established by Postmark's documentation, so it is
recorded but never used to gate reactivation — only `SuppressionReason` and
the staleness cap decide.

**Staleness cap** — `subscriptions.utils.postmark_webhook.REACTIVATION_MAX_AGE`
(365 days, a module constant rather than a Django setting, since it's a
policy decision that shouldn't be changed without revisiting the reasoning).
Auto-restore requires the original suppression to be less than 365 days old,
measured from that original event's `ChangedAt`. **When no matching
`SuppressionEvent` exists at all — true for every suppression that predates
this model — reactivation fails safe and does not restore.** This is
intentional, not a gap to fix: an unsuppress for one of those subscribers
will always show up in the admin as recorded-and-flagged, because their age
and prior subscription state are both unknowable.

### Retry behaviour and why the endpoint responds fast

| Events | Retry schedule |
|:--|:--|
| Bounce, Inbound | 1m, 5m, 10m×3, 15m, 30m, 1h, 2h, 6h — ~10 hours |
| Delivery, Open, Click, **Subscription Change** | 1m, 5m, 15m — **~21 minutes** |

Subscription Change — the event this whole feature depends on — is in the
short bucket. More than ~20 minutes of downtime and those events are gone for
good; Postmark does not replay them later. The view responds 200 as fast as
possible for exactly this reason, and everything above stays cheap enough to
do inline. This is also why the reactive 406 path is not being retired: it is
the backstop for suppression events lost to an outage that outlasted the
retry window.

### Tagging

`send_email` accepts an optional `tag` parameter (Postmark's `Tag` field —
one per message), passed through as the email type: `weekly_summary`,
`admin_summary`, `trial_notification`, `announcement`, `author_outreach`.
Not required for suppression (`Recipient` already identifies the
subscriber) but makes Postmark-side stats and debugging much better. Lists
are not used as tags — Postmark's tag reporting is designed for a small,
low-cardinality set.

`send_email` also accepts `metadata`, `reply_to`, `track_opens`, and
`track_links`, all optional and all defaulting to `None`/`False`. None of
the four original senders (weekly digest, admin summary, trial
notification, announcement) pass them, so their Postmark payload is
unchanged; `send_author_outreach` (see
[author-outreach.md](author-outreach.md)) is the one caller that opts in
per message rather than per stream. `metadata` is where
`EmailMessage.msg_token` gets echoed back to Postmark, for a webhook event
to correlate against later (see below).

---

## Email Message and Event Log

Two tables, written for **every** sender (weekly digest, admin summary,
trial notification, announcement, and — from author outreach onward — that
feature too), not just outreach:

- **`EmailMessage`** — one row per message handed to Postmark. Written at
  send time by `record_sent_message()`
  (`subscriptions/management/commands/utils/send_email.py`), called right
  after `send_email()` by every sender, success or failure. Holds the
  Postmark `MessageID`, an opaque `msg_token` UUID, recipient, tag, stream,
  and the aggregate outcome fields the webhook updates later: `delivered_at`,
  `first_opened_at` (first open only), `bounced_at` / `bounce_type`,
  `complained_at`.
- **`EmailEvent`** — one row per webhook call, append-only. Written by
  `subscriptions.utils.email_events.handle_email_event`, called from
  `postmark_webhook` for every recognised `RecordType` **except**
  `SubscriptionChange`: `Delivery`, `Bounce`, `SpamComplaint`, `Open`,
  `Click`. `SubscriptionChange` is handled entirely by
  `handle_subscription_change` instead (`SuppressionEvent`, above, is its
  sole record) — the two were briefly dual-written; see "Deduplication"
  below for why that was removed.

### Correlation

Two independent keys, tried in order: `Metadata.msg_token` (the UUID a
sender can choose to echo back via `send_email(metadata=...)`), then
Postmark's own `MessageID`. Neither may match — the four existing senders
don't pass `metadata` yet, and a message sent before this feature shipped
has no `EmailMessage` row at all — in which case the event is still
recorded, with `email_message=NULL`.

### Deduplication

Unique constraint: `(record_type, message_id, occurred_at, recipient,
link_url)`. Widened from an original `(record_type, message_id,
occurred_at)` after two real collisions surfaced:

- **`recipient`** — Postmark sends an all-zero placeholder `MessageID`
  (`00000000-0000-0000-0000-000000000000`) on an event with no originating
  message. Without `recipient` in the key, two different people affected in
  the same second collided on that placeholder and the second was silently
  discarded as a "replay" it wasn't. (This was first found via
  `SubscriptionChange`, which is why that type was also pulled out of this
  table entirely — see above — but the same all-zero-`MessageID` hazard
  isn't unique to that one type, so `recipient` stays in the key for every
  `record_type` this table does still record.)
- **`link_url`** — corporate mail scanners (Proofpoint, Mimecast, and
  similar — common on the university addresses author outreach targets)
  click every link in a message within the same second to vet it. Without
  `link_url` in the key, three `Click` events on one message in the same
  second — three different `OriginalLink` values — collapsed onto a single
  row and dropped two real clicks.

A genuine replay (the identical payload delivered twice) still dedupes to
one row, since every field in the key is identical both times. See
`subscriptions/tests/test_email_events.py::WidenedDedupKeyTest` and
`::ReplayIsANoOpTest` for the regression coverage.

### What is deliberately not stored

Open and Click payloads carry `Geo`, `IP`, `UserAgent`, `OS`, `Client`,
`Platform`, and `ReadSeconds` for the recipient; Bounce/SpamComplaint
payloads additionally embed the full original message `Content`. **None of
it is stored.** `EmailEvent` has no field for any of them — see its model
docstring (`subscriptions/models.py`) for the full list and reasoning, and
`subscriptions/tests/test_email_events.py::PrivacyRegressionGuardTest` for
the regression guard that feeds a full Open payload and asserts the stored
row is clean.

Open and link tracking (`TrackOpens`/`TrackLinks`) are opt-in per message
via `send_email()` and default off — the four existing senders don't set
them, so this ships with no behaviour change for weekly digest, admin
summary, trial notification, or announcement email.

`track_links=True` sends `TrackLinks: "HtmlAndText"`; passing a string
instead (`"HtmlOnly"`, `"TextOnly"`) forwards it verbatim. That matters for
any message with a link that must stay untracked: Postmark's per-link
`data-pm-no-track` marker is an HTML attribute, so a URL excluded in the
HTML body is still rewritten and tracked in the text body under
`HtmlAndText`. Author outreach sends `"HtmlOnly"` for exactly this reason —
see [author-outreach.md](author-outreach.md).

### A quirk worth knowing: `Email` vs `Recipient`

Every Postmark webhook event names the recipient field `Recipient` —
**except** `Bounce` and `SpamComplaint`, which use `Email` instead
(confirmed against Postmark's own documented example payloads). Easy to get
backwards, since nothing about the field's absence raises; `handle_email_event`
looks up the correct field per `RecordType` rather than assuming one name.

### Retention

| Table | Default retention | Command |
|:--|:--|:--|
| `EmailEvent` | 180 days | `prune_email_events --days N --dry-run` |
| `EmailMessage` | 730 days, **except** rows an `AuthorOutreach` references (never pruned) | `prune_email_messages --days N --dry-run` |

**Invariant, restated in both commands' docstrings: pruning telemetry must
never weaken a suppression.** Neither command ever touches
`AuthorContactOptOut` or `SuppressionEvent` — both are permanent, by design,
independent of this log.

### Admin

Both models are registered read-only (`has_add_permission` /
`has_change_permission` return `False`) — `EmailMessageAdmin` includes an
inline listing of its `EmailEvent` rows.

---

## Author Outreach Opt-Out

`AuthorContactOptOut` (see
[02.1-database-tables-and-fields.md](02.1-database-tables-and-fields.md) for
the full field list) is a global "never contact this address again" list,
keyed on the email address — **not** on `Authors` or `Subscribers`. That
independence is deliberate: an author contacted under
[author outreach](author-outreach.md) is not a newsletter subscriber, so
this table and `Subscribers`/`ListSubscription` must never be able to
drift into each other. The same address can appear in both, correctly, at
the same time.

Every write goes through `subscriptions.utils.author_optout.
record_author_opt_out(email, reason, note="")`, which is idempotent (an
address already opted out is left alone, regardless of which `reason` a
later event carries) and never raises — every caller below depends on that,
the same way `handle_email_event` and `handle_subscription_change` depend
on never turning a webhook call into a non-200 response.

### The three write paths

| Trigger | Caller | `reason` |
|:--|:--|:--|
| Hard bounce (Postmark Bounce `Type` of `HardBounce` or `BadEmailAddress`) | `handle_email_event` | `hard_bounce` |
| Spam complaint (`SpamComplaint` record) | `handle_email_event` | `spam_complaint` |
| A `SubscriptionChange` suppression whose recipient matches an existing `AuthorOutreach` row | `handle_subscription_change` | `hard_bounce` / `spam_complaint` / `admin`, mapped from Postmark's `SuppressionReason` via `optout_reason_for_suppression_reason` (`ManualSuppression`, blank, or anything unrecognised falls back to `admin`) |
| The opt-out link, `POST`ed | `subscriptions.views.author_optout` | `opt_out` |

The first two apply to **any** message this system sends, not only
outreach — a hard bounce or complaint means the address must never be used
again anywhere, regardless of which sender triggered it. The third only
fires when `SuppressSending` is `True`; an *un*suppress is never read as
"undo the opt-out" — opt-out is one-directional, mirroring spam complaints
being sticky in the reactivation policy above. All three writes are
independent of, and cannot block, the `SuppressionEvent` handling described
earlier in this document: a failure recording an opt-out is caught and
logged separately from (and in addition to) `record_author_opt_out`'s own
internal try/except, so it can never cost a webhook call its 200 response
or an already-written `EmailEvent`/`SuppressionEvent` row.

### The opt-out endpoint

**`GET`/`POST /subscriptions/author-optout/<uuid:token>/`** — registered in
`admin/urls.py` next to the three unsubscribe routes above, but a distinct
system: `token` is `AuthorOutreach.opt_out_token`, not
`Subscribers.unsubscribe_token`.

- **`GET`** renders a confirmation page and mutates nothing. Mail clients
  and security scanners prefetch links; a prefetching `GET` that performed
  the opt-out would silently unsubscribe someone who never clicked
  anything.
- **`POST`** performs the opt-out via `record_author_opt_out` and is
  idempotent, mirroring `_unsubscribe_confirm`'s GET/POST split and reusing
  its template styling (`templates/subscriptions/author_optout_confirm.html`
  / `author_optout_done.html`).
- An unknown token 404s (`get_object_or_404`).

The opt-out affects **future email only**. It does not change
`AuthorOutreach.status` on the row the token resolved (that queue row stays
whatever it was — cancelling a still-pending send is the admin's Skip
action, a separate thing), and it never touches `Authors` or anything the
public author profile page reads — the profile page stays published
exactly as it was.

---

## Announcement Send Lifecycle

Announcements (one-off emails sent to selected `Lists` from the admin, as
opposed to the recurring digest/summary/notification commands above) went
through the same robustness pass as the three digest commands, plus a
structural change to get the actual Postmark calls out of the request cycle.

**Status machine:** `draft` → `queued` → `sending` → `sent` / `failed`.

- **`draft`** — editable. Clicking "Queue Send to Subscribers" in the admin
  (`AnnouncementAdmin.send_view`) validates every target list's site/domain
  configuration (`subscriptions.utils.announcement_send_validation.validate_announcement_send_config`)
  and, if all lists pass, flips the announcement to `queued` and returns
  immediately — no Postmark call happens in this request.
- **`queued`** — waiting for the `send_announcement` management command
  (cron-driven; see `docs/cookbook.md`) to pick it up. This is what actually
  gets an announcement out of the request/response cycle: the widest
  announcement seen in production (192 subscribers) takes 58–192 seconds of
  Postmark round-trips at 0.3–1.0s each, which straddles nginx's 60s
  `proxy_read_timeout` and gunicorn's 300s worker timeout
  (`nginx-example-configuration/nginx.conf`, `Dockerfile`) — a send that used
  to run inside the admin request could be silently killed mid-flight by
  either one, leaving no record of where it stopped.
- **`sending`** — the command has started `subscriptions.utils.announcement_send.send_announcement`
  for this announcement. If the process is killed mid-run (OOM, restart), the
  announcement is stuck here — use the **"Reset stuck 'Sending' announcements
  back to Draft"** admin action to move it back to `draft`, then queue it
  again.
- **`sent`** / **`failed`** — set once the send loop finishes, based on
  non-suppressed failures only (see below). `failed` announcements show the
  send buttons again (unlike `sent`, which is locked) so they can be queued
  and retried directly from the change page.

**Idempotent and resumable by construction:** `send_announcement()` skips any
subscriber who already has a successful `AnnouncementRecipient` row for that
announcement. This means queueing a `failed` announcement again, or
resetting-then-requeueing a stuck `sending` one, never re-mails anyone who
was already delivered to — the classic trap of a naive retry re-sending to
an entire list. `recipients_count` / `failures_count` are recomputed from
`AnnouncementRecipient` rows on every run rather than incremented, so the
counts stay correct across any number of partial runs.

**Resume semantics reach further than "retry the failures."** The skip rule
is "already successfully delivered", not "was part of the original attempt" —
so re-queueing an announcement mails **every subscriber currently on the
target lists who hasn't already received it**, which includes anyone who
subscribed *after* the original send, not only prior delivery failures. This
follows correctly from an idempotent-by-`AnnouncementRecipient`-row design,
but it surprised an operator retrying announcement #9: of the 44 subscribers
who would receive it on a retry, only 12 were genuine delivery failures — the
other ~32 had joined the list in the three months since the original April
send, and would receive a three-month-old announcement alongside a fresh one.
The `send_view` confirmation page (`AnnouncementAdmin.send_view`,
`admin/subscriptions/announcement/send_confirm.html`) reflects this directly:
it reports the post-skip count that will actually be queued to send,
separately from the count of subscribers who already received it and will be
skipped, computed the same way `send_announcement()` filters — by excluding
subscribers with an existing successful `AnnouncementRecipient` row for that
announcement — so this fan-out is visible before queueing, not discovered
afterwards.

**Suppression is handled exactly like the three digest commands** (see
"Bounce and Suppression Handling" above): the response is routed through
`classify_postmark_response`, a 406 deactivates the subscriber via
`deactivate_subscribers`, and the `AnnouncementRecipient` row is marked
`suppressed=True` rather than a plain failure. A suppressed recipient does
not count toward `failures_count` or push the announcement to `failed` — one
bounced address on an otherwise-clean send should not read as "the send
failed". Two live announcements motivated this: #12 (177 delivered, 5
suppressed) and #9 (176 delivered, 12 suppressed) were both sitting in
`failed` for no reason other than this miscount, and retrying either under
the old code would have re-mailed everyone who had already received it.

A `requests.RequestException` (timeout, DNS, reset) for one subscriber is
recorded on that subscriber's row and the loop continues to the next —
previously any exception, including programming errors, was caught by a
blanket `except Exception` and silently counted as a delivery failure.

---

## Email Footer Unsubscribe Links

The email footer template (`emails/components/footer.html`) renders unsubscribe links when the following context variables are present:

| Variable | Set by | Value |
|---|---|---|
| `subscriber` | Email command | `Subscribers` instance |
| `list_id` | Email command | `digest_list.list_id` |
| `unsubscribe_lists` | Announcement send | `[(list_id, list_name), ...]` |
| `unsubscribe_base_url` | Email command | `https://<site.domain>` |
| `site` | Email command | `Site` instance |

All three weekly summary, admin summary, and trials notification commands inject
`list_id`, since each of those emails is inherently single-list — the footer
renders one "Unsubscribe from this list" link.

Announcements are different: a single announcement can target several lists at
once, and a subscriber can be on more than one of them. Rather than attribute
the send to whichever list happened to be encountered first (ambiguous — the
recipient can't tell which subscription "this list" refers to), the footer
renders one named "Unsubscribe from &lt;list name&gt;" link per list the
subscriber actually matched, via `unsubscribe_lists`. A subscriber on three of
the announcement's lists still receives exactly one email, with three links.
When `unsubscribe_lists` is present it takes precedence over `list_id`; when
absent (every digest command) the single-`list_id` behaviour is unchanged.

---

## Frontend Integration Guide

### Subscription Form

Post to `POST /subscriptions/new/` with `Content-Type: application/x-www-form-urlencoded`. A minimal form looks like:

```html
<form method="POST" action="https://api.brain-regeneration.com/subscriptions/new/">
  <input type="text"   name="first_name" required placeholder="First name" />
  <input type="text"   name="last_name"  placeholder="Last name" />
  <input type="email"  name="email"      required placeholder="Email address" />

  <select name="profile">
    <option value="">-- Select your role --</option>
    <option value="patient">Patient</option>
    <option value="caregiver">Caregiver</option>
    <option value="doctor">Doctor</option>
    <option value="clinical centre">Clinical Centre</option>
    <option value="researcher">Researcher</option>
  </select>

  <!-- One hidden (or visible checkbox) input per list -->
  <!-- List IDs are available from GET /lists/ in the admin or ask your backend team -->
  <input type="hidden" name="list" value="1" />

  <!-- Multiple lists: repeat the field with different values -->
  <label><input type="checkbox" name="list" value="2" /> MS Research weekly digest</label>
  <label><input type="checkbox" name="list" value="3" /> Clinical Trials alerts</label>

  <button type="submit">Subscribe</button>
</form>
```

**Key points:**
- The `list` field must appear **once per list ID** — use `name="list"` repeated, not `name="list[]"`.
- The form's `Origin` header (set automatically by the browser) must match a domain in the site's `CustomSetting.allowed_domains` (or the site's own domain), otherwise the request is rejected and the redirect falls back to the API domain. Ask your backend team to add your frontend domain to the site's `allowed_domains` in the admin (one site-level setting covers every list on that site).
- The endpoint redirects on both success and failure (no JSON response). Handle the destination pages:
  - `/thank-you/` — shown after a successful subscription
  - `/error/` — shown when the form is invalid

### Multi-list, multi-site scenario

If your site has users subscribing to lists that belong to different teams/sites, just include all the relevant `list` IDs in the same form submission. The backend will create one `ListSubscription` row per list, each recording the source site from the request origin.

### CORS

The endpoint accepts cross-origin POST requests. The nginx configuration allows `Access-Control-Allow-Origin: $http_origin` for all methods.

If submitting via `fetch` or `axios` instead of a plain HTML form, set `Content-Type: application/x-www-form-urlencoded` and serialize the body accordingly, for example:

```js
const params = new URLSearchParams();
params.append('first_name', 'Jane');
params.append('email', 'jane@example.com');
params.append('list', '2');
params.append('list', '3');  // repeat for each list

await fetch('https://api.brain-regeneration.com/subscriptions/new/', {
  method: 'POST',
  body: params,
});
// The response will be a redirect — follow it or ignore it depending on your UX
```

> Note: `JSON` bodies are **not** supported. The endpoint reads `request.POST`, which requires form-encoded data.

### Getting List IDs

List IDs are stable integers assigned by the database. To find them:

- Django admin → **Subscriptions → Lists** — the ID is shown in the URL when you open a list record.
- Ask your backend team to provide the IDs for each list you need to subscribe to.
