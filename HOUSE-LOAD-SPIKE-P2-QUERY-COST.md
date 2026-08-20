# P2 — bound `/authors/` and cut per-request query cost

Split out of `HOUSE-LOAD-SPIKE-PLAN.md`, then rewritten after the P2 items were measured against the dev database (50,183 articles / 257,948 authors) and the endpoints were profiled end-to-end.

Status: items 1–3 implemented on `feature/authors-pagination-cap-and-query-cost` (2026-08-20). Scope revised — one item added, one dropped, one reprioritised.

---

## What changed since the first draft

The first draft of this document was written before P1 landed. Two things invalidate its numbers and its priorities:

1. `FlexiblePagination.max_offset = 10000` is now live (`django/api/pagination.py:35`). The `OFFSET 50290` query the draft was built around can no longer be issued through pagination, so every measurement was redone at `OFFSET 9990` — the deepest page still legal.
2. `GET /authors/` never got that cap. `AuthorsViewSet` (`django/api/views.py:3047`) sets no `pagination_class`, so it falls back to DRF's plain `PageNumberPagination`. Confirmed at runtime. It is the largest table in the API with the most expensive visibility filter, and it is still unbounded.

The revised order puts the `/authors/` cap first. It is the biggest remaining exposure, it is the smallest change, and it alters no query semantics at all.

---

## Where request time actually goes

End-to-end through Django, warm, DEBUG query timing:

| Request | Total | DB share | Dominant query |
|:--------|------:|---------:|:---------------|
| `/articles/?ordering=title&page=1` | 49 ms | 71% | `COUNT(*)` — 33 ms, 67% of the whole request |
| `/articles/?ordering=title&page=999` | 231 ms | 87% | list 156 ms, count 44 ms |
| `/authors/?page=1` | 107–136 ms | 93% | `COUNT(*)` — 93–121 ms, ~90% of the request |
| `/authors/?page=25000` | 492–525 ms | 96–98% | list 366–412 ms, count 86–117 ms |

Serialization is not the bottleneck anywhere. Query cost is the right lever, and on both endpoints the unbounded paginator `COUNT(*)` is the single largest line item on a shallow page.

---

## Item 1 — cap the offset on `GET /authors/`

Priority: do this first. Highest exposure, no semantic change, no visibility surface touched.

`/authors/?page=25000` is served today at ~500 ms. The table has 257,948 rows and the org-visibility `EXISTS` on it is the most expensive one in the API. The next crawler that sweeps `/authors/` the way `meta-externalagent` swept `/articles/` gets a five-times-worse per-request cost with no ceiling.

### Do not reuse `FlexiblePagination` here

This is the trap, and it is the reason this item needs writing down rather than just doing.

`AuthorsViewSet.list` (`django/api/views.py:3278`) already contains the branch `target = page if page is not None else list(queryset)`. That branch is unreachable today because plain `PageNumberPagination.paginate_queryset` never returns `None`. Attaching `FlexiblePagination` makes it reachable via `all_results=true`, and it would then:

1. materialise all 257,908 visible author rows, and
2. run `author_articles_count_subquery` — a correlated `COUNT(DISTINCT)` per author — across every one of them in a single `IN (...)` query.

Measured scaling of that annotation: 11 ms at n=10, 204 ms at n=1,000, 447 ms at n=5,000, converging to roughly 20–25 s at full table size on a fast laptop with a warm cache. On House that is minutes, past the 90 s nginx `proxy_read_timeout`, producing exactly the 499-then-keep-computing pattern that caused the original incident.

Swapping in `FlexiblePagination` would close a bounded hole by opening an unbounded one.

### The change

Extract the offset cap out of `FlexiblePagination` into a small mixin holding `max_offset` and the enforcement helper, then:

- `FlexiblePagination` keeps its current behaviour by using the mixin.
- A new cap-only class combines the mixin with plain `PageNumberPagination` — the offset ceiling and nothing else. No `all_results`, no `page_size` override, no response-envelope change.
- `AuthorsViewSet.pagination_class` points at the cap-only class.

Use the same `max_offset = 10000` as everywhere else. The escape hatch for legitimate bulk reads is `GET /authors/search/`, which already has `FlexiblePagination` and `all_results` and is team/subject-scoped, so it is never the full table.

### Risk

Low. The response envelope is unchanged (`count`/`next`/`previous`/`results`), no queryset semantics move, and the only behaviour change is a 400 past offset 10,000.

### Done when

- A request past offset 10,000 on `/authors/` returns 400 with the same message shape as `/articles/`.
- `all_results=true` on `/authors/` is still not a bypass — assert this explicitly in a test, since it is the thing most likely to be reintroduced by accident later.
- `docs/03-api-and-rss-feeds.md` (the `GET /authors/` row at line 180 lists no pagination params at all today) and `docs/authors-api.md` updated.
- `AuthorsViewSet` docstring updated; `python manage.py spectacular --file schema.yml --fail-on-warn` regenerated and committed.

---

## Item 2 — restructure the org `EXISTS` on Articles and Trials

The subquery's `articles U0` joins its own primary key to the outer row's primary key, so it always matches exactly one row: the outer row itself. Provably redundant, and verified equal on real data — 50,178 rows both ways.

Rewrite it to traverse the through table directly, **and resolve organisation ids to team ids in Python**. One call site: `OrgVisibilityMixin.get_queryset` (`django/api/views.py:368`).

### The team-id resolution is not optional

The first draft called it optional and its gain "modest". It is the load-bearing half. Measured on `articles`, warm, `SELECT *`:

| Form | `COUNT(*)` | list @ OFFSET 9990 | buffers | plan |
|:-----|-----------:|-------------------:|--------:|:-----|
| Current, self-join | 24 ms | 41 ms | 70,329 | `Gather Merge`, 2 workers |
| Through-table only | 13–16 ms | 30 ms | — | `Gather Merge`, 2 workers |
| Through-table, team ids resolved | 7 ms | 15 ms | 40,173 | serial |

Cold, the same pair measured 937 ms against 51 ms.

The through-table rewrite alone still goes parallel. Only the resolved form drops the planner's cost estimate below the parallel threshold. Since three Postgres backends per request — one client backend plus two parallel workers — was the mechanism behind load 12.6 on a 2-core box, this item is a concurrency fix and not merely a CPU one. That is a stronger claim than the first draft made for itself.

It also overlaps with P0.5 (`max_parallel_workers_per_gather = 0` on House). The two are complementary, and this one is the durable half: P0.5 is a manual per-server config edit with the same fragility the main plan already flags for the autovacuum `ALTER TABLE`, and it does not propagate to `gregory-001` / `gregory-002`. This lands the same effect in repo-tracked code.

### Resolve through `Team.all_objects`, not `Team.objects`

This is the one place this item can go wrong, and it is invisible in dev.

`Team.objects` is an `ActiveTeamManager` (`django/gregory/models.py:1670`) that filters `is_active=True`. The current SQL joins `gregory_team` raw with no such filter, so soft-deleted teams' articles **are** visible today. Resolving team ids via `Team.objects` would silently stop showing them — a change to the tenant visibility boundary, dressed up as a performance refactor.

All four teams in the dev database are active, so nothing in local testing would catch it.

Use `Team.all_objects` to preserve current behaviour. If the visibility of soft-deleted teams' content should change, that is a separate decision with its own PR.

### Other constraints

- Keep the rewrite path-aware. `_org_filter_distinct = True` is only the default (ArticleViewSet, TrialViewSet); lines 2893, 3450 and 3516 override it to `False` with plain FK paths. Derive the through model from `model.teams.through` rather than hardcoding `articles_teams`, and prefer the through model's field objects over building `f"{model._meta.model_name}_id"` strings.
- An empty resolved team-id list must yield zero rows. Verified: it does, fail-closed.
- Resolution costs one extra small query per request. Do not cache it — the table has four rows, and a stale cache on a tenant boundary fails open in the dangerous direction.

### The regression tests this needs do not exist yet

The first draft said this "must be covered by the existing visibility tests". It is not. There are 235 tests across `django/api/tests/test_visibility_*.py` and **zero** occurrences of `is_active=False` anywhere in `django/api/tests/`, and no team-less fixtures.

Both regressions are real in the data — 5 team-less articles and 40 team-less authors are correctly excluded today. Write the two missing cases before touching the mixin:

- an article whose team is `is_active=False` stays visible
- a team-less article stays invisible

### Done when

Visibility suite green, the two new tests present, and `SELECT COUNT(*)` matches the pre-change value on dev for both Articles and Trials.

---

## Item 3 — cache the paginator `COUNT(*)`

Worth more than the first draft implied, and it is the only thing that helps `/authors/`, where the count is ~90% of a shallow request and item 2 does not apply.

`DatabaseCache` measured at 0.16 ms get, 0.81 ms set, 0.21 ms miss. A 33 ms articles count becomes 0.16 ms; a 100 ms authors count becomes 0.16 ms.

Key derivation: reuse `CachedStatsActionMixin._stats_cache_key` (`django/api/views.py:381`). Its security constraint holds unchanged — the visible org ids and the normalised query string are both load-bearing, and dropping either serves one tenant's numbers to another caller.

Insertion point: DRF builds the paginator inline as `self.django_paginator_class(queryset, page_size)` inside `paginate_queryset`, with no window to intervene between construction and the first `count` access. Extend the existing `paginate_queryset` override rather than trying to swap `django_paginator_class`.

### Prerequisites, both currently unmet

**Raise `MAX_ENTRIES`.** `CACHES` (`django/admin/settings.py:168`) sets only `BACKEND` and `LOCATION`, so `MAX_ENTRIES` is the default 300 and `CULL_FREQUENCY` is 3. Django's db-cache cull runs a `COUNT(*)` on the cache table on every `set()` once over the limit, then deletes by `cache_key <` lexical order — with sha256 keys that is arbitrary eviction, not LRU. Left at 300 you get thrash: evict, miss, recompute the expensive count, set, evict.

**Trim the key.** `_stats_key_ignored_params` only excludes `page`, `page_size` and `all_results`. A count does not depend on `ordering` or `format`, but the key does, so a crawler's 14 orderings across 3 formats produce 42 cache entries for one number. Give the count its own ignored-params set; do not widen the stats one.

### Known limitations, accept or handle deliberately

- Stampede on expiry: N concurrent requests all recompute at the TTL boundary. Same pileup shape this whole plan exists to avoid, just narrower.
- A stale count changes `total_pages` and the `next` link. Combined with the offset cap, a stale-high count can advertise a page that then returns 400.

---

## Dropped from the original plan

**The `EXISTS` rewrite on `AuthorsViewSet.get_queryset` (`django/api/views.py:3101`).** Measured gain is 5–12%, not the 2x the first draft's table implied: count 65 ms against 53–67 ms — no reliable gain — and deep list 333 ms against 294 ms, with planner cost moving 50217 to 47774. The dominant cost is the HashAggregate over 428,022 `articles_authors` rows, which the rewrite does not touch.

It is also the riskiest of the three rewrites — a four-table join with a different shape from the mixin's — for the smallest return. Item 1 caps the endpoint and item 3 caches its count; that covers the same ground at a fraction of the risk.

The first draft's claim of "a consistent ~2x" across both endpoints does not hold. It holds for Articles only.

---

## Expected result

| Request | Now | After items 1–3 | |
|:--------|----:|----------------:|--:|
| `/articles/?page=1` | 49 ms | ~16 ms | 3x |
| `/articles/?page=999` | 231 ms | ~35 ms | ~6x, and 3 backends to 1 |
| `/authors/?page=1` | ~120 ms | ~25 ms | ~5x, almost entirely from the cache |
| `/authors/?page=25000` | ~500 ms | 400 | capped |

Suggested sequence: item 1 alone, then the two tests from item 2, then item 2, then the cache prerequisites and item 3.

---

## Adjacent finding, not part of this plan

While reproducing the authors count under a parallel plan, Postgres returned `could not resize shared memory segment: No space left on device` — the same class of shared-memory failure seen during the incident.

The running `db` container has `/dev/shm` at 64 MB, the Docker default, while `docker-compose.yaml:6` declares `shm_size: '256mb'`. The container predates the setting and was never recreated, so the declared value is not in effect. Worth checking whether House is in the same state. This is a container-recreation question, not a code change, and it belongs to whoever deploys House.

Related: `HOUSE-LOAD-SPIKE-PLAN.md`.
