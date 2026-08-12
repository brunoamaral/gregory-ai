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

Throttled requests return `429` (`limit_req_status`), not nginx's default `503` — `503`
reads as "server broken" rather than "you're going too fast." A rejected request never
reaches the MCP server, so its own logs can't show a throttling event; `/mcp/` logs to
its own file (`mcp-access.log`, `mcp_combined` format) with an `mcp_name="..."` field so
429s can be attributed per tool from nginx's side instead:

```bash
awk '$9 == 429' /var/log/nginx/mcp-access.log | grep -o 'mcp_name="[^"]*"' | sort | uniq -c | sort -rn
```

Whether 30 r/m per (client, tool) and 120 r/m per client are the right numbers is an open
question — tune them from what this log actually shows, not speculatively.

### Telemetry and intent logs on disk

`gregory_mcp`'s own two log streams (`mcp_request` telemetry on stdout, `mcp_intent` on
stderr — see `mcp-server/gregory_mcp/logging_config.py`) are, by default, only readable
via `docker logs`, with no rotation and retention entirely at the mercy of Docker's log
driver. `docker-compose.yaml` sets `MCP_LOG_DIR=/var/log/gregory-mcp` for the
`gregory-mcp` service (bind-mounted to `./mcp-server/logs/` on the host), which
additionally writes each stream to its own file — `telemetry.log` and `intent.log` —
rotated in-app at 10 MB × 5 backups, no `logrotate` needed. `docker logs` keeps showing
the same events either way; the files are additive, not a replacement. `MCP_LOG_DIR` is
only unset when running the server directly (`python -m gregory_mcp`, e.g. in tests),
which keeps that path stdout/stderr-only.

The container runs as non-root `appuser` (UID 1000, see `mcp-server/Dockerfile`), so
`./mcp-server/logs/` must exist and be writable by that UID before the container starts
— `mkdir -p mcp-server/logs && chown 1000:1000 mcp-server/logs` on the host, or Docker
will create it as root on first `up` and the container won't be able to write to it. If
the directory isn't writable, `configure_logging()` catches the error, logs one warning,
and falls back to stdout/stderr-only rather than crashing the server — so a permissions
mistake here silently loses the on-disk mirror rather than taking the server down.

Audit directly from the files instead of `docker logs` once deployed — use `tail -F`
(capital F), not `-f`: rotation renames the current file out from under a plain `-f`,
which then stops following:

```bash
tail -F mcp-server/logs/telemetry.log | jq
tail -F mcp-server/logs/intent.log | jq
```

This ships persistence and rotation only. The 90-day `intent` hard-delete retention
policy from `MCP-TELEMETRY-PLAN.md`'s Phase 6 is still separate, not-yet-built work.

## Deployment

The image is `amaralbruno/gregory-mcp`, built and pushed by
[`.github/workflows/build-push.yaml`](../.github/workflows/build-push.yaml) alongside
`amaralbruno/gregory-ai`. Both build from the same matrix and deploy in the same step, so
the MCP server is never left proxying an API build it wasn't tested against — which matters
because django-filter silently ignores unknown query params, so a stale MCP server returns
wrong results rather than an error.

The gate is the `Tests` workflow: `pytest` (Django), `mcp-tests` (MCP), and `lint` all have
to pass before either image is built.

### One-time setup on the server

The deploy step **does not `git pull`** — it only pulls images and restarts containers. So
the `gregory-mcp` service definition has to reach `/home/gregory/gregory-ai` once, by hand,
before the first automated deploy will do anything:

```bash
cd /home/gregory/gregory-ai
git pull                                  # brings in the service's `image:` key
docker compose pull gregory-mcp
docker compose up -d gregory-mcp
docker compose ps gregory-mcp             # expect "healthy" within ~40s
```

Then add the `/mcp` block to the live nginx config. The version in
[`nginx-example-configuration/nginx.conf`](../nginx-example-configuration/nginx.conf) is an
example, not the deployed file — it needs copying across, including the two `limit_req_zone`
directives and the `map $http_mcp_name $mcp_tool_bucket` block, which live in the `http`
context rather than the `server` block (on Debian/Ubuntu, `conf.d/` is included there).

Two things that are easy to get wrong:

- **`location /mcp`, no trailing slash, and `proxy_pass http://127.0.0.1:8001;` with no
  path.** The app is mounted at `/mcp` and 307-redirects `/mcp/` → `/mcp`. A `location
  /mcp/` block leaves that redirect target unmatched, so it falls through to `location /`,
  reaches Django, and 404s. The prefix match catches both spellings and the pathless
  `proxy_pass` preserves the URI.
- **`http2` syntax depends on the nginx version.** Below 1.25.1 it is part of the listen
  line (`listen 443 ssl http2;`, which is what the example config and House both use); from
  1.25.1 it is a separate `http2 on;` directive and the old form warns. Using the wrong one
  fails the config test with `unknown directive "http2"`.

```bash
nginx -t && systemctl reload nginx
```

### Verifying

```bash
curl -s -o /dev/null --max-time 5 -H 'Accept: application/json' -w '%{http_code}\n' https://<host>/mcp
```

Expect **`406`**. A `404` means the location block isn't active; a `502` means nginx is up
but the container isn't reachable on `127.0.0.1:8001`; `000` usually means TLS isn't
serving that hostname yet.

> **Never probe `/mcp` with curl's default `Accept: */*`.** That returns `200` and then an
> open SSE stream — the command hangs until you kill it. It looks like a failure and is
> actually the server working. Same reason not to add `-L` to a `/mcp/` request: following
> the 307 lands on the streaming path. See `mcp-server/healthcheck.py` for the full matrix
> of what each method and `Accept` combination returns.

A status code only proves something is listening. This exercises the real protocol —
note the `_meta` envelope is **mandatory** under the stateless `2026-07-28` core, since
every request has to be self-describing; omit `protocolVersion` or `clientCapabilities` and
the server returns `-32602`:

```bash
curl -s --max-time 10 -X POST https://<host>/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H 'MCP-Protocol-Version: 2026-07-28' \
  -H 'Mcp-Method: tools/list' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{"_meta":{
       "io.modelcontextprotocol/protocolVersion":"2026-07-28",
       "io.modelcontextprotocol/clientInfo":{"name":"curl","version":"1.0"},
       "io.modelcontextprotocol/clientCapabilities":{}}}}'
```

Expect a JSON-RPC result listing all ten tools. This is the check that matters: it is a
POST, which is what real clients use, and it is what proves the routing above is right.

To confirm rate limiting is applied and returns `429` rather than nginx's default `503`,
send the same request ~16 times in a row with `-H 'Mcp-Name: list_subjects'` — the first
dozen should return `200` and the rest `429`.

Client config URL, **without the trailing slash**: `https://<host>/mcp`

### After that

Every push to `main` that passes `Tests` rebuilds and redeploys both containers with no
manual step. The one exception is another change to a *service definition* in
`docker-compose.yaml` — those still need the checkout on the server updating first.

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
