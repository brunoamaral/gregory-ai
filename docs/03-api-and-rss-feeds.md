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
| Research by condition (subject) | `GET /feed/sites/{site_id}/articles/subject/{subject_slug}/` |
| Research by condition, relevant only | `GET /feed/sites/{site_id}/articles/subject/{subject_slug}/relevant/` |
| Research by topic (category) | `GET /feed/sites/{site_id}/articles/category/{category_slug}/` |
| Research by topic, relevant only | `GET /feed/sites/{site_id}/articles/category/{category_slug}/relevant/` |
| Recruiting clinical trials by subject | `GET /feed/sites/{site_id}/trials/subject/{subject_slug}/recruiting/` |
| New clinical trials by subject | `GET /feed/sites/{site_id}/trials/subject/{subject_slug}/` |
| Articles by author (ORCID) | `GET /feed/sites/{site_id}/author/{orcid}/` |
| Article search results | `GET /feed/sites/{site_id}/articles/search/?…` — see [Search feeds](#search-feeds) |
| Clinical trial search results | `GET /feed/sites/{site_id}/trials/search/?…` — see [Search feeds](#search-feeds) |
| New patent families by subject | `GET /feed/sites/{site_id}/patents/subject/{subject_slug}/` — see [Patent feeds](#patent-feeds) |
| New patent families by topic (category) | `GET /feed/sites/{site_id}/patents/category/{category_slug}/` — see [Patent feeds](#patent-feeds) |
| Patent search results | `GET /feed/sites/{site_id}/patents/search/?…` — see [Patent feeds](#patent-feeds) |

The variants of the subject, category and author feeds are fixed path segments, not query parameters, so the set of URLs a site can have is small and known in advance (about subjects × 4 + categories × 2). **On those feeds any query string is ignored** and does not change the response. The [search feeds](#search-feeds) are the one exception: a search is open-ended, so they take an allowlisted query string.

**Feeds are editorial content.** They follow stricter rules than the API, where the frontend decides how to use what the API returns:

- **Retracted articles are never included** (`retracted=true`), in any article feed.
- The editorial text in an item (plain-English summary, takeaways) is **the requested site's own** `ArticleSiteContent`; another site's text for the same article never appears.
- *Relevant only* means an editor's verdict wins for each subject, and the ML models decide only when there is none. That is the editorial rule the relevant-only sitemaps use, and it is stricter than `relevant=true` on `/articles/` (see [`relevant`](#articles-query-parameters)), where an editor's "not relevant" does not hide an ML "yes". It is limited to the feed's subject: the one subject for a subject feed, the category's subjects within the site's scope for a category feed.

**Which items.**

- *Subject feeds*: items tagged with the subject.
- *Category feeds*: items in the category **and** tagged with at least one subject that is in both the category's subjects and the site's scope. An article that sits in a category only through a subject the site doesn't cover does not appear.
- *Recruiting trials*: the trials feed narrowed to `recruitment_status_normalized` of `recruiting` or `not_yet_recruiting`. `enrolling_by_invitation` is left out because a patient can't sign up for one of those, and a trial with no normalised status is left out too. A trial is picked up when it is discovered; moving from "not yet recruiting" to "recruiting" later does not bring it back.
- Trials have no relevance judgement, so there are no relevant-only trial feeds.

**Order, dates and size.** Article and trial feeds are ordered by **discovery date**, newest first, and each item's `<pubDate>` is its discovery date. Ordering by publication date would let a paper published three months ago but found today land below the cut-off and never be delivered; the journal's publication date is in the description instead. The **author feed is the exception**: it is ordered by, and shows, the publication date, because it is low volume (nothing drops off the end) and readers expect it.

| Feed | Items |
|:-----|:------|
| Research by subject / category (all and relevant-only) | 100 |
| Trials (new and recruiting) | 50 |
| Articles by author | 50 |

A busy condition gets dozens of new articles a day, so a reader that polls less than daily can miss some of the 100 in the unfiltered feed. **For low-volume reading, recommend the relevant-only variant.**

**Item content.** Article items carry the article title, a link to `https://{site.domain}/articles/{article_id}/`, a `guid` of `doi:{doi}` (or `urn:gregory:article:{id}`, not a permalink), the first three authors in `dc:creator` followed by "et al." when there are more (omitted when there are none), and one `<category>` per in-scope subject and per category. The HTML description holds these sections, each left out when empty: *In plain English*, *Key takeaways* (this site's editorial content), a metadata row (journal or publisher · publication date `YYYY-MM-DD` · "Open access"), and the *Abstract*. Every database value is HTML-escaped and line breaks become `<br>`, so editorial or registry text can't inject markup. Authors are listed in the order they were attached to the article (the join table has no position field). Trial items keep the trial description (status, phase, sponsor, countries, condition, intervention, eligibility, …) with every value escaped. Their `<category>` elements are the normalised phase and status labels followed by the in-scope subject names, and their link is the site's own trial page `https://{site.domain}/trials/{trial_id}/` when the site's `CustomSetting.sitemap_include_trials` is on (it means "this frontend publishes trial pages"), the trial registry's page otherwise.

**Channel.** Article feeds are titled `{Subject} research — {site}` (`Relevant {Subject} research — {site}`) and `{Category} — {site}` (`Relevant research: {Category} — {site}`); the trial feeds are `{Subject} clinical trials — {site}` and `Recruiting {Subject} trials — {site}`. The channel `<link>` of the article feeds is the site's homepage; the trial feeds link to `/trials/subject/{slug}/`.

**Empty feed or 404.** A subject or category that is in the site's scope but has nothing to show right now returns an **empty feed (200)**: a new subject, or a recruiting feed with nothing open at the moment, is a valid subscription that will fill up later. The author feed keeps its **404** when none of the author's non-retracted articles carries an in-scope subject, because an author with nothing to show on that site isn't a valid subscription.

**Slug resolution.** A subject slug is looked up *only among the requested site's scope subjects*: `Subject.subject_slug` is unique per team, not globally, so two teams may share a slug. If two subjects in the **same** site's scope share a slug the feed returns 404 and logs a warning, because that is a curation error and the feed doesn't guess which was meant. `TeamCategory.category_slug` is globally unique; a category is in scope when its subjects overlap the site's scope, otherwise 404.

**Caching.** Every site-scoped feed is cached for **one hour** (`cache_page(3600)`, like the sitemaps), so editorial edits and newly found items take up to an hour to show up. The [search feeds](#search-feeds) cache themselves for the same hour, keyed on the canonical query string. The 301 redirects below are not cached.

**Feeds are site-scoped, not caller-scoped** — the same design as the [sitemaps](#sitemaps) below, and a deliberate change from how this worked before. A feed serves the *requested site's* `CustomSetting.scope_subjects`: the subject, category and trials feeds 404 when the requested subject or category is not in that site's scope, and the author feed 404s when none of the author's articles carries a subject in it, otherwise listing only the articles that do. The response never varies by who (or what) is asking — a feed reader has no identity and the response is cached, so there is nothing to key a caller-specific response on. `?include_public=true` and any notion of "the caller's own scope" (API key, signed-in user) do not apply to these URLs at all.

A site's feed is gated by its own `CustomSetting.rss_enabled` alone (the [patent feeds](#patent-feeds) also need `patents_enabled`) — 404 when it's off, or when the site has no `CustomSetting` row, or when `site_id` doesn't exist. Unlike the sitemaps, a feed's scope is **not** narrowed to the publicly-visible subject set: `rss_enabled` and `scope_subjects` are the whole gate, whether or not the site is `api_public` — the same rule a site-bound API key follows for its own scope (see [Visibility rules summary](#visibility-rules-summary)). Curation into the site's scope is the whole grant otherwise, so team ownership plays no part: a subject with no team is served once some site's scope names it, exactly as for the API and sitemaps.

The author feed's `<link>` element points at the requested site's author profile page (`https://{site.domain}/authors/{orcid}/`) when that site's `CustomSetting.has_author_pages` is on, and at `https://orcid.org/{orcid}` otherwise. See [Author profile page links](06-organisations-teams-and-sites.md#author-profile-page-links).

### Search feeds

"Tell me when something new matches what I searched for." A reader runs a search on the frontend and gets a feed URL for it; the feed returns the newest items matching the same search. It is the general-purpose version of the fixed feeds, and it covers combinations no fixed feed will ever have ("recruiting MS trials in Europe for a 40-year-old").

| Feed | URL |
|:-----|:----|
| Articles | `GET /feed/sites/{site_id}/articles/search/?…` |
| Clinical trials | `GET /feed/sites/{site_id}/trials/search/?…` |

The parameters have **the same names and meaning as on `/articles/` and `/trials/`** (the feeds reuse `ArticleFilter` and `TrialFilter`), so a frontend can pass its current search state straight through. Only these are accepted:

| Feed | Allowed parameters |
|:-----|:-------------------|
| Articles | `search`, `title`, `summary`, `subject_id`, `category_slug`, `relevant`, `open_access`, `has_takeaways` |
| Trials | `search`, `title`, `summary`, `subject_id`, `category_slug`, `recruitment_status_normalized`, `phase_normalized`, `region`, `country`, `sponsor_slug`, `age_eligible`, `inclusion_gender_normalized` |

Everything else is **ignored**: `utm_*`, anything a reader adds, and deliberately also date windows (`published_date_*`, `last_days`, `week`, `year`; a feed already shows "newest first" and a fixed window would eventually leave it permanently empty), `team_id` and `site_id` (the site comes from the path), the ML parameters (`ml_threshold`, `ml_score_min`; `relevant=true` covers what readers need), single-record lookups (`doi`, `nct`, identifiers) and the legacy raw-text filters. An ignored parameter does not change the response or the cache key.

- **Base set.** Everything in the requested site's scope, minus retracted articles, ordered by discovery date (newest first) with `<pubDate>` the discovery date, 50 items, no pagination. With no parameters at all the feed is the whole site, newest first.
- **`relevant` is scoped to the site.** `relevant=true` / `false` uses the editorial rule of the relevant-only feeds (an editor's verdict wins, the ML models decide only when there is none), restricted to the subjects the feed covers: the site's scope, narrowed by `subject_id` and by the category's subjects. That is not what `relevant=` does on `/articles/`, where the rule is "relevant by a human or by AI" and, with no `subject_id`, any auto-predicted subject in the database counts.
- **`has_takeaways`** means *this site* has takeaways for the article, whoever is asking.
- **`subject_id` / `category_slug`** outside the site's scope return **404**, like the fixed feeds. A category is also narrowed to the subjects it shares with the site, so an article can't leak through a subject the site doesn't cover. A `subject_id` and a `category_slug` that are each in scope but share no subject give an empty feed (200).
- **Invalid values return 400** with a plain-text message naming the parameters (never the values): a non-integer `subject_id`, `relevant` / `open_access` / `has_takeaways` other than `true` or `false`, a choice outside its allowed values (`region=mars`), a non-numeric or out-of-range `age_eligible` (0 to 150), and any value longer than **300 characters** (`search`, `title` and `summary` are capped at 300; there is no feed equivalent of the API's POST route for very long boolean searches, because feed readers only send GET). The boolean search parser is best-effort and never rejects a `search` string; a malformed one falls back to a phrase match. Only `GET` and `HEAD` are accepted (no POST).
- **Canonical query string.** The allowlisted parameters, values trimmed, boolean values lower-cased, sorted by key, empty ones dropped. It is the cache key (with the host and site) and it is the query string in the feed's own `<atom:link rel="self">`, so `?b=1&a=2&utm_source=x` and `?a=2&b=1` are one feed. It is also the key a future saved-search model would use.
- **Channel.** The title spells out the search, built from the canonical parameters: `Search: "remyelination" · Multiple Sclerosis · relevant only — Brain Regeneration`; with no parameters, `All articles — {site}` or `All clinical trials — {site}`. The `<link>` is the site's homepage.
- **Crawler and load protection.** Every response carries `X-Robots-Tag: noindex, nofollow`; the frontend's "Get updates for this search" link should be `rel="nofollow"` and only rendered after a search has run; each distinct search costs at most one query an hour whatever the number of subscribers; and `/robots.txt` on the API host disallows `/feed/sites/*/articles/search/`, `/feed/sites/*/trials/search/` and `/feed/sites/*/patents/search/`. Django serves that file (`rss.search_feeds.robots_txt`); the API host's nginx server block has to proxy `/robots.txt` to Django rather than answer it itself, as `nginx-example-configuration/nginx.conf` does.

### Patent feeds

New patent families, for researchers and industry watchers: who is patenting what for a condition or a treatment. They follow the scope, slug-resolution, empty-feed and caching rules above, with these differences, which come from the EPO licence the data is served under (see [patents.md](patents.md#licence-and-the-api)):

| Feed | URL |
|:-----|:----|
| By subject | `GET /feed/sites/{site_id}/patents/subject/{subject_slug}/` |
| By category | `GET /feed/sites/{site_id}/patents/category/{category_slug}/` |
| Search | `GET /feed/sites/{site_id}/patents/search/?…` |

- **Two switches.** A patent feed needs the site's `CustomSetting.rss_enabled` **and** `CustomSetting.patents_enabled`. With `patents_enabled` off it is a **404** even when `rss_enabled` is on and the subject is in scope, the same gate as `/patents/`, the MCP server and the editor address. This applies to the search feed too.
- **Which families.** *By subject*: families tagged with the subject. *By category*: families in the category **and** tagged with a subject that is in both the category's subjects and the site's scope (no leaking through a subject the site doesn't cover). Families with no `earliest_publication_date` are left out. Patents have no relevance judgement, so there are no relevant-only variants.
- **Ordered by publication, not discovery.** This is the one place feeds don't use the discovery date. The patent importer backfills history (back to 2016 priority dates), so `discovery_date` says nothing about when a patent is new: on dev, all 2,649 families were discovered within one month and only 38 were published in the last 90 days. Items are ordered by `earliest_publication_date`, newest first (ties by newest family id), and `<pubDate>` is that date at midnight UTC. **50 items**: a busy condition has a couple of hundred new families a year, so 50 covers roughly three to four months, far longer than the weekly import lag. A newly imported family always lands inside the window; backfilled history lands far below it and never reaches readers. A later publication in an existing family (a grant, say) does not put the family back in the feed.
- **Item content: processed records only.** Under the OPS terms we may serve processed records but not make "the data as such" public, and a feed is syndicated verbatim by aggregators. So an item is built only from what Gregory adds to a record:
	- `title`; `link` is the **Espacenet** page (the licence requires it wherever a patent is shown); `guid` is `urn:gregory:patent:{patent_id}` (not a permalink)
	- `dc:creator`: the applicants' canonical **sponsor** names, first three then "et al."; an applicant that didn't resolve to a sponsor (an individual) is left out
	- one `<category>` per in-scope subject and per category
	- the HTML description, each part omitted when empty: *In plain English* and *Key takeaways* (this site's `PatentSiteContent`); a facts row (applicant sponsors · "Priority {date}" · "First published {date}" · "Grant published" when `has_grant`, never "Granted", because `has_grant` is a heuristic on kind codes and not legal status); the **linked trials**, one line each, "Filed during trial: {trial title} ({sponsor})" or "Filed after trial: …", linking to the trial (the site's `/trials/{trial_id}/` page when `sitemap_include_trials` is on, the registry otherwise), at most three, then "and N more"; and a "View on Espacenet" link. Only unsuppressed `basis="category"` links to trials in the site's scope are listed (the same rule as `trial_links` on `/patents/`); subject-basis links are noisy for large sponsors. Trial titles are cut to 120 characters. Every value is HTML-escaped.
	- **Never in a feed:** the abstract (`summary`), `claims`, `inventors`, CPC/IPC codes, publication numbers. The query that loads the items doesn't even select those columns.
- **Channel.** `{Subject} patents — {site}` and `Patents: {Category} — {site}`; the description says these are new patent families by first publication, sourced from the EPO's Open Patent Services, with a link to the OPS terms. The `<link>` is the site's homepage.
- **Search.** `patents/search/` works like the other [search feeds](#search-feeds) (allowlist, canonical query string, 400 for invalid values, 300-character cap, `X-Robots-Tag: noindex, nofollow`, one-hour cache, no POST, and `/robots.txt` disallows it). Allowed parameters: `search`, `title`, `summary`, `subject_id`, `category_slug`, `applicant_sponsor_slug`, `has_grant`, `has_trial_link` (the last two must be `true` or `false`). They have the same meaning as on `/patents/` (`PatentFilter`); `has_trial_link` counts only visible category-basis links to trials in the site's scope. `search`, `title` and `summary` match against the patent text on the server, but the text is never output. Everything else (`applicant`, `cpc`, `publication_number`, `family_id`, date windows, `include_subject_links`, …) is ignored. The channel title is `Patent search: … — {site}`, with the labels `grant published` / `no grant published` and `linked to a trial` / `no linked trial`, and `All patents — {site}` with no parameters.

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

The trials section is off by default and appears in the index only when *Include trials* is on: not every frontend gives each trial its own page, and advertising `/trials/{trial_id}/` on a site that only has a trials listing would send crawlers to 404s. *Relevant only* filters articles alone — trials carry no relevance judgement (no manual review flag, no ML predictions). For articles it applies the **editorial** rule, limited to the site's sitemap subjects: an article is listed when an editor marked it relevant for one of them, or when no editor rejected it for that subject and the ML models agree. An editor's "not relevant" for a subject overrides the models for that subject. This is stricter than `relevant=true` on `/articles/`, which counts relevance by a human *or* by AI: a sitemap is something the site publishes under its own name, while the API is raw material each frontend uses as it sees fit.

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
| `POST /api/token/` | Obtain a JWT pair (`username`, `password` → `access`, `refresh`) |
| `GET /protected_endpoint/` | Test protected endpoint (requires auth header) |
| `/api-auth/login/`, `/api-auth/logout/` | Session login for the browsable API (Django REST framework's own pages) |

The API key used by `POST /articles/post/`, `POST /articles/edit/` and `POST /trials/edit/`, and for [private site data](#accessing-private-organisation-data), is a different credential: an `APIAccessScheme` key sent as the raw `Authorization` header value.

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
- Writes take effect immediately, run as the editor (history rows record the editor and `via = "mcp"`), and are throttled per (editor, site): 60 per hour and 500 per day by default (`MCP_EDITOR_WRITES_PER_HOUR`, `MCP_EDITOR_WRITES_PER_DAY`). A throttled write is `429` with `Retry-After`. Reads are never throttled. The source routes below have limits of their own.

### Reads

The read endpoints are the ordinary ones, mounted again under `/editor/` with editor authentication: `/editor/articles/`, `/editor/articles/{id}/`, `/editor/articles/stats/`, `/editor/trials/`, `/editor/trials/{id}/`, `/editor/trials/stats/`, `/editor/authors/`, `/editor/categories/`, `/editor/subjects/`, `/editor/sponsors/`, `/editor/patents/`, `/editor/patents/{id}/`, `/editor/patents/stats/` and `/editor/stats/`. They accept the same parameters and return the same bodies, scoped to the editor's one site: its `scope_subjects` whether or not the site is `api_public`. `?include_public`, `?site_id` and `?team_id` cannot widen that scope. They are not repeated in the OpenAPI schema.

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
| `POST /editor/categories/` | Creates a category. Body: `category_name`, `subject_ids` (in scope, one team, which becomes the category's team), `category_terms` (at least one for an automatic category; trimmed and deduplicated ignoring case), and optionally `category_exclude_terms` (terms that keep content out of the category: content containing any of them in a searched field is never assigned automatically, whatever its score), `category_description`, `category_slug` (defaults to the slugified name), `modality`, `category_type` (`automatic` or `manual`) and `match_scope`. `201`. A taken slug is `409`, with the holder's `category_id` when it is in scope. The next `rebuild_categories` run fills an automatic category |
| `PATCH /editor/categories/{id}/` | Changes `category_name`, `category_description` (empty string clears), `modality`, `match_scope` or `subject_ids` (replaces them; same team). `category_terms` replaces every term; `add_terms` and `remove_terms` edit the current list (case-insensitive) and can't be combined with it. `category_exclude_terms`, `add_exclude_terms` and `remove_exclude_terms` edit the veto list the same way. The slug and team never change. Every subject of the category must be in scope: `404` when none is, `403` when only some are, since the change shows on every site listing any of them |
| `GET /editor/articles/{article_id}/history/?limit=` | Who changed this site's editorial content, the article's relevance for subjects in scope, its links to trials in scope and its hand assignments to categories in scope; newest first, `limit` up to 200 (default 50) |

`GET /editor/categories/` and `GET /editor/categories/{id}/` stay the read endpoints described above; the category writes share their paths. `GET /categories/` and the editor category responses include `category_exclude_terms`: the veto list used by `rebuild_categories`, where any excluded term (whole word, case-insensitive) found in a field the category searches stops an article or trial being assigned automatically, whatever its score; manual assignments are never touched. Changing terms, excluded terms, subjects or scope makes the next pipeline run re-match the whole category. Category changes are recorded in `HistoricalTeamCategory` under the editor's name.

#### Patent routes

The same actions on patent families. A patent is found through the site's **patent scope** (its `scope_subjects`, but only when the site has `patents_enabled` on, see [Patents](#patents)), so every one of these routes is `404` for an editor of a site without the switch, and for a family outside the scope. Writes need a grant with `can_edit`, run under the editor's name, and count against the same per-(editor, site) limit as article writes.

| Method and path | Purpose |
|:----------------|:--------|
| `GET /editor/patents/resolve/?publication_number=` | Publication number to `patent_id` among in-scope families, in any common format (`EP4123456A1`, `EP 4123456 A1`; without a kind code it matches every kind, and a letter alone matches that letter with or without its digit). Takes one number: a list (commas or semicolons) is `400`, since `GET /patents/?publication_number=a,b` is the endpoint for lists. Returns `patent_id`, `family_id`, `title`, `representative_publication`. `404` for none, or for one outside the scope. `409` with `patent_ids` when several in-scope families match, as DOI resolution does for articles |
| `PATCH /editor/patents/{patent_id}/editorial/` | Upsert this site's `takeaways` and/or `summary_plain_english`. Omitted fields are left alone; an empty string clears. Returns the stored values with `updated_by` and `updated_at` |
| `PUT /editor/patents/{patent_id}/categories/{category_id}/` | Assigns the family to the category by hand, `201`. A family the pipeline already matched becomes a hand assignment, `200`, which `rebuild_categories` never removes; one already assigned by hand is returned unchanged, `200`. The category is in scope when one of its subjects is |
| `DELETE /editor/patents/{patent_id}/categories/{category_id}/` | Removes a hand assignment. `409` for one the pipeline made from the category's terms. `404` when the family isn't in the category |
| `DELETE /editor/patents/{patent_id}/trials/{trial_id}/` | Suppresses the link between the family and a trial: it stays, marked `suppressed`, hidden everywhere, and detection never recreates it. Idempotent: an already suppressed link is returned unchanged, `200`. `404` when the pair isn't linked, or the trial is outside the site's scope |
| `PUT /editor/patents/{patent_id}/trials/{trial_id}/` | Restores a suppressed link, `200`; a link that isn't suppressed is returned unchanged. Editors **cannot create** a link (links need a shared sponsor and timing, which only detection computes), so a pair with no link is `404` and the message says so |
| `GET /editor/patents/{patent_id}/history/?limit=` | Who changed this site's editorial content for the family, its hand assignments to categories in scope, and the suppressing or restoring of its links to trials in scope; newest first, `limit` 1 to 200 (default 50). Entry kinds are `editorial`, `category` and `trial_link`: patents have no relevance. Only people's changes are recorded (the pipeline's category matching and link detection write no history), so every entry is an editor, admin or API-key action |

The reads `GET /editor/patents/` and `GET /editor/patents/{id}/` accept `?include=editorial` (and `claims` on the detail) like the public endpoints; each editorial entry also carries `updated_at` and `updated_by`.

#### Source routes

Only for a grant with `can_manage_sources` (the *Can add sources* flag, set by a superuser; see [Grants and scopes](07-mcp-server.md#grants-and-scopes)). Every method needs it, `GET` included: any other editor, read-only or not, gets `403`. They are the only way to add a source over MCP; editing or pausing one stays in the admin.

| Method and path | Purpose |
|:----------------|:--------|
| `GET /editor/sources/` | The sources whose subject is in the site's scope, of every type, ordered by name: `id`, `name`, `source_type`, `source_for`, `method`, `subject_id`, `subject_name`, `team_id`, `team_name`, `active`, `settings` and `last_successful_fetch_at`. `source_type` is a key of `gregory/source_types.py` (`legacy` for a pair no importer fetches); `settings` shows the type's settings for `rss_papers`, `rss_trials`, `pubmed` and `ctgov` and is empty for the rest. Filters: `subject_id`, `source_type`, `active=true\|false`. Not paginated. Not throttled |
| `POST /editor/sources/preview/` | Takes the body of the create below and saves nothing. Returns `counts` (each `{key, value, label}`), up to 5 `samples` (`title`, `date`, `link`), `first_run` (what the first pipeline run brings in), `pubmed` (`querytranslation` and `warnings`, for PubMed), `refused` (why create would refuse, or `null`) and `duplicate_of`. A refusal is still `200`: the reason is in `refused`. Takes `name` and `description` and ignores them |
| `POST /editor/sources/` | Adds a source. `201` with the new `source`, the same `counts`, `samples`, `first_run` and `pubmed`, and `first_fetch` (`next pipeline run (every 12 hours), from today on`) |

Body of preview and create: `source_type` and `subject_id`, plus the settings of the type; create also takes `name` and `description`.

| `source_type` | Settings | Stored as |
|:--------------|:---------|:----------|
| `rss_papers` | `link` (required), `keyword_filter` | science paper / rss |
| `rss_trials` | `link` (required) | trials / rss |
| `pubmed` | `pubmed_query` (required), `excluded_publication_types` (names, matched ignoring case; an unknown name is `400`) | science paper / pubmed_api |
| `ctgov` | `ctgov_search_condition`, `ctgov_search_intervention`, `ctgov_search_term` (at least one) | trials / ctgov_api |

A setting that belongs to another type is `400`, and so is any field not listed, so `ignore_ssl` or `team_id` is never silently ignored. CTIS, EPO, scrape and manual sources cannot be added this way.

What the server decides, never the caller: the source belongs to the subject's team; it is `active`; `ignore_ssl` is off (a feed with a broken certificate is added in the admin); `name` defaults to `RSS | <subject>`, `PubMed | <subject>` or `CTGov | <subject>`. A subject outside the site's scope is `404`.

What a create does, in order, with no reliance on a preview having run:

1. The admin's checks, with its messages (`gregory/source_checks.py`, shared with the admin form): a feed needs a link, a ClinicalTrials.gov source needs at least one search field, a PubMed search has no date clause and balanced brackets and quotes. `400`, keyed by field.
2. A duplicate: an **active** source of the same team and type with the same address (RSS: ignoring case and a trailing slash, and the address the feed redirects to) or search (PubMed: ignoring case and extra whitespace; ClinicalTrials.gov: the three fields the same way). `409` with `duplicate_of` (`source_id`, `name`, `subject_id`, `subject_name`). Sources of other teams are never looked at, so never named.
3. One call to the outside service of the type:
   - **RSS:** the server fetches the address, so it only goes to public hosts. The scheme must be `http` or `https`; the host must resolve only to public addresses (no loopback, private, link-local, reserved or multicast, IPv4-mapped addresses included); redirects are followed by hand, at most 3, and each is checked the same way. 10-second timeout, 5 MB limit, and the body must parse as a feed with at least one entry. Anything else is `400` on `link`.
   - **PubMed:** a count of the papers created in the last 30 days, which also confirms PubMed accepts the search (`400` with PubMed's message if not).
   - **ClinicalTrials.gov:** the number of matching trials and of those updated in the last 30 days (`400` if it rejects the search). It builds the search exactly as the importer does (`gregory/utils/ctgov_search.py`).
4. If PubMed or ClinicalTrials.gov cannot be reached the answer is `503` ("try again later") and nothing is created, preview included.
5. Saved in one transaction as the editor (history records the editor and `via = "mcp"`). For `pubmed` and `ctgov`, `last_successful_fetch_at` is set to the time of creation, so the first run is an ordinary incremental one: PubMed looks at the last 2 days, ClinicalTrials.gov at trials updated in the last 2 days. A source added over MCP has **no backfill**. An RSS source has no anchor: its first run reads the feed's current entries, as for every RSS source. A source added in the admin keeps its first-run backfill.

There is no volume limit: a big count is reported in `counts`, never refused.

**Counts.** RSS: `entries_in_feed`, and `entries_passing_keyword_filter` when a filter is given (title and summary only; the importer also checks a paper's CrossRef abstract when a feed sends no summary, so the real number can be a little higher). PubMed: `papers_last_30_days`, the monthly volume to expect. ClinicalTrials.gov: `trials_matching` and `trials_updated_last_30_days`. Samples are the feed's first entries, PubMed's most recent papers, or the most recently updated trials; PubMed's are left out when their titles cannot be fetched. A PubMed search comes back with `pubmed.querytranslation` (PubMed's own reading of the search) from the same single call that counts it; a search with 9,999 or more results still reports its count and reading.

**Throttles.** Per editor and site, apart from the general write limits above (a source request neither spends nor is stopped by those): `POST /editor/sources/` is limited to 30 a day (`MCP_SOURCE_CREATES_PER_DAY`) and the preview to 30 an hour (`MCP_SOURCE_PREVIEWS_PER_HOUR`). Over the limit is `429` with `Retry-After`. A request counts when it is made, whether or not it is then refused.

Trials are read-only over MCP: an editor can link a trial to an article but cannot change the trial. `access`, `retracted` and `kind` stay on `POST /articles/edit/` (API key) and the admin.

---

## CORS (calling the API from a browser on another origin)

Django sends the CORS headers ([django-cors-headers](https://github.com/adamchainz/django-cors-headers)); nginx adds none. With `DEBUG=True` every origin is allowed. Otherwise an origin gets `Access-Control-Allow-Origin` only if it is one of:

- `https://<DOMAIN_NAME>` and `https://www.<DOMAIN_NAME>`, added automatically.
- An exact entry in `CORS_ALLOWED_ORIGINS`: comma-separated, each with its scheme (`https://app.example.com`). A bare domain fails Django's system check (`corsheaders.E013`) and stops the whole app from starting.
- A match for a pattern in `CORS_ALLOWED_ORIGIN_REGEXES`, for hosts that change on every deploy, like Cloudflare Pages previews (`https://<hash-or-branch>.<project>.pages.dev`). Patterns are separated by whitespace, not commas, because a regex can contain a comma (`{1,63}`). Anchor each one with `^…$`. django-cors-headers anchors only the start, so `^https://[a-z0-9-]+\.brain-regeneration\.pages\.dev` without the `$` also matches `https://x.brain-regeneration.pages.dev.attacker.com`. Example:

  ```bash
  CORS_ALLOWED_ORIGIN_REGEXES=^https://[a-z0-9-]+\.brain-regeneration\.pages\.dev$
  ```

  In `.env`, wrap the value in single quotes (or leave it unquoted): docker compose then passes `\.`, `{…}` and a trailing `$` through unchanged.

Credentials are not enabled for cross-origin requests (`CORS_ALLOW_CREDENTIALS` is off), so a browser on an allowed origin reads exactly what an anonymous `curl` would. Being allowed by CORS does not pick a site: a preview origin such as `feature-x.brain-regeneration.pages.dev` matches no Site, so its requests take the fallback in step 4 below.

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
    "patents_enabled": true,
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

`patents_enabled` says whether patent families are reachable through this tenant (see [Patents](#patents)): `true` when a settings row in the same slice of the site that supplies its scope has `patents_enabled` on. The MCP server uses it to hide the patent tools and leave patents out of the instructions. `subjects` carries `id` and `subject_name` only — no `team_id`, anywhere. `prompts` and `documents` list only `is_active` rows, in their model `ordering`; a prompt's `template` is raw text (`$name`-style placeholders), substituted by the MCP server at render time, not here.

Like `GET /sites/`, this endpoint is **never gated by the resolution above** — it answers the same way no matter how many `api_public` sites exist, even where `/articles/` would 400 on an anonymous caller who names none. Every response carries `Vary: Authorization`, since the answer depends on the caller's key.

The MCP server fetches this endpoint the way it already fetches `GET /sites/` — cached for 10 minutes, with a stale-copy fallback on fetch failure rather than treating an outage as "no tenants exist" — and refuses any request whose Host doesn't resolve to one of these rows, before making any other API call. See [07-mcp-server.md](07-mcp-server.md#tenant-resolution-site_id) and its §"Prompts"/§"Resources".

---

## Accessing private organisation data

Content visibility (articles, trials, RSS) is **subject-scoped**, not organisation-scoped — see [Visibility rules summary](#visibility-rules-summary) below. `OrganizationApiSettings.make_api_public` plays no part in it; that flag now governs only the `?team_id=`/`?organization=` scope validations (org-keyed checks kept deliberately organisation-scoped — see [06-organisations-teams-and-sites.md](06-organisations-teams-and-sites.md#api-visibility-for-organisation-keyed-surfaces)). Callers that need to read a **private** site's data must identify themselves in one of two ways.

### Option 1 — API key bound to a site

Create an `APIAccessScheme` record in the Django admin with `organization` and `site` set to the target site. The client sends the raw key in the `Authorization` header (no prefix):

```http
GET /articles/
Authorization: <raw_api_key>
```

The key is validated against its date window (`begin_date` / `end_date`) and, if configured, an IP allowlist. A valid key grants that site's `scope_subjects`, whether or not the site is `api_public`.

The IP allowlist (`APIAccessScheme.ip_addresses`, comma-separated, exact match) is checked against the `X-Real-IP` request header, falling back to the socket address (`REMOTE_ADDR`) when it is absent. `X-Forwarded-For` is ignored, because its first entry is whatever the client chose to send. The same address is what `APIAccessSchemeLog.ip_addr` records.

This means the allowlist only sees real client addresses when Django runs behind a reverse proxy that **overwrites** `X-Real-IP` with the connecting address, as the bundled nginx configuration does (`proxy_set_header X-Real-IP $remote_addr;` on every proxied location). Don't expose Django directly, or through a proxy that passes a client-sent `X-Real-IP` through, if any key has an allowlist. If nginx itself sits behind a CDN such as Cloudflare, `$remote_addr` is the CDN's edge address: configure nginx's `real_ip` module (`set_real_ip_from <CDN ranges>; real_ip_header CF-Connecting-IP;`) so `$remote_addr` is the client again, or allowlisted keys will be refused.

**The scheme of absolute URLs.** The API builds absolute URLs in several places: the `next` and `previous` links of every paginated response, the locations in the sitemap index, the self link of the RSS feeds, and an author's `articles_list`. Django is reached over plain HTTP behind nginx, so it takes the scheme from `X-Forwarded-Proto` (`SECURE_PROXY_SSL_HEADER`, set in `admin/settings.py`): a request nginx forwarded as `https` gets `https://` URLs, and a request that arrives without the header (the MCP server calls `http://gregory:8000` directly) keeps `http://`. This relies on the same rule as the allowlist above: nginx must **overwrite** `X-Forwarded-Proto` (`proxy_set_header X-Forwarded-Proto $scheme;`, as the bundled configuration does on every proxied location), and Django must not be exposed directly, or a client could send the header itself.

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

One endpoint is not content and does not follow this rule:

- **`/teams/`** lists a team when `Team.api_listed` is on, **or** when the team owns a subject already in the caller's scope. The flag is the publication switch; the second clause exists so a private site's own authenticated frontend can list its own teams. It gates *listing*, not access — an unlisted team's articles and trials are governed by subject scope like everything else. The same rule decides whether a team appears nested inside an article or trial.

There is no `/organizations/` endpoint: it was removed from the URL configuration, and its viewset and serializer have since been deleted.

**`/sponsors/`** is scoped indirectly: a sponsor carries no subject and is visible when at least one of its trials is in scope. Its `trials_count` counts only in-scope trials, so neither the number nor `?ordering=-trials_count` discloses trials the caller cannot read.

> **Note:** This caller-scoped rule is the API's alone. RSS feeds are scoped to the *requested site*, not the caller — see [RSS feeds](#rss-feeds) — much like the [sitemaps](#sitemaps) below. The Django admin uses a deliberately different rule again — see [06-organisations-teams-and-sites.md#admin-visibility](06-organisations-teams-and-sites.md#admin-visibility).

---

## Articles query parameters

The `/articles/` endpoint supports the following filters. Multiple parameters can be combined.

| Parameter | Type | Description |
|:----------|:-----|:------------|
| `team_id` | integer | Filter by team |
| `site_id` | integer | Articles carrying a subject in that site's `scope_subjects`. A content filter; the same parameter is also read for anonymous site resolution (see [Resolving a site](#resolving-a-site-for-an-anonymous-caller)) |
| `subject_id` | integer | Filter by subject |
| `subjects` | comma-separated integers | Articles tagged with **all** the listed subjects (AND), e.g. `?subjects=1,3` |
| `subjects_any` | comma-separated integers | Articles tagged with **any** of the listed subjects (OR), e.g. `?subjects_any=1,3` |
| `author_id` | integer | Filter by author |
| `doi` | string | Exact DOI match (case-insensitive) |
| `pmid` | string | Exact PubMed ID match (digits only), e.g. `?pmid=38812345` |
| `category_slug` | string | Filter by category slug |
| `category_id` | integer | Filter by category ID |
| `category_modality` | string | Filter by a category's curated intervention modality (`small_molecule`, `biologic_antibody`, `cell_gene_therapy`, `rehabilitation`, `device_neuromodulation`, `natural_product`, `research_topic`, `other`). Matches if *any* of the article's categories carry that modality |
| `journal_slug` | string | Filter by journal (spaces → dashes) |
| `source_id` | integer | Filter by source |
| `search` | string | Boolean search over title and summary: bare terms are AND-ed, uppercase `OR` / `NOT` and `"quoted phrases"` are supported, e.g. `?search=stem OR cells` |
| `title` | string | Case-insensitive substring of the title only |
| `summary` | string | Case-insensitive substring of the summary (abstract) only |
| `relevant` | boolean | `true`: articles relevant by a human *or* by AI, meaning an editor marked them relevant or the ML consensus says so (see `ml_threshold`); `false`: the exact complement. An editor's "not relevant" does **not** hide an ML "yes" here. That is deliberate: the frontend decides how to use these results. Relevant-only sitemaps apply the stricter editorial rule instead (see [Sitemaps](#sitemaps)). Scoped to `subject_id` (or `subjects` / `subjects_any`) when provided. |
| `ml_threshold` | float 0–1 | The consensus rule behind `relevant=true`, with a custom confidence: enough models (per the subject's ML consensus setting) must have classed the article relevant at this probability or higher. Models only do so at or above the cutoff their prediction run used (0.8 by default), so values below that cutoff act as the cutoff. Subjects with ML predictions turned off never match. Scoped to `subject_id` when provided. For the score shown on each article, use `ml_score_min` |
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

**PubMed fields.** Each article carries `pmid` and `pmcid` (null when the article is not in PubMed), `keywords` (the authors' keywords, in the order PubMed gives them), `publication_types` (a list of names, e.g. `["Journal Article", "Review"]`) and `mesh` (a list of `{descriptor, qualifier, major_topic}`: one entry per MeSH descriptor with `qualifier: null`, and one per descriptor/qualifier pair). They are filled from PubMed's record, so they stay empty until the article has been matched to one.

**Data from PubMed.** The PMID, PMCID, abstract, keywords, publication types and MeSH headings of an article may come from PubMed, which is produced by the U.S. National Library of Medicine (NLM) and distributed by the National Center for Biotechnology Information (NCBI). See [NCBI's disclaimer and copyright notice](https://www.ncbi.nlm.nih.gov/About/disclaimer.html). Abstracts may be copyrighted by their publishers.

> **Changelog (2026-10-16):** for a few hours `relevant=true` (and the `relevant` count on `/articles/stats/`) hid articles an editor had marked not relevant whenever the ML models liked them. That is reverted: `relevant=` means "relevant by a human or by AI" again. The stricter editorial rule, where an editor's "not relevant" wins, stays on sitemaps with *Relevant only* on.

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

Editorial content is the `takeaways` and `summary_plain_english` of a record. It is **off by default** and returned only with `?include=editorial`, on `/articles/`, `/articles/{id}/`, `/trials/`, `/trials/{id}/` and both search endpoints (as a query parameter or a body field), and on `/patents/` and `/patents/{id}/`. CSV output never includes it.

Articles and patents carry editorial content **per site**, so two sites of one organisation can say different things about the same article. Each entry is labelled by site:

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
- `?include=` is a comma-separated list; `editorial` is the value for articles and trials. `claims` is also accepted but does something only on a single patent (see [Patents](#patents)); on other endpoints it is ignored. An unknown value returns 400.
- `?has_takeaways=true|false` filters on the same site(s) (articles) or organisation(s) (trials) and doesn't need `include=editorial`. Empty-string takeaways count as missing.
- Responses already vary by `Origin` whenever site resolution depends on it, so caches in front of the API must honour `Vary: Origin`.

**Breaking change (articles).** `editorial[].organization` was replaced by `editorial[].site` (`id`, `domain`, `name`) on article responses, and the content is now selected by site instead of by organisation. Existing content was copied to every site of its organisation, so a single-site organisation sees the same text. Clients that read `editorial[0].takeaways` keep working; clients that read `editorial[].organization` on articles must switch to `editorial[].site`. See the [changelog entry](changelog/article-editorial-per-site.md). Trial responses are unchanged.

**Earlier breaking change.** The top-level `takeaways` and `summary_plain_english` fields were removed from article and trial responses, and `?team_id=` (or an API key) no longer unlocks them. Read `editorial[0].takeaways` after adding `?include=editorial`.

## Available endpoints

**Breaking change (2026-10): `team_categories` is now `categories`.** Article, trial and patent responses (list, detail and search endpoints, including `/articles/?author_id=`), the streaming CSV export, the patents and trials XLSX exports and the MCP tools return a record's categories under `categories`. The items inside are unchanged (`id`, `category_name`, `category_slug`, `modality`, …), and so are the `category_slug` / `category_id` / `category_modality` query parameters and the `/authors/by_team_category/` endpoint. Clients that read `team_categories` must switch to `categories`. See the [changelog entry](changelog/rename-team-categories.md).

| Model | Endpoint | Parameters | Notes |
|:------|:---------|:-----------|:------|
| Articles | `GET /articles/` | `include`, `has_takeaways`, `team_id`, `site_id`, `subject_id`, `subjects`, `subjects_any`, `author_id`, `doi`, `pmid`, `category_slug`, `category_id`, `category_modality`, `journal_slug`, `source_id`, `search`, `title`, `summary`, `ordering`, `relevant`, `ml_threshold`, `ml_score_min`, `open_access`, `last_days`, `week`, `year`, `has_clinical_trials`, `published_date_after`, `published_date_before`, pagination | |
| Articles | `POST /articles/post/` | `title`, `link`, `doi`, `summary`, `source_id`, `kind` | Create article — see [response codes below](#post-articlespost-response-codes) |
| Articles | `POST /articles/edit/` | `doi` *(req)*, `takeaways`, `summary_plain_english`, `access`, `retracted`, `kind` | Edit an existing article, API key required — see [below](#post-articlesedit) |
| Articles | `GET /articles/{id}/` | `id` (path) | |
| Articles | `GET /articles/stats/` | Same filters as `GET /articles/` | Aggregate counts over the filtered set: `total`, `by_access` (NULL folded into `unknown`), `relevant`, `retracted`, `missing_doi`, `by_subject`. Cached for `STATS_CACHE_TTL` seconds |
| Articles | `GET /articles/search/` | `team_id` *(req)*, `subject_id` *(req)*, `title`, `summary`, `search`, `format`, `all_results`, plus every `GET /articles/` filter (`published_date_after`, `published_date_before`, `relevant`, `subjects`, …) | See [Search endpoints](#search-endpoints) below |
| Articles | `POST /articles/search/` | Request **body** accepts the same fields as `GET /articles/search/`: `team_id`, `subject_id`, `title`, `summary`, `search`, `ordering`, `page`, `page_size`, `all_results`, plus every `GET /articles/` filter | See [GET vs POST](#get-vs-post-on-search-endpoints) |
| Authors | `GET /authors/` | `author_id`, `full_name`, `given_name`, `family_name`, `orcid`, `country`, `sort_by`, `order`, `team_id`, `subject_id`, `subjects_any`, `category_slug`, `category_id`, `date_from`, `date_to`, `timeframe`, `page`, `page_size` | `page * page_size` above 10,000 returns 400, same as `/articles/` and `/trials/` — but there is **no** `all_results` bypass here (see [authors-api.md](authors-api.md#pagination-limits)); use `/authors/search/` for bulk/team-scoped reads |
| Authors | `GET /authors/{id}/` | `id` (path) | |
| Authors | `GET /authors/search/` | `team_id` *(req)*, `subject_id` *(req)*, `full_name`, `format`, `all_results`, plus every `GET /authors/` filter (`author_id`, `orcid`, `country`, `given_name`, `family_name`) | See [Search endpoints](#search-endpoints) below |
| Authors | `POST /authors/search/` | Request **body** accepts the same fields as `GET /authors/search/`: `team_id`, `subject_id`, `full_name`, `page`, `page_size`, `all_results`, plus every `GET /authors/` filter | See [GET vs POST](#get-vs-post-on-search-endpoints). Results are always ordered by `author_id`; this endpoint has no `ordering` support |
| Authors | `GET /authors/by_team_subject/` | `team_id` *(req)*, `subject_id` *(req)* | |
| Authors | `GET /authors/by_team_category/` | `team_id` *(req)*, `category_slug` or `category_id` *(req)* | |
| Authors | `GET /authors/{id}/coauthors/` | `id` (path) | Co-authors of the given author |
| Categories | `GET /categories/` | `team_id`, `subject_id`, `subjects_any`, `category_id`, `category_terms`, `get_categories`, `search`, `ordering` (`category_name`, `id`, `article_count_annotated`, `trials_count_annotated`, `authors_count_annotated`), `include_authors`, `max_authors`, `monthly_counts`, `ml_threshold`, `date_from`, `date_to`, `timeframe`, pagination | `ordering=authors_count_annotated` is the expensive sort — see [Categories ordering](#categories-ordering). Each category carries `patents_count_total`, scoped to the caller's [patent scope](#patents) |
| Categories | `GET /categories/{id}/` | `id` (path) | Includes `category_terms` and `category_exclude_terms` |
| Categories | `GET /categories/{id}/authors/` | `id` (path), `min_articles`, `sort_by`, `order`, date filters | Author stats for a category |
| Sources | `GET /sources/` | `source_id`, `team_id`, `subject_id`, `active`, `source_for`, `link`, `search`, `ordering` (`name`, `source_id`), pagination | `search` matches name and description; `link` is a case-insensitive substring of the feed or search URL; `active=true\|false` filters on whether the pipeline polls it. `source_for` is `science paper`, `news article`, `trials` or `patents` (patent sources feed the staff-only patent records; see [patents.md](patents.md)). |
| Sources | `GET /sources/{id}/` | `id` (path) | |
| Sponsors | `GET /sponsors/` | `sponsor_type`, `subject_id`, `subjects_any`, `include_patent_applicants`, `search`, `ordering` (`name`, `trials_count`), pagination | Canonical, deduplicated sponsor entities — see [Sponsor canonicalization](#sponsor-canonicalization) below. Each row carries `trials_count` and `patents_count`; `include_patent_applicants=true` also lists sponsors that only applied for patents ([Patents](#patents)). `subject_id` / `subjects_any` keep sponsors with at least one trial in the subject(s) |
| Sponsors | `GET /sponsors/{id}/` | `id` (path) | |
| Subjects | `GET /subjects/` | `team_id`, `search`, `ordering`, pagination | |
| Subjects | `GET /subjects/{id}/` | `id` (path) | |
| Teams | `GET /teams/` | Standard pagination | |
| Teams | `GET /teams/{id}/` | `id` (path) | |
| Teams | `GET /teams/{id}/subjects/{subject_id}/categories/` | `id`, `subject_id` (path) | |
| Trials | `GET /trials/` | `team_id`, `site_id`, `subject_id`, `subjects`, `subjects_any`, `category_id`, `category_slug`, `category_modality`, `source_id`, `status`, `search`, `title`, `summary`, `ordering`, `has_patent_link`, `include_subject_links`, trial-specific filters, pagination | See parameter details below |
| Trials | `POST /trials/edit/` | `identifiers` *(req)*, `takeaways`, `summary_plain_english` | Edit a trial's editorial content for the key's organisation, API key required — see [below](#post-trialsedit) |
| Trials | `GET /trials/{id}/` | `id` (path), `include_subject_links` | Adds `trial_sites` and `patent_links` ([Patents](#patents)) |
| Trials | `GET /trials/stats/` | Same filters as `GET /trials/` | Totals per `recruitment_status_normalized` bucket (not_yet_recruiting, recruiting, enrolling_by_invitation, active_not_recruiting, not_recruiting, suspended, completed, terminated, withdrawn, unknown, other — always present, 0 when empty) plus `no_status`, `by_subject`, `by_phase` (per `TrialPhase` + `no_phase`), `by_region` (per `TrialRegion` + `no_region`), `by_country` (`[{country, count}]`, null-country entry last), `by_year` (`[{year, count}]`, null-year entry last), `by_sponsor` (top 25 `[{sponsor_id, slug, name, sponsor_type, count}]`) + `no_sponsor`, `by_sponsor_type` (per `SponsorType` + `no_type`), `by_modality` (per `CategoryModality` + `no_modality`, joined over `categories.modality` — **not** a partition of `total`: a trial in two categories of different modalities is counted once per modality, and `no_modality` conflates "no category at all" with "category not yet curated with a modality"), `by_study_type` (per `TrialStudyType` + `no_study_type`; `no_study_type` is large — ~13.2k globally — mostly legacy rows with no `source_register` rather than a normalization gap), and `by_sex` (per `TrialSexEligibility` + `no_sex_data`; `no_sex_data` is large — ~46% globally — same legacy-rows caveat as `no_study_type`) over the filtered set. Replaces the `stats` block formerly embedded in `GET /trials/` list responses (breaking change). Cached for `STATS_CACHE_TTL` seconds |
| Trials | `GET /trials/search/` | `team_id` *(req)*, `subject_id` *(req)*, `title`, `summary`, `search`, `status`, `format`, `all_results`, plus every `GET /trials/` filter (`date_registration_after`, `date_registration_before`, `phase_normalized`, `country`, …) | See [Search endpoints](#search-endpoints) below |
| Trials | `POST /trials/search/` | Request **body** accepts the same fields as `GET /trials/search/`: `team_id`, `subject_id`, `title`, `summary`, `search`, `status`, `ordering`, `page`, `page_size`, `all_results`, plus every `GET /trials/` filter | See [GET vs POST](#get-vs-post-on-search-endpoints) |
| Trials | `GET /trials/sites/` | Same filters as `GET /trials/`, plus `latitude__isnull` (`true`/`false`) | Flat, paginated `TrialSite` listing across the filtered trial set: `{trial_id, name, city, country, latitude, longitude}` per row — for a site-level pin map. `trial_sites` itself is detail-only (appears on `GET /trials/{id}/`, never on the list/CSV/search responses). Pagination is mandatory here — `?all_results=true` returns 400; max `page_size` is 500 |
| Patents | `GET /patents/` | `search`, `title`, `summary`, `subject_id`, `site_id`, `category_id`, `category_slug`, `category_modality`, `applicant_sponsor_id`, `applicant_sponsor_slug`, `applicant`, `priority_date_after`, `priority_date_before`, `publication_date_after`, `publication_date_before`, `has_grant`, `cpc`, `publication_number`, `family_id`, `has_trial_link`, `include_subject_links`, `ordering`, pagination | Patent families — only through sites with `patents_enabled`. JSON only: no CSV, no `all_results`. See [Patents](#patents) |
| Patents | `GET /patents/{id}/` | `id` (path), `include=claims`, `include_subject_links` | Adds `publications`, `inventors`, `ipc_classes`, `links`, `trial_links`; `claims` only with `include=claims` |
| Patents | `GET /patents/stats/` | Same filters as `GET /patents/` | `total`, `with_grant`, `with_trial_link`, `by_priority_year`. Cached for `STATS_CACHE_TTL` seconds |
| Email templates | `GET /emails/` | None | Template preview dashboard. **Staff only** (Django admin session), like every `/emails/` route |
| Email templates | `GET /emails/preview/{template_name}/` | `template_name` (path) | Rendered template |
| Email templates | `GET /emails/context/{template_name}/` | `template_name` (path) | The template's context as JSON |
| Email templates | `GET /emails/lists/` | `email_type` (`weekly_summary`, `admin_summary`, `trial_notification`, `author_outreach`) | JSON `{lists: [...]}` for the dashboard's list picker; for `author_outreach` it lists outreach campaigns instead |
| Email templates | `GET /emails/subscribers/` | `q` (search), `list_id` | JSON list of subscribers for the dashboard's preview picker |
| RSS feeds | `GET /feed/sites/{site_id}/articles/search/` | `site_id` (path); `search`, `title`, `summary`, `subject_id`, `category_slug`, `relevant`, `open_access`, `has_takeaways` | Search feed; other parameters ignored, invalid values 400 — see [Search feeds](#search-feeds) |
| RSS feeds | `GET /feed/sites/{site_id}/trials/search/` | `site_id` (path); `search`, `title`, `summary`, `subject_id`, `category_slug`, `recruitment_status_normalized`, `phase_normalized`, `region`, `country`, `sponsor_slug`, `age_eligible`, `inclusion_gender_normalized` | Search feed; other parameters ignored, invalid values 400 — see [Search feeds](#search-feeds) |
| RSS feeds | `GET /feed/sites/{site_id}/patents/search/` | `site_id` (path); `search`, `title`, `summary`, `subject_id`, `category_slug`, `applicant_sponsor_slug`, `has_grant`, `has_trial_link` | Patent search feed; needs `rss_enabled` and `patents_enabled`; other parameters ignored, invalid values 400 — see [Patent feeds](#patent-feeds) |
| RSS feeds | `GET /feed/sites/{site_id}/patents/subject/{subject_slug}/` | `site_id`, `subject_slug` (path) | New patent families, ordered by first publication; needs `rss_enabled` and `patents_enabled` — see [Patent feeds](#patent-feeds) |
| RSS feeds | `GET /feed/sites/{site_id}/patents/category/{category_slug}/` | `site_id`, `category_slug` (path) | Same, for a category — see [Patent feeds](#patent-feeds) |
| RSS feeds | `GET /feed/sites/{site_id}/author/{orcid}/` | `site_id`, `orcid` (path) | Site-scoped — see [RSS feeds](#rss-feeds) |
| RSS feeds | `GET /feed/sites/{site_id}/trials/subject/{subject_slug}/` | `site_id`, `subject_slug` (path) | Site-scoped — see [RSS feeds](#rss-feeds) |
| RSS feeds | `GET /feed/sites/{site_id}/trials/subject/{subject_slug}/recruiting/` | `site_id`, `subject_slug` (path) | Recruiting and not-yet-recruiting trials only — see [RSS feeds](#rss-feeds) |
| RSS feeds | `GET /feed/sites/{site_id}/articles/subject/{subject_slug}/`, `…/relevant/` | `site_id`, `subject_slug` (path) | All articles for a condition / relevant only — see [RSS feeds](#rss-feeds) |
| RSS feeds | `GET /feed/sites/{site_id}/articles/category/{category_slug}/`, `…/relevant/` | `site_id`, `category_slug` (path) | All articles for a topic / relevant only — see [RSS feeds](#rss-feeds) |
| RSS feeds (old, no `site_id`) | `GET /feed/author/{orcid}/`, `GET /feed/trials/subject/{subject_slug}/` | `orcid` / `subject_slug` (path) | **301** to the `/feed/sites/3/...` equivalent — see [Old feed URLs](#old-feed-urls--permanent-redirect) |
| Stats | `GET /stats/` | `team`, `site`, `subject`, `include_public`, `organization` (alias `org`, deprecated) | See [Stats endpoint](#stats-endpoint) below |
| Sites | `GET /sites/` | None | Publicly readable sites as `{site_id, domain, name}`. **Unscoped by design** — it is the discovery entry point for callers that need a `site_id`, so it cannot require one. Everything returned is already public |
| Tenants | `GET /tenants/` | None | MCP tenants — see [Discovering MCP tenants](#discovering-mcp-tenants) above. Never gated by site resolution; carries `Vary: Authorization` |
| Subscriptions | `POST /subscriptions/new/` | `first_name`, `last_name`, `email`, `profile`, `list` | POST-only; `GET` returns `405` with `Allow: POST` |
| Subscriptions | `GET`/`POST /subscriptions/unsubscribe/{token}/list/{list_id}/`, `…/site/{site_id}/`, `…/all/` | `token` (path, the subscriber's unsubscribe token) | Unsubscribe links in emails. `GET` shows a confirmation page and changes nothing (so a link scanner can't unsubscribe anyone); the `POST` from that page does it — see [subscriptions.md](subscriptions.md) |
| Subscriptions | `GET`/`POST /subscriptions/author-optout/{token}/` | `token` (path, `AuthorOutreach.opt_out_token`) | Author outreach opt-out, same confirm-then-`POST` pattern — see [author-outreach.md](author-outreach.md) |
| Webhooks | `POST /webhooks/` | Postmark payload | Postmark suppression and reactivation events — see [subscriptions.md](subscriptions.md) |
| Robots | `GET /robots.txt` | None | The API host's `robots.txt`; disallows the search feeds — see [Search feeds](#search-feeds) |

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
| `site_id` | Trials carrying a subject in that site's `scope_subjects` (a content filter, as on `/articles/`) |
| `subjects` / `subjects_any` | Comma-separated subject ids: tagged with **all** of them (AND) / with **any** of them (OR) |
| `search` | Boolean search (bare terms AND-ed, uppercase `OR` / `NOT`, `"quoted phrases"`) over title, summary and scientific title — see [Search endpoints](#search-endpoints) for why the scientific title is included |
| `title`, `summary` | Case-insensitive substring of that one field |
| `acronym` | Trial acronym(s), comma-separated, matches any, case-insensitive, e.g. `?acronym=ReCOVER,MODIF-MS` |
| `has_takeaways` | `true`/`false`: whether the caller's own organisation has written takeaways for the trial (see [Editorial content](#editorial-content)) |
| `include` | `editorial` adds the `editorial` list to each trial (see [Editorial content](#editorial-content)) |
| `has_patent_link` | `true`/`false`: at least one visible, unsuppressed link to a patent family (see [Patents](#patents)). Category-basis links only unless `include_subject_links=true`. A caller whose sites don't show patents matches nothing |
| `include_subject_links` | `true` also counts (and, on the detail, shows) patent links that rest on a shared subject only |
| `internal_number` | Filter by WHO internal number |
| `phase` | Filter by trial phase (e.g., `Phase III`) |
| `phase_normalized` | Exact match against the canonical phase: `early_phase_1`, `phase_1`, `phase_1_2`, `phase_2`, `phase_2_3`, `phase_3`, `phase_3_4`, `phase_4`, `post_market`, `not_applicable`, `other`. Accepts a single value or a comma-separated list matched with OR, e.g. `?phase_normalized=phase_2,phase_3` |
| `recruitment_status`, `status` | Legacy: case-insensitive exact match on the registry's raw status string (`Recruiting` matches `RECRUITING`, not `Not Recruiting`). `status` is an alias. Misses spelling variants across registries — prefer `recruitment_status_normalized` |
| `recruitment_status_normalized` | Exact match against the canonical recruitment status: `not_yet_recruiting`, `recruiting`, `enrolling_by_invitation`, `active_not_recruiting`, `not_recruiting`, `suspended`, `completed`, `terminated`, `withdrawn`, `unknown`, `other`. Accepts a single value or a comma-separated list matched with OR, e.g. `?recruitment_status_normalized=recruiting,active_not_recruiting` |
| `study_type` | Legacy free-text `icontains` filter on the raw registry study-type string — prefer `study_type_normalized` |
| `study_type_normalized` | Exact match against the canonical study type: `interventional`, `observational`, `expanded_access`, `basic_science`, `other` |
| `primary_sponsor` | Legacy free-text filter on the raw registry sponsor string — prefer `sponsor_id`/`sponsor_slug` |
| `sponsor_id` | Exact match against a canonical sponsor's id (see [Sponsor canonicalization](#sponsor-canonicalization)) |
| `sponsor_slug` | Exact match against a canonical sponsor's slug |
| `source_register` | Filter by source registry (e.g., `ClinicalTrials.gov`) |
| `countries` | Legacy: case-insensitive substring of the registry's raw countries string — prefer `country` |
| `country` | Normalised ISO 3166-1 alpha-2 code(s), comma-separated, OR: `?country=DE` or `?country=DE,FR`. Derived from every source's country data |
| `region` | Normalised region derived from the trial's countries: `africa`, `asia`, `europe`, `north_america`, `south_america`, `oceania` |
| `condition` | Filter by medical condition |
| `intervention` | Filter by intervention type |
| `therapeutic_areas` | Filter by therapeutic areas |
| `inclusion_agemin` / `inclusion_agemax` | Legacy exact-match filter on the raw registry age strings (e.g., `18 Years`) — prefer `age_eligible` |
| `age_eligible` | Numeric age in years (e.g. `?age_eligible=40`). Returns trials whose canonical eligible age range (`inclusion_age_min_years`/`inclusion_age_max_years`) includes this age; a trial with no stated bound on either side is treated as open on that side. Both fields are also exposed on `TrialSerializer` for direct use. |
| `inclusion_gender_normalized` | Exact match against canonical sex eligibility: `all`, `female`, `male`. **Breaking change (2026-07-20):** replaces the removed legacy `inclusion_gender` substring filter, which returned confidently wrong results (`?inclusion_gender=Female` matched "Female, Male", a both-sexes trial — 82% false positives). `?inclusion_gender=...` is no longer a recognised parameter; requests using it are silently unfiltered rather than rejected. |
| `has_results` | `true`/`false`: results posted, by any of the registry signals (`results_posted`, a results completion date, a results link, or results-available = `Yes`) |
| `date_registration_after` | Date (`YYYY-MM-DD`): trials registered on or after this date (inclusive). Returns 400 for invalid dates |
| `date_registration_before` | Date (`YYYY-MM-DD`): trials registered on or before this date (inclusive). Returns 400 for invalid dates |

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

## Patents

`GET /patents/` lists patent families: one DOCDB simple family per record, with every published document of the family nested on the detail. Where the data comes from and how it is kept up to date is in [patents.md](patents.md).

### Which sites show patents

Patents are shown only through sites with `CustomSetting.patents_enabled` on (off by default). The patents are fetched with Brain Regeneration's EPO OPS account, so only sites covered by that licence decision should turn it on.

`patents_enabled` is checked on top of the usual scope: a patent is visible when it carries a subject in a site's `scope_subjects` **and** that site has the switch on. A site with it off shows no patents even when its scope lists the subject. The caller rules are those of every other endpoint (see [Visibility rules summary](#visibility-rules-summary)), counting only sites with the switch on:

| Caller | Sees patents of |
|:-------|:----------------|
| Site-bound API key | its own site, if the switch is on |
| MCP editor | the one site their token is bound to, if the switch is on |
| Signed-in user | every site of their organisations that has the switch on |
| Anonymous | the one resolved `api_public` site, if the switch is on |
| `?include_public=true` | adds every `api_public` site that has the switch on |

A caller with nothing in scope gets an empty list from `/patents/`, `404` on a single record, no `patent_links` on a trial, `patents_count` of `0` on sponsors and `patents_count_total` of `0` on categories. The same set decides `has_patent_link` and the patent counts in stats.

### `GET /patents/`

Every parameter is optional.

| Parameter | Matches |
|:----------|:--------|
| `search` | Boolean search over `title` and `summary`, as on `/articles/` and `/trials/` (bare terms AND-ed, uppercase `OR` / `NOT`, `"quoted phrases"`). Claims are not searched |
| `title`, `summary` | Case-insensitive substring of one field |
| `subject_id`, `site_id` | As on `/trials/`. `site_id` of a site with the switch off returns nothing |
| `category_id`, `category_slug`, `category_modality` | As on `/trials/` |
| `applicant_sponsor_id`, `applicant_sponsor_slug` | Any applicant linked to that canonical sponsor |
| `applicant` | Case-insensitive substring of an applicant's name as published; for applicants not linked to a sponsor |
| `priority_date_after`, `priority_date_before` | `earliest_priority_date`, inclusive, `YYYY-MM-DD`. Invalid dates are `400` |
| `publication_date_after`, `publication_date_before` | `earliest_publication_date`, same rules |
| `has_grant` | `true`/`false`. A heuristic on kind codes, not legal status |
| `cpc` | Prefix match against any of the family's CPC classes: `A61K31`, `A61P25/28`. Comma-separated, OR. Spaces are ignored |
| `publication_number` | Any member publication, in any common format: `EP4123456A1`, `EP 4123456 A1`, `EP-4123456-A1`. Without a kind code (`EP4123456`) it matches every kind. Matches on the stored country, number and kind, never as a prefix: `EP123` does not find `EP1234567`. Comma-separated, OR |
| `family_id` | Exact DOCDB family id |
| `has_trial_link` | At least one visible, unsuppressed link to a trial. See [Trial links](#trial-links) |
| `include_subject_links` | Also count and show subject-basis links. See [Trial links](#trial-links) |
| `ordering` | `earliest_priority_date`, `earliest_publication_date`, `discovery_date`, `last_updated`, each with an optional `-`. Default `-discovery_date`. Families without the date sort last in both directions; ties break on `-patent_id` |
| `page`, `page_size` | `page_size` up to 100; `page * page_size` above 10,000 is `400`, as elsewhere |

A family appears once however many of its publications or applicants match.

**No bulk export.** The licence rules out republishing the records, so there is no CSV and no `all_results`: `?format=csv` is `404`, and `?all_results=true` is ignored (you get an ordinary page).

**Espacenet link.** `link` is always present and points to the family's Espacenet page. Showing where a record comes from is a condition of the EPO OPS terms, so keep the link wherever you display a patent.

### Fields

The list returns:

| Field | Meaning |
|:------|:--------|
| `patent_id`, `family_id` | Gregory's id and the DOCDB family id |
| `title`, `summary` | As published (the summary is the abstract) |
| `representative_publication`, `link` | The publication OPS flags as representative, and its Espacenet page |
| `earliest_priority_date`, `earliest_publication_date` | Dates, or `null` |
| `has_grant` | A member publication has a grant kind code |
| `cpc_classes` | Strings such as `A61K31/435` |
| `applicants` | In published order: `raw_name`, `sponsor_id`, `sponsor_name`, `sponsor_slug` (`null` when not linked to a sponsor), `is_individual` |
| `categories`, `subjects` | Categories and subjects in the caller's scope |
| `publications_count` | Member publications |
| `trial_links_count` | Visible trial links, by the [rules below](#trial-links) |

`GET /patents/{id}/` adds:

| Field | Meaning |
|:------|:--------|
| `publications` | Every member: `publication_number`, `country`, `kind`, `publication_date`, `application_number`, `application_date` |
| `inventors` | Names as published |
| `ipc_classes`, `links` | IPC classes; source URLs keyed by source (`espacenet`) |
| `trial_links` | `trial_id`, `trial_title`, `sponsor_id`, `timing` (`during` / `after`), `days_after_completion`, `basis` |
| `claims` | **Only with `?include=claims`**; otherwise the key is absent. Averages ~12,000 characters and runs past 200,000, so it is never on the list. `null` when OPS has no full text for the family's offices (US-, CN- or JP-only families) |

`?include=claims` (detail only) and `?include=editorial` (list and detail) are the `include` values that do anything on `/patents/`; combine them with a comma. `editorial` adds an `editorial` list of the caller's own sites' `takeaways` and `summary_plain_english`, one entry per site, as on articles (see [Editorial content](#editorial-content)); for an MCP editor each entry also carries `updated_at` and `updated_by`. Only sites with `patents_enabled` on get an entry, since the others never show the patent, even when the caller owns them (on an article the same caller would get every site they own). `claims` is ignored on other endpoints.

### Trial links

A sponsor can file a patent while one of its trials is running or shortly after it ends; detection records that as a link. They are shown in both directions: `trial_links` on a patent, `patent_links` (`patent_id`, `title`, `representative_publication`, `timing`, `days_after_completion`, `basis`) on `GET /trials/{id}/`. A link shows only when all of these hold:

- it is not suppressed (an editor dismissed it), ever;
- its trial is visible to the caller;
- its patent is visible to the caller, which needs a site with the switch on, so **a trial seen only through a site without patents shows no patent links**;
- its `basis` is `category`, meaning the patent and the trial share a team category (typically the molecule), unless the request has `include_subject_links=true`.

`subject`-basis links rest on a shared subject alone. They are the large majority and noisy for big sponsors, which is why they are opt-in. The same rule feeds `trial_links_count` and the filters `has_trial_link` (patents) and `has_patent_link` (trials).

### Sponsors and categories

- `GET /sponsors/` rows carry `patents_count`: the families the sponsor applied for within the caller's patent scope. Without a parameter the list is unchanged: sponsors with a trial in scope. `?include_patent_applicants=true` also lists sponsors that only appear as applicants on patents in scope (their `trials_count` is `0`).
- `GET /categories/` rows carry `patents_count_total`, scoped the same way.

### `GET /patents/stats/`

Takes the same parameters as the list and the same scope. Returns `total`, `with_grant`, `with_trial_link` (category-basis unless `include_subject_links=true`) and `by_priority_year` (`[{year, count}]`, ascending, with a trailing `{"year": null, ...}` for families with no priority date). Cached for `STATS_CACHE_TTL` seconds; the cache key includes the patent scope, since two callers with the same subjects can differ on which of their sites show patents.

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

### `POST /trials/edit/`

Edits a trial's editorial content for the API key's **organisation** (trials still carry editorial content per organisation, see [Editorial content](#editorial-content)). Requires an `APIAccessScheme` API key with an organisation. Registry data is never editable here: it comes from the registries.

| Field | Description |
|:------|:------------|
| `identifiers` | **Required.** A JSON object with any of `nct`, `euct`, `eudract`; a trial matching any of them is found. Exact match on the stored registry identifier (unlike the `?nct=` filters, no format normalisation) |
| `takeaways`, `summary_plain_english` | Upserted into the trial's `TrialOrgContent` row for the key's organisation. An empty string clears the field (stored as `NULL`). Fields absent from the payload are left alone |

| HTTP status | Condition |
|:------------|:----------|
| `200 OK` | Edited. The body carries `trial_id`, `organization_id` and `updated_fields` |
| `400 Bad Request` | Body is not JSON, or `identifiers` missing |
| `401 Unauthorized` | No API key, key invalid, or the request IP is not in the key's allowlist |
| `403 Forbidden` | The key has no organisation, or the trial belongs to no team of the key's organisation |
| `404 Not Found` | No trial matches the identifiers |
| `409 Conflict` | More than one trial matches (`trial_ids` lists them); resolve the duplicates first |

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

The REST endpoints support three formats. Specify via the `format` query parameter or `Accept` header.

| Format | `format=` value | `Accept` header |
|:-------|:----------------|:----------------|
| JSON (default) | `json` | `application/json` |
| Browsable API | `html` | `text/html` |
| CSV | `csv` | `text/csv` |

Exceptions: `/patents/` and its detail and stats are JSON and browsable API only (`?format=csv` is `404`, a condition of the EPO licence, see [Patents](#patents)). RSS feeds, sitemaps, `robots.txt`, the `/emails/` previews and the `/subscriptions/` and `/webhooks/` routes are plain Django views with a fixed content type and ignore `format`.

For CSV export details and streaming behaviour, see [csv-export.md](csv-export.md).
