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

`Lists` has three fields that bound how much content a single email can carry.
They exist because nothing capped the number of trials on the way into an
email: a bulk import once stamped thousands of historical trials with a
same-day `discovery_date`, one notification list tried to render 3,570 trial
cards in a single send, and Postmark rejected the body outright
(`ErrorCode: 300`). Because the send failed, no `SentTrialNotification` rows
were written, so the next run rebuilt the identical oversized payload and
failed again — 413 times over 15 days before anyone noticed.

| Field | Default | Applies to |
|---|---|---|
| `article_limit` | 15 | Weekly digest, admin summary, and trial notification emails |
| `trial_limit` | 15 | Weekly digest, admin summary, and trial notification emails |
| `trial_max_age_days` | 90 | `get_trials_for_list` (admin summary, trial notification) |

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
The check only applies to `get_trials_for_list` (admin summary and trial
notification); it is not applied to the weekly digest's own trial query,
where the trial count cap alone is enough to bound the payload. Set
`trial_max_age_days` to blank on a list to disable the check entirely.

The default of 90 days (rather than 30) exists because WHO ICTRP and CTIS
feeds lag: a trial registered 45 days ago may only reach GregoryAI today. At
30 days it would be dropped by the age check and would then also age out of
the 30-day discovery window before ever qualifying for an email.

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

## Email Footer Unsubscribe Links

The email footer template (`emails/components/footer.html`) renders unsubscribe links when the following context variables are present:

| Variable | Set by | Value |
|---|---|---|
| `subscriber` | Email command | `Subscribers` instance |
| `list_id` | Email command | `digest_list.list_id` |
| `unsubscribe_base_url` | Email command | `https://<site.domain>` |
| `site` | Email command | `Site` instance |

All three weekly summary, admin summary, and trials notification commands inject these variables.

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
