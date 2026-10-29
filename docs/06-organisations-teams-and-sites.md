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
| **CustomSetting** | Per-site settings: site title, email footer, admin email, sender email prefix, whether the site publishes author profile pages, `scope_subjects` (what the site owns for anonymous API/RSS visibility) and `api_public` (whether that scope is visible to anonymous API callers), `rss_enabled` (whether the site serves [RSS feeds](03-api-and-rss-feeds.md#rss-feeds) at `/feed/sites/{site_id}/...`, scoped to `scope_subjects` regardless of `api_public`), the [sitemap](03-api-and-rss-feeds.md#sitemaps) switches (master switch, curated subjects, articles-relevant-only, include trials, trial recruitment statuses), the export/about metadata (description, contact email, data licence, citation) shown on the "About this file" sheet of `export_trials_xlsx` and `export_patents_xlsx` workbooks (each with its own default citation), and `mcp_enabled`/`mcp_description` (whether this site offers a research assistant and how it's described to the model — see [docs/02.1-database-tables-and-fields.md](02.1-database-tables-and-fields.md#customsetting-sitesettings-app)). The Site admin page also carries `SiteMcpPrompt` and `SiteMcpDocument` inlines for that site's authored prompts and reference documents. |
| **TeamCredentials** | Postmark API token and URL scoped to a specific team. |
| **OrganisationCredentials** | Postmark API token and URL scoped to an organisation. Used as fallback when a team has no credentials. |
| **OrganisationSite** | Links an Organisation to one or more Sites. A Site belongs to exactly one Organisation (database constraint `unique_site_organization`). One of an Organisation's Sites can be marked as `is_default`. |

---

## API visibility for organisation-keyed surfaces

**Article/trial/RSS content visibility does not use this section at all** — it is subject-scoped via `CustomSetting.scope_subjects` + `api_public` on the site(s) an organisation owns; see [03-api-and-rss-feeds.md](03-api-and-rss-feeds.md#visibility-rules-summary). `OrganizationApiSettings.make_api_public` was replaced for that purpose, deliberately, by the site-scoped API visibility project.

The flag survives for one organisation-keyed surface that the site project kept organisation-scoped on purpose, because it answers an organisation-shaped question rather than a content-shaped one:

- Every `?team_id=`/`?organization=` scope validation in the API (`gregory.visibility.visible_org_ids()`). (The `/organizations/` listing used to be governed by the flag too; that endpoint has since been removed.)

Editorial content (`takeaways`, `summary_plain_english`) no longer reads this flag. It is opt-in with `?include=editorial` and follows the caller's site instead; see [Editorial content](#editorial-content-includeeditorial) below.

- When `make_api_public = True`, anonymous callers can validate `?team_id=`/`?organization=` against the organisation.
- When `make_api_public = False` (the default), only callers granted access — an API key or a member account — get through those checks for that organisation.

### Granting access to a private organisation, for these org-keyed surfaces

**Via API key** — create an `APIAccessScheme` in the admin under **API > API Access Schemes**:

| Field | Value |
|:------|:------|
| Client name | Descriptive label for the consumer |
| Organisation | The private organisation |
| Site | The site this key reads content from (governs article/trial/RSS scope — independent of `make_api_public`) |
| Begin / end date | Validity window |
| IP addresses | Optional comma-separated allowlist, matched exactly against the address nginx saw (`X-Real-IP`, else the socket address; `X-Forwarded-For` is ignored). Requires Django to be proxied through nginx or another proxy that sets `X-Real-IP` — see [03-api-and-rss-feeds.md](03-api-and-rss-feeds.md#option-1--api-key-bound-to-a-site) |

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

### Editing: your team only

A staff user **sees** their organisations' content, as above, but **edits** only what belongs to one of their teams (the teams they are a member of, under **Team memberships** on their user page). `gregory/team_scope.py` defines what belongs to a team:

| Rows | Belong to a team when |
|:-----|:----------------------|
| Articles, trials, patents | One of their sources is the team's, or they are tagged with the team (**Teams** field) |
| Sources, subjects, categories, prediction run logs | Their **team** is the team |
| Site takeaways and summaries, trial links (article–trial), trial organisation content, patent–trial links | Their article, trial or patent belongs to the team. They are edited from the article, trial or patent page (an article's trial links: **Add a trial link** and the delete box on the *Referenced Trials* list); their standalone pages are superuser-only for writes |
| Teams | Never: a team's settings and members are superuser-only, because membership is what grants editing |

In practice:

- Another team's row opens read-only, inlines included, and saving it is refused. Deleting follows the same rule.
- Bulk actions run only on the selected rows the user may edit. Any others are left alone, with a warning. Exports and other view-only actions still cover the whole selection.
- In a list with editable columns (categories' modality), other teams' rows are shown but locked.
- Pickers offer only the user's own teams' rows: **teams**, **subjects** and **sources** on content, categories, the team of a category, source or subject, and the target of **Reassign to another team**.
- **Add a trial link** on an article offers every trial the user's organisations can see (broader than the MCP tool, which stays inside the site's subjects). The picker searches the trial's title, acronym, database id and identifiers (paste an NCT or EudraCT number), not the long text fields the trials list searches.
- A staff user on no active team can see their organisation but edit or add nothing. `setup_roles --check` lists such accounts.
- **Add by DOI** on a DOI that's already in the database attaches the user's source, team and subject to that article, which makes it their team's too. It needs the *add articles* permission, which the Editor role doesn't have.
- Newsletters (lists, subscribers, announcements) are outside this rule: they stay organisation-scoped and outside the Editor role.

Superusers edit everything.

### Shared fields are superuser-only

An article or trial can belong to several teams and organisations at once, so its own record (title, abstract, DOI, authors, dates, links, registry fields) is shared: a change by one team changes it for all. On a saved article or trial, staff can change only **teams**, **subjects** and **sources**, plus the inlines that hold their own layer (categories, subject relevance, site takeaways and summaries, trial organisation content). Everything else on the page is read-only for them, and the **Refresh from CrossRef** button, which rewrites the record, is superuser-only. So is deleting an article, trial or patent: it removes the record for every team that has it. Patents already worked this way: their importer-managed fields are read-only for everyone. Adding a new article or trial by hand is unaffected.

### List filters

The Articles and Trials admin changelists each carry two filters built on this rule:

- **Site** — narrows the (already organisation-scoped) list to one site the caller's organisation owns, via `OrganizationSite`. It can only narrow: `get_queryset()` applies the organisation scoping first, and the filter's own choices never include another organisation's sites.
- **Not in any site's scope (curation queue)** — the same "not yet curated" set that's invisible to the API, but scoped to the caller's own organisation rather than shown globally, which would otherwise leak every organisation's uncurated content to every staff member. Superusers see the true global queue.

`/organizations/`- and `/teams/`-style admin pages are already organisation-keyed directly and carry neither filter.

### Sources, teams and subjects

On an article, trial or patent page, a staff user can only pick their own teams' **sources**, **teams** and **subjects**. The fields check this when the form is saved, so a hand-edited form can't link another team's rows either. This matters because publishing is subject-scoped: adding another organisation's subject would put the content on that organisation's site.

Links the content already has to other teams' rows are hidden from staff and kept when they save. On articles and trials, where **teams** and **subjects** are required, those links count towards them, so staff don't have to add one of their own just to save. Superusers see and can change every link.

### Permissions for actions and custom pages

Organisation scoping decides *which rows* a staff user sees. Django model permissions decide *what they may do* with them. Every bulk action in the admin declares the permission it needs: `change` for actions that edit, `delete` for actions that delete or merge, `add` for duplicating, and `view` for exports. A user with view-only access to a model is not offered the action, and a hand-made POST naming it does nothing. A test fails if someone adds an action without a permission.

The custom admin pages follow the same rule:

| Page | Needs |
|:-----|:------|
| Articles → Review status (batch marking) and its per-row buttons | `add` and `change` on *article subject relevance*. The page lists the organisation's subjects, but marking needs a subject of one of the user's teams (the buttons are hidden otherwise), and only articles carrying that subject are marked. Relevance belongs to the subject, so here the article itself need not be the team's, unlike on the article page |
| Sources → Overview (activate, deactivate, reassign) | `change` on *sources*; acts only on the user's teams' sources, and reassigns only to one of their teams |
| Teams → Reassign | Superuser only (team settings are); another organisation's team is a 404 |
| Prediction run logs → ML coverage | `view` on *prediction run log*; lists only the user's organisations' teams and subjects. The counts and recent runs on the run log list are the user's organisations' only |
| Announcements → Send, Send test | `change` on *announcement*. Without it the send buttons are hidden. Preview needs `view` |
| Sponsor merge candidates → Merge | `change` and `delete` on *sponsor*: a merge deletes a sponsor and repoints its trials in every organisation. Dismiss needs `change` on the candidate |

The OAuth pages (applications, access, refresh and ID tokens, grants) are superuser-only, whatever model permissions a staff account holds, like the MCP editor grants.

### Category assignments

The **Category assignments** inline on an article, trial or patent page is scoped by the category's team, not by the content. A staff user sees and can assign only their own teams' categories. The category field enforces this when the form is saved, not just in the autocomplete list, so a hand-edited form can't attach another team's category.

The same article, trial or patent can belong to more than one team, so it can also carry other teams' categories. Those rows are hidden from staff, and saving the page leaves them as they are. Superusers see and can assign every category.

### Subject relevance

The **Subject relevance** inline on an article page works the same way, keyed by subject. A staff user sees and marks only their own teams' subjects that have `auto_predict` on, on articles their team may edit. Opening the page of an article they may edit creates the missing rows for those subjects only; opening one read-only creates nothing. A new row can only name one of their subjects. An existing row's subject is fixed: the form shows it but ignores any change to it. Other teams' rows on a shared article are hidden and left as they are. Superusers see and can mark every subject.

### History pages

The history list and the old-version pages of articles, trials, patents, sources, trial organisation content and site takeaways and summaries follow the same scoping: a staff user gets a 403 for a row outside their organisations, even when the history table still holds it. **Revert** (saving an old version from its history page) is superuser-only. A revert rewrites every field of a row that other organisations may share, and simple-history would otherwise save it with view permission alone.

### Categories and authors

On a category's page, the **team** and **subjects** pickers offer only the user's own teams and their subjects, and a hand-edited form naming another team's is rejected. An author is shared, but their articles aren't: the **Author's articles** list on an author's page shows staff only the articles their organisations can see.

---

## The Editor role

The admin code above decides *which rows* a staff user sees and which of those they may edit (their team's). Django model permissions decide *what they may do* with them. The **Editor** group holds the permissions for an organisation's editor. It is defined in code, in `gregory/roles.py`, and synced by a command, so the code is the record of what an editor can do. Don't change the group in the Groups admin: the next sync removes anything that isn't in the code.

| Editors can | Models |
|:------------|:-------|
| View and change, but not add or delete | Articles, trials, patents (shared between organisations). Patent–trial links. Medicine–subject links (the medicines approved for a subject's condition; they only suppress or restore one, from the *Medicine subject links* page) |
| View, add and change | Per-site takeaways and summaries (articles, patents), trial organisation content, subject relevance |
| View, add, change and delete | Category assignments (articles, trials, patents), article–trial links (added by hand, or removed from the article page; an automatic link is hidden instead of deleted), medicine–category links, categories |
| View only | Sources, subjects, teams, prediction run logs. Authors, sponsors, entities, publication types, MeSH terms, EMA medicines and their events (shared reference data). The read-only rows inside the article, trial and patent pages (MeSH headings, trial countries and sites, patent publications and applicants) |

Editors get nothing from user accounts and groups, organisations, sites, API keys, OAuth and MCP grants, or newsletters. Anyone who can edit user accounts can make themselves a superuser. Several newsletter pages are not organisation-scoped yet and show subscribers' personal data.

`gregory/tests/test_roles.py` keeps the role honest: no forbidden apps, no add or delete on shared content, and every model the role can write to must be listed with the scoping that keeps an editor inside their organisation.

### Setting up an editor

1. Sync the group. The House deploy (`.github/workflows/build-push.yaml`) does this after every deploy, once the new container is healthy, so a change to `gregory/roles.py` takes effect on its own. On any other instance, or to sync by hand:

   ```bash
   docker exec gregory python manage.py setup_roles
   ```

2. In **Users**, tick *Staff status*, add the person to the **Editor** group, and add them to their team under **Team memberships**. Joining a team also adds them to the team's organisation. The team is what they can edit: an editor on no team can only look.
3. Don't give editors permissions directly on their account. Those bypass the role and never change when the role does.

Every sync sets the group to exactly the role, so anything added to the "Editor" group by hand is removed, on House at the next deploy. Change `gregory/roles.py` instead. Before the first sync on an instance, run `setup_roles --check` to see what would change.

`setup_roles --check` writes nothing. It exits non-zero if the group has drifted from the code. It also lists active non-superuser staff accounts that are on no team, that hold permissions directly on the account, or that aren't in the Editor group.

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
| Can add sources | Superuser-only power on top of *Can edit* (it needs it): gives the person's tokens the `sources:add` scope, for adding RSS feeds and PubMed or ClinicalTrials.gov searches. Saving it unticked ends the person's existing sessions on this site. Ticking it does not change existing sessions: the person signs in again to get the scope. Nothing uses the scope yet |
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

### What an editor can do

Once connected to a site's editor address, an editor reads that site's published scope (`scope_subjects`, on a private site too) and can change, for that site only, the editorial text of an article, the relevance of an article for a subject in the site's scope, and the links between an article and a trial. Each edit is attributed to the person in the history that Django keeps. The routes behind it are in [03-api-and-rss-feeds.md](03-api-and-rss-feeds.md#editor-routes-editor). Relevance is shared: it describes the article and the subject, not the site, so an edit on one site is visible on every other site that lists the subject.

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
| `OAUTH_ISSUER` | `https://api.<DOMAIN_NAME>` | The authorization server's issuer URL, with scheme and no path. It is pinned rather than derived from the request: the MCP server reaches Django over plain HTTP inside the compose network, where a derived issuer would differ from the one browsers see |
| `MCP_EDITOR_HOST_PREFIX` | `gregory-ai` | The host prefix of editor addresses |
| `OAUTH_LOGIN_MAX_FAILURES` | `10` | Failed sign-ins per 15 minutes, per client address and per username, before the login page refuses |
| `OAUTH_DCR_MAX_PER_HOUR` | `30` | Dynamic registrations per client address per hour |
| `MCP_EDITOR_WRITES_PER_HOUR`, `MCP_EDITOR_WRITES_PER_DAY` | `60`, `500` | Edits allowed per editor and site per window. Tune from production logs |
| `MCP_SOURCE_CREATES_PER_DAY`, `MCP_SOURCE_PREVIEWS_PER_HOUR` | `30`, `30` | Sources a source manager may add per day, and previews per hour, per editor and site. Separate from the edit limits above |

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
