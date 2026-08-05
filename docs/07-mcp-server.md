# MCP server

> Audience: developers connecting an LLM client to GregoryAI, or maintaining `mcp-server/`.

`mcp-server/` is a read-only [MCP](https://modelcontextprotocol.io/) server that lets LLM
clients (Claude Code, Claude Desktop, etc.) query the GregoryAI REST API — articles,
clinical trials, authors, subjects, categories, and sponsors — without hand-writing HTTP
calls. It is a thin, stateless proxy: no database access, no ORM, no write path. Every
request it can issue is a `GET`.

It runs as its own container (`gregory-mcp` in `docker-compose.yaml`), independent of the
Django app, and talks to a GregoryAI instance over plain HTTP exactly like any other API
client — see [03-api-and-rss-feeds.md](03-api-and-rss-feeds.md).

---

## Connecting

The server exposes a single Streamable HTTP endpoint, no authentication required (see
[Auth](#auth) below):

```
https://api.<your-domain>/mcp/
```

### Claude Code

```bash
claude mcp add --transport http gregory https://api.<your-domain>/mcp/
```

### Claude Desktop

Add a remote MCP connector pointing at the same URL from Settings → Connectors.

---

## Tools

Ten task-shaped tools rather than a 1:1 mirror of every API endpoint — a large tool list
crowds context and degrades model tool selection.

| Tool | Backing endpoint | Notes |
|:---|:---|:---|
| `list_subjects` | `GET /subjects/` | Discovery entry point. Every row carries `team_id`, which most other tools' filters need. |
| `search_articles` | `GET /articles/` | Boolean `search` plus subject, category, `category_modality`, journal, DOI, `relevant`, `ml_threshold`, `open_access`, `has_clinical_trials`, date range, `last_days`. Compact results — see [Payload shaping](#payload-shaping). |
| `get_article` | `GET /articles/{article_id}/` | Full record. |
| `search_trials` | `GET /trials/` | `search` plus `recruitment_status_normalized`, `phase_normalized`, `study_type_normalized`, country, region, sponsor, `age_eligible`, `inclusion_gender_normalized`, registration dates, registry IDs (`nct`, `euct`, `eudract`, `ctis`), `acronym`, `has_results`, `therapeutic_areas`. |
| `get_trial` | `GET /trials/{trial_id}/` | Full record, incl. eligibility text and results detail. |
| `search_authors` | `GET /authors/` | Name, ORCID, country, team/subject scope, `sort_by`/`order`. Fixed page size (10) — this endpoint doesn't support `page_size`. |
| `get_author` | `GET /authors/{id}/` (+ `/coauthors/`) | Co-authors optional (`include_coauthors`), off by default. |
| `list_categories` | `GET /categories/` | Fetches every page — a small, slow-changing taxonomy. Does not expose `ordering=authors_count_annotated`; that sort is expensive. |
| `list_sponsors` | `GET /sponsors/` | Paginated, not fetched in full — sponsors can number in the thousands. |
| `get_stats` | `GET /stats/`, `/articles/stats/`, `/trials/stats/` | `scope` selects which. |

### `search` syntax

`search_articles`'s and `search_trials`'s `search` parameter is boolean over title +
summary (same semantics as the REST API's `?search=` — see
[03-api-and-rss-feeds.md](03-api-and-rss-feeds.md)):

- space-separated terms are AND-ed
- uppercase `OR` for alternatives, `-term` / `NOT term` to exclude
- `"quoted phrases"` match contiguously, `(parentheses)` group

Use `title=` / `summary=` instead to match only one field.

### Payload shaping

`search_articles` / `search_trials` / `search_authors` return a compact projection — id,
title, date, a few key fields, and a summary truncated to ~400 characters — not the full
record. A ten-result search with full abstracts and nested author lists is a very large
response, and most searches are followed by a `get_*` read of one or two records anyway.
`get_article` / `get_trial` / `get_author` return the untouched record.

No tool exposes `all_results=true`. Bulk export is deliberately out of scope for this
server — see [Risks](#risks).

---

## Resources

Slow-changing reference data, served with a 10-minute `ttlMs` cache hint (public scope, so
clients can share one cached copy) so repeated conversations stop refetching it. The server
also caches these two server-side, for the same 10 minutes (`gregory_mcp/cache.py`,
`CATALOG_CACHE_TTL_MS` — the one constant both the hint and the actual cache derive from) —
`/categories/` costs about a second per request and takes 12 requests to read in full, so
this is the difference between a call that answers instantly and one that visibly stalls.
Per-replica, in-process, with single-flight (concurrent cold-cache callers await one fetch
rather than each starting their own). `list_subjects`/`list_categories` share the same cache
entries as these resources when called with equivalent filters — search tools are never
cached.

- `gregory://subjects` — every subject, with `team_id`
- `gregory://categories` — every category

No sponsors resource: at 8,000+ rows / ~700 KB it isn't catalog-shaped the way
subjects and categories are — use the `list_sponsors` tool (search + pagination)
instead.

## Prompts

- `research_topic` — survey recent articles and trials on a topic
- `recent_trials_for_subject` — actively recruiting / recently registered trials for a subject
- `author_profile` — build a profile of a researcher from their articles and affiliation

---

## Instance targeting

The server proxies whatever instance `GREGORY_API_URL` names — one codebase serves
brain-regeneration.com, encefalites.pt, clinicaltrialupdates.com, or a local dev instance,
with no code change. See `mcp-server/gregory_mcp/config.py`.

## Auth

None. The server exposes exactly what an anonymous API caller already sees — the same
public organisations any unauthenticated `GET` against the REST API returns. Nothing new
is leaked, but the endpoint is unauthenticated, so it's rate-limited at the nginx layer,
per (client address, tool name) — the tool name coming from the client-controlled
`Mcp-Name` request header, whitelisted to the ten known names so a caller can't dodge the
limit by inventing new header values. Every tool shares one flat rate rather than a
stricter one for the search/stats tools — nginx's `limit_req` has no notion of a
per-request "cost", and doing that correctly needs routing each tool class to its own
internal location, which is more machinery than this example config carries; see the
comment above `limit_req_zone` in `nginx-example-configuration/nginx.conf` for what was
tried and why it was reverted. A flat per-client cap backstops the per-tool buckets.

## Risks

**Unauthenticated endpoint.** No data-leak risk, but anyone who learns the URL can drive
query load against Django. Per-tool `limit_req` in nginx is the mitigation, not optional.

**Bulk export stays out.** `all_results=true` on `/articles/` is a known failure mode
(~98s, very large responses — see [csv-export.md](csv-export.md)). No tool here exposes it.

---

## Development

```bash
cd mcp-server
pip install -e ".[dev]"
GREGORY_API_URL=http://localhost:8000 python -m gregory_mcp   # run locally
pytest                                                         # unit + schema-contract tests
```

`tests/test_schema_contract.py` checks the contract in both directions against
`django/schema.yml` (see [Stage 1](03-api-and-rss-feeds.md#openapi-schema)) — regenerate
that file (`python manage.py spectacular --file schema.yml --fail-on-warn`) before running
the suite after changing a filter this server depends on:
- every filter a tool passes must be a real, declared parameter (catches a renamed or
  removed filter);
- every parameter a tool *doesn't* expose must be explicitly reviewed in
  `KNOWN_UNEXPOSED_PARAMS` (catches a new filter landing on the Django side that nobody
  added to the matching tool — this is how `search_authors`'s team/subject scope went
  missing the first time).
