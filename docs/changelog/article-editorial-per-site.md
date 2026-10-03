# Article editorial content is per site

> This is a historical implementation note. For current behaviour, see [Editorial content](../03-api-and-rss-feeds.md#editorial-content).

**Context**: Editorial content (`takeaways`, `summary_plain_english`) belonged to an organisation, so two sites of one organisation always showed the same text for an article. It now belongs to a site (`ArticleSiteContent`). Trials are unchanged and still use `TrialOrgContent`.

---

## What changed in the API

| Endpoint | Before | After |
|:---------|:-------|:------|
| `GET /articles/?include=editorial` (and `/articles/{id}/`, both search endpoints) | `editorial[].organization` is `{"id", "name"}`; entries follow the caller's organisation(s) | `editorial[].site` is `{"id", "domain", "name"}`; entries follow the caller's site(s) |
| `GET /articles/?has_takeaways=` | Matches takeaways written by the caller's organisation | Matches takeaways written for the caller's site(s) |
| `POST /articles/edit/` | Writes to the key's organisation; response has no `site_id` | Writes to the key's site; response adds `site_id`. A key with no site, or a site owned by another organisation, gets `403` |

Which site the caller gets is still decided by who the caller is:

| Caller | Site(s) |
|:-------|:--------|
| API key | The key's site |
| Logged-in user | Every site owned by an organisation they belong to, one entry per site |
| Anonymous | The `api_public` site resolved from `?site_id=`, `Origin`, `Referer`, or the single public site |

Entry order is by site id. A site with no content for the article still gets an entry, with `null` fields.

## Data migration

Migration `gregory/0105` copies every `ArticleOrgContent` row to each site its organisation owns, keeping text and timestamps, so a site shows the same content after the upgrade as before. An organisation that owns no site has nothing to show it on and keeps its rows in `ArticleOrgContent` only. The migration applies to an empty database as a no-op and is safe to re-run. `ArticleOrgContent` is read-only from here on and is dropped in a later release.

## Migration guide

- Clients that read `editorial[0].takeaways` and `editorial[0].summary_plain_english` need no change.
- Clients that read `editorial[].organization` on articles must read `editorial[].site` instead. The site's `id`, `domain` and `name` replace the organisation's `id` and `name`.
- Clients that write through `POST /articles/edit/` need an API key bound to a site (**Admin → API → API access schemes → Site**). Keys that already had a site keep working.
- `import_articles_from_api --target-org` now writes a copy to every site of that organisation, and fails if the organisation owns none. `get_takeaways` fills each site of the article's organisations, and `--org-id` limits it to that organisation's sites.
- Newsletters carry the text of the site their List is sent for, falling back to the organisation's default site.
