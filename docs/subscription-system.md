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
<form method="POST" action="https://api.gregory-ms.com/subscriptions/new/">
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

await fetch('https://api.gregory-ms.com/subscriptions/new/', {
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
