# MCP editor deployment

> This is a historical implementation note. For current behaviour, see [Enabling editor access](../07-mcp-server.md#enabling-editor-access).

**Context**: The editor endpoint, the OAuth server and the editor routes were built in earlier changes and are inert until configured. This adds the deployment pieces and the connection guide.

---

## What was added

- nginx example: a separate rate-limit zone and `location /mcp/editor`, and a proxied `location = /.well-known/oauth-protected-resource/mcp/editor`, on the per-site MCP host. On the API host, `/editor/` and `/o/introspect/` answer 404 and the `X-Gregory-Editor-*` request headers are cleared.
- Compose: `GREGORY_MCP_SERVICE_KEY` for both containers, `OAUTH_ISSUER` and `MCP_EDITOR_HOST_PREFIX` for Django, and `GREGORY_OAUTH_ISSUER` for the MCP server (defaulting to `https://api.<DOMAIN_NAME>`).
- `docs/07-mcp-server.md`: how editors connect (one connector per site), how to enable and disable editor access, how to verify it, and the service key under Risks.

## What was not changed

- No new DNS records or certificates: the editor address is on the host each site already uses for `/mcp`.
- Without `GREGORY_MCP_SERVICE_KEY` set, nothing changes for the running system.
