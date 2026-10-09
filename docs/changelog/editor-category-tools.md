# Editor category tools

> This is a historical implementation note. For current behaviour, see [Editor access](../07-mcp-server.md#editor-access-mcpeditor) and [Editor routes](../03-api-and-rss-feeds.md#editor-routes-editor).

**Context**: Adding a category used to need the Django admin. Editors who evaluate a candidate topic over MCP can now create and maintain categories, and fix an article the terms miss, without leaving their LLM client.

---

## What was added

- Django: `POST /editor/categories/` and `PATCH /editor/categories/{id}/` on the same paths as the category reads, plus `PUT` and `DELETE /editor/articles/{article_id}/categories/{category_id}/` for hand assignments. Same rules as the other editor writes: the service credential, a re-checked grant with `can_edit`, `editing_as(user, "mcp")`, and the per-editor throttle.
- MCP: `create_category`, `update_category`, `assign_article_category` and `unassign_article_category` for editors with the edit scope. `get_article_history` now lists hand category assignments.
- History: `HistoricalTeamCategory` and `HistoricalArticleCategoryAssignment` (migration `gregory/0107`), with the editor and API-key attribution fields the other history tables carry.

## What changed

- `rebuild_categories` stores each category's sync state with a queryset update instead of `save()`, so the pipeline adds no history rows.
- `ApiKeyHistoryMixin` and `EditorHistoryMixin` moved above `TeamCategory` in `gregory/models.py`, unchanged, because `TeamCategory`'s history now uses them.

## Scope rules

- A category is in an editor's scope when one of its subjects is in the site's `scope_subjects`. Assigning an article needs only that.
- Changing a category needs every one of its subjects in scope, because the change shows on every site that lists any of them. A shared category is `403` and stays an admin task.
- New and changed subjects must belong to one team: the category's.

## Not changed

- A category's slug and team can't be changed over MCP, and categories can't be deleted.
- Trial category assignments stay automatic or admin-only.
