# Source manager grant and the `sources:add` scope

> This is a historical implementation note. For current behaviour, see [Grants and scopes](../07-mcp-server.md#grants-and-scopes).

**Context**: Trusted editors are going to be able to add sources (RSS feeds, PubMed and ClinicalTrials.gov searches) from their MCP client. Adding a source is a different kind of power from editing records, so it has its own permission. This change adds only the permission; no tool or route uses it yet.

---

## What changed

- `SiteEditor.can_manage_sources` (label "Can add sources"), set by a superuser on the **MCP editors** inline. It needs `can_edit`: `clean()` refuses it otherwise, and the `siteeditor_sources_need_edit` check constraint backs that up.
- Unticking it deletes the person's tokens for the site in the same save, as removing `can_edit` does.
- A new OAuth scope, `sources:add`. `SiteAccess.scopes` includes it for the editor tier with both flags, and every place that builds a `SiteAccess` (consent, code exchange, refresh, introspection) passes the flag, so all of them clamp alike. It appears in the authorization server's `scopes_supported` and on the consent screen.
- `EditorAuth.can_manage_sources`, read from the live grant on every `/editor/` request.

## What did not change

- Existing sessions keep their scopes. A person who is given the flag signs in again to get `sources:add`.
- No MCP tool and no `/editor/` route checks the scope yet.
- The MCP server does not yet ask for or accept the scope: it intersects introspected scopes with the two article scopes.

## Deploying

`python manage.py migrate` (`mcpauth` 0002: one column with default false, and the constraint).
