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

Subscribes a visitor to one or more lists.

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

`Lists` has four fields that bound how much content a single email can carry.
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

This handling is reactive only: suppression is discovered by attempting a
send and reading the response, not by a Postmark bounce webhook. A real-time
webhook and a reactivation flow are tracked separately, out of scope here.

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
