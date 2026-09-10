# Organisations, teams, and sites

> Audience: operators configuring a multi-tenant GregoryAI instance.

GregoryAI supports a multi-tenant structure where content, credentials, and email sending can be scoped at the **Organisation** or **Team** level. This document explains how to configure each layer and how the system resolves which settings to use when sending emails.

---

## Concepts

| Entity | Description |
|---|---|
| **Organisation** | Top-level grouping (provided by `django-organizations`). Owns teams, credentials, and sites. |
| **Team** | Belongs to one Organisation. Owns subjects, sources, and optionally its own site and credentials. |
| **Site** | A Django `sites` framework entry (`domain` + `name`). Used as the base URL for email sender addresses. |
| **CustomSetting** | Per-site settings: site title, email footer, admin email, sender email prefix, whether the site publishes author profile pages, the [sitemap](03-api-and-rss-feeds.md#sitemaps) switches (master switch, curated subjects, articles-relevant-only, include trials, trial recruitment statuses), and the export/about metadata (description, contact email, data licence, citation) shown on the "About this file" sheet of `export_trials_xlsx` workbooks. |
| **TeamCredentials** | Postmark API token and URL scoped to a specific team. |
| **OrganisationCredentials** | Postmark API token and URL scoped to an organisation. Used as fallback when a team has no credentials. |
| **OrganisationSite** | Links an Organisation to one or more Sites. One can be marked as `is_default`. |

---

## API visibility for private organisations

Each Organisation has an `OrganizationApiSettings` record with a `make_api_public` flag.

- When `make_api_public = True` the organisation's data is visible to **all callers**, including anonymous requests.
- When `make_api_public = False` (the default) the data is **private** — only callers that have been explicitly granted access can read it.

### Granting access to a private organisation

**Via API key** — create an `APIAccessScheme` in the admin under **API > API Access Schemes**:

| Field | Value |
|:------|:------|
| Client name | Descriptive label for the consumer |
| Organisation | The private organisation |
| Begin / end date | Validity window |
| IP addresses | Optional comma-separated allowlist |

The consumer sends the generated key in every request:

```http
Authorization: <raw_api_key>
```

**Via user account** — add the user to the organisation as an `OrganizationUser`. After logging in they will see the organisation's data automatically.

In both cases the caller can append `?include_public=true` to a request to also receive data from public organisations.

See [03-api-and-rss-feeds.md](03-api-and-rss-feeds.md#accessing-private-organisation-data) for the full visibility rules table.

---

## Admin visibility

The Django admin (`/admin/`) uses a different visibility rule than the public API and RSS feeds. The API and feeds are [subject-scoped](03-api-and-rss-feeds.md#visibility-rules-summary): a caller sees a row when one of its subjects sits in a site's `scope_subjects`. The admin is deliberately **not** — it scopes content through **source → team → organisation** instead.

This is a design choice, not an inconsistency to fix:

- A `Sources` row is attached at ingestion, before anyone has looked at the content. `subjects` and `teams` are assigned by a curator afterwards. Scoping the admin on subjects would hide exactly the not-yet-curated rows a curator needs to see in order to curate them.
- Publication (which sites, and anonymous callers, can read) and editorial access (which staff member can edit a row) are independent policies. Changing one must never silently change the other.

| Caller | Sees |
|:-------|:-----|
| Staff user | Content whose source's team belongs to one of their organisations, optionally narrowed to a site that organisation owns |
| Staff user, "Not in any site's scope" filter | Their own organisation's content that is in no site's `scope_subjects` yet — the curation queue |
| Superuser | Everything, including content with no source at all |

Content with no `Sources` row at all cannot be attributed to any organisation, so it is visible only to superusers. This is a small sliver of legacy data rather than a normal outcome of ingestion (measured on the development database, before the September 2026 prunes: 375 articles, 0 trials — production, having been pruned, is not directly comparable).

### List filters

The Articles and Trials admin changelists each carry two filters built on this rule:

- **Site** — narrows the (already organisation-scoped) list to one site the caller's organisation owns, via `OrganizationSite`. It can only narrow: `get_queryset()` applies the organisation scoping first, and the filter's own choices never include another organisation's sites.
- **Not in any site's scope (curation queue)** — the same "not yet curated" set that's invisible to the API, but scoped to the caller's own organisation rather than shown globally, which would otherwise leak every organisation's uncurated content to every staff member. Superusers see the true global queue.

`/organizations/`- and `/teams/`-style admin pages are already organisation-keyed directly and carry neither filter.

---

## Setting Up Sites

Sites are managed at **Sites > Sites** in the Django admin (`/admin/sites/site/`).

Each Site has:
- **Domain name** — used in email sender addresses (e.g. `gregory@example.com`)
- **Display name** — human-readable label
- **Custom Settings** — editable inline when creating or editing a Site:
  - **Title** — name of the site used in emails
  - **Email footer** — footer text for newsletters
  - **Admin email** — recipient for admin digest emails
  - **Sender email prefix** — local part of the `From` address (default: `gregory`)
    - Example: prefix `ms-research` on domain `example.com` → `ms-research@example.com`

---

## Linking Sites to Organisations

An Organisation can have one or more Sites. To configure this:

1. Go to **Organisations > Organisations** in the admin.
2. Open an Organisation.
3. In the **Sites** inline section, add one or more Sites.
4. Tick **Is default** on the Site that should be used as the fallback for teams without an explicit site.

Only one Site per Organisation can be marked as default (enforced by a database constraint).

---

## Author profile page links

Some sites publish an author profile page for each author at `/authors/<orcid>/`. Tick **Has author pages** in the Site's Custom Setting (under **Website URLs**) to have GregoryAI link author names there instead of `orcid.org`:

- **Weekly digest and admin summary emails** — each author's name links to `https://{site.domain}/authors/{ORCID}/`.
- **Author RSS feed** (`/feed/author/<orcid>/`) — the feed's `<link>` element points at the same URL.

When the flag is off (the default), all of the above link to `https://orcid.org/{ORCID}` instead. The base URL is derived from the Site's `domain`, so no separate URL field is needed.

---

## Assigning a Site to a Team

If a team sends emails from a different domain than its organisation, assign a Site directly to the team:

1. Go to **Gregory > Teams** in the admin.
2. Open a Team.
3. Set the **Site** field to the appropriate Site.

When a Site is assigned to the Team, it takes precedence over the Organisation's default Site.

---

## Site Resolution Order

When sending emails, GregoryAI resolves the Site for a team using this fallback chain:

1. **Team's own site** — if `Team.site` is set, use it.
2. **Organisation's default site** — if the Organisation has an `OrganisationSite` with `is_default=True`, use its Site.
3. **Organisation's first site** — if no default is set, use the first Site linked to the Organisation.
4. **Global fallback** — use `Site.objects.get_current()` (the site configured via `SITE_ID` in Django settings).

---

## Setting Up Postmark Credentials

Postmark credentials (API token and API URL) can be set at the team or organisation level.

### Team credentials

1. Go to **Gregory > Teams** in the admin.
2. Open a Team.
3. In the **Credentials** inline, enter the **Postmark API token** and optionally override the **Postmark API URL** (default: `https://api.postmarkapp.com/email`).

### Organisation credentials

1. Go to **Organisations > Organisations** in the admin.
2. Open an Organisation.
3. In the **Credentials** inline, enter the **Postmark API token** and optionally override the **Postmark API URL**.

---

## Credentials Resolution Order

When sending an email for a team, GregoryAI resolves the Postmark credentials using this fallback chain:

1. **Team credentials** — if `TeamCredentials` exists with both `postmark_api_token` and `postmark_api_url` set, use them.
2. **Organisation credentials** — if the team has no complete credentials, check `OrganisationCredentials` for the same conditions.
3. **Django settings** — fall back to `settings.EMAIL_POSTMARK_API_KEY` and `settings.EMAIL_POSTMARK_API_URL` from the `.env` file.

> **Note:** The fallback is all-or-nothing per level. If a team has a token but no URL (or vice versa), the system falls through to the next level.

---

## Example Configuration

**Scenario:** Two teams under the same organisation, each sending from a different domain.

| | Team A (ms-research) | Team B (cancer-research) |
|---|---|---|
| Site | `ms.example.com` | `cancer.example.com` |
| Sender prefix | `news` | `updates` |
| Sender address | `news@ms.example.com` | `updates@cancer.example.com` |
| Credentials | Uses org-level token | Has its own token |

Steps:
1. Create two Sites: `ms.example.com` and `cancer.example.com`.
2. Add a CustomSetting inline to each site with the appropriate prefix and footer.
3. Assign `ms.example.com` to Team A and `cancer.example.com` to Team B via the Team admin.
4. Enter Postmark credentials for Team B on the Team page.
5. Enter org-level Postmark credentials on the Organisation page (used by Team A).

---

## Environment Variables

The Django settings fallback uses these variables from `.env`:

```env
# Used when no team or organisation credentials are configured
EMAIL_POSTMARK_API_KEY=your-postmark-server-token
EMAIL_POSTMARK_API_URL=https://api.postmarkapp.com/email
```
