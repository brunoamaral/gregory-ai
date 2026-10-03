# MCP authentication and editor access

Status: draft, not started. Written 2026-10-03.

Adds a second, authenticated access level to the MCP server (`mcp-server/`). Anonymous
callers keep what they have today: read access to one site's public data. Named editors,
from our own team and from client organisations, sign in once per site. They get wider
read access to that site and three article editing tools. Every edit is attributed to the
person who made it.

## Goal

| Level | Who | Reads | Writes |
|:--|:--|:--|:--|
| Anonymous | Anyone who reaches a tenant hostname | The site's `scope_subjects`, `api_public` sites only (unchanged) | None |
| Editor | A named person granted editor access to one site | More than anonymous (see [Editor read scope](#editor-read-scope)) | Site editorial content, subject relevance, article-trial links |

## Decisions taken

These come from the requirements discussion on 2026-10-03.

| # | Decision |
|:--|:--|
| D1 | Editors are our own team and client editors at each organisation. |
| D2 | Every MCP client must work: Claude Code, Claude Desktop, claude.ai connectors, and any client that follows the MCP authorization spec. This requires OAuth 2.1. A static API key is not enough. |
| D3 | Editorial content becomes per site, not per organisation. Two sites of the same organisation can carry different takeaways for the same article. This needs a schema change. |
| D4 | One credential grants exactly one site. An editor working on two sites holds two grants and signs in twice. |
| D5 | Editors read more than anonymous callers. |
| D6 | Editable: `takeaways`, `summary_plain_english`, subject relevance (`ArticleSubjectRelevance.is_relevant`), and links between an article and a trial. |
| D7 | Global article fields (`access`, `retracted`, `kind`) are not editable over MCP. They stay on `POST /articles/edit/` (API key) and the admin. |
| D8 | Write tools accept an article by `article_id` or by DOI. |
| D9 | Edits apply immediately. No draft or review queue. |
| D10 | Trials stay read-only. Editors can link a trial to an article, but can't edit the trial itself. |
| D11 | Every edit records the named person, not a shared key. |
| D12 | Write tools ask the client for confirmation (`destructive_hint`), and each credential has its own edit rate limit. |

## What exists today

### MCP server

- Stateless proxy that only sends `GET` requests (`gregory_mcp/client.py` has only `get()`). Every tool is annotated `READ_ONLY` in `gregory_mcp/server.py`.
- Calls Django anonymously. The tenant comes from the inbound `Host` header (or `GREGORY_SITE_ID`), matched against `GET /tenants/` (`tenants.py`). `?site_id=` is added to every upstream call (`site_context.py` → `client.py`).
- Per-request state goes through `ContextVar`s and a middleware chain: `SiteMiddleware → TelemetryMiddleware → TenantGateMiddleware → TenantIdentityMiddleware`.
- Tools are registered once per process. Prompts and resources are served per tenant through `_replace_handler`.
- Identity text says every tool is read-only (`identity.instructions_for()`). `tests/test_identity.py` checks that wording.
- `docs/07-mcp-server.md` § Auth: "None". nginx rate-limits per (client address, tool name).

### Django authentication

| Mechanism | Scope | Used by |
|:--|:--|:--|
| `APIAccessScheme` key in the `Authorization` header | One organisation and one site. Has an IP allowlist, a validity window, quotas, and an audit log (`APIAccessSchemeLog`). | `post_article`, `edit_article`, `edit_trial`, and site-scoped reads |
| User accounts (session, Basic, simplejwt at `/api/token/`) | Every site owned by every organisation the user belongs to (`visibility.visible_subject_ids`) | Reads and the admin |

There is no OAuth authorization server. `oauthlib` is installed only as a dependency of Google auth. `django-oauth-toolkit` is not installed.

### Editable data

| Data | Model | Current granularity | Audit |
|:--|:--|:--|:--|
| `takeaways`, `summary_plain_english` | `ArticleOrgContent(article, organization)` | Per organisation | `HistoricalRecords(bases=[ApiKeyHistoryMixin])`, which records the API key, not a person |
| Relevance | `ArticleSubjectRelevance(article, subject, is_relevant)` | Per subject, shared by every site that scopes the subject | None |
| Article-trial link | `ArticleTrialReference(article, trial, identifier_type, identifier_value)` | Global | None |

Two existing behaviours conflict with this feature:

- `?include=editorial` (`api/editorial.py`) selects content by organisation: the API key's, the user's, or the anonymous site's owner. D3 changes this to selection by site.
- `detect_trial_references --reset` runs `ArticleTrialReference.objects.all().delete()`. Without a change, that would delete every link an editor added by hand.

### MCP SDK

The server is pinned to `mcp==2.3.0` (upgraded from 2.0.0 on branch `claude/mcp-sdk-2.3`). The SDK includes resource-server support: `MCPServer(auth=AuthSettings(...), token_verifier=...)`, protected resource metadata, and `WWW-Authenticate` on 401. Since 2.2.0, `AuthSettings.validate_token_resource=True` rejects a token whose RFC 8707 resource isn't `resource_server_url`. It defaults to off and warns when unset, and 3.0 will turn it on by default.

Two limits shape this design:

- `streamable_http_app()` wraps the whole endpoint in `RequireAuthMiddleware`, so with auth enabled every caller must authenticate. MCP clients start the OAuth flow only when they receive an HTTP 401, so a single URL can't serve anonymous callers and still prompt editors to sign in.
- `AuthSettings` takes one fixed `resource_server_url` per server. That one URL drives the token audience check, the protected resource metadata route, and the `WWW-Authenticate` header. This process serves every tenant hostname, and each tenant needs its own resource URL. The SDK's `auth=` wiring can't express that.

## Design

### Two endpoints per tenant

Each tenant hostname serves two MCP endpoints from the same process:

| Endpoint | Auth | Tools |
|:--|:--|:--|
| `https://gregory-ai.<domain>/mcp` | None (unchanged) | The ten read tools |
| `https://gregory-ai.<domain>/mcp/editor` | OAuth 2.1 bearer token, required | The ten read tools plus the editor tools |

`__main__.py` builds two `MCPServer` instances (`build_server(editor=False|True)`) and mounts both in one Starlette app. Because each endpoint has a fixed tool list, `tools/list` doesn't have to change per request, and the `STATIC_CACHE` hint still holds.

The editor mount doesn't use the SDK's `auth=` wiring, because that takes a single resource URL (see [MCP SDK](#mcp-sdk)). It sits behind a small Starlette middleware of our own that works per `Host`:

1. Resolves the tenant from `Host`, the same way `SiteMiddleware` does, and derives that tenant's resource URL: `https://<host>/mcp/editor`.
2. Serves `/.well-known/oauth-protected-resource/mcp/editor` for that resource URL.
3. With no valid token, returns 401 with a `WWW-Authenticate` header pointing at that tenant's metadata URL.
4. With a token, verifies it through a `TokenVerifier` and accepts it only if `AccessToken.resource` equals the tenant's resource URL. The comparison follows the SDK's `BearerAuthBackend._issued_for_this_resource()` (URL-normalised, trailing slash ignored).

The middleware reuses the SDK's types (`TokenVerifier`, `AccessToken`, the protected resource metadata model) so the protocol details stay the SDK's. If a later SDK release accepts a resource URL per request, the editor mount switches to the built-in wiring with `validate_token_resource=True`.

Editors add the editor URL as a separate connector. The anonymous connector stays as it is.

### Why the token is per site

The OAuth resource indicator (RFC 8707) for the editor endpoint is that tenant's URL, for example `https://gregory-ai.brain-regeneration.com/mcp/editor`. A token issued for one tenant's URL is rejected on any other tenant's hostname by step 4 of the editor middleware above. This implements D4 at the protocol level. The verifier also checks that the token's site matches the tenant resolved from `Host`, so a mismatch fails even if the resource check has a bug.

### Authorization server: Django

Django becomes the OAuth 2.1 authorization server with `django-oauth-toolkit` (DOT). It already holds the users and the organisation memberships.

| Component | Detail |
|:--|:--|
| Endpoints | `/o/authorize/`, `/o/token/`, `/o/revoke/`, `/o/introspect/`, on the API domain |
| Metadata | `/.well-known/oauth-authorization-server` (RFC 8414) |
| Client registration | Dynamic client registration (RFC 7591) and Client ID Metadata Documents, so clients register without manual setup. DOT supports neither out of the box, so this is custom work (see [Open questions](#open-questions)). |
| Grant | Authorization code with PKCE (S256) only. No implicit grant, no password grant. |
| Login and consent | A minimal, branded login page plus a consent screen naming the site, the client application, and the actions it can take. Not the Django admin login. |
| Token lifetime | Access token 1 hour. Refresh token 30 days, rotated on each use. |
| Site binding | Set at authorization time from the `resource` parameter: resource URL → host → site, the same matching rule as `site._match_domain()`. Stored on a custom access token model (`OAUTH2_PROVIDER_ACCESS_TOKEN_MODEL`) as `site_id`. |
| Scopes | `articles:read` and `articles:edit`. Consent can grant read without edit. |

The MCP editor endpoint serves `/.well-known/oauth-protected-resource/mcp/editor` (RFC 9728), naming the Django API domain as its authorization server.

### Editor grants

A new model records which person may edit which site:

```python
class SiteEditor(models.Model):
	user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="site_editor_grants")
	site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name="editor_grants")
	can_edit = models.BooleanField(default=True)
	granted_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="+")
	created_at = models.DateTimeField(auto_now_add=True)
	revoked_at = models.DateTimeField(null=True, blank=True)

	class Meta:
		constraints = [
			models.UniqueConstraint(
				fields=["user", "site"],
				condition=models.Q(revoked_at__isnull=True),
				name="unique_active_site_editor",
			)
		]
```

- Managed from a `SiteEditor` inline on the Site admin page, next to the MCP prompt and document inlines.
- `/o/authorize/` refuses a user with no active grant for the requested site.
- Revoking a grant (setting `revoked_at`) deletes that user's tokens for that site in the same transaction, so access ends immediately rather than when the token expires.
- A client editor's grant is allowed only on a site owned (`OrganizationSite`) by an organisation they belong to (`OrganizationUser`). Our own team members are granted explicitly too. Superuser status doesn't grant MCP edit access by itself, so every editor appears in the grant list.

### Calling Django from the MCP server

The MCP authorization spec forbids passing the client's token to a downstream API. The token's audience is the MCP endpoint, not the Django API. The editor endpoint therefore:

1. Validates the inbound token through Django's introspection endpoint (RFC 7662). Results are cached in-process for 60 seconds, keyed by a hash of the token.
2. Gets back `user_id`, `site_id`, `scope`, and `exp`, and stores them in a new `ContextVar` (`editor_context`).
3. Calls Django with its own service credential (`GREGORY_MCP_SERVICE_KEY`, a new single-purpose credential, not an `APIAccessScheme`), plus the verified editor in headers: `X-Gregory-Editor-User` and `X-Gregory-Editor-Site`.
4. Django accepts these headers only together with a valid service credential. It then runs the request as that user, restricted to that site. Requests carrying either header without the service credential are rejected with 401.

`client.py` gains `post()`, `put()`, `patch()`, and `delete()`. These methods don't retry. An edit that timed out may already have been applied, and retrying it would create duplicates. `get()` keeps its current retry behaviour.

### Editor read scope

D5 says editors read more. The proposed scope, all limited to the token's site:

| Data | Anonymous | Editor |
|:--|:--|:--|
| Articles and trials in `scope_subjects` | Yes, `api_public` sites only | Yes, private sites too |
| Articles from the site organisation's sources with no subject in any scope (the curation queue) | No | Yes |
| Relevance not yet reviewed (`is_relevant` null) | Hidden by `relevant=true` filters | Filterable explicitly |
| Editorial content | Site content (after D3) | Same, plus `updated_by` and `updated_at` |
| Edit history of a record | No | Yes, through `get_article_history` |

Private sites: `/tenants/` and `TenantGateMiddleware` currently serve only sites with `mcp_enabled`. An `mcp_enabled` site with `api_public=False` would then serve its editor endpoint and refuse its anonymous one. That is a new tenant state, covered under [Open questions](#open-questions).

Cache isolation: `CatalogCache` keys by `site_id` only. Editor reads can return more than anonymous reads, so the key becomes `(site_id, tier)`, where `tier` is `anon` or `editor`. The cached data is the same for every editor of a site, so the user isn't part of the key.

### Editor tools

All tools accept `article_id` or `doi`. Passing both, or neither, raises `INVALID_PARAMS`. A DOI is matched case-insensitively, the same way `edit_article` matches it. A DOI matching more than one article returns the conflicting ids rather than guessing. An article outside the token site's editor read scope returns the same "not found" as a missing one, so its existence isn't revealed.

| Tool | Effect | Annotations |
|:--|:--|:--|
| `update_article_editorial` | Sets `takeaways` and/or `summary_plain_english` for this site. An empty string clears the field. | `read_only=False`, `destructive=True`, `idempotent=True` |
| `set_article_relevance` | Sets `is_relevant` (`true`, `false`, `null`) for one `subject_id`. The subject must be in the site's `scope_subjects`. | `read_only=False`, `destructive=True`, `idempotent=True` |
| `link_trial_to_article` | Adds a manual `ArticleTrialReference`. The trial must be visible to the editor. | `read_only=False`, `destructive=False`, `idempotent=True` |
| `unlink_trial_from_article` | Removes a link. See [Manual trial links](#manual-trial-links) for what unlinking an auto-detected link does. | `read_only=False`, `destructive=True`, `idempotent=True` |
| `get_article_history` | Read: who changed this article's site content, relevance, or links, and when | `READ_ONLY` |

Every write tool returns the stored values after the write, plus `updated_by` and `updated_at`, so the model can confirm what changed without a second read. `destructive_hint=True` asks the client to confirm before calling. Clients decide how to act on it, so the confirmation isn't guaranteed. The rate limit and the audit trail are the safeguards that don't depend on the client.

`instructions_for()` gains an editor variant. It names the tools, says edits are live immediately and attributed to the signed-in person, and tells the model to show the proposed text before writing. The anonymous variant keeps "every tool is read-only".

### Django editor endpoints

New views in `api/editor_views.py`, authenticated only by the service credential plus editor headers (a dedicated `EditorServiceAuthentication` DRF class), mounted under `/editor/`:

| Method and path | Purpose |
|:--|:--|
| `GET /editor/articles/resolve/?doi=` | DOI → `article_id`, inside editor scope |
| `PATCH /editor/articles/{article_id}/editorial/` | Upsert this site's editorial content |
| `PUT /editor/articles/{article_id}/relevance/{subject_id}/` | Set relevance |
| `POST /editor/articles/{article_id}/trials/` | Add a manual link |
| `DELETE /editor/articles/{article_id}/trials/{trial_id}/` | Remove a link |
| `GET /editor/articles/{article_id}/history/` | Edit history |

Reads under `/editor/` reuse the existing viewsets with an editor-aware branch in `visibility.visible_subject_ids()`, placed before the user branch: the token's single site, `public_only=False`. This replaces the user branch's "every site of every organisation" with D4's single site.

Per CLAUDE.md, this PR also updates `docs/03-api-and-rss-feeds.md`, the view docstrings, `docs/02.1-database-tables-and-fields.md`, and the regenerated `schema.yml` (`@extend_schema` on every manual view).

### Schema changes

#### Per-site editorial content (D3)

```python
class ArticleSiteContent(models.Model):
	article = models.ForeignKey(Articles, on_delete=models.CASCADE, related_name="site_contents")
	site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name="article_contents")
	takeaways = models.TextField(blank=True, null=True)
	summary_plain_english = models.TextField(blank=True, null=True)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)
	history = HistoricalRecords(bases=[ApiKeyHistoryMixin, EditorHistoryMixin])

	class Meta:
		constraints = [
			models.UniqueConstraint(fields=["article", "site"], name="unique_article_site_content")
		]
```

- Data migration: copy each `ArticleOrgContent` row to every site its organisation owns. This keeps what each site shows today. Copying only to the default site would hide content on the other sites.
- `?include=editorial` switches to site selection: an API key uses its `site`, an editor uses the token's site, an anonymous caller uses `resolve_anonymous_site()`. A logged-in user with no site context gets the union of their organisations' sites, labelled by site. This changes the public API's output and must ship with a changelog entry.
- `POST /articles/edit/` writes to `ArticleSiteContent` for the key's `site`.
- `ArticleOrgContent` becomes read-only after migration and is dropped in a later release.
- `TrialOrgContent` stays per organisation for now (D10). The inconsistency is temporary and written down under [Non-goals](#non-goals).

#### Attribution (D11)

- `EditorHistoryMixin` adds `editor_user` (FK, `SET_NULL`) and `editor_label` (name and email at save time, kept after the user is deleted), set by a signal the same way `stamp_api_access_scheme_on_history` sets the key fields today. It also records `via` (`mcp`, `api_key`, `admin`).
- `ArticleSubjectRelevance` and `ArticleTrialReference` gain `HistoricalRecords` with the same mixins. Neither has history today.

#### Manual trial links

`ArticleTrialReference` gains `source` (`auto` or `manual`, default `auto`) and `created_by` (nullable user FK).

- Manual links use `identifier_type="manual"`. `identifier_value` holds the trial's primary identifier.
- `detect_trial_references --reset` deletes only `source="auto"` rows.
- Unlinking an `auto` link hides it rather than deleting it: a new `suppressed` boolean that detection respects. Otherwise the next detection run would recreate it.

#### Relevance is shared across sites

`ArticleSubjectRelevance` is keyed by subject, not by site. If two sites list the same subject in `scope_subjects`, an editor on one site changes relevance for both. D3 makes editorial content per site but doesn't cover relevance. See [Open questions](#open-questions).

### Rate limits (D12)

| Layer | Limit | Key |
|:--|:--|:--|
| nginx, `/mcp/editor` | Separate `limit_req_zone` from `/mcp` | Client address |
| Django, editor writes | DRF `ScopedRateThrottle`, starting at 60 edits per hour and 500 per day | `(user_id, site_id)` |

A throttled write returns 429. The MCP tool turns that into a clear error and doesn't retry.

### Telemetry and logs

- `mcp_request` gains `tier` (`anon` or `editor`) and `user_id` (Django user id, editor only).
- `mcp_intent` still carries neither `site_id` nor any user field. The rule in `docs/07-mcp-server.md` keeps intent text from being joined to other records, and adding a named person would make that join trivial.
- Write tools log an `mcp_edit` event: tool, `article_id`, field names changed (never the values), outcome. The values live in Django history, which has its own access control.
- `Authorization` headers are never logged. A test asserts this, in the same style as `test_no_internal_url_leak.py`.

## Security checklist

- PKCE (S256) required. Redirect URIs matched exactly. Registered clients that go unused for 90 days are deleted.
- Tokens are bound to one resource URL, and therefore one site. The verifier checks the audience and the site match against `Host`.
- The service credential is accepted only from the MCP container's network. nginx denies `/editor/` from the public internet, and Django also checks the credential.
- The editor endpoint sends `Cache-Control: no-store`. The cache hints for editor responses stay `private`.
- Introspection results are cached for at most 60 seconds, so a revocation takes effect within that time.
- CSRF and clickjacking protection on the login and consent pages, plus a login rate limit.
- Both endpoints refuse a tenant whose site has `mcp_enabled=False`, as today.

## Rollout

Each phase is one PR and leaves production working.

1. Per-site editorial content. Add `ArticleSiteContent`, run the data migration, switch `?include=editorial` and `edit_article` to sites, and update the docs, schema, and changelog. This phase is useful on its own and has no MCP changes.
2. Attribution and trial-link safety. Add `EditorHistoryMixin`, history on relevance and trial links, and `source`/`suppressed` on `ArticleTrialReference`, then update `detect_trial_references`.
3. OAuth authorization server. Install DOT, add the custom token model with `site_id`, `SiteEditor` and its admin inline, the login and consent pages, metadata, introspection, and client registration.
4. Django editor endpoints. Add `EditorServiceAuthentication`, the `/editor/` views, the editor branch in `visible_subject_ids`, throttles, docs, and `schema.yml`.
5. MCP editor endpoint. Add the second mount, the per-host auth middleware and protected resource metadata, token verifier, `editor_context`, write methods on the client, editor tools, identity variant, cache tier, telemetry, and tests.
6. Deployment. Add the nginx location and rate-limit zone, compose env (`GREGORY_MCP_SERVICE_KEY`), the `docs/07-mcp-server.md` Auth section, and a connection guide for editors per client.

## Testing

- Django: grant checks (no grant, revoked grant, wrong organisation), token bound to the wrong site, service credential missing or wrong, editor headers without credential, every write endpoint inside and outside scope, DOI resolution with zero, one, or several matches, throttle, history attribution, migration from organisation content to site content on an organisation with two sites, detection reset keeping manual links and respecting suppressed ones.
- MCP: `/mcp` behaves exactly as before (all existing tests pass without changes); `/mcp/editor` returns 401 with `WWW-Authenticate` and a resource metadata URL; protected resource metadata content; token for site A refused on site B's host; write tools absent from `/mcp`'s `tools/list`; no retries on writes; annotations on each tool; editor identity text; cache tier separation; no token in logs.
- End to end on a staging tenant, once with each of Claude Code, Claude Desktop, and a claude.ai connector, before production.

## Open questions

1. Relevance per site. Keep relevance per subject and accept that sites sharing a subject share relevance? Or add a `site` field to `ArticleSubjectRelevance`? The second option also changes the ML training labels and every `relevant=` filter. Answering needs a production count of subjects that appear in more than one site's `scope_subjects`.
2. Client registration. claude.ai and Claude Desktop need a client to register itself. Build dynamic client registration on DOT, support Client ID Metadata Documents only, or both? Check which one each target client uses at implementation time.
3. Private sites. Should an `mcp_enabled` site with `api_public=False` serve an editor endpoint only? `TenantGateMiddleware` and `/tenants/` would need a per-endpoint rule.
4. Editor read scope. Confirm the table in [Editor read scope](#editor-read-scope), especially the curation queue, which exposes content not yet published anywhere.
5. Rate limit numbers. 60 per hour and 500 per day are placeholders. Set them from expected editor workload.
6. Login identity. Django username and password only, or single sign-on (for example Google) for client editors who don't have a Django password?
7. Notification. Should the site's admin email receive a digest of MCP edits, alongside `send_admin_summary`?

## Non-goals

- Editing trials, or any trial field (D10). `TrialOrgContent` stays per organisation until trials are in scope.
- Editing `access`, `retracted`, `kind`, subjects, or categories over MCP (D7).
- A draft or approval workflow (D9).
- API keys (`APIAccessScheme`) as an MCP credential. They stay for server-to-server REST use.
- Cross-site credentials (D4).
- Bulk edits. Each tool call changes one article.
