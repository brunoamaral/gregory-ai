# OAuth 2.1 authorization server for MCP editor access

> This is a historical implementation note. For current behaviour, see [MCP editor access](../06-organisations-teams-and-sites.md#mcp-editor-access) and [OAuth 2.1 authorization server](../03-api-and-rss-feeds.md#oauth-21-authorization-server-mcp-editor-access).

**Context**: Named editors need to sign in to the MCP server and be recognised as people. Every MCP client that follows the MCP authorization spec expects OAuth 2.1, so Django becomes the authorization server. This phase adds the server; the MCP server and the editor API follow in later changes, so nothing reads these tokens yet.

---

## What was added

- `django-oauth-toolkit` 3.4.1 as the engine, with `jwcrypto` 1.5.6 and `oauthlib` bumped from 3.2.2 to 3.3.1 (DOT needs 3.3.0 or later).
- A `mcpauth` app with `SiteEditor` (who may edit which site), the access, refresh and ID token models swapped in for DOT's (`AccessToken` carries `site` and `tier`), the login page, the consent screen, the introspection endpoint, and the Site admin inline.
- Routes under `/o/` and `/.well-known/oauth-authorization-server` on the API domain.
- `prune_oauth_clients`, and `cleartokens` (DOT's) for cron.

## What DOT already did

The spec expected dynamic client registration and Client ID Metadata Documents to be custom work. DOT 3.4.1 ships both, and RFC 8707 resource indicators (stored on the grant and on every token, narrowed on refresh), so they are configured and tightened rather than written. What is ours is the policy around them: one `resource` per request that must be a site's editor address, only the authorization-code grant, https or loopback redirect URIs, and a rate limit on registration.

## Upgrade steps

1. `pip install -r requirements.txt` (new packages, `oauthlib` bump).
2. `python manage.py migrate` (`mcpauth` 0001 and DOT's own migrations).
3. Set `GREGORY_MCP_SERVICE_KEY` in `.env`. It is unused until the MCP server and `/editor/` endpoints ship, but a missing key makes introspection refuse every call.
4. Optional: `OAUTH_ISSUER` if the API is not served at `https://api.<DOMAIN_NAME>`.
5. Add the cron lines from [MCP editor access](../06-organisations-teams-and-sites.md#maintenance).
6. Route `/o/` and `/.well-known/oauth-authorization-server` to Django on the API domain, and keep `/o/introspect/` off the public internet.

No existing endpoint changes.
