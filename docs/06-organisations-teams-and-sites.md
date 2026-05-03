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
| **CustomSetting** | Per-site settings: site title, email footer, admin email, and sender email prefix. |
| **TeamCredentials** | Postmark API token and URL scoped to a specific team. |
| **OrganisationCredentials** | Postmark API token and URL scoped to an organisation. Used as fallback when a team has no credentials. |
| **OrganisationSite** | Links an Organisation to one or more Sites. One can be marked as `is_default`. |

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
