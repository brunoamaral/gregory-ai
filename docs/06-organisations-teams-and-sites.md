# Organisations, teams, and sites

> Audience: operators configuring a multi-tenant GregoryAI instance.

GregoryAI supports a multi-tenant structure where content, credentials, and email sending can be scoped at the **Organisation** or **Team** level. This document explains how to configure each layer and how the system resolves which settings to use when sending emails.

---

## Concepts

| Entity | Description |
|---|---|
| **Organisation** | Top-level grouping (provided by `django-organizations`). Owns teams, credentials, and sites. |
| **Team** | Belongs to one Organisation. Owns subjects, sources, and optionally its own credentials. |
| **Site** | A Django `sites` framework entry (`domain` + `name`). Used as the base URL for email sender addresses. |
| **CustomSetting** | Per-site settings: site title, email footer, admin email, sender email prefix, whether the site publishes author profile pages, `scope_subjects` (what the site owns for anonymous API/RSS visibility) and `api_public` (whether that scope is visible to anonymous API callers), `rss_enabled` (whether the site serves [RSS feeds](03-api-and-rss-feeds.md#rss-feeds) at `/feed/sites/{site_id}/...`, scoped to `scope_subjects` regardless of `api_public`), the [sitemap](03-api-and-rss-feeds.md#sitemaps) switches (master switch, curated subjects, articles-relevant-only, include trials, trial recruitment statuses), the export/about metadata (description, contact email, data licence, citation) shown on the "About this file" sheet of `export_trials_xlsx` workbooks, and `mcp_enabled`/`mcp_description` (whether this site offers a research assistant and how it's described to the model — see [docs/02.1-database-tables-and-fields.md](02.1-database-tables-and-fields.md#customsetting-sitesettings-app)). The Site admin page also carries `SiteMcpPrompt` and `SiteMcpDocument` inlines for that site's authored prompts and reference documents. |
| **TeamCredentials** | Postmark API token and URL scoped to a specific team. |
| **OrganisationCredentials** | Postmark API token and URL scoped to an organisation. Used as fallback when a team has no credentials. |
| **OrganisationSite** | Links an Organisation to one or more Sites. A Site belongs to exactly one Organisation (database constraint `unique_site_organization`). One of an Organisation's Sites can be marked as `is_default`. |

---

## API visibility for organisation-keyed surfaces

**Article/trial/RSS content visibility does not use this section at all** — it is subject-scoped via `CustomSetting.scope_subjects` + `api_public` on the site(s) an organisation owns; see [03-api-and-rss-feeds.md](03-api-and-rss-feeds.md#visibility-rules-summary). `OrganizationApiSettings.make_api_public` was replaced for that purpose, deliberately, by the site-scoped API visibility project.

The flag survives for one organisation-keyed surface that the site project kept organisation-scoped on purpose, because it answers an organisation-shaped question rather than a content-shaped one:

- **`/organizations/`**, and every `?team_id=`/`?organization=` scope validation elsewhere in the API (`gregory.visibility.visible_org_ids()`).

Editorial content (`takeaways`, `summary_plain_english`) no longer reads this flag. It is opt-in with `?include=editorial` and follows the caller's site instead; see [Editorial content](#editorial-content-includeeditorial) below.

- When `make_api_public = True`, anonymous callers can see the organisation in `/organizations/` and validate `?team_id=`/`?organization=` against it.
- When `make_api_public = False` (the default), only callers granted access — an API key or a member account — get through those checks for that organisation.

### Granting access to a private organisation, for these org-keyed surfaces

**Via API key** — create an `APIAccessScheme` in the admin under **API > API Access Schemes**:

| Field | Value |
|:------|:------|
| Client name | Descriptive label for the consumer |
| Organisation | The private organisation |
| Site | The site this key reads content from (governs article/trial/RSS scope — independent of `make_api_public`) |
| Begin / end date | Validity window |
| IP addresses | Optional comma-separated allowlist |

The consumer sends the generated key in every request:

```http
Authorization: <raw_api_key>
```

**Via user account** — add the user to the organisation as an `OrganizationUser`. After logging in, the org-keyed checks above pass for that organisation, and content visibility separately follows every site that organisation owns.

In both cases the caller can append `?include_public=true` to a request to also receive the scopes of every `api_public` site.

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

Content with no `Sources` row at all cannot be attributed to any organisation, so it is visible only to superusers. A source is attached at ingestion, so a row without one never came through the normal pipeline — it was created by hand in the admin, or by an importer that failed before linking a source.

Two different counts get quoted about this population, so to be exact about which is which:

| Measured | Articles | Trials |
|:---|---:|---:|
| No `Sources` row at all (development database, pre-prune) | 375 | 0 |
| …of those, also carrying a team, so they *lose* admin visibility under this rule | 370 | 0 |
| **Remaining in production**, after the September 2026 prunes removed 364 of them | **6** | **0** |

Production is the number that matters operationally: six articles move from "visible to their team's organisation" to "superuser only". There is no cross-organisation case — **zero** articles or trials have sources pointing at a different organisation than their curated teams, so nothing is being taken from one organisation and given to another.

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

Only one Site per Organisation can be marked as default (enforced by a database constraint). A Site can belong to only one Organisation: adding a Site that another Organisation already owns is rejected with a validation error.

---

## Author profile page links

Some sites publish an author profile page for each author at `/authors/<orcid>/`. Tick **Has author pages** in the Site's Custom Setting (under **Website URLs**) to have GregoryAI link author names there instead of `orcid.org`:

- **Weekly digest and admin summary emails** — each author's name links to `https://{site.domain}/authors/{ORCID}/`.
- **Author RSS feed** (`/feed/sites/<site_id>/author/<orcid>/`) — the feed's `<link>` element points at the same URL.

When the flag is off (the default), all of the above link to `https://orcid.org/{ORCID}` instead. The base URL is derived from the Site's `domain`, so no separate URL field is needed.

---

## Site Resolution Order

Emails are sent per **List** (`subscriptions.Lists`), not per Team — each list carries its own `site` FK, auto-populated on save if left blank:

1. **Organisation's default site** — if the list's team's organisation has an `OrganisationSite` with `is_default=True`, use its Site.
2. **Global fallback** — use `Site.objects.get_current()` (the site configured via `SITE_ID` in Django settings).

`Lists.site` determines footer branding, links, and unsubscribe URLs for that list's emails, independently of which team the list belongs to. Teams do not carry a site of their own — `Team.site` was removed as part of the site-scoped API visibility project, since production data showed it unmaintained (every team on one organisation pointed at a decommissioned site or nothing).

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

## MCP editor access

Named editors, from our own team and from client organisations, can read more than an anonymous visitor and edit article content through the MCP server's editor address. See [07-mcp-server.md](07-mcp-server.md) for the connector side; this section covers who may do what, and where it is managed.

### One address per site

Each site with `mcp_enabled` has its own editor address, `https://gregory-ai.<site domain>/mcp/editor` (the prefix is `MCP_EDITOR_HOST_PREFIX`). It is shown on the Site admin page, so it can be sent to a new editor. An editor adds one connector per site; each connector signs in on its own and gets permissions for that site only. There is no site switcher and no address that covers several sites. A token for one site is refused on another site's address.

### Granting access

On the Site admin page, the **MCP editors** inline lists the site's grants. Only superusers see or change it.

| Field | Meaning |
|:------|:--------|
| User | The person. Editors sign in with their Django username and password; a client editor without an account needs one created first |
| Can edit | Ticked: the edit tools. Unticked: the editor read scope without the edit tools. Saving it unticked ends the person's existing sessions on this site |
| Granted by, Created | Filled in on save |
| Revoke | Tick and save to end the grant. The person's tokens for this site are deleted in the same transaction, so access ends at once, not when the token expires |

A client editor can only be granted a site owned by an organisation they belong to. A superuser (our own team) can be granted any site. Superuser status alone never gives edit access: every editor appears in the list. Grants are revoked, not deleted, so the list stays the record of who had access.

### Tiers

Which tier a sign-in gets is decided when the token is issued:

| Signed-in person | Site has `api_public` data | Site has no public data |
|:-----------------|:---------------------------|:------------------------|
| Active grant | Editor tier | Editor tier |
| No grant | Public tier: the same read access as the anonymous `/mcp` address, no edit tools | Refused on the consent screen; no token is issued |

The consent screen for a person without a grant says they have read access to public data only and names the site's `admin_email` as the contact for editor access. Revoking a grant turns that person's next sign-in on the site into the public tier. A refresh keeps the tier the token was issued with, so a newly granted editor signs in again to get the edit tools.

### Maintenance

Clients register themselves, so two commands keep the tables small. Run them from cron, daily or weekly:

```cron
# Delete expired and revoked OAuth tokens
15 3 * * * docker exec gregory python manage.py cleartokens
# Delete self-registered OAuth clients unused for 90 days
25 3 * * 0 docker exec gregory python manage.py prune_oauth_clients
```

`prune_oauth_clients` never touches clients created by hand in the admin; `--dry-run` reports what it would delete and `--days` changes the 90.

### Settings

| Variable | Default | Meaning |
|:---------|:--------|:--------|
| `GREGORY_MCP_SERVICE_KEY` | empty | Shared secret between this API and the MCP server. Unset means introspection refuses every request. Generate with `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `OAUTH_ISSUER` | `https://api.<DOMAIN_NAME>` | The authorization server's issuer URL, with scheme and no path. Django sits behind a TLS-terminating proxy, so this is not derived from the request |
| `MCP_EDITOR_HOST_PREFIX` | `gregory-ai` | The host prefix of editor addresses |
| `OAUTH_LOGIN_MAX_FAILURES` | `10` | Failed sign-ins per 15 minutes, per client address and per username, before the login page refuses |
| `OAUTH_DCR_MAX_PER_HOUR` | `30` | Dynamic registrations per client address per hour |

## Editorial content (`?include=editorial`)

Editorial content is the `takeaways` and `summary_plain_english` of a record. Article content is stored per site in `ArticleSiteContent`, so two sites of one organisation can carry different text for the same article. (It used to be per organisation in `ArticleOrgContent`, which is now read-only and is dropped in a later release; a data migration copied every row to each site its organisation owns.) Trial content is still per organisation in `TrialOrgContent`. Article and trial responses include it only when the caller sends `?include=editorial`, nested under `editorial`. Whose content comes back is decided by who the caller is, never by `?team_id=` or any other parameter:

| Caller | Articles: site(s) whose content is returned | Trials: organisation(s) |
|:-------|:--------------------------------------------|:------------------------|
| Valid API key | The key's site (none if it has no site, or one owned by another organisation) | The key's organisation |
| Logged-in user | Every site owned by an organisation the user belongs to | Every organisation the user belongs to |
| Anonymous | The `api_public` site resolved from `?site_id=`, `Origin`, `Referer`, or the single public site | The organisation that owns that site |
| Anonymous, no `api_public` site | None (`editorial: []`) | None |

The same rule decides where `POST /articles/edit/` writes (the key's site) and which text a newsletter carries (the site its List is sent for, or the organisation's default site when that site isn't one the organisation owns). `get_takeaways` and `import_articles_from_api` write a copy to every site of the organisation.

Because the anonymous case goes through `OrganizationSite`, a site must belong to exactly one organisation (database constraint `unique_site_organization`). See [03-api-and-rss-feeds.md](03-api-and-rss-feeds.md#editorial-content) for the response shape and the `has_takeaways` filter.
