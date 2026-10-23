# `team_categories` renamed to `categories`

> This is a historical implementation note. For current behaviour, see the [API reference](../03-api-and-rss-feeds.md#available-endpoints).

**Context**: Articles, trials and patents linked to their categories through a field called `team_categories`. In the admin it showed up as "Team categories" (sidebar, form fields, the "By team categories" filter) right next to the real `teams` field, which made it look like a team setting. The field is now `categories` everywhere.

---

## What changed in the API

| Where | Before | After |
|:------|:-------|:------|
| `GET /articles/`, `/trials/`, `/patents/` and their detail and search endpoints | `team_categories: [...]` | `categories: [...]` |
| Streaming CSV export (`?format=csv`) | `team_categories` column | `categories` column |
| Patents and trials XLSX exports (`export_patents_xlsx`, `export_trials_xlsx` and the patents admin action) | `team_categories` column header | `categories` column header |
| MCP `get_article` / `get_trial` / `get_patent` | `team_categories` | `categories` |

The category objects inside the list are unchanged. These stay the same too:
- the `category_slug`, `category_id` and `category_modality` query parameters
- `GET /authors/by_team_category/`
- the `/categories/` endpoint

This is a breaking change for clients that read `team_categories`. The Brain Regeneration site was changed beforehand to read `categories` and fall back to `team_categories` ([Human-Singularity/brain-regeneration#65](https://github.com/Human-Singularity/brain-regeneration/pull/65)), so it keeps working whichever order the two are deployed in.

## Code and database

- `Articles.team_categories`, `Trials.team_categories` and `Patents.team_categories` are now `.categories`. `team.team_categories` is now `team.categories`.
- The admin labels the `TeamCategory` model "category" / "categories".
- The model class `TeamCategory`, the through models (`ArticleCategoryAssignment`, `TrialCategoryAssignment`, `PatentCategoryAssignment`) and all table names (`team_categories`, `articles_team_categories`, `trials_team_categories`, `patents_team_categories`) are unchanged.
- Migration `gregory/0121` only renames fields and changes Django metadata. `sqlmigrate` shows every operation as a no-op, so it runs no SQL.
