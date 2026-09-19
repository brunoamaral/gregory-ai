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

The server runs on its own dedicated host rather than as a `location` under the main API
domain — its own DNS record, its own certificate, its own nginx `server` block (see
[Deployment](#deployment)). No authentication is required (see [Auth](#auth) below):

```
https://gregory-ai.<your-domain>/mcp
```

Note there is no trailing slash — the app 307-redirects `/mcp/` to `/mcp`, and not every
client follows redirects on this transport.

### Claude Code

```bash
claude mcp add --transport http gregory https://gregory-ai.<your-domain>/mcp
```

### Claude Desktop

Add a remote MCP connector pointing at the same URL from Settings → Connectors.

---

## Tools

Ten task-shaped tools rather than a 1:1 mirror of every API endpoint — a large tool list
crowds context and degrades model tool selection.

| Tool | Backing endpoint | Notes |
|:---|:---|:---|
| `list_subjects` | `GET /subjects/` | Discovery entry point for subject IDs, which most article/trial filters need. |
| `search_articles` | `GET /articles/` | Boolean `search` plus subject, category, `category_modality`, journal, DOI, `relevant`, `ml_threshold`, `open_access`, `has_clinical_trials`, date range, `last_days`. Compact results — see [Payload shaping](#payload-shaping). |
| `get_article` | `GET /articles/{article_id}/` | Full record. |
| `search_trials` | `GET /trials/` | `search` (title, summary and scientific title) plus `recruitment_status_normalized`, `phase_normalized`, `study_type_normalized`, country, region, sponsor, `age_eligible`, `inclusion_gender_normalized`, registration dates, registry IDs (`nct`, `euct`, `eudract`, `ctis` — any common format, matched against the trial's registry record, secondary ids and sponsor study code, not just the exact stored value), `acronym`, `has_results`, `therapeutic_areas`. Results include `identifiers_normalized`, the canonical id list a registry-ID filter actually matched against. |
| `get_trial` | `GET /trials/{trial_id}/` | Full record, incl. eligibility text and results detail. |
| `search_authors` | `GET /authors/` | Name, ORCID, country, subject scope, `sort_by`/`order`. Fixed page size (10) — this endpoint doesn't support `page_size`. |
| `get_author` | `GET /authors/{id}/` (+ `/coauthors/`) | Co-authors optional (`include_coauthors`), off by default. |
| `list_categories` | `GET /categories/` | Fetches every page — a small, slow-changing taxonomy. Does not expose `ordering=authors_count_annotated`; that sort is expensive. |
| `list_sponsors` | `GET /sponsors/` | Paginated, not fetched in full — sponsors can number in the thousands. |
| `get_stats` | `GET /stats/`, `/articles/stats/`, `/trials/stats/` | `scope` selects which. |

### `search` syntax

`search_articles`'s and `search_trials`'s `search` parameter is boolean (same semantics
as the REST API's `?search=` — see [03-api-and-rss-feeds.md](03-api-and-rss-feeds.md)):

- space-separated terms are AND-ed
- uppercase `OR` for alternatives, `-term` / `NOT term` to exclude
- `"quoted phrases"` match contiguously, `(parentheses)` group

`search_articles`'s `search` reads title + summary. `search_trials`'s also reads the
scientific title — registries other than ClinicalTrials.gov (WHO ICTRP-sourced records)
commonly put a trial's real name and acronym there rather than in the public title or a
summary, since WHO-only trials rarely have one. Use `title=` / `summary=` instead to
match only one field.

### Zero-hit guidance

When `search_articles` or `search_trials` returns zero results, the response carries a
`guidance` key instead of just an empty list:

- `applied_filters` — the names (never the values) of every filter that was set on the
  call, sorted.
- `suggestions` — ranked, structural advice for what's most likely over-constraining the
  search — e.g. drop `relevant`/`ml_threshold`, widen a date range, or double-check a
  taxonomy/registry/sponsor ID. Checked in priority order per tool; the broadest ones
  (like `search`) rank last since a narrower filter is a more likely culprit.
- `fields_read` — which fields the *text-matching* filters actually searched: `search`
  (both tools), and for trials also `acronym` and the registry-ID filters (`nct`, `euct`,
  `eudract`, `ctis`) — the ones where a zero hit usually means the term sits in a field
  the filter doesn't read, e.g. `{"search": ["title", "summary", "scientific_title"]}`
  for a `search_trials` call. A registry-ID filter reports
  `["identifiers", "secondary_id", "ctg_secondary_ids"]`: all three feed the derived
  `identifiers_normalized` field these filters match against (see
  [trials-field-normalization.md](trials-field-normalization.md#field-identifiers--secondary_id--ctg_secondary_ids--identifiers_normalized-multi-input)).
  Every other applied filter is left out of the map; that's not a claim about which
  fields it reads.

Like `applied_filters`, none of this ever echoes a filter's value back — only filter
names and static advice text.

### Payload shaping

`search_articles` / `search_trials` / `search_authors` return a compact projection — id,
title, date, a few key fields, and a summary truncated to ~400 characters — not the full
record. A ten-result search with full abstracts and nested author lists is a very large
response, and most searches are followed by a `get_*` read of one or two records anyway.
`get_article` / `get_trial` / `get_author` return the full record, minus team data:
`get_article` and `get_trial` drop every `teams` and `team_id` key, at any depth
(`compact.strip_team_data`). A tenant sees its site's scope, and no tool takes a team, so
team plumbing is noise to the model. `team_categories` stays — despite the name, it holds
the record's category tags.

No tool exposes `all_results=true`. Bulk export is deliberately out of scope for this
server — see [Risks](#risks).

### Not-found errors

`get_article`, `get_trial`, and `get_author` turn a `404` from their endpoint into a clear
`ValueError` naming the record — e.g. `Article 123 was not found in this instance.` — rather
than the generic upstream error text. Django returns `404` both for a record that doesn't
exist and one outside this site's scope (see [Site scoping](#site-scoping-site_id)),
deliberately identical so existence isn't leaked; this server preserves that and never
implies the record might exist elsewhere. Any other error status still propagates unchanged.

---

## Resources

Slow-changing reference data, served with a 10-minute `ttlMs` cache hint so repeated
conversations stop refetching it. The list of resource URIs (`resources/list`) is `public`
scope — it never varies by site, so clients can share one cached copy. The content each one
reads (`resources/read`) is `private` scope instead: it varies by the resolved site (see
[Site scoping](#site-scoping-site_id) below), and a hint can't switch per call, so it has to
assume the conservative value in both cases rather than let a shared cache hand one site's
catalog to another's caller. The server also caches these two server-side, for the same 10
minutes (`gregory_mcp/cache.py`, `CATALOG_CACHE_TTL_MS` — the one constant both the hint and
the actual cache derive from), already keyed by site there —
`/categories/` costs about a second per request and takes 12 requests to read in full, so
this is the difference between a call that answers instantly and one that visibly stalls.
Per-replica, in-process, with single-flight (concurrent cold-cache callers await one fetch
rather than each starting their own). `list_subjects`/`list_categories` share the same cache
entries as these resources when called with equivalent filters — search tools are never
cached.

- `gregory://subjects` — every subject
- `gregory://categories` — every category

No sponsors resource: at 8,000+ rows / ~700 KB it isn't catalog-shaped the way
subjects and categories are — use the `list_sponsors` tool (search + pagination)
instead.

## Prompts

- `research_topic` — survey recent articles and trials on a topic
- `recent_trials_for_subject` — actively recruiting / recently registered trials for a subject
- `author_profile` — build a profile of a researcher from their articles and affiliation

Per-site prompts and reference documents can now be written on the Site admin
page (`CustomSetting.mcp_enabled`/`mcp_description`, and the `SiteMcpPrompt` /
`SiteMcpDocument` inlines next to it — see
[docs/02.1-database-tables-and-fields.md](02.1-database-tables-and-fields.md)).
The server doesn't read them yet, so clients still get the three built-ins
listed above; a later release publishes and serves the authored ones instead.

The three built-ins above also exist as editable `SiteMcpPrompt` rows for
brain-regeneration.com, seeded by a data migration. `seed_mcp_prompts --site
<id>` gives a new tenant the same starting set — it never overwrites a row
that already exists, so an edited prompt is never touched by a re-run.

Authored prompts and documents are published at
[`GET /tenants/`](03-api-and-rss-feeds.md#discovering-mcp-tenants) — the MCP
server does not read that endpoint yet, so this only changes what an admin
can write, not what a client gets back. A later release makes the server
fetch `/tenants/` (the way it already fetches `/sites/`) and serve each
tenant's own prompts and documents instead of the three built-ins.

---

## Instance targeting

The server proxies whatever instance `GREGORY_API_URL` names — one codebase serves
brain-regeneration.com, encefalites.pt, clinicaltrialupdates.com, or a local dev instance,
with no code change. See `mcp-server/gregory_mcp/config.py`.

### Tenant resolution (`?site_id=`)

Every upstream call carries a `site_id` query parameter — except `GET /tenants/` itself,
the unscoped call this resolution depends on; it can't require the thing it exists to
provide, and `test_tenants_call_itself_carries_no_site_id` enforces that it never gets
one, even transitively through the same client every other call goes through. Otherwise,
`site_id` is resolved once per request, from the resolved tenant
(`mcp-server/gregory_mcp/tenants.py`), in this order:

1. **`GREGORY_SITE_ID`** (env) — picks the `GET /tenants/` entry with that `site_id`. Set
   this for a single-tenant deployment that should always report as one tenant
   regardless of how it's reached. Unlike before Phase 3, this does not skip the network:
   the server still fetches `/tenants/` to get that tenant's full record (name, title,
   description, subjects, prompts, documents), not just permission to omit `site_id`.
2. **The inbound `Host` header** this server was reached on (nginx sets
   `proxy_set_header Host $host` — see [Deployment](#deployment)), matched against
   `GET /tenants/`'s domains the same way `django/gregory/site_resolution.py`'s
   `find_site_by_domain()` resolves a domain: exact match, then one subdomain level
   stripped — so `gregory-ai.brain-regeneration.com` resolves via
   `brain-regeneration.com`. `GET /tenants/` is cached in-process for 10 minutes
   (`CATALOG_CACHE_TTL_MS`), not fetched per call. A fetch failure serves the last
   successfully fetched directory rather than caching the outage; with nothing yet
   fetched, the directory counts as unavailable.
3. **Neither resolves, or the directory is unavailable** — the request is refused (below).

**A hostname that isn't a tenant is refused before any API call.** This is the one
behaviour change Phase 3 makes on purpose (decision 1,
`MCP-MULTI-TENANCY-PHASE-3-PLAN.md`): every request except `ping` gets the error "This
research assistant is not available at this address." when no tenant resolves — a
hostname pointed at the container that resolves to nothing, or a site that has never
ticked `mcp_enabled`, serves nothing rather than a generic, unscoped identity. This
includes the connection handshake itself (`initialize`/`server/discover`), so a client
pointed at a non-tenant hostname cannot connect at all, not just call tools. The refusal
is still logged as an `mcp_request` with `site_id: null` and `error_kind:
"protocol_error"`.

**Local development needs `GREGORY_SITE_ID` set to a tenant's id** (`3` in dev, for
brain-regeneration.com) — an in-memory or local client carries no usable Host header, so
without the override every request is refused.

A tenant's `site_id` is what scopes the server to its corpus. The server calls the API
anonymously, and the API scopes an anonymous caller to a single `api_public` site's
`scope_subjects`, resolved from `?site_id=` — see
[Resolving a site for an anonymous caller](03-api-and-rss-feeds.md#resolving-a-site-for-an-anonymous-caller).
On `/articles/` and `/trials/` the same parameter is also a subject-scope content filter.
So each tenant gets its own content, and a tool cannot return anything outside that
scope: the API never sends it. Because a non-tenant hostname is refused before any API
call now, the old "two or more `api_public` sites and nothing resolved" `400` from the
API no longer applies to this server — it refuses first.

Anonymous resolution only ever considers `api_public` sites, so a private site's
`site_id` never grants that site's scope, wherever it comes from. This server cannot
serve a private site today.

Tenant resolution is transport-level (`GregoryClient.get()` and `CatalogCache`'s cache
key, not a parameter on any tool) — no tool signature changes, and no LLM caller ever
chooses a `site_id` itself.

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
reaches the MCP server, so its own logs can't show a throttling event; `/mcp` logs to its
own file (`mcp-access.log`, `mcp_combined` format) with `mcp_name="..."` and
`mcp_method="..."` fields — the tool name and the JSON-RPC method (`tools/list`,
`tools/call`, …) — so 429s, and traffic shape generally, can be attributed from nginx's
side instead:

```bash
awk '$9 == 429' /var/log/nginx/mcp-access.log | grep -o 'mcp_name="[^"]*"' | sort | uniq -c | sort -rn
```

Both fields come from client-controlled request headers (`Mcp-Name` / `Mcp-Method`) —
treat an empty value as "unknown," not as "no tool" / "no method."

The dedicated host's `access_log` is set at the **server** level, not just inside
`location /mcp`, so this file also captures every request that misses `/mcp` and falls
through to the catch-all `location /` 404 — useful for seeing scanner traffic against a
single-purpose host, but it means any analysis should filter to `$7 ~ /^\/mcp/` first, or
it counts probe noise against `/` alongside real MCP requests.

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

Every `mcp_request` event on the telemetry stream carries a `site_id` field — the integer
resolved for that request (see [Site scoping](#site-scoping-site_id)), or `null` when
nothing resolved. The field is always present, even when `null`, so a log consumer can tell
"resolved to no site" apart from "this server predates site_id" — an absent key can't
distinguish the two. Other log lines on that stream (retry warnings and the like) never
carry it.

**`mcp_intent` events deliberately do not carry `site_id`.** The intent stream keeps the
model's full query text and must share no field with telemetry. Each tenant hostname gets
its own nginx server block, so nginx's IP logs are already split by tenant; a `site_id` on
an intent line would let it be matched to that tenant's IP log by timestamp — exactly the
join the two streams are kept apart to prevent. The intent file writer drops the field even
if a caller passes it. Per-tenant separation of intent text, if it is ever wanted, belongs
with the retention and access rules for private MCP servers.

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

The MCP server lives on its own dedicated host (`gregory-ai.<your-domain>` below), not as a
`location` under the API domain, so it needs a DNS record and a certificate of its own
before nginx can serve it — the old topology needed neither because it inherited the API
host's cert.

```bash
# point an A/AAAA record at the server first, then:
certbot certonly --webroot -d gregory-ai.<your-domain> \
  -w /var/www/_letsencrypt -n --agree-tos --force-renewal
```

Wait for DNS to propagate before running `certbot` — the HTTP-01 challenge fails against a
record that hasn't resolved yet.

Then bring the live nginx config in line with
[`nginx-example-configuration/nginx.conf`](../nginx-example-configuration/nginx.conf),
split across two files the way that example is commented:

- **`/etc/nginx/conf.d/mcp.conf`** — the `map $http_mcp_name $mcp_tool_bucket` block, both
  `limit_req_zone` directives, and the `mcp_combined` log format. These are `http`-context
  directives and can't live inside a `server` block; on Debian/Ubuntu `conf.d/` is included
  in the `http` context, so anything dropped there applies to every site.
- **`/etc/nginx/sites-enabled/gregory-ai.<your-domain>.conf`** — a dedicated `server` block
  on 443 with its own cert, its own `error_log`, `access_log … mcp_combined` at the
  **server** level (see [Auth](#auth) for what that means for reading the log), a
  `location /mcp` proxying to `127.0.0.1:8001`, a catch-all `location /` returning 404, and
  a companion port-80 server redirecting to HTTPS.

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
