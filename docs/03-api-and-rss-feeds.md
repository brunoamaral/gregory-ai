# API and RSS feeds

> Audience: developers integrating with the GregoryAI REST API.

GregoryAI's API is open and does not require authentication unless you need to create articles or clinical trials. JWT authentication is available for write operations.

---

## OpenAPI schema

The API also publishes a generated [OpenAPI 3](https://swagger.io/specification/) schema, built from the views/filtersets/serializers themselves via [drf-spectacular](https://drf-spectacular.readthedocs.io/) — it cannot drift from the code the way this hand-written page can.

| Resource | URL |
|:---------|:----|
| Raw schema (YAML/JSON) | `GET /api/schema/` |
| Swagger UI | `GET /api/schema/swagger-ui/` |
| ReDoc | `GET /api/schema/redoc/` |

This page stays the canonical *prose* reference — narrative context, examples, changelogs, and the "why" behind a parameter. The schema is the canonical *machine-checkable* reference for exact parameter names, types, and response shapes. When the two disagree, treat it as a bug: fix the mismatch (see `CLAUDE.md`'s docs rule), don't just pick one and move on. CI generation guard: `python manage.py spectacular --fail-on-warn` (see `api/tests/test_openapi_schema.py`).

A read-only [MCP server](07-mcp-server.md) also exposes this API to LLM clients (Claude Code, Claude Desktop) as a small set of task-shaped tools, built against this schema — see [07-mcp-server.md](07-mcp-server.md).

---

## RSS feeds

| Feed | URL pattern |
|:-----|:------------|
| Articles by author (ORCID) | `GET /feed/sites/{site_id}/author/{orcid}/` |
| Clinical trials by subject | `GET /feed/sites/{site_id}/trials/subject/{subject_slug}/` |

Both feeds return the 50 most recent items, ordered by newest first.

**Feeds are site-scoped, not caller-scoped** — the same design as the [sitemaps](#sitemaps) below, and a deliberate change from how this worked before. A feed serves the *requested site's* `CustomSetting.scope_subjects`: the trials feed 404s when the requested subject is not in that site's scope, and the author feed 404s when none of the author's articles carries a subject in it, otherwise listing only the articles that do. The response never varies by who (or what) is asking — a feed reader has no identity and the response is cached, so there is nothing to key a caller-specific response on. `?include_public=true` and any notion of "the caller's own scope" (API key, signed-in user) do not apply to these URLs at all.

A site's feed is gated by its own `CustomSetting.rss_enabled` alone — 404 when it's off, or when the site has no `CustomSetting` row, or when `site_id` doesn't exist. Unlike the sitemaps, a feed's scope is **not** narrowed to the publicly-visible subject set: `rss_enabled` and `scope_subjects` are the whole gate, whether or not the site is `api_public` — the same rule a site-bound API key follows for its own scope (see [Visibility rules summary](#visibility-rules-summary)). Curation into the site's scope is the whole grant otherwise, so team ownership plays no part: a subject with no team is served once some site's scope names it, exactly as for the API and sitemaps.

The author feed's `<link>` element points at the requested site's author profile page (`https://{site.domain}/authors/{orcid}/`) when that site's `CustomSetting.has_author_pages` is on, and at `https://orcid.org/{orcid}` otherwise. See [Author profile page links](06-organisations-teams-and-sites.md#author-profile-page-links).

### Old feed URLs — permanent redirect

`GET /feed/author/{orcid}/` and `GET /feed/trials/subject/{subject_slug}/` (no `site_id`) still resolve, but now return **301** to their `/feed/sites/3/...` equivalent (`3` = brain-regeneration.com, the project's one `api_public` site), preserving any query string. They are not reimplemented against a caller's own scope any more — before this change they were the only caller-scoped surface on this list, returning different content to an anonymous caller, a signed-in member, and an API key on the exact same URL. A feed reader caches a permanent redirect and stops re-requesting the old path; a `404` would instead go unnoticed and silently drop the subscription. **These routes are permanent** — do not remove them in a future cleanup, or the redirect becomes a 404 for every reader still on the old path.

---

## Sitemaps

| Sitemap | URL pattern |
|:--------|:------------|
| Sitemap index | `GET /sitemap/sites/{site_id}/index.xml` |
| Articles section (paginated) | `GET /sitemap/sites/{site_id}/articles.xml` (`?p=2…N`) |
| Trials section (paginated, opt-in) | `GET /sitemap/sites/{site_id}/trials.xml` (`?p=2…N`) |
| Authors section (paginated, opt-in) | `GET /sitemap/sites/{site_id}/authors.xml` (`?p=2…N`) |

One sitemap per frontend site, enabled and curated per site in the Django admin (Sites → the site's settings inline → *Generate sitemap*, *Sitemap subjects*, *Relevant only*, *Include trials*, *Sitemap trial statuses*, *Sitemap include authors*). URLs point at the requested site's frontend domain, not the API host — `/articles/{article_id}/`, `/trials/{trial_id}/` and `/authors/{orcid}/`. Only content tagged with a publicly visible subject is included, regardless of caller identity — a site's sitemap subjects are intersected with the public scope (the union of *Scope subjects* over every site with *API public* on) before anything is queried, so curation can narrow what a site publishes but never widen it. Team ownership plays no part: a row carrying a published subject is listed whatever team owns it, including one with no team at all. Each section page holds up to 10,000 URLs; all endpoints are cached for 1 hour. A site with the switch off, no `CustomSetting` row, or no publicly visible sitemap subjects configured returns 404.

The trials section is off by default and appears in the index only when *Include trials* is on: not every frontend gives each trial its own page, and advertising `/trials/{trial_id}/` on a site that only has a trials listing would send crawlers to 404s. *Relevant only* filters articles alone — trials carry no relevance judgement (no manual review flag, no ML predictions).

The authors section is off by default and appears in the index only when *Sitemap include authors* is on, for the same 404-avoidance reason as trials. Unlike articles/trials, `Authors` carries no `subjects`/`teams` of its own, so membership is derived by traversing each author's tracked articles: an author qualifies only when they have at least 10 articles tagged with one of the site's sitemap subjects (already narrowed to the public scope, as above). That 10-article threshold is fixed in code (`SiteAuthorsSitemap.MIN_ARTICLES`), not configurable per site, and is deliberately stricter than the frontend's own thin-content `noindex` cutoff so the sitemap never advertises a URL the page itself marks `noindex`.

*Sitemap trial statuses* is the trials section's curation lever, since it has no relevance filter to lean on. Tick nothing and every trial for the selected subjects is listed; tick one or more `recruitment_status_normalized` values and the section narrows to those (trials whose registry status never normalised are dropped too, since `NULL` is not one of the values asked for). Restricting to the open/upcoming statuses — *Recruiting*, *Not yet recruiting*, *Active, not recruiting* — is the usual choice: it keeps the trials section from dwarfing the curated articles section, and matches what search traffic actually wants, since a completed or withdrawn trial is a historical record rather than something a reader can act on.

---

## Subscription endpoint

`POST /subscriptions/new/` accepts HTML form submissions (no CSRF token required).

| Field | Required | Description |
|:------|:---------|:------------|
| `first_name` | yes | Subscriber first name |
| `last_name` | no | Subscriber last name |
| `email` | yes | Subscriber email address |
| `profile` | yes | One of: `patient`, `caregiver`, `doctor`, `clinical centre`, `researcher` |
| `list` | no | List ID(s) to subscribe to; may be repeated for multiple lists |

On success the subscriber is created or updated and the browser redirects to `/thank-you/`. On failure it redirects to `/error/`.

### Redirect domain

The redirect base URL is not hardcoded. The view reads the `Origin` header (falling back to `Referer`) and checks whether that domain appears in the **Allowed Domains** field of at least one selected list. Configure allowed origins per list in the Django admin under **Subscriptions → Lists → Allowed Domains** as a comma-separated list:

```text
example.com, staging.example.com
```

This prevents open-redirect attacks — only explicitly whitelisted domains are used.

---

## Authentication

| Endpoint | Description |
|:---------|:------------|
| `POST /api/token/` | Obtain JWT token |
| `GET /protected_endpoint/` | Test protected endpoint (requires auth header) |

### OAuth 2.1 authorization server (MCP editor access)

Named editors sign in to the MCP server's editor address with their Django username and password, through an OAuth 2.1 authorization server built on `django-oauth-toolkit`. These routes are served by Django on the API domain, not by the REST API: they are not part of `/api/schema/`. The flow, and what an editor sees, is described in [06-organisations-teams-and-sites.md](06-organisations-teams-and-sites.md#mcp-editor-access).

| Endpoint | Purpose |
|:---------|:--------|
| `GET /.well-known/oauth-authorization-server` | RFC 8414 server metadata. `issuer` is `OAUTH_ISSUER`, or `https://api.<DOMAIN_NAME>`. Advertises the `authorization_code` and `refresh_token` grants, PKCE `S256`, the `articles:read` and `articles:edit` scopes, `registration_endpoint` and `client_id_metadata_document_supported` |
| `GET /o/authorize/` | Authorization endpoint. Needs `response_type=code`, PKCE (`code_challenge`, `S256`) and exactly one `resource`: a site's editor address, `https://<host>/mcp/editor`. Sends an anonymous browser to `/o/login/`, then shows a consent screen naming the site and the client |
| `POST /o/token/` | Token endpoint: authorization code (with `code_verifier`) and refresh. Access tokens last 1 hour; refresh tokens 30 days and are rotated on each use. Other grants are refused |
| `POST /o/revoke/` | RFC 7009 revocation |
| `POST /o/introspect/` | RFC 7662 introspection, for the MCP server only: `Authorization: Bearer <GREGORY_MCP_SERVICE_KEY>`. Returns `active`, `scope`, `exp`, `aud`, `user_id`, `site_id` and `tier`. Registration-management tokens are never active |
| `POST /o/register/` | RFC 7591 dynamic client registration. Open, rate limited per address (`OAUTH_DCR_MAX_PER_HOUR`), `authorization_code` clients only, https or loopback redirect URIs |
| `GET /o/login/` | The sign-in page for the flow (CSRF protected, not frameable, rate limited on failures) |

Clients may instead use an `https` URL as their `client_id` (Client ID Metadata Documents); the server fetches the document with an SSRF-hardened fetcher and applies the same redirect rules as for dynamic registration. Grants differ: a metadata document is published once for every authorization server, so it may list grants this server doesn't support (claude.ai's lists the JWT bearer grant). If the document asks for `authorization_code`, its list is narrowed to `authorization_code` and `refresh_token`; a document without `authorization_code` is refused. A dynamic registration is addressed to this server alone and is refused if it asks for any other grant. Either way, the token endpoint issues only the authorization code and refresh grants.

Every token is bound to one site by its `resource`, and carries a tier: `editor` for a person with an active `SiteEditor` grant, `public` for a signed-in person without one on a site that has `api_public` data. A public-tier token never carries `articles:edit`. The `resource` is the only way to name a site; the MCP server rejects a token sent to any other host.

---

## Editor routes (`/editor/`)

The MCP server calls these on behalf of a signed-in editor. They are not for browsers or API keys, and nginx keeps them off the public internet (see [MCP editor access](06-organisations-teams-and-sites.md#mcp-editor-access)); Django does not rely on that and checks every request itself.

Every request carries:

| Header | Value |
|:-------|:------|
| `Authorization` | `Bearer <GREGORY_MCP_SERVICE_KEY>` |
| `X-Gregory-Editor-User` | The Django user id of the editor, verified by the MCP server through `/o/introspect/` |
| `X-Gregory-Editor-Site` | The one site the editor's token is bound to |

Rules:

- Django re-checks the editor's active `SiteEditor` grant on every request, so a revocation takes effect on the next call whatever the MCP server has cached. A bad or missing credential, or an unknown or inactive user, is `401` with `WWW-Authenticate: Bearer`. A valid credential for an editor without an active grant on that site, or for a site with `mcp_enabled` off, is `403`. A grant with `can_edit` off reads but gets `403` on every write.
- A request carrying either editor header without the credential is `401` on every route, and the headers are refused on any route outside `/editor/`.
- The site is the token's, never a parameter. An article, trial or subject outside that site's `scope_subjects` is `404`, the same answer as one that doesn't exist.
- Writes take effect immediately, run as the editor (history rows record the editor and `via = "mcp"`), and are throttled per (editor, site): 60 per hour and 500 per day by default (`MCP_EDITOR_WRITES_PER_HOUR`, `MCP_EDITOR_WRITES_PER_DAY`). A throttled write is `429` with `Retry-After`. Reads are never throttled.

### Reads

The read endpoints are the ordinary ones, mounted again under `/editor/` with editor authentication: `/editor/articles/`, `/editor/articles/{id}/`, `/editor/articles/stats/`, `/editor/trials/`, `/editor/trials/{id}/`, `/editor/trials/stats/`, `/editor/authors/`, `/editor/categories/`, `/editor/subjects/`, `/editor/sponsors/` and `/editor/stats/`. They accept the same parameters and return the same bodies, scoped to the editor's one site: its `scope_subjects` whether or not the site is `api_public`. `?include_public`, `?site_id` and `?team_id` cannot widen that scope. They are not repeated in the OpenAPI schema.

Differences from the public endpoints:

- `?include=editorial` returns only the editor's site, and each article entry also carries `updated_at` and `updated_by` (the name and email of whoever last changed it, or the API key's name when a key did).
- `GET /editor/tenants/` lists every site with `mcp_enabled` and a non-empty scope, private ones included, each with its full scope and an `api_public` flag. It takes the service credential alone, with no editor headers. The MCP server's editor mount uses it to tell which site a host is.

### Writes and history

| Method and path | Purpose |
|:----------------|:--------|
| `GET /editor/articles/resolve/?doi=` | DOI to `article_id` among in-scope articles. `404` for none, or for one outside the scope. `409` with `article_ids` when several in-scope articles share the DOI |
| `PATCH /editor/articles/{article_id}/editorial/` | Upsert this site's `takeaways` and/or `summary_plain_english`. Omitted fields are left alone; an empty string clears. Returns the stored values with `updated_by` and `updated_at` |
| `PUT /editor/articles/{article_id}/relevance/{subject_id}/` | Body `{"is_relevant": true \| false \| null}`. The subject must be in the site's scope (`404` otherwise). Relevance is per subject, so the change applies to every site listing the subject |
| `POST /editor/articles/{article_id}/trials/` | Body `{"trial_id": n}`. Adds a manual link, `201`; an already linked pair returns the existing link, `200`. The trial must be in scope |
| `DELETE /editor/articles/{article_id}/trials/{trial_id}/` | Removes a manual link, or hides an auto-detected one (`suppressed`) so detection doesn't recreate it. `404` when the pair isn't linked |
| `PUT /editor/articles/{article_id}/categories/{category_id}/` | Assigns the article to the category by hand, `201`. An article the pipeline already matched becomes a hand assignment, `200`, which `rebuild_categories` never removes; one already assigned by hand is returned unchanged, `200`. The category is in scope when one of its subjects is |
| `DELETE /editor/articles/{article_id}/categories/{category_id}/` | Removes a hand assignment. `409` for one the pipeline made from the category's terms, since the next run would add it back. `404` when the article isn't in the category |
| `POST /editor/categories/` | Creates a category. Body: `category_name`, `subject_ids` (in scope, one team, which becomes the category's team), `category_terms` (at least one for an automatic category; trimmed and deduplicated ignoring case), and optionally `category_description`, `category_slug` (defaults to the slugified name), `modality`, `category_type` (`automatic` or `manual`) and `match_scope`. `201`. A taken slug is `409`, with the holder's `category_id` when it is in scope. The next `rebuild_categories` run fills an automatic category |
| `PATCH /editor/categories/{id}/` | Changes `category_name`, `category_description` (empty string clears), `modality`, `match_scope` or `subject_ids` (replaces them; same team). `category_terms` replaces every term; `add_terms` and `remove_terms` edit the current list (case-insensitive) and can't be combined with it. The slug and team never change. Every subject of the category must be in scope: `404` when none is, `403` when only some are, since the change shows on every site listing any of them |
| `GET /editor/articles/{article_id}/history/?limit=` | Who changed this site's editorial content, the article's relevance for subjects in scope, its links to trials in scope and its hand assignments to categories in scope; newest first, `limit` up to 200 (default 50) |

`GET /editor/categories/` and `GET /editor/categories/{id}/` stay the read endpoints described above; the category writes share their paths. Category changes are recorded in `HistoricalTeamCategory` under the editor's name.

Trials are read-only over MCP: an editor can link a trial to an article but cannot change the trial. `access`, `retracted` and `kind` stay on `POST /articles/edit/` (API key) and the admin.

---

## Resolving a site for an anonymous caller

An anonymous request is scoped to exactly one site's `scope_subjects`, resolved in this order:

1. **`?site_id=`** — used if it names an *API public* site.
2. **`Origin` header** — resolved to a Site by domain (exact match, then one subdomain level stripped), used if that site is *API public*.
3. **`Referer` header** — same, for the cases where a browser sends this instead of `Origin`.
4. **The public union, if unambiguous.** The union of every *API public* site's scope is data anyone may already read, so it is served automatically whenever it comes from **zero or one** site — zero means an empty scope, one means the union just *is* that site's scope. **Two or more *API public* sites and nothing above resolved one of them → `400`**, naming the sites the caller could ask for instead:

   ```json
   {"error": "No site could be determined for this request.",
    "detail": "Pass ?site_id=, or call from a registered site origin.",
    "public_sites": [{"site_id": 3, "domain": "brain-regeneration.com", "name": "Brain Regeneration"}]}
   ```

   This project has exactly one *API public* site today, so step 4 always resolves and the `400` never fires in practice — it exists for the moment a second one is onboarded, so an anonymous caller never silently receives two sites' content blended into one response.

Only *API public* sites are candidates at every step, which is what makes trusting the client-controlled `Origin`/`Referer` headers safe: resolution can only ever **narrow** to a public site, never grant a private one's scope by a caller merely claiming to come from it.

A **site-bound API key ignores `Origin`/`Referer`/`?site_id=` entirely** — the credential's own site always wins, so a client-controlled header can't override what the key grants. The same is true for a signed-in user: their organisations' sites decide, unaffected by any of the above.

Responses that consulted `Origin` or `Referer` to reach their result carry `Vary: Origin, Referer`, so an HTTP cache in front of the API never serves one Origin's (or Referer's) resolution to a request carrying a different one. A response resolved purely by `?site_id=` does not vary by either header at all.

> **`?site_id=` is also, separately, a content filter on `/articles/` and `/trials/`.** The two uses are independent and the same query parameter feeds both — one resolves anonymous *visibility*, the other narrows the *result set* to a site's scope. Through Phase 5 the content filter read the legacy `Team.site` field (`teams__site_id`), which was already stale for most teams; Phase 6 reimplemented it on `subjects__in=<that site's scope_subjects>` — the same field visibility resolution reads. They compose as independent constraints rather than becoming one rule: the content filter unions `scope_subjects` across *every* `CustomSetting` row for the site, while anonymous visibility only ever counts the `api_public=True` row, so a site carrying a second, private settings row can have the filter admit a result that visibility then excludes.
>
> **This changed what the filter returns, with no error, for anyone who had a `site_id` pinned:**
>
> | Query | Before Phase 6 | After Phase 6 |
> |:---|---:|---:|
> | `site_id=3` — brain-regeneration.com, the live site | 0 articles, 0 trials | 52,324 articles, 17,238 trials |
> | `site_id=1` — gregory-ms.com, decommissioned | 50,953 articles, 17,007 trials | 0 articles, 0 trials |
>
> Nobody could have been relying on `site_id=3`, since it returned nothing; anyone relying on `site_id=1` was reading a number that was never meaningful — so this is a fix, not a regression. It is still called out here rather than only in a changelog, because a documented parameter whose results change completely with no error is exactly the failure mode this project exists to prevent.

### Discovering which sites exist

`GET /sites/` lists every *API public* site as `{site_id, domain, name}`. It is the discovery entry point a new caller needs before it can pass `?site_id=`, so it is **never gated by the resolution above** — it answers with no site indicator at all, even amid the exact ambiguity that would 400 every other endpoint. It is also the same code path the `400` body's `public_sites` list is drawn from, so the two can never disagree.

### Discovering MCP tenants

`GET /tenants/` is a **separate, richer** endpoint for sites offering a research assistant (MCP) — it is not a replacement for `GET /sites/`, and the two are kept apart on purpose (see `PublicSiteSerializer`'s docstring): `/sites/` is unscoped public discovery and stays exactly as it is; `/tenants/` carries MCP configuration — a description written for the model, authored prompts, and reference documents — and can include a caller's own **private** site.

A site is a **public tenant** when it has a settings row with `api_public=True`, `mcp_enabled=True`, and a non-empty `scope_subjects`. Who sees what:

| Caller | Gets |
|:-------|:-----|
| Anonymous, a signed-in user, or an invalid/expired key | Every public tenant |
| A valid key whose site belongs to its organisation | Every public tenant, plus its own site if that site is a tenant (`mcp_enabled` and a non-empty scope), whether or not it is `api_public` |
| A key with no site, or a site outside its organisation | The same as anonymous |

The response is a plain JSON array, ordered by `site_id`, with no pagination:

```json
[
  {
    "site_id": 3,
    "domain": "brain-regeneration.com",
    "name": "Brain Regeneration",
    "title": "Brain Regeneration",
    "api_public": true,
    "mcp_description": "",
    "subjects": [{"id": 1, "subject_name": "Multiple Sclerosis"}],
    "prompts": [
      {
        "name": "research_topic",
        "title": "Research a topic",
        "description": "Survey recent articles and clinical trials on a topic.",
        "template": "Research the topic \"$topic\". …",
        "arguments": [{"name": "topic", "description": "The topic to research.", "required": true}]
      }
    ],
    "documents": []
  }
]
```

`subjects` carries `id` and `subject_name` only — no `team_id`, anywhere. `prompts` and `documents` list only `is_active` rows, in their model `ordering`; a prompt's `template` is raw text (`$name`-style placeholders), substituted by the MCP server at render time, not here.

Like `GET /sites/`, this endpoint is **never gated by the resolution above** — it answers the same way no matter how many `api_public` sites exist, even where `/articles/` would 400 on an anonymous caller who names none. Every response carries `Vary: Authorization`, since the answer depends on the caller's key.

The MCP server fetches this endpoint the way it already fetches `GET /sites/` — cached for 10 minutes, with a stale-copy fallback on fetch failure rather than treating an outage as "no tenants exist" — and refuses any request whose Host doesn't resolve to one of these rows, before making any other API call. See [07-mcp-server.md](07-mcp-server.md#tenant-resolution-site_id) and its §"Prompts"/§"Resources".

---

## Accessing private organisation data

Content visibility (articles, trials, RSS) is **subject-scoped**, not organisation-scoped — see [Visibility rules summary](#visibility-rules-summary) below. `OrganizationApiSettings.make_api_public` plays no part in it; that flag now governs only `/organizations/` and the `?team_id=`/`?organization=` scope validations (org-keyed surfaces kept deliberately organisation-scoped — see [06-organisations-teams-and-sites.md](06-organisations-teams-and-sites.md#api-visibility-for-organisation-keyed-surfaces)). Callers that need to read a **private** site's data must identify themselves in one of two ways.

### Option 1 — API key bound to a site

Create an `APIAccessScheme` record in the Django admin with `organization` and `site` set to the target site. The client sends the raw key in the `Authorization` header (no prefix):

```http
GET /articles/
Authorization: <raw_api_key>
```

The key is validated against its date window (`begin_date` / `end_date`) and, if configured, an IP allowlist. A valid key grants that site's `scope_subjects`, whether or not the site is `api_public`.

The IP allowlist (`APIAccessScheme.ip_addresses`, comma-separated, exact match) is checked against the `X-Real-IP` request header, falling back to the socket address (`REMOTE_ADDR`) when it is absent. `X-Forwarded-For` is ignored, because its first entry is whatever the client chose to send. The same address is what `APIAccessSchemeLog.ip_addr` records.

This means the allowlist only sees real client addresses when Django runs behind a reverse proxy that **overwrites** `X-Real-IP` with the connecting address, as the bundled nginx configuration does (`proxy_set_header X-Real-IP $remote_addr;` on every proxied location). Don't expose Django directly, or through a proxy that passes a client-sent `X-Real-IP` through, if any key has an allowlist. If nginx itself sits behind a CDN such as Cloudflare, `$remote_addr` is the CDN's edge address: configure nginx's `real_ip` module (`set_real_ip_from <CDN ranges>; real_ip_header CF-Connecting-IP;`) so `$remote_addr` is the client again, or allowlisted keys will be refused.

### Option 2 — Authenticated Django user

A user account that is a member of the organisation owning the site (an `OrganizationUser` record exists) sees the scopes of every site that organisation owns, automatically, after logging in via the session-based endpoints.

### Including public sites alongside private data

Identified callers can append `?include_public=true` to any request to add the scopes of every *API public* site to their own — how a private site's frontend reads public content alongside its own.

```bash
GET /articles/?include_public=true
```

It has no effect for an anonymous caller: their own resolved scope (see [Resolving a site for an anonymous caller](#resolving-a-site-for-an-anonymous-caller)) already belongs to an *API public* site by construction, so OR-ing in the full public union would silently discard that resolution — the opposite of what site resolution exists to guarantee. Before subject scoping this parameter meant "adds public organisations"; it now adds public *sites'* subject scopes for an identified caller, and is declared in the OpenAPI schema rather than being undeclared-but-working as it was.

### Visibility rules summary

Content visibility is **subject-scoped**: a caller sees a row when one of its subjects is in the `scope_subjects` of a site that caller can reach.

| Caller | Visible subjects |
|:-------|:-----------------|
| Anonymous (no credentials) | Exactly ONE resolved *API public* site's scope — see [Resolving a site for an anonymous caller](#resolving-a-site-for-an-anonymous-caller); `400` if that can't be pinned to one site |
| API key bound to a site | That site's scope (+ public sites' if `?include_public=true`) |
| API key with no site set | Nothing (+ public sites' if `?include_public=true`) |
| Authenticated user, member of org X | The scopes of every site org X owns (+ public if `?include_public=true`) |

> **Note:** An expired key or a key used from a non-allowed IP is treated as anonymous.

Two endpoints are not content and do not follow this rule:

- **`/teams/`** lists a team when `Team.api_listed` is on, **or** when the team owns a subject already in the caller's scope. The flag is the publication switch; the second clause exists so a private site's own authenticated frontend can list its own teams. It gates *listing*, not access — an unlisted team's articles and trials are governed by subject scope like everything else. The same rule decides whether a team appears nested inside an article or trial.
- **`/organizations/`** remains organisation-keyed.

**`/sponsors/`** is scoped indirectly: a sponsor carries no subject and is visible when at least one of its trials is in scope. Its `trials_count` counts only in-scope trials, so neither the number nor `?ordering=-trials_count` discloses trials the caller cannot read.

> **Note:** This caller-scoped rule is the API's alone. RSS feeds are scoped to the *requested site*, not the caller — see [RSS feeds](#rss-feeds) — much like the [sitemaps](#sitemaps) below. The Django admin uses a deliberately different rule again — see [06-organisations-teams-and-sites.md#admin-visibility](06-organisations-teams-and-sites.md#admin-visibility).

---

## Articles query parameters

The `/articles/` endpoint supports the following filters. Multiple parameters can be combined.

| Parameter | Type | Description |
|:----------|:-----|:------------|
| `team_id` | integer | Filter by team |
| `subject_id` | integer | Filter by subject |
| `author_id` | integer | Filter by author |
| `doi` | string | Exact DOI match (case-insensitive) |
| `category_slug` | string | Filter by category slug |
| `category_id` | integer | Filter by category ID |
| `category_modality` | string | Filter by a category's curated intervention modality (`small_molecule`, `biologic_antibody`, `cell_gene_therapy`, `rehabilitation`, `device_neuromodulation`, `natural_product`, `research_topic`, `other`). Matches if *any* of the article's categories carry that modality |
| `journal_slug` | string | Filter by journal (spaces → dashes) |
| `source_id` | integer | Filter by source |
| `search` | string | Search in title and summary |
| `relevant` | boolean | Relevant articles only. Scoped to `subject_id` when provided. |
| `ml_threshold` | float 0–1 | The consensus rule behind `relevant=true`, with a custom confidence: enough models (per the subject's ML consensus setting) must have classed the article relevant at this probability or higher. Models only do so at 0.8 or above, so values below 0.8 act as 0.8. Subjects with ML predictions turned off never match. Scoped to `subject_id` when provided. For the score shown on each article, use `ml_score_min` |
| `ml_score_min` | float 0–1 | Only articles whose `ml_score` is at least this value (inclusive). Articles without an `ml_score` are left out. Applies no consensus rule and is not scoped by `subject_id`, because `ml_score` averages every subject the article was scored for. Out-of-range or non-numeric values return 400 |
| `open_access` | boolean | Open access articles only |
| `has_clinical_trials` | boolean | Filter by whether articles are linked to at least one trial. A link an editor removed (an auto-detected link marked `suppressed`) doesn't count, and isn't listed in `clinical_trials` or a trial's `articles` either |
| `has_takeaways` | boolean | `true`: articles with non-empty takeaways written for the caller's own site (see [Editorial content](#editorial-content)). `false`: everything else. Doesn't require `include=editorial` |
| `include` | string | `editorial` adds the `editorial` list to each article (see [Editorial content](#editorial-content)). Unknown values return 400 |
| `last_days` | integer | Articles from the last N days |
| `week` | integer 1–52 | Filter by week number (requires `year`) |
| `year` | integer | Year for week filtering |
| `published_date_after` | date (YYYY-MM-DD) | Articles published on or after this date (inclusive). Returns 400 for invalid dates. |
| `published_date_before` | date (YYYY-MM-DD) | Articles published on or before this date (inclusive — the full day is included). Returns 400 for invalid dates. |
| `ordering` | string | Order results (e.g., `-published_date`, `title`) |
| `page` | integer | Page number. `page * page_size` above 10,000 returns 400 — use `all_results=true` for deep/bulk reads instead (optionally with `format=csv`; `format=csv` alone is still paginated and subject to this same limit) |
| `page_size` | integer | Items per page (max 100) |
| `all_results` | boolean | Bypass pagination (useful for CSV export) |
| `format` | string | `json` (default) or `csv` |

### Examples

```bash
GET /articles/?team_id=1&subject_id=4&relevant=true
GET /articles/?relevant=true&ml_threshold=0.9
GET /articles/?subject_id=1&ml_score_min=0.7&ordering=-ml_score
GET /articles/?relevant=true&last_days=15
GET /articles/?team_id=1&search=stem+cells
GET /articles/?format=csv&all_results=true
GET /articles/?has_clinical_trials=true
GET /articles/?published_date_after=2023-01-01&published_date_before=2023-12-31
GET /articles/?team_id=1&subjects=1,3&published_date_after=2022-06-01&format=csv&all_results=true
```

---

## Editorial content

Editorial content is the `takeaways` and `summary_plain_english` of a record. It is **off by default** and returned only with `?include=editorial`, on `/articles/`, `/articles/{id}/`, `/trials/`, `/trials/{id}/` and both search endpoints (as a query parameter or a body field). CSV output never includes it.

Articles carry editorial content **per site**, so two sites of one organisation can say different things about the same article. Each entry is labelled by site:

```json
"editorial": [
	{
		"site": {"id": 1, "domain": "brain-regeneration.com", "name": "Brain Regeneration"},
		"takeaways": "Experimental autoimmune encephalomyelitis ...",
		"summary_plain_english": null
	}
]
```

Trials still carry editorial content **per organisation**, and each trial entry is labelled by organisation:

```json
"editorial": [
	{
		"organization": {"id": 1, "name": "Brain Regeneration"},
		"takeaways": "...",
		"summary_plain_english": null
	}
]
```

- `editorial` is always a list, sorted by site id (articles) or organisation id (trials). When the site or organisation has no content for a record, its fields are `null`. When the caller has no editorial site or organisation, the list is `[]`.
- **Which site** (articles) is decided by the caller, never by a parameter: an API key gets its own site, a logged-in user gets every site owned by an organisation they belong to, and an anonymous caller gets the `api_public` site resolved from `?site_id=`, `Origin`, `Referer` or the single public site (see [Resolving a site for an anonymous caller](#resolving-a-site-for-an-anonymous-caller)). An API key whose site is missing, or belongs to a different organisation, gets `[]`. `?team_id=` has no effect, and there is no way to request another site's content.
- **Which organisation** (trials) follows the same rule: an API key gets its own organisation, a logged-in user gets every organisation they belong to, and an anonymous caller gets the organisation that owns the resolved site.
- Anonymous and API-key callers get 0 or 1 entries; a logged-in user whose organisations own two sites gets 2 on articles.
- `?include=` is a comma-separated list; the only accepted value is `editorial`, and an unknown value returns 400.
- `?has_takeaways=true|false` filters on the same site(s) (articles) or organisation(s) (trials) and doesn't need `include=editorial`. Empty-string takeaways count as missing.
- Responses already vary by `Origin` whenever site resolution depends on it, so caches in front of the API must honour `Vary: Origin`.

**Breaking change (articles).** `editorial[].organization` was replaced by `editorial[].site` (`id`, `domain`, `name`) on article responses, and the content is now selected by site instead of by organisation. Existing content was copied to every site of its organisation, so a single-site organisation sees the same text. Clients that read `editorial[0].takeaways` keep working; clients that read `editorial[].organization` on articles must switch to `editorial[].site`. See the [changelog entry](changelog/article-editorial-per-site.md). Trial responses are unchanged.

**Earlier breaking change.** The top-level `takeaways` and `summary_plain_english` fields were removed from article and trial responses, and `?team_id=` (or an API key) no longer unlocks them. Read `editorial[0].takeaways` after adding `?include=editorial`.

## Available endpoints

| Model | Endpoint | Parameters | Notes |
|:------|:---------|:-----------|:------|
| Articles | `GET /articles/` | `include`, `has_takeaways`, `team_id`, `subject_id`, `author_id`, `category_slug`, `category_id`, `category_modality`, `journal_slug`, `source_id`, `search`, `ordering`, `relevant`, `open_access`, `last_days`, `week`, `year`, `has_clinical_trials`, `published_date_after`, `published_date_before`, pagination | |
| Articles | `POST /articles/post/` | `title`, `link`, `doi`, `summary`, `source_id`, `kind` | Create article — see [response codes below](#post-articlespost-response-codes) |
| Articles | `POST /articles/edit/` | `doi` *(req)*, `takeaways`, `summary_plain_english`, `access`, `retracted`, `kind` | Edit an existing article, API key required — see [below](#post-articlesedit) |
| Articles | `GET /articles/{id}/` | `id` (path) | |
| Articles | `GET /articles/stats/` | Same filters as `GET /articles/` | Aggregate counts over the filtered set: `total`, `by_access` (NULL folded into `unknown`), `relevant`, `retracted`, `missing_doi`, `by_subject`. Cached for `STATS_CACHE_TTL` seconds |
| Articles | `GET /articles/search/` | `team_id` *(req)*, `subject_id` *(req)*, `title`, `summary`, `search`, `format`, `all_results`, plus every `GET /articles/` filter (`published_date_after`, `published_date_before`, `relevant`, `subjects`, …) | See [Search endpoints](#search-endpoints) below |
| Articles | `POST /articles/search/` | Request **body** accepts the same fields as `GET /articles/search/`: `team_id`, `subject_id`, `title`, `summary`, `search`, `ordering`, `page`, `page_size`, `all_results`, plus every `GET /articles/` filter | See [GET vs POST](#get-vs-post-on-search-endpoints) |
| Authors | `GET /authors/` | `author_id`, `full_name`, `orcid`, `country`, `sort_by`, `order`, `team_id`, `subject_id`, `category_slug`, `date_from`, `date_to`, `timeframe`, `page`, `page_size` | `page * page_size` above 10,000 returns 400, same as `/articles/` and `/trials/` — but there is **no** `all_results` bypass here (see [authors-api.md](authors-api.md#pagination-limits)); use `/authors/search/` for bulk/team-scoped reads |
| Authors | `GET /authors/{id}/` | `id` (path) | |
| Authors | `GET /authors/search/` | `team_id` *(req)*, `subject_id` *(req)*, `full_name`, `format`, `all_results`, plus every `GET /authors/` filter (`author_id`, `orcid`, `country`, `given_name`, `family_name`) | See [Search endpoints](#search-endpoints) below |
| Authors | `POST /authors/search/` | Request **body** accepts the same fields as `GET /authors/search/`: `team_id`, `subject_id`, `full_name`, `page`, `page_size`, `all_results`, plus every `GET /authors/` filter | See [GET vs POST](#get-vs-post-on-search-endpoints). Results are always ordered by `author_id`; this endpoint has no `ordering` support |
| Authors | `GET /authors/by_team_subject/` | `team_id` *(req)*, `subject_id` *(req)* | |
| Authors | `GET /authors/by_team_category/` | `team_id` *(req)*, `category_slug` or `category_id` *(req)* | |
| Authors | `GET /authors/{id}/coauthors/` | `id` (path) | Co-authors of the given author |
| Categories | `GET /categories/` | `team_id`, `subject_id`, `category_id`, `get_categories`, `search`, `ordering` (`category_name`, `id`, `article_count_annotated`, `trials_count_annotated`, `authors_count_annotated`), `include_authors`, `max_authors`, `monthly_counts`, `ml_threshold`, `date_from`, `date_to`, `timeframe`, pagination | `ordering=authors_count_annotated` is the expensive sort — see [Categories ordering](#categories-ordering) |
| Categories | `GET /categories/{id}/` | `id` (path) | |
| Categories | `GET /categories/{id}/authors/` | `id` (path), `min_articles`, `sort_by`, `order`, date filters | Author stats for a category |
| Sources | `GET /sources/` | `team_id`, `subject_id`, `source_for`, `search`, `ordering`, pagination | |
| Sources | `GET /sources/{id}/` | `id` (path) | |
| Sponsors | `GET /sponsors/` | `sponsor_type`, `search`, `ordering` (`name`, `trials_count`), pagination | Canonical, deduplicated sponsor entities — see [Sponsor canonicalization](#sponsor-canonicalization) below |
| Sponsors | `GET /sponsors/{id}/` | `id` (path) | |
| Subjects | `GET /subjects/` | `team_id`, `search`, `ordering`, pagination | |
| Subjects | `GET /subjects/{id}/` | `id` (path) | |
| Teams | `GET /teams/` | Standard pagination | |
| Teams | `GET /teams/{id}/` | `id` (path) | |
| Teams | `GET /teams/{id}/subjects/{subject_id}/categories/` | `id`, `subject_id` (path) | |
| Trials | `GET /trials/` | `team_id`, `subject_id`, `category_id`, `category_modality`, `source_id`, `status`, `search`, `ordering`, trial-specific filters, pagination | See parameter details below |
| Trials | `GET /trials/{id}/` | `id` (path) | |
| Trials | `GET /trials/stats/` | Same filters as `GET /trials/` | Totals per `recruitment_status_normalized` bucket (not_yet_recruiting, recruiting, enrolling_by_invitation, active_not_recruiting, not_recruiting, suspended, completed, terminated, withdrawn, unknown, other — always present, 0 when empty) plus `no_status`, `by_subject`, `by_phase` (per `TrialPhase` + `no_phase`), `by_region` (per `TrialRegion` + `no_region`), `by_country` (`[{country, count}]`, null-country entry last), `by_year` (`[{year, count}]`, null-year entry last), `by_sponsor` (top 25 `[{sponsor_id, slug, name, sponsor_type, count}]`) + `no_sponsor`, `by_sponsor_type` (per `SponsorType` + `no_type`), `by_modality` (per `CategoryModality` + `no_modality`, joined over `team_categories.modality` — **not** a partition of `total`: a trial in two categories of different modalities is counted once per modality, and `no_modality` conflates "no category at all" with "category not yet curated with a modality"), `by_study_type` (per `TrialStudyType` + `no_study_type`; `no_study_type` is large — ~13.2k globally — mostly legacy rows with no `source_register` rather than a normalization gap), and `by_sex` (per `TrialSexEligibility` + `no_sex_data`; `no_sex_data` is large — ~46% globally — same legacy-rows caveat as `no_study_type`) over the filtered set. Replaces the `stats` block formerly embedded in `GET /trials/` list responses (breaking change). Cached for `STATS_CACHE_TTL` seconds |
| Trials | `GET /trials/search/` | `team_id` *(req)*, `subject_id` *(req)*, `title`, `summary`, `search`, `status`, `format`, `all_results`, plus every `GET /trials/` filter (`date_registration_after`, `date_registration_before`, `phase_normalized`, `country`, …) | See [Search endpoints](#search-endpoints) below |
| Trials | `POST /trials/search/` | Request **body** accepts the same fields as `GET /trials/search/`: `team_id`, `subject_id`, `title`, `summary`, `search`, `status`, `ordering`, `page`, `page_size`, `all_results`, plus every `GET /trials/` filter | See [GET vs POST](#get-vs-post-on-search-endpoints) |
| Trials | `GET /trials/sites/` | Same filters as `GET /trials/`, plus `latitude__isnull` (`true`/`false`) | Flat, paginated `TrialSite` listing across the filtered trial set: `{trial_id, name, city, country, latitude, longitude}` per row — for a site-level pin map. `trial_sites` itself is detail-only (appears on `GET /trials/{id}/`, never on the list/CSV/search responses). Pagination is mandatory here — `?all_results=true` returns 400; max `page_size` is 500 |
| Email templates | `GET /emails/` | None | Template preview dashboard |
| Email templates | `GET /emails/preview/{template_name}/` | `template_name` (path) | |
| Email templates | `GET /emails/context/{template_name}/` | `template_name` (path) | |
| RSS feeds | `GET /feed/sites/{site_id}/author/{orcid}/` | `site_id`, `orcid` (path) | Site-scoped — see [RSS feeds](#rss-feeds) |
| RSS feeds | `GET /feed/sites/{site_id}/trials/subject/{subject_slug}/` | `site_id`, `subject_slug` (path) | Site-scoped — see [RSS feeds](#rss-feeds) |
| RSS feeds (old, no `site_id`) | `GET /feed/author/{orcid}/`, `GET /feed/trials/subject/{subject_slug}/` | `orcid` / `subject_slug` (path) | **301** to the `/feed/sites/3/...` equivalent — see [Old feed URLs](#old-feed-urls--permanent-redirect) |
| Stats | `GET /stats/` | `team`, `site`, `subject`, `include_public`, `organization` (alias `org`, deprecated) | See [Stats endpoint](#stats-endpoint) below |
| Sites | `GET /sites/` | None | Publicly readable sites as `{site_id, domain, name}`. **Unscoped by design** — it is the discovery entry point for callers that need a `site_id`, so it cannot require one. Everything returned is already public |
| Tenants | `GET /tenants/` | None | MCP tenants — see [Discovering MCP tenants](#discovering-mcp-tenants) above. Never gated by site resolution; carries `Vary: Authorization` |
| Subscriptions | `POST /subscriptions/new/` | `first_name`, `last_name`, `email`, `profile`, `list` | POST-only; `GET` returns `405` with `Allow: POST` |

### Search endpoints

The dedicated search endpoints — `/articles/search/`, `/trials/search/`, and
`/authors/search/` — scope results to a single team + subject and accept richer
search parameters than the plain list endpoints.

**Shared contract:**

- Both **GET** (query params) and **POST** (JSON body) are supported, and
  accept the same fields — see [GET vs POST](#get-vs-post-on-search-endpoints).
  Prefer GET where practical: it's cacheable and linkable. POST exists mainly
  for a `search` string too long or awkward to put in a URL.
- `team_id` **and** `subject_id` are **required**. The subject must belong to the
  team.
- Results are paginated (default `page_size` 10, max 100) and ordered by
  `discovery_date` newest-first by default; override with `ordering`.
- CSV export: add `format=csv` (and usually `all_results=true`). See
  [csv-export.md](csv-export.md).

**Search parameters:**

Every parameter below works identically on GET (query string) and POST (JSON
body) — see [GET vs POST](#get-vs-post-on-search-endpoints) for how that's
implemented and the one precedence rule to know.

| Parameter | Endpoints | GET | POST | Description |
|:----------|:----------|:---:|:----:|:------------|
| `title` | articles, trials | ✅ | ✅ | Match in the title only (case-insensitive, partial). |
| `summary` | articles, trials | ✅ | ✅ | Match in the summary/abstract only. |
| `search` | articles, trials | ✅ | ✅ | Boolean search (e.g. `a OR b`). Articles: title or summary. Trials: title, summary or scientific title. |
| `status` | trials | ✅ | ✅ | Case-insensitive exact match on the raw `recruitment_status` string (e.g. `Recruiting`). For the canonical vocabulary use `recruitment_status_normalized` on `GET /trials/`. |
| `full_name` | authors | ✅ | ✅ | Match on the author's full name (case-insensitive, partial). |
| `page`, `page_size`, `all_results` | all | ✅ | ✅ | Pagination. `FlexiblePagination` checks the POST body for all three. |
| `ordering` | articles, trials | ✅ | ✅ | Not supported on `/authors/search/`, which always orders by `author_id`. |
| `published_date_after` / `published_date_before` | articles | ✅ | ✅ | Publication date range — same semantics as on `GET /articles/`. |
| `date_registration_after` / `date_registration_before` | trials | ✅ | ✅ | Registration date range — same semantics as on `GET /trials/`. |
| Any other list-endpoint filter | articles, trials, authors | ✅ | ✅ | `relevant`, `subjects`, `open_access`, `phase_normalized`, `country`, `sponsor_id`, `orcid`, … — the search endpoints mount the same `ArticleFilter` / `TrialFilter` / `AuthorFilter` as the list endpoints, so every filter defined there applies. |

Trials' `search` also reads `scientific_title` because trials from registries other than
ClinicalTrials.gov, which arrive through WHO ICTRP, rarely have a `summary`, and their
`title` is the registry's lay public title — the trial's actual name is usually only in
the scientific title. This is additive for bare and `OR`-ed terms (a trial can only gain
matches), but `-term` / `NOT term` now also excludes a trial whose scientific title
contains the excluded word, even if its title and summary don't.

#### GET vs POST on search endpoints

`/articles/search/`, `/trials/search/` and `/authors/search/` accept the same
fields on GET and POST. On POST, `BodyParamsAsQueryParamsMixin` merges the JSON
body into the request's query params before django-filter and `OrderingFilter`
run, so both verbs share one filtering code path. The one precedence rule, for
filter and ordering keys specifically: **if a key is set in both places, the
query-string value wins.**

`team_id` and `subject_id` (and `full_name` on `/authors/search/`) are the
exception — the views need those for required-parameter validation before any
filtering happens, so they're always taken from the JSON body on POST
(`identity_params` in the mixin), overriding the usual query-string-wins rule.
This isn't just about validation: `team_id`/`subject_id` are also
`ArticleFilter`/`TrialFilter` fields (and `full_name` an `AuthorFilter` field),
so without this carve-out a mismatched query-string value would leak into the
filterset while validation used the body's value — silently intersecting the
two and emptying the response, rather than the query string simply having no
effect as intended.

**Pagination doesn't follow the query-string-wins rule at all.** `page` and
`page_size` go through `FlexiblePagination`, which reads them straight from the
JSON body on POST when present — body wins there, the opposite of the
filter/ordering rule. `all_results` behaves like the filter keys (query string
wins) since it happens to fall back through the same merged query params.

A list-valued body field (e.g. `{"subjects": [1, 3]}`) is joined into a
comma-separated string before merging, matching the query-string form
(`?subjects=1,3`) that `BaseInFilter`-based fields expect.

```bash
GET /trials/search/?team_id=1&subject_id=1&date_registration_after=2024-01-01&date_registration_before=2024-01-31

POST /trials/search/
{"team_id": 1, "subject_id": 1, "date_registration_after": "2024-01-01", "date_registration_before": "2024-01-31"}
```

Both return the same trials.

> **Changelog:** before this fix, POST only read `title`/`summary`/`search`/
> `status`/`ordering`/pagination from the body; every other filter (e.g.
> `date_registration_after`, `relevant`, `country`) was silently dropped and the
> POST returned an unfiltered `200`. If you previously worked around this by
> putting filters on the query string of a POST request, that still works
> (query string wins), but it's no longer required.

Prefer GET where practical — it's cacheable and linkable. POST exists for
`search` strings with long boolean expressions that would exceed practical URL
length limits:

```bash
POST /articles/search/
{"team_id": 1, "subject_id": 1, "search": "<very long boolean query>"}
```

**Error responses** (identical across the three endpoints):

| Status | Body | Condition |
|:-------|:-----|:----------|
| `400` | `{"error": "Missing required parameters: team_id, subject_id"}` | `team_id` or `subject_id` omitted |
| `400` | `{"error": "team_id and subject_id must be integers"}` | Non-integer `team_id`/`subject_id` |
| `404` | `{"error": "Team with ID <id> not found"}` | `team_id` does not exist |
| `404` | `{"error": "Subject with ID <id> not found or does not belong to team <team_id>"}` | `subject_id` not found or not in that team |

Each result is a full serializer object — the same shape as the corresponding
list endpoint (`ArticleSerializer` / `TrialSerializer` / `AuthorSerializer`).

**Examples:**

```bash
# Article search by keyword (GET)
GET /articles/search/?team_id=1&subject_id=2&search=Ocrelizumab

# Article search by abstract keyword (POST)
POST /articles/search/
{"team_id": 1, "subject_id": 2, "summary": "treatment"}

# Recruiting trials matching a title keyword
POST /trials/search/
{"team_id": 1, "subject_id": 2, "title": "cancer", "status": "Recruiting"}

# Export all matching trials as CSV
GET /trials/search/?team_id=1&subject_id=2&search=diabetes&format=csv&all_results=true
```

### Stats endpoint

`GET /stats/` returns aggregate counts for the data visible to the caller.

```
GET /stats/
GET /stats/?site=2
GET /stats/?site=2&subject=1
GET /stats/?organization=3   # deprecated, still supported
GET /stats/?organization=3,7
GET /stats/?team=12
GET /stats/?organization=3&team=12
GET /stats/?include_public=true
GET /stats/?subject=4
GET /stats/?subject=4,9
GET /stats/?team=12&subject=4
```

Response shape (additive: `by_subject` is only meaningfully populated when the
in-scope team(s) have subjects, but the key is always present):

```json
{
  "articles": 1234,
  "trials": 56,
  "subscribers": 78,
  "authors": 910,
  "sources": {
    "total": 42,
    "by_type": { "science paper": 35, "clinical trial": 7 },
    "by_domain": [
      { "domain": "pubmed.ncbi.nlm.nih.gov", "count": 12 },
      ...
    ]
  },
  "by_subject": [
    { "subject_id": 2, "subject_name": "Multiple Sclerosis", "articles": 812, "trials": 30, "authors": 640, "sources": 12 },
    { "subject_id": 5, "subject_name": "Rare Disease", "articles": 0, "trials": 0, "authors": 0, "sources": 0 }
  ]
}
```

#### Filter parameters

| Parameter | Type | Behaviour |
|:----------|:-----|:----------|
| `team` | int or CSV of ints | Scope counts to one or more teams. |
| `subject` | int or CSV of ints | Scope counts to one or more subjects (union, not intersection — matches `team`/`organization`, not the `subject_id` filter on the list endpoints). Adds/populates `by_subject`. IDs only — subject slugs are not unique across teams, so there is no slug form of this filter here. |
| `site` | int or comma-separated ints | Scope to the subjects one or more sites publish. Sugar for `subject=` with that site's scope; intersects with an explicit `subject=` rather than overriding it. A site the caller cannot reach returns 404. |
| `include_public` | bool (`true`/`false`) | Handled by the visibility layer — adds public sites' scopes for identified callers. |
| `organization` (alias `org`) | int or comma-separated ints | **Deprecated** — prefer `site`. Organisations are no longer the visibility unit. Still works and still means exactly what it always meant; it is deprecated rather than redefined, because silently changing what a documented parameter returns is worse than keeping it. |

When both `organization` and `team` are given the effective scope is their **intersection**: teams that belong to the requested org(s). The same applies to `subject` versus `team`/`organization`: a subject the caller can see but that doesn't belong to the requested team/org scope does **not** 404 — it returns a well-formed payload with every count at zero and `by_subject: []`.

#### `by_subject`

- Lists **every subject in scope**, including ones with zero articles and zero trials — this is deliberate (it doubles as the data a subject picker needs) and differs from `/articles/stats/` and `/trials/stats/`, which aggregate off the through table and omit empty subjects.
- Scope: subjects whose team is in the resolved team scope, further narrowed to `?subject=` when given. Ordered by `subject_name` ascending.
- Per-subject counts are `articles`, `trials`, `authors`, `sources` — **no per-subject `subscribers`**. With `Lists` as the only path from a subscriber to a subject, that number would describe list-tagging more than the subject itself.
- `sources` counts distinct **domains** (matching the top-level `sources.total` semantics), not feed rows — two RSS feeds on the same domain count once. A `Sources` row with `subject` unset (`null`) is excluded from every `by_subject` row and from the totals, with or without `?subject=` — content counts are scoped to the caller's visible subjects, and a NULL subject matches none of them. This matches `/sources/`, where such a row has always been unreachable.
- Neither `authors` nor `sources` in a `by_subject` row sums to the top-level total, and that's correct: both are *distinct within that subject*. An author publishing under two subjects appears in both rows and once at the top; a domain feeding two subjects likewise.
- A trial with no subject assigned is excluded from every `by_subject` row, so a subject-filtered `trials` count can be lower than the team-scoped one. Coverage is now near-complete — measured 2026-09-09, 63 of 17,404 trials (0.4%) and 372 of 53,054 articles (0.7%) carry no subject — so the gap is small, but it is a gap, not a bug.

#### Error responses

| Status | Condition |
|:-------|:----------|
| `400 Bad Request` | Non-integer value in `team`, `organization`, or `subject`. |
| `404 Not Found` | Any requested `team`, `organization`, or `subject` is not visible to the caller (hidden org — existence is not leaked). Subject visibility is judged against the caller's visible organisations only, independent of `team`/`organization` scoping (see the intersection note above). |

#### Caching

Results are cached for `STATS_CACHE_TTL` seconds (default 600 s / 10 min) using Django's database cache. All gunicorn workers share the same cached value. The cache key encodes both the resolved set of in-scope team IDs and the requested `subject` IDs, so different filter combinations — including the same team with and without a subject filter — are cached independently.

`by_subject` has a second, finer-grained cache layer underneath the whole-payload one: each subject's `articles`/`trials`/`authors` numbers are cached per `(team scope, subject)`, independently of which *other* subjects were requested alongside it. A `?subject=` combination that's new but overlaps a team scope seen before reuses whatever rows are already warm and only computes the missing ones — so, unlike the totals, `by_subject`'s numbers can be up to `STATS_CACHE_TTL` older than the rest of the payload if the whole-payload entry happens to expire before the per-subject one does. `sources` and `subject_name` are excluded from that layer (recomputed from data the call already fetches for the totals) and are therefore always current.

### Trials-specific filter parameters

| Parameter | Description |
|:----------|:------------|
| `trial_id` | Filter by specific trial ID |
| `has_takeaways` | `true`/`false`: whether the caller's own organisation has written takeaways for the trial (see [Editorial content](#editorial-content)) |
| `include` | `editorial` adds the `editorial` list to each trial (see [Editorial content](#editorial-content)) |
| `internal_number` | Filter by WHO internal number |
| `phase` | Filter by trial phase (e.g., `Phase III`) |
| `phase_normalized` | Exact match against the canonical phase: `early_phase_1`, `phase_1`, `phase_1_2`, `phase_2`, `phase_2_3`, `phase_3`, `phase_3_4`, `phase_4`, `post_market`, `not_applicable`, `other`. Accepts a single value or a comma-separated list matched with OR, e.g. `?phase_normalized=phase_2,phase_3` |
| `recruitment_status_normalized` | Exact match against the canonical recruitment status: `not_yet_recruiting`, `recruiting`, `enrolling_by_invitation`, `active_not_recruiting`, `not_recruiting`, `suspended`, `completed`, `terminated`, `withdrawn`, `unknown`, `other`. Accepts a single value or a comma-separated list matched with OR, e.g. `?recruitment_status_normalized=recruiting,active_not_recruiting` |
| `study_type` | Legacy free-text `icontains` filter on the raw registry study-type string — prefer `study_type_normalized` |
| `study_type_normalized` | Exact match against the canonical study type: `interventional`, `observational`, `expanded_access`, `basic_science`, `other` |
| `primary_sponsor` | Legacy free-text filter on the raw registry sponsor string — prefer `sponsor_id`/`sponsor_slug` |
| `sponsor_id` | Exact match against a canonical sponsor's id (see [Sponsor canonicalization](#sponsor-canonicalization)) |
| `sponsor_slug` | Exact match against a canonical sponsor's slug |
| `source_register` | Filter by source registry (e.g., `ClinicalTrials.gov`) |
| `countries` | Filter by trial countries |
| `condition` | Filter by medical condition |
| `intervention` | Filter by intervention type |
| `therapeutic_areas` | Filter by therapeutic areas |
| `inclusion_agemin` / `inclusion_agemax` | Legacy exact-match filter on the raw registry age strings (e.g., `18 Years`) — prefer `age_eligible` |
| `age_eligible` | Numeric age in years (e.g. `?age_eligible=40`). Returns trials whose canonical eligible age range (`inclusion_age_min_years`/`inclusion_age_max_years`) includes this age; a trial with no stated bound on either side is treated as open on that side. Both fields are also exposed on `TrialSerializer` for direct use. |
| `inclusion_gender_normalized` | Exact match against canonical sex eligibility: `all`, `female`, `male`. **Breaking change (2026-07-20):** replaces the removed legacy `inclusion_gender` substring filter, which returned confidently wrong results (`?inclusion_gender=Female` matched "Female, Male", a both-sexes trial — 82% false positives). `?inclusion_gender=...` is no longer a recognised parameter; requests using it are silently unfiltered rather than rejected. |
| `date_registration_after` | date (YYYY-MM-DD) | Trials registered on or after this date (inclusive). Returns 400 for invalid dates. |
| `date_registration_before` | date (YYYY-MM-DD) | Trials registered on or before this date (inclusive). Returns 400 for invalid dates. |

### Registry identifier filters

`?nct=`, `?eudract=`, `?euct=`, `?ctis=`, and the umbrella `?identifiers=` on
`GET /trials/` (and everywhere it composes, e.g. `/trials/search/`). Each
accepts a single value or a comma-separated list and matches any of them,
case-insensitively.

**Any common format is accepted** — a bare number (`2020-004505-32`), a
registry-prefixed one (`EUDRACT2020-…`, `EUCTR2020-…-DE`, `CTIS2023-…`). NCT numbers
also match with a space or dash after `NCT`. The value's
own *shape* decides what it matches, not which param it arrived in: an
EudraCT-shaped number passed to `?ctis=` still matches as EudraCT, and a
CTIS-shaped number passed to `?eudract=` still matches as CTIS. `?identifiers=`
is the true umbrella — it isn't limited to the four typed params' registries;
any format `identifiers_normalized` recognises works there too, e.g.
`?identifiers=ISRCTN14048364` or `?identifiers=U1111-1299-8084` (the WHO
Universal Trial Number), neither of which has its own dedicated param.

**What they read.** Not only the trial's registry record (`identifiers`
JSON) — also its secondary ids (`secondary_id`, and ClinicalTrials.gov's
typed `ctg_secondary_ids`) and the sponsor's own study code
(`identifiers.org_study_id`), wherever a matching id happens to sit. This is
what `identifiers_normalized` is: the derived, canonical union of all three,
recomputed on every save — see
[trials-field-normalization.md](trials-field-normalization.md#field-identifiers--secondary_id--ctg_secondary_ids--identifiers_normalized-multi-input)
for exactly which sources are trusted and which are dropped on a conflict.

**A lookup can return more than one row for what is really one trial.**
Gregory stores one row per source import; a trial registered in two
registries (e.g. ClinicalTrials.gov and EU CTIS) is two rows until the
separate dedup/merge project runs, and a registry-id lookup finds both. This
is intentional, not a bug — see `docs/trials-identity-dedup.md`.

`identifiers_normalized` is also on `TrialSerializer` (next to `identifiers`)
so a caller can see which of a trial's ids satisfied its query.

### Trials ordering

`GET /trials/?ordering=<field>` (prefix with `-` to reverse). Accepted values: `discovery_date` (default, newest first via `-discovery_date`), `published_date`, `title`, `trial_id`, `last_updated`, `recruiting_first`.

`recruiting_first` sorts by recruitment *availability* — "can a patient join this today?" — not alphabetically on `recruitment_status_normalized`:

`recruiting` → `enrolling_by_invitation` → `not_yet_recruiting` → `active_not_recruiting` → `suspended` → `not_recruiting` → `unknown` → `other` → `completed` → `terminated` → `withdrawn`, with null status last.

That order is for plain `?ordering=recruiting_first` (ascending). `?ordering=-recruiting_first` reverses the whole scale, so null-status trials come first there instead, not last.

Many trials share a rank, so ties are broken automatically by `-discovery_date` in both directions — page-to-page ordering stays stable.

**Unrecognised `ordering` values are silently ignored, not rejected** (DRF `OrderingFilter`'s default behaviour) — a typo or a stale field name falls back to the default ordering instead of returning an error. This applies to every endpoint that accepts `ordering`.

### Categories ordering

`GET /categories/?ordering=<field>` (prefix with `-` to reverse). Accepted values: `category_name` (default), `id`, `article_count_annotated`, `trials_count_annotated`, `authors_count_annotated`.

`article_count_annotated` and `trials_count_annotated` sort by the same numbers the response reports as `article_count_total` and `trials_count_total`. Both are free: the queryset already computes them for every request.

`authors_count_annotated` sorts by the number of distinct authors across a category's articles — the same number reported as `authors_count` in the response body. Expect it to be slower than the other four on teams with large categories.

`authors_count` is present in every response regardless of ordering, so this sort does not introduce the author counting; it widens it. Ordering is applied before pagination, so ranking by this value counts distinct authors for every category matching the filters, where an unsorted request only counts the ones on the page it returns.

### Sponsor canonicalization

Duplicate/variant spellings of the same real-world sponsor (`"Novartis"`, `"Novartis Pharma AG"`, `"NOVARTIS FARMA"`, ...) are resolved to a single canonical `Sponsor` entity — see `docs/trials-field-normalization.md` for how the resolution works.

Every trial response includes a nested, read-only `sponsor` object resolved from its raw `primary_sponsor` string (the raw `primary_sponsor`/`secondary_sponsor`/`sponsor_type` fields are untouched):

```json
"sponsor": { "id": 12, "slug": "novartis", "name": "Novartis", "sponsor_type": "industry" }
```

`sponsor` is `null` when the raw string hasn't been resolved to a canonical entity yet (tracked by the `no_sponsor` count on `/trials/stats/`). Filter trials by canonical sponsor with `sponsor_id` or `sponsor_slug` (both listed above), and browse the full canonical list at `GET /sponsors/`.

---

## `POST /articles/post/` response codes

This endpoint requires an `APIAccessScheme` API key (sent as the raw value in the `Authorization` header). The following status codes are returned:

| HTTP status | Condition |
|:------------|:----------|
| `200 OK` | Article or trial created successfully |
| `200 OK` | Duplicate — an item with the same DOI, title, or trial identifier already exists; the source/team/subject links on the existing record were updated |
| `400 Bad Request` | A required field is missing or invalid: `kind`, `source_id`, both `doi` and `title` absent, `kind` value does not match the source's `source_for`, or unsupported `kind` value |
| `400 Bad Request` | The `source_id` belongs to an organisation different from the one bound to the API key (cross-org payload) |
| `401 Unauthorized` | No API key provided, key is invalid, or the request IP is not in the key's allowlist |
| `403 Forbidden` | The API key has no organisation assigned (`organization = null`) |
| `404 Not Found` | The `source_id` does not exist in the database, or the source has no team assigned |
| `500 Internal Server Error` | The article or trial could not be saved, or an unexpected error occurred |

> **Breaking change (introduced in this release):** Prior to this release, `FieldNotFoundError` returned `200`, `SourceNotFoundError` returned a non-standard code, and `ArticleNotSavedError` returned `204`. These have been standardised to `400`, `404`, and `500` respectively.

### Required fields

| Field | Required | Description |
|:------|:---------|:------------|
| `kind` | yes | One of: `science paper`, `trials`, `news article` — must match the source's `source_for` value |
| `source_id` | yes | ID of the `Sources` record; must belong to the same org as the API key |
| `doi` or `title` | at least one | Used for dedup and CrossRef enrichment (`science paper` only) |
| `link` | no | URL of the article or trial |
| `summary` | no | Abstract or description |
| `published_date` | no | ISO 8601 date string |
| `identifiers` | no | JSON object with trial identifiers (`euct`, `nct`, `eudract`) — `trials` kind only |

### `POST /articles/edit/`

Edits an existing article, looked up by `doi` (case-insensitive). Requires an `APIAccessScheme` API key bound to a site of its organisation. The article must belong to the key's organisation through one of its teams.

| Field | Scope | Description |
|:------|:------|:------------|
| `takeaways`, `summary_plain_english` | The key's site | Upserted into the article's `ArticleSiteContent` row for the key's site. An empty string clears the field (stored as `NULL`). Fields absent from the payload are left alone. Other sites' text is never touched |
| `access`, `retracted`, `kind` | The article | Written to the article itself, so every site sees the change |

| HTTP status | Condition |
|:------------|:----------|
| `200 OK` | Edited. The body carries `article_id`, `doi`, `organization_id`, `site_id` and `updated_fields` |
| `400 Bad Request` | `doi` missing, or an invalid `access`, `retracted` or `kind` value |
| `401 Unauthorized` | No API key, key invalid, or the request IP is not in the key's allowlist |
| `403 Forbidden` | The key has no organisation, no site, or a site that belongs to another organisation; or the article is not visible to the key's organisation |
| `404 Not Found` | No article has that DOI |
| `409 Conflict` | The DOI matches more than one article (`article_ids` lists them) |

> **Breaking change.** Before per-site editorial content, `takeaways` and `summary_plain_english` were written for the key's organisation, and the response had no `site_id`. A key with no site can no longer edit these fields: bind it to a site in the admin (**API access schemes**).

---

## Planned endpoints (not yet implemented)

<details>
<summary>Endpoints not yet available</summary>

| Model | Endpoint | Notes |
|:------|:---------|:------|
| Authors | `POST /authors/` | Write disabled |
| Authors | `PUT /authors/{id}/` | Write disabled |
| Authors | `DELETE /authors/{id}/` | Write disabled |
| Categories | `POST /categories/` | Write disabled |
| Categories | `PUT /categories/{id}/` | Write disabled |
| Categories | `DELETE /categories/{id}/` | Write disabled |
| Entities | All endpoints | Not implemented |
| Subjects | `POST /subjects/` | Write disabled |
| Subjects | `PUT /subjects/{id}/` | Write disabled |
| Subjects | `DELETE /subjects/{id}/` | Write disabled |
| Sources | `POST /sources/` | Write disabled |
| Sources | `PUT /sources/{id}/` | Write disabled |
| Sources | `DELETE /sources/{id}/` | Write disabled |
| Articles | `PUT /articles/{id}/` | Write disabled |
| Articles | `DELETE /articles/{id}/` | Write disabled |
| Trials | `POST /trials/` | Write disabled |
| Trials | `PUT /trials/{id}/` | Write disabled |
| Trials | `DELETE /trials/{id}/` | Write disabled |
| Teams | `POST /teams/` | Write disabled |
| Teams | `PUT /teams/{id}/` | Write disabled |
| Teams | `DELETE /teams/{id}/` | Write disabled |
| MLPredictions | All endpoints | Not implemented |
| ArticleSubjectRelevance | All endpoints | Not implemented |

</details>

---

## Removed team-based endpoints

The following URL patterns have been removed (they now return `404 Not Found`). Use the parameter-based equivalents shown instead:

| Removed | Replacement |
|:--------|:------------|
| `GET /teams/{id}/articles/` | `GET /articles/?team_id={id}` |
| `GET /teams/{id}/articles/subject/{subject_id}/` | `GET /articles/?team_id={id}&subject_id={subject_id}` |
| `GET /teams/{id}/articles/category/{category_slug}/` | `GET /articles/?team_id={id}&category_slug={slug}` |
| `GET /teams/{id}/articles/source/{source_id}/` | `GET /articles/?team_id={id}&source_id={source_id}` |
| `GET /teams/{id}/subjects/` | `GET /subjects/?team_id={id}` |

The parameter-based approach supports combining any set of filters in a single request.

Note: `GET /teams/{id}/subjects/{subject_id}/categories/` is unaffected and continues to work.

---

## Data formats

All endpoints support three formats. Specify via the `format` query parameter or `Accept` header.

| Format | `format=` value | `Accept` header |
|:-------|:----------------|:----------------|
| JSON (default) | `json` | `application/json` |
| Browsable API | `html` | `text/html` |
| CSV | `csv` | `text/csv` |

For CSV export details and streaming behaviour, see [csv-export.md](csv-export.md).
