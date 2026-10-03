# MCP authentication and editor access

Status: implemented and merged on 2026-10-03. Phases 1 to 4 are #890 to #893; phases 5 and 6 are #895 (#894, phase 5, merged into its stacked base branch instead of `main`, so #895 carried it). Written 2026-10-03.

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
| D13 | Each site has its own editor address. The editor adds that address to their MCP client, signs in through it, and gets permissions for that site only. An organisation with more sites means one more connector per site, each with its own sign-in. There is no site switcher and no address that covers several sites. |
| D14 | Deployment option A: one process serves every site, with our own per-`Host` auth middleware on the editor mount. |
| D15 | Signing in on a site's editor address without editor access for that site still gives read access to that site's public data, if it has any. |
| D16 | Relevance stays per subject. An editor can set relevance only for subjects in their site's `scope_subjects`, and the change applies wherever that subject is used. No schema change, and ML training labels are unchanged. |
| D17 | Editors read their site's published scope only: `scope_subjects`, including on private sites, plus edit history. The curation queue (unpublished articles) is not exposed over MCP. |
| D18 | A private site (`api_public=False`) with `mcp_enabled` serves its editor address to granted editors and refuses its anonymous `/mcp` address. |
| D19 | Editors sign in with their Django username and password. Client editors without an account get one. No single sign-on in this project. |
| D20 | Client registration supports both dynamic client registration (RFC 7591) and Client ID Metadata Documents. |
| D21 | Rate limits start at 60 edits per hour and 500 per day per (user, site), configurable in settings, and get tuned from production logs. |
| D22 | No email digest of MCP edits in this project. Edit history is available through `get_article_history` and the admin. |
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

The server is pinned to `mcp==2.3.0` (upgraded from 2.0.0 in #889). The SDK includes resource-server support: `MCPServer(auth=AuthSettings(...), token_verifier=...)`, protected resource metadata, and `WWW-Authenticate` on 401. Since 2.2.0, `AuthSettings.validate_token_resource=True` rejects a token whose RFC 8707 resource isn't `resource_server_url`. It defaults to off and warns when unset, and 3.0 will turn it on by default.

Two limits shape this design:

- `streamable_http_app()` wraps the whole endpoint in `RequireAuthMiddleware`, so with auth enabled every caller must authenticate. MCP clients start the OAuth flow only when they receive an HTTP 401, so a single URL can't serve anonymous callers and still prompt editors to sign in.
- `AuthSettings` takes one fixed `resource_server_url` per server process. That one URL drives the token audience check, the protected resource metadata route, and the `WWW-Authenticate` header. One address per site (D13) is fine for editors, but today a single process serves every site's hostname, so the process needs a different resource URL per request. The SDK's `auth=` wiring can't express that. See [Deployment options](#deployment-options).

## Design

### Two endpoints per tenant

Each tenant hostname serves two MCP endpoints from the same process:

| Endpoint | Auth | Tools |
|:--|:--|:--|
| `https://gregory-ai.<domain>/mcp` | None (unchanged) | The ten read tools |
| `https://gregory-ai.<domain>/mcp/editor` | OAuth 2.1 bearer token, required | The ten read tools plus the editor tools |

`__main__.py` builds two `MCPServer` instances (`build_server(editor=False|True)`) and mounts both in one Starlette app. The anonymous mount's tool list stays fixed. The editor mount's `tools/list` depends on the token's tier (see [Access tiers on the editor address](#access-tiers-on-the-editor-address)), so it's served per request through `_replace_handler`, the seam prompts and resources already use. Its cache hint is `private` with a 5-minute TTL instead of `STATIC_CACHE`'s 30 minutes, so a newly granted editor sees the edit tools soon after reconnecting.

The editor mount doesn't use the SDK's `auth=` wiring, because that takes a single resource URL (see [MCP SDK](#mcp-sdk)). It sits behind a small Starlette middleware of our own that works per `Host`:

1. Resolves the tenant from `Host`, the same way `SiteMiddleware` does, and derives that tenant's resource URL: `https://<host>/mcp/editor`.
2. Serves `/.well-known/oauth-protected-resource/mcp/editor` for that resource URL.
3. With no valid token, returns 401 with a `WWW-Authenticate` header pointing at that tenant's metadata URL.
4. With a token, verifies it through a `TokenVerifier` and accepts it only if `AccessToken.resource` equals the tenant's resource URL. The comparison follows the SDK's `BearerAuthBackend._issued_for_this_resource()` (URL-normalised, trailing slash ignored).

The middleware reuses the SDK's types (`TokenVerifier`, `AccessToken`, the protected resource metadata model) so the protocol details stay the SDK's. If a later SDK release accepts a resource URL per request, the editor mount switches to the built-in wiring with `validate_token_resource=True`.

Editors add the editor URL as a separate connector. The anonymous connector stays as it is.

### Editor experience: one address per site

D13 in practice. Example: Ana edits for an organisation that owns `brain-regeneration.com` and `encefalites.pt`, and has a `SiteEditor` grant on both.

| Step | What Ana does | What happens |
|:--|:--|:--|
| 1 | Adds `https://gregory-ai.brain-regeneration.com/mcp/editor` as a connector in her MCP client | The client calls the endpoint, gets a 401, and reads that site's protected resource metadata |
| 2 | Signs in when the client opens the browser | Django's login page, then a consent screen naming Brain Regeneration, the client application, and the actions it can take |
| 3 | Approves | Django issues a token bound to the brain-regeneration.com editor address. The connector shows that site's name and title (`TenantIdentityMiddleware`). |
| 4 | Adds `https://gregory-ai.encefalites.pt/mcp/editor` as a second connector | Same flow. The consent screen names Encefalites. If her Django session is still active, she skips the password and only approves. |
| 5 | Uses both connectors | Each connector's tools act only on its own site. Brain Regeneration's token sent to the Encefalites address is rejected. |

Rules this implies:

- One connector, one site, one token. Permissions are checked against the site the address belongs to, never against a site the user or the model names.
- A user with no active grant for the address's site gets the public tier if the site has public data, and is refused otherwise (D15; see [Access tiers on the editor address](#access-tiers-on-the-editor-address)).
- Revoking a grant for one site ends that connector's editor access only. On its next request the client gets a 401, signs in again, and receives the public tier. The user's other connectors keep working.
- Signing out of one connector (token revocation) doesn't affect the others.
- The editor address for each site is shown on the Site admin page, next to the `SiteEditor` inline, so it can be sent to new editors. The connection guide in `docs/07-mcp-server.md` says to add one connector per site.

### Access tiers on the editor address

D15 means the editor address serves two tiers. The tier is fixed when the token is issued and stored on the token, next to `site_id`.

| Signed-in user | Site has public data (`api_public=True`) | Site has no public data |
|:--|:--|:--|
| Active `SiteEditor` grant | Editor tier: [editor read scope](#editor-read-scope) and the editor tools | Editor tier |
| No grant | Public tier: the same data and tools as the anonymous `/mcp` address | Refused at consent. No token is issued. |

What counts as "auth fails" decides the response:

| Situation | Response | Why |
|:--|:--|:--|
| No token | 401 with `WWW-Authenticate` | MCP clients only start the sign-in flow on a 401. Serving public data here would mean the client never asks the user to sign in. |
| Expired, revoked or malformed token | 401 with `WWW-Authenticate` | The client refreshes the token or signs in again. Falling back to public data here would hide an expired session, and the editor tools would disappear without explanation. |
| Token for another site's address | 401 | Tokens are never accepted across sites (D4). |
| Valid token, no editor grant | Public tier | The user signed in successfully but has no editor access. This is D15's fallback. |
| User declines consent, or sign-in fails | No token, so the connector doesn't connect | The anonymous `/mcp` address stays available for public data without signing in. |

Public-tier details:

- The consent screen says the user has read access to public data only, and names the site's admin email from `CustomSetting` as the contact for editor access.
- The token carries only `articles:read`. Upstream reads go out exactly like anonymous ones (`?site_id=`, no editor headers), so the public tier can never see more than `/mcp`.
- `tools/list` returns the ten read tools. `instructions_for()` uses the anonymous wording, plus one line saying editing needs editor access on this site.
- Telemetry records `tier: "public"`, so we can count users who sign in without a grant.

### Deployment options

D14 chose option A. Option B stays documented as the fallback if the per-`Host` middleware proves harder than expected.

| Option | How | Trade-off |
|:--|:--|:--|
| A. One process for all sites (chosen, D14) | Today's `gregory-mcp` container serves every hostname. The editor mount uses the per-`Host` auth middleware described above. | One container to deploy and monitor, as today. About 150 lines of our own auth code, reusing the SDK's types. |
| B. One process per site | One `gregory-mcp` container per site, each with `GREGORY_SITE_ID` set and the SDK's built-in `auth=AuthSettings(resource_server_url=<site's editor address>, validate_token_resource=True)` | No custom auth middleware. A container, a compose service and an nginx upstream per site, and every new site needs a deploy change rather than only the `mcp_enabled` tick. |

Option A keeps the current operating model, where turning on a site's assistant is a Site admin change. If the SDK later accepts a resource URL per request, option A switches to the built-in wiring without changing anything editors see.

### Why the token is per site

The OAuth resource indicator (RFC 8707) for the editor endpoint is that tenant's URL, for example `https://gregory-ai.brain-regeneration.com/mcp/editor`. A token issued for one tenant's URL is rejected on any other tenant's hostname by step 4 of the editor middleware above. This implements D4 at the protocol level. The verifier also checks that the token's site matches the tenant resolved from `Host`, so a mismatch fails even if the resource check has a bug.

### Authorization server: Django

Django becomes the OAuth 2.1 authorization server with `django-oauth-toolkit` (DOT). It already holds the users and the organisation memberships.

| Component | Detail |
|:--|:--|
| Endpoints | `/o/authorize/`, `/o/token/`, `/o/revoke/`, `/o/introspect/`, on the API domain |
| Metadata | `/.well-known/oauth-authorization-server` (RFC 8414) |
| Client registration | Dynamic client registration (RFC 7591) and Client ID Metadata Documents, so clients register without manual setup. DOT supports neither out of the box, so this is custom work (D20). |
| Grant | Authorization code with PKCE (S256) only. No implicit grant, no password grant. |
| Login and consent | A minimal, branded login page plus a consent screen naming the site, the client application, and the actions it can take. Not the Django admin login. |
| Token lifetime | Access token 1 hour. Refresh token 30 days, rotated on each use. |
| Site binding | Set at authorization time from the `resource` parameter: resource URL → host → site, the same matching rule as `site._match_domain()`. Stored on a custom access token model (`OAUTH2_PROVIDER_ACCESS_TOKEN_MODEL`) as `site_id`. |
| Scopes and tier | `articles:read` and `articles:edit`. The token also stores `tier` (`editor` or `public`); a public-tier token never carries `articles:edit`. |

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
- `/o/authorize/` issues an editor-tier token to a user with an active grant for the requested site, a public-tier token to a user without one when the site is `api_public`, and refuses otherwise (D15).
- Revoking a grant (setting `revoked_at`) deletes that user's tokens for that site in the same transaction, so access ends immediately rather than when the token expires.
- A client editor's grant is allowed only on a site owned (`OrganizationSite`) by an organisation they belong to (`OrganizationUser`). Our own team members are granted explicitly too. Superuser status doesn't grant MCP edit access by itself, so every editor appears in the grant list.

### Calling Django from the MCP server

The MCP authorization spec forbids passing the client's token to a downstream API. The token's audience is the MCP endpoint, not the Django API. The editor endpoint therefore:

1. Validates the inbound token through Django's introspection endpoint (RFC 7662). Results are cached in-process for 60 seconds, keyed by a hash of the token.
2. Gets back `user_id`, `site_id`, `tier`, `scope`, and `exp`, and stores them in a new `ContextVar` (`editor_context`).
3. For a public-tier token, calls Django anonymously with `?site_id=`, exactly like `/mcp`, and skips the rest of this list. For an editor-tier token, calls Django with its own service credential (`GREGORY_MCP_SERVICE_KEY`, a new single-purpose credential, not an `APIAccessScheme`), plus the verified editor in headers: `X-Gregory-Editor-User` and `X-Gregory-Editor-Site`.
4. Django accepts these headers only together with a valid service credential, and re-checks the user's active `SiteEditor` grant on every write. It then runs the request as that user, restricted to that site. Requests carrying either header without the service credential are rejected with 401.

`client.py` gains `post()`, `put()`, `patch()`, and `delete()`. These methods don't retry. An edit that timed out may already have been applied, and retrying it would create duplicates. `get()` keeps its current retry behaviour.

### Editor read scope

D5 says editors read more. D17 limits that to published content. The scope, all limited to the token's site:

| Data | Anonymous | Editor |
|:--|:--|:--|
| Articles and trials in `scope_subjects` | Yes, `api_public` sites only | Yes, private sites too |
| Relevance not yet reviewed (`is_relevant` null) | Hidden by `relevant=true` filters | Filterable explicitly |
| Editorial content | Site content (after D3) | Same, plus `updated_by` and `updated_at` |
| Edit history of a record | No | Yes, through `get_article_history` |

Private sites (D18): `/tenants/` and `TenantGateMiddleware` currently serve only sites with `mcp_enabled`. An `mcp_enabled` site with `api_public=False` serves its editor address and refuses its anonymous `/mcp` address with the existing "not available at this address" error. `GET /tenants/` already returns `api_public` per tenant (`Tenant.api_public`), so `TenantGateMiddleware` gets a per-mount rule: the anonymous mount requires `api_public=True`, the editor mount doesn't. On a private site, a signed-in user without a grant is refused at consent (D15), since there is no public data to fall back to.

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

`ArticleSubjectRelevance` is keyed by subject, not by site. If two sites list the same subject in `scope_subjects`, an editor on one site changes relevance for both. D16 accepts this: relevance describes the article and the subject, not the site. `set_article_relevance` rejects a `subject_id` outside the token site's `scope_subjects`, and the tool description tells the model the change applies to every site covering that subject.

### Rate limits (D12)

| Layer | Limit | Key |
|:--|:--|:--|
| nginx, `/mcp/editor` | Separate `limit_req_zone` from `/mcp` | Client address |
| Django, editor writes | DRF `ScopedRateThrottle`, starting at 60 edits per hour and 500 per day | `(user_id, site_id)` |

A throttled write returns 429. The MCP tool turns that into a clear error and doesn't retry.

### Telemetry and logs

- `mcp_request` gains `tier` (`anon`, `public` or `editor`) and `user_id` (Django user id, editor only).
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
- Multi-site editor: one user with grants on two sites of the same organisation signs in on each address and gets two tokens; each token works only on its own address; revoking one grant leaves the other connector working; a user with no grant on the second site gets a public-tier token with the ten read tools when that site is `api_public`, and is refused at consent when it isn't.
- Tier fallback: no token, an expired token, and another site's token all return 401, never public data; a public-tier token can't call write endpoints even if a client sends a `tools/call` for an editor tool; revoking a grant turns the next sign-in into the public tier.
- MCP: `/mcp` behaves exactly as before (all existing tests pass without changes); `/mcp/editor` returns 401 with `WWW-Authenticate` and a resource metadata URL; protected resource metadata content; token for site A refused on site B's host; write tools absent from `/mcp`'s `tools/list`; no retries on writes; annotations on each tool; editor identity text; cache tier separation; no token in logs.
- End to end on a staging tenant, once with each of Claude Code, Claude Desktop, and a claude.ai connector, before production.

## Open questions

None. Questions raised during drafting were resolved on 2026-10-03 and recorded as D13 to D22.

## Non-goals

- Editing trials, or any trial field (D10). `TrialOrgContent` stays per organisation until trials are in scope.
- Editing `access`, `retracted`, `kind`, subjects, or categories over MCP (D7).
- A draft or approval workflow (D9).
- API keys (`APIAccessScheme`) as an MCP credential. They stay for server-to-server REST use.
- Cross-site credentials (D4), a single editor address covering several sites, or a site switcher inside one connector (D13).
- Bulk edits. Each tool call changes one article.
