# Editor routes for the MCP server

> This is a historical implementation note. For current behaviour, see [Editor routes](../03-api-and-rss-feeds.md#editor-routes-editor).

**Context**: The MCP server needs to read a site's whole published scope and to make three kinds of edit as a named person. This adds the Django side: `/editor/` routes authenticated by a service credential plus the verified editor. Nothing calls them yet, and no public endpoint changes.

---

## What was added

- Authentication: `Authorization: Bearer <GREGORY_MCP_SERVICE_KEY>` with `X-Gregory-Editor-User` and `X-Gregory-Editor-Site`. The grant is re-checked on every request. A middleware refuses the two headers on any request without the credential, and on any route outside `/editor/`.
- Writes: `PATCH .../editorial/`, `PUT .../relevance/{subject_id}/`, `POST` and `DELETE .../trials/`, each limited to the editor's site scope, recorded under the editor's name, and throttled at 60 per hour and 500 per day per editor and site.
- Reads: `GET .../resolve/?doi=` and `GET .../history/`, plus the existing article, trial, author, category, subject, sponsor and stats endpoints mounted again under `/editor/`. `visible_subject_ids` and `visible_org_ids` gained an editor branch: the token's one site, private or not.
- `GET /editor/tenants/`: every `mcp_enabled` site, private ones included, so the MCP server's editor mount can resolve a private site's host. `GET /tenants/` is unchanged.
- `?include=editorial` on `/editor/` routes adds `updated_at` and `updated_by` to each entry.

## What was not added

- No dedicated filter for relevance that is not yet reviewed (`is_relevant` null). `relevant=false` already returns those articles along with the ones marked not relevant.
- No new setting for the nginx rules or the compose environment. Those come with the deployment change.

## Upgrade steps

1. `python manage.py migrate` (a help-text change on `CustomSetting.mcp_enabled`).
2. Keep `GREGORY_MCP_SERVICE_KEY` set, as for introspection.
3. Block `/editor/` from the public internet at nginx.
