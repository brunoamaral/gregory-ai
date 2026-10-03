# MCP editor endpoint

> This is a historical implementation note. For current behaviour, see [Editor access](../07-mcp-server.md#editor-access-mcpeditor).

**Context**: Signed-in editors can now read and edit a site's article content from an LLM client. This adds the MCP side: a second endpoint per site, `/mcp/editor`, next to the unchanged anonymous `/mcp`.

---

## What was added

- `/mcp/editor` in the same process and port, off until `GREGORY_MCP_SERVICE_KEY` and `GREGORY_OAUTH_ISSUER` are set. Per-`Host` bearer-token checks by introspection against Django, `401` with a protected-resource-metadata pointer otherwise, and `GET /.well-known/oauth-protected-resource/mcp/editor` per host.
- Tools by tier: the public tier sees the ten read tools, a read-only editor adds `get_article_history`, an editor with the edit scope adds `update_article_editorial`, `set_article_relevance`, `link_trial_to_article` and `unlink_trial_from_article`.
- Reads on this address use Django's `/editor/` routes as the signed-in person; the response cache is keyed by tier.
- `mcp_request` log lines carry `tier` and `user_id`; each write logs an `mcp_edit` line with field names only.

## What changed

- `GregoryClient` now has `post`, `put`, `patch` and `delete`. They need an editor session with the edit scope and are never retried. The anonymous server registers no tool that uses them.

## What was not changed

- `/mcp` behaves exactly as before. nginx and compose configuration for the new address come with the deployment change.
