# MCP source tools

> This is a historical implementation note. For current behaviour, see [The source tools](../07-mcp-server.md#the-source-tools).

**Context**: The permission (`sources:add`), history on Sources and the `/editor/sources/` routes are in place. This change adds the MCP side: three tools for people whose grant has *Can add sources*.

---

## What changed

- `list_sources`, `preview_source` and `create_source`, listed and callable only for a sign-in with the `sources:add` scope. Without it they are answered like tools that do not exist.
- The MCP server accepts the `sources:add` scope (it was dropped when introspected scopes were intersected with the two article scopes) and lists it in `scopes_supported`.
- `GregoryClient` refuses any `/editor/sources/` request, reads included, from a session without the scope.
- The two source POSTs get their own timeout, `GREGORY_SOURCE_REQUEST_TIMEOUT` (default 75 s), because Django checks an outside service before answering. A create that timed out but was saved reads as "may have been added", and the second try as "already exists".
- `create_source` tells the model that the source is live at once, feeds every site that covers the subject and brings in content from today on, and to run `preview_source` first, show the numbers and samples and get confirmation. A session with the scope gets one more sentence in the server instructions saying so; other sessions' text is unchanged.
- `mcp_edit` log lines gain `source_id`, and `record_type` `source`.

## What did not change

- No Django code. The routes, checks, limits and history are as in the previous change.
- Editing, pausing and deleting sources stay in the admin.

## Deploying

1. Restart the MCP server container (it needs the new code to accept the scope).
2. In the Site admin, a superuser ticks *Can add sources* for the person.
3. The person disconnects and reconnects the MCP connector, so they sign in again and get the new scope. The consent screen lists "Add RSS feeds and PubMed or ClinicalTrials.gov searches for this site's subjects".
4. Optional: `GREGORY_SOURCE_REQUEST_TIMEOUT` for the MCP container (default 75 seconds).
