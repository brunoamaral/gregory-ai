# P2 — reduce per-request query cost (deferred for further analysis)

Split out of `HOUSE-LOAD-SPIKE-PLAN.md`. This is not urgent — P0/P0.5/P1 remove the actual load/failure cause (the crawler and its parallel-worker fan-out). This item only reduces the cost of each legitimate deep-offset request; it does not reduce request volume.

Status: not started, deferred.

---

## Background

Measured against local dev database (50,183 articles):

```sql
SELECT * FROM articles
WHERE EXISTS (
  SELECT 1 FROM articles U0
  INNER JOIN articles_teams U1 ON (U0.article_id = U1.articles_id)
  INNER JOIN gregory_team U2 ON (U1.team_id = U2.id)
  WHERE U0.article_id = articles.article_id AND U2.organization_id IN (1, 6)
) ORDER BY title ASC LIMIT 10 OFFSET 50290;
```

The subquery's `articles U0` joins its own primary key to the outer row's primary key, so it always matches exactly one row: the outer row itself. It is provably redundant and can be dropped without changing semantics.

| Query | Current form | Through-table | Through-table, team ids resolved |
|:------|:-------------|:--------------|:---------------------------------|
| Articles `COUNT(*)` | 39 ms | 19 ms | 16 ms |
| Authors `COUNT(*)` | 154 ms | — | 78 ms |
| Articles list page | 1.4 ms | — | 0.3 ms |

A consistent ~2x. The paginated list page is already fast; the expense lives in the unbounded `COUNT(*)` and in the deep-offset sort.

The unique index `articles_teams(articles_id, team_id)` already exists, so the rewritten subquery becomes an index-only scan with no new DDL required.

---

## Proposed work

6. **Restructure the org `EXISTS`** to traverse `articles_teams` directly, dropping the redundant self-join. Two call sites:
	- `OrgVisibilityMixin.get_queryset` — `django/api/views.py:368`
	- the hand-rolled equivalent in `AuthorsViewSet.get_queryset` — `django/api/views.py:3101`

	Optionally resolve organization ids to team ids in Python first (the `gregory_team` table is tiny), which lets Postgres use the composite unique index with an index condition. That adds one small query per request unless cached; the measured gain over the plain through-table form is modest for counts but meaningful for list queries.

	Must be covered by the existing visibility tests (`django/api/tests/test_visibility_*.py`) — 5 team-less articles and 36 team-less authors are the regression to watch (see rejected "drop the filter" idea in the main plan — same underlying fact).

7. **Cache the paginator `COUNT(*)`** keyed on (visible org set, filter set). The key-derivation machinery in `CachedStatsActionMixin` (`django/api/views.py:381`) already does exactly this hashing and can be reused. Note the security constraint documented there: the org ids and the query string are both load-bearing in the key.

Related: `HOUSE-LOAD-SPIKE-PLAN.md`.
