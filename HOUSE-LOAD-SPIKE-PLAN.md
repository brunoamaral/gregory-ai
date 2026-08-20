# House load spike — diagnosis and remediation plan

Status: not started. Analysis complete, no code written, nothing committed.

Server: House (142.93.160.186), 2 vCPU / 3.9 GB RAM. Django stack in Docker, Postgres 17 in container `db`, database `gregorybackoffice`.

---

## Summary

House sat at load average 12.6 on a 2-core box. An earlier investigation attributed this to stale visibility-map / dead-tuple accumulation on the `articles` table and applied a `VACUUM ANALYZE` plus per-table autovacuum tuning.

That diagnosis was wrong, and the applied fix did not work. The real cause is Meta's AI crawler (`meta-externalagent`) sweeping the API with deep offset pagination across every `ordering` permutation, each request triggering a full table sort plus two Postgres parallel workers.

This document records the evidence, grades the previously proposed fix, and lays out the remediation in priority order.

---

## Evidence

### The applied fix did not reduce load

Checked on House after the vacuum work had been applied:

```
00:08:45 up 8 days, load average: 10.58, 7.83, 6.55
```

Load is still high and the 1-minute average is above the 5- and 15-minute averages, meaning it is climbing, not recovering.

### The vacuum theory does not survive arithmetic

`Heap Fetches: 9562` on a 50k-row table is roughly 9,562 buffer accesses, which costs single-digit milliseconds against a warm cache. It cannot account for a 1.66s query. The observation was real; the causal attribution was not.

### Traffic analysis

From `/var/log/nginx/api.brain-regeneration.com.access.log`, a single 10-minute window (07/Aug/2026 00:00:03 to 00:10:12):

| Metric | Value |
|:-------|:------|
| Total requests | 661 |
| From `meta-externalagent` | 488 (74%) |
| Those ending in HTTP 499 | 290 of 488 (59%) |
| Highest `page=` observed | 25778 |
| Source IPs | spread across `57.141.20.0/24` |
| Sustained rate | ~49 req/min from the crawler alone |

Representative requests:

```
GET /trials/?format=csv&ordering=-last_updated&page=2996     499
GET /articles/?format=json&ordering=title&page=5030          499
GET /articles/?format=api&ordering=published_date&page=5029  499
```

The crawler is performing a cartesian sweep: every exposed `ordering` value (`±title`, `±published_date`, `±discovery_date`, `±trial_id`, `±ml_score`, `±last_updated`, `±recruiting_first`) across three `format` values across thousands of pages.

HTTP 499 means the client disconnected before nginx responded. Gunicorn and Django do not cancel on client disconnect, so each abandoned request continues running to completion for nobody. Nginx `proxy_read_timeout` is 90s, so nginx is not cutting these off either — the crawler gives up first and the work continues regardless. This is why load compounds rather than self-limiting.

### Query cost reproduced

The crawler's query shape, run against the local dev database (50,183 articles versus prod's 50,686):

```sql
SELECT * FROM articles
WHERE EXISTS (
  SELECT 1 FROM articles U0
  INNER JOIN articles_teams U1 ON (U0.article_id = U1.articles_id)
  INNER JOIN gregory_team U2 ON (U1.team_id = U2.id)
  WHERE U0.article_id = articles.article_id AND U2.organization_id IN (1, 6)
) ORDER BY title ASC LIMIT 10 OFFSET 50290;
```

Plan and timing:

```
Parallel Seq Scan on articles (7,637 buffers) + full sort
Workers Launched: 2
Execution Time: 469 ms
```

469 ms on a fast laptop with a warm cache. On a 2-core VM with a cold cache and a dozen competing backends, that becomes the 1.66s to 4.4s durations originally observed.

### Mechanism

Each such request occupies **three** Postgres backends: one client backend plus two parallel workers (`max_parallel_workers_per_gather = 2`). Four to six concurrent crawler requests therefore produce 12 to 18 running processes on 2 cores.

This matches the reported `htop` signature exactly — multiple `postgres: gregory gregorybackoffice ... SELECT` processes alongside several `parallel worker` processes — and matches load average 12.6.

### Aggravating configuration

- `https://api.brain-regeneration.com/robots.txt` returns **404**. The crawler has no back-off signal.
- Rate limiting exists only in `/etc/nginx/conf.d/mcp.conf`, scoped to the MCP host. `api.brain-regeneration.com` has none.
- `FlexiblePagination` (`django/api/pagination.py:19`) places no upper bound on `page`, so offsets are unbounded.

---

## Grading the previously proposed fix

### Item 1 — autovacuum tuning: keep, but reprioritise

Sound hygiene. The `ALTER TABLE` was applied manually on House and will not survive a redeploy or propagate to `gregory-001` / `gregory-002`, so the proposed migration is worth landing. It is simply not a load fix. Reclassified to P3 below.

### Item 2 — the EXISTS restructure: valid, and worth more than stated

The subquery's `articles U0` joins its own primary key to the outer row's primary key, so it always matches exactly one row: the outer row itself. It is provably redundant and can be dropped without changing semantics.

Measured on the local dev database:

| Query | Current form | Through-table | Through-table, team ids resolved |
|:------|:-------------|:--------------|:---------------------------------|
| Articles `COUNT(*)` | 39 ms | 19 ms | 16 ms |
| Authors `COUNT(*)` | 154 ms | — | 78 ms |
| Articles list page | 1.4 ms | — | 0.3 ms |

A consistent ~2x. Note that the paginated list page is already fast; the expense lives in the unbounded `COUNT(*)` and in the deep-offset sort. Worth doing, but P2 — it reduces the cost of each abusive request without reducing their number.

**Superseded.** Re-measured after P1 landed; see `HOUSE-LOAD-SPIKE-P2-QUERY-COST.md`. Two corrections: the ~2x holds for Articles only (Authors measured 5–12%, not 2x, and that rewrite is now dropped), and the restructure is worth more than "reduces per-request cost" — resolving org ids to team ids flips the Articles plan from `Gather Merge` to serial, cutting three Postgres backends per request to one.

The unique index `articles_teams(articles_id, team_id)` already exists, so the rewritten subquery becomes an index-only scan with no new DDL required.

### Item 3 — "the filter is unselective, consider dropping it": reject

Two independent reasons:

1. It is a tenant visibility boundary. Its selectivity on today's data says nothing about its purpose.
2. It is not a no-op. Verified against the dev database: **5 articles and 36 authors have no `articles_teams` row at all** and are correctly excluded by the current `EXISTS`. Removing or short-circuiting the filter would silently begin exposing them.

If a "visible orgs covers every org" fast path is ever added, it must still retain an `Exists(any team)` check to preserve current semantics.

---

## Remediation plan

Ordered by leverage. P0 and P0.5 require no deploy and are reversible.

### P0 — stop the crawler

nginx only, on House. Expected to bring load down within minutes.

1. **Serve `robots.txt` on `api.brain-regeneration.com`.** Currently 404. `Disallow` the paginated collection endpoints and name the AI crawlers explicitly (`meta-externalagent`, `GPTBot`, `ClaudeBot`, `Bytespider`, `Amazonbot`). Compliance is voluntary and adoption is slow, so this is necessary but not sufficient.
2. **Rate-limit by user agent** in `/etc/nginx/sites-enabled/api.brain-regeneration.com.conf`. Must key on user agent, not `$binary_remote_addr` — the traffic sprays across a whole `/24`, so per-IP zones will not bite. The `map` plus `limit_req_zone` pattern in `/etc/nginx/conf.d/mcp.conf` is the template to copy. Return 429.
3. **Consider returning 403 to `meta-externalagent` outright.** It contributes nothing and is consuming 74% of API capacity. This is the immediate-relief lever if 1 and 2 prove too slow.

### P0.5 — disable parallel query on House

Set `max_parallel_workers_per_gather = 0` in the Postgres config. Takes effect on reload, no deploy.

On a 2-core box under concurrency, parallel query triples the process count per request and buys nothing — the box is already saturated, so there is no idle core for a worker to use. This is the direct cause of the `parallel worker` pileup in `htop`. Expected to cut the process count per request from 3 to 1.

Verify afterwards with `uptime` and by confirming `parallel worker` rows disappear from `pg_stat_activity`.

### P1 — bound the worst case in code

4. **Cap deep offsets in `FlexiblePagination`** (`django/api/pagination.py:19`). Reject requests where `page * page_size` exceeds roughly 10,000, returning HTTP 400 with a message pointing at `all_results=true` and the CSV export. This makes the endpoint structurally immune regardless of caller — the durable fix, since the next crawler will not read `robots.txt` either. Needs tests plus a docs update in `docs/03-api-and-rss-feeds.md` and a schema regeneration.
5. **Audit exposed `ordering` fields for index coverage.** ~~`ordering=title` has no supporting index~~ — **incorrect**: `articles_title_ed7ced3d btree (title)` exists, as do indexes for `published_date`, `discovery_date`, `last_updated` and `ml_score`. The planner discards them at deep offsets because a seq scan plus sort wins over reading 50k index entries with heap fetches; the offset cap in item 4 is what addresses that, not new DDL. Do not add a duplicate title index. Still worth auditing the allowlist for orderings with no index at all.

### P2 — reduce per-request cost (deferred)

Analysed and rewritten in `HOUSE-LOAD-SPIKE-P2-QUERY-COST.md`. **One part of it is no longer deferred:** `GET /authors/` never received the P1 offset cap — `AuthorsViewSet` sets no `pagination_class` and falls back to DRF's plain `PageNumberPagination`. It is the largest table in the API with the most expensive visibility filter, and `?page=25000` is served today at ~500 ms. That cap is now the first item in the P2 document and should be treated as P1 work.

The rest — the `EXISTS` restructure and the count cache — stays lower priority, but note it is not purely a per-request-cost item: the restructure also removes the parallel-worker fan-out in code, which P0.5 only fixes via a manual per-server Postgres setting that does not propagate to `gregory-001` / `gregory-002`.

### P3 — land the autovacuum change properly

8. **Add the migration** as originally drafted, so the setting is tracked in the repo and propagates to `gregory-001` and `gregory-002`. It will be a no-op on House, where the value is already set manually.

	```bash
	python manage.py makemigrations gregory --empty --name articles_autovacuum_tuning
	```

	```python
	operations = [
		migrations.RunSQL(
			sql="ALTER TABLE articles SET (autovacuum_vacuum_scale_factor = 0.05, autovacuum_analyze_scale_factor = 0.05);",
			reverse_sql="ALTER TABLE articles RESET (autovacuum_vacuum_scale_factor, autovacuum_analyze_scale_factor);",
		),
	]
	```

	Two caveats:
	- `ALTER TABLE ... SET (autovacuum_*)` is Postgres-specific. Either guard it on the connection vendor or accept that the test suite must run on Postgres.
	- Worth extending the same treatment to `trials` and `articles_authors`, which see comparable update churn.

	The latest migration at time of writing is `0093_historicaltrials_inclusion_age_max_years_and_more`. Do not renumber or rewrite any existing migration.

---

## Verification

After P0 and P0.5:

```bash
ssh House 'uptime'
```

```bash
ssh House 'docker exec db psql -U gregory -d gregorybackoffice -c "SELECT backend_type, count(*) FROM pg_stat_activity WHERE state = '"'"'active'"'"' GROUP BY backend_type;"'
```

Expect load average trending toward single digits and zero `parallel worker` rows.

Crawler share of traffic, after the nginx changes have been live for a few minutes:

```bash
ssh House 'grep -c meta-externalagent /var/log/nginx/api.brain-regeneration.com.access.log'
```

Vacuum state, for the P3 item only:

```bash
ssh House 'docker exec db psql -U gregory -d gregorybackoffice -c "SELECT relname, last_autovacuum, n_dead_tup, n_live_tup FROM pg_stat_user_tables WHERE relname = '"'"'articles'"'"';"'
```

---

## Open questions

- Should the deep-offset cap in P1 apply uniformly, or be relaxed for authenticated / API-key callers who may have a legitimate bulk-read need? The `all_results=true` path already exists for that purpose, but it has its own known cost profile (see `CSV-STREAMING-PLAN.md`).

**Resolved:** no reason to keep serving `meta-externalagent` — blocked outright (P0 item 3) via the `$blocked_ua` map + `if` in `nginx-example-configuration/nginx.conf`. Items 1 (robots.txt) and 2 (UA rate limiting) remain undecided for future crawlers, not applied yet.

**Resolved:** `format=csv` is intentionally left alone — it's the mechanism the frontend uses to let non-technical users download data, and is not to be gated or restricted as part of this plan. Any deep-offset cap in P1 must not break that path.
