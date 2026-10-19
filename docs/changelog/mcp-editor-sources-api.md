# Editor sources API

> This is a historical implementation note. For current behaviour, see [Source routes](../03-api-and-rss-feeds.md#source-routes).

**Context**: Trusted editors are going to add sources (RSS feeds, PubMed and ClinicalTrials.gov searches) from their MCP client. This change adds the Django side: three `/editor/sources/` routes that need the `can_manage_sources` grant flag. The MCP tools that call them come in a later change.

---

## What changed

- `GET /editor/sources/` lists the sources of the site's subjects; `POST /editor/sources/preview/` says what a source would bring in and saves nothing; `POST /editor/sources/` adds it. All three are `403` without the flag, `GET` included.
- `gregory/source_checks.py` holds the admin form's source checks (a feed needs a link, a ClinicalTrials.gov source needs a search field, a PubMed search has no date clause and balanced brackets, PubMed accepts the search). The admin form calls it; its behaviour and messages are unchanged. The editor API runs the same checks.
- RSS addresses are fetched only from public hosts: the name must resolve to public addresses only, redirects are followed by hand (at most 3) and checked like the first address, with a 10-second timeout and a 5 MB limit. feedparser gets the bytes.
- Previews: entries in the feed, PubMed's 30-day count with its reading of the search, ClinicalTrials.gov's total and 30-day counts, with up to 5 samples.
- Duplicates (active source, same team and type, same address or search) are `409`, naming the existing source. Other teams' sources are never revealed.
- A `pubmed` or `ctgov` source added this way gets `last_successful_fetch_at` set at creation, so its first run is incremental (no backfill). RSS sources keep no anchor.
- The ClinicalTrials.gov search the importer builds moved to `gregory/utils/ctgov_search.py`, shared with the preview. The importer builds the same parameters as before.
- Creates and previews have their own throttles, per editor and site: `MCP_SOURCE_CREATES_PER_DAY` (30) and `MCP_SOURCE_PREVIEWS_PER_HOUR` (30).

## What did not change

- No MCP tool calls the routes yet, and the MCP server does not yet ask for the `sources:add` scope.
- There is no volume limit and no admin digest line for sources added this way.
- No migration.

## Deploying

Optionally set `MCP_SOURCE_CREATES_PER_DAY` and `MCP_SOURCE_PREVIEWS_PER_HOUR` in `.env`; the defaults are 30 and 30.
