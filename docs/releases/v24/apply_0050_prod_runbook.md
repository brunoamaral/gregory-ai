# Applying migration `0050_perf_add_indexes_drop_redundant_unique` on production

`0050` creates 19 indexes and drops 3 redundant hand-made ones. Run naively
(`manage.py migrate`) it does all of this inside **one atomic transaction with
plain `CREATE INDEX`**, which takes write-blocking locks on `articles`, `trials`,
`gregory_mlpredictions`, `gregory_historicalarticles` and `gregory_historicaltrials`
for the entire build. On prod the biggest tables are `gregory_historicalarticles`
(~375 MB table / 277k rows) and `articles` (~55 MB), so that's a multi-minute
write stall on the history table the pipeline writes to on every save.

**Recommended: Option A** (short maintenance window). It is the safest path —
Django builds *and names* the indexes itself, all inside one transaction, so
there is no `--fake`, no chance of an index-name mismatch, and the whole change
is atomic (it either fully applies or rolls back). The trade-off is a few minutes
of blocked writes; with these table sizes the build is expected to take roughly
1–3 minutes, not hours. Use Option B only if you truly cannot pause writes.

**Run `inspect_db_pre_0050.sql` first** and confirm: `mlpred_art_subj_date_idx`
absent, no INVALID indexes, `0050` not yet applied.

---

## Option A — short maintenance window (recommended)

The only writer that touches these tables on a schedule is the `pipeline` cron
(it writes `articles`/`trials` and, via simple-history, the history tables on
every save). Pause it for the duration, then let Django do everything:

1. **Make sure no pipeline is mid-run** and stop the cron from starting a new one.
   The cron uses `flock -n /tmp/pipeline`, so either wait for any running job to
   finish or comment out the `pipeline` line in crontab during the window:
   ```bash
   crontab -l                      # find the pipeline line
   crontab -e                      # comment out the `... manage.py pipeline` line
   ```

2. **Apply the migration** (builds 19 indexes, drops the 3 redundant manual ones,
   all atomically):
   ```bash
   docker exec gregory python manage.py migrate gregory 0050
   ```
   If anything goes wrong mid-build, the transaction rolls back and the DB is
   left exactly as before — safe to retry.

3. **Verify** Django and the DB agree:
   ```bash
   docker exec gregory python manage.py showmigrations gregory | tail -3   # 0050 = [X]
   docker exec gregory python manage.py migrate --check                    # no pending
   docker exec -i gregory python manage.py shell < docs/releases/v24/inspect_db_pre_0050.py
   ```
   Expect: all 19 new indexes present and valid; the three `idx_*` duplicates
   gone; `idx_trials_discovery_date` + both `idx_*_covering` still present;
   `unique_title_case_insensitive` untouched.

4. **Re-enable the `pipeline` cron** (uncomment the crontab line).

---

## Option B — zero-downtime (`CONCURRENTLY` + fake-apply)

> Only needed if you cannot tolerate a short write pause. This path uses
> `--fake`, which trusts that the hand-built indexes match `0050` exactly.
> **Do not copy the index names below by hand** — generate them from the
> deployed code so they are guaranteed correct:
> ```bash
> docker exec gregory python manage.py sqlmigrate gregory 0050
> ```
> Then add `CONCURRENTLY` to each `CREATE INDEX` / `DROP INDEX` it prints, run
> them one at a time (not in a transaction), and finish with the `--fake` step.

`CREATE INDEX CONCURRENTLY` builds without blocking writes but **cannot run inside
a transaction**, so it can't be expressed in a normal Django migration. Build the
indexes by hand with the *exact names* Django expects, then mark `0050` applied
with `--fake`.

> Index names below are Django's deterministic names for this model/Django
> version — they must match exactly or the `--fake` will leave Django's state
> out of sync. Run statements one at a time (not in a single transaction).
> `psql` without `--single-transaction` is fine; do **not** use `BEGIN`.

### B1. Create the new indexes (idempotent, non-blocking)

```sql
-- articles
CREATE INDEX CONCURRENTLY IF NOT EXISTS "articles_discovery_date_bf807f86" ON "articles" ("discovery_date");
CREATE INDEX CONCURRENTLY IF NOT EXISTS "articles_doi_34a64b16" ON "articles" ("doi");
CREATE INDEX CONCURRENTLY IF NOT EXISTS "articles_doi_34a64b16_like" ON "articles" ("doi" varchar_pattern_ops);
CREATE INDEX CONCURRENTLY IF NOT EXISTS "articles_published_date_c37bd844" ON "articles" ("published_date");
CREATE INDEX CONCURRENTLY IF NOT EXISTS "articles_retracted_17fa413a" ON "articles" ("retracted");

-- gregory_historicalarticles  (largest table — bulk of the build time)
CREATE INDEX CONCURRENTLY IF NOT EXISTS "gregory_historicalarticles_discovery_date_59dd1a54" ON "gregory_historicalarticles" ("discovery_date");
CREATE INDEX CONCURRENTLY IF NOT EXISTS "gregory_historicalarticles_doi_204905e0" ON "gregory_historicalarticles" ("doi");
CREATE INDEX CONCURRENTLY IF NOT EXISTS "gregory_historicalarticles_doi_204905e0_like" ON "gregory_historicalarticles" ("doi" varchar_pattern_ops);
CREATE INDEX CONCURRENTLY IF NOT EXISTS "gregory_historicalarticles_published_date_047f368b" ON "gregory_historicalarticles" ("published_date");
CREATE INDEX CONCURRENTLY IF NOT EXISTS "gregory_historicalarticles_retracted_08a2c03e" ON "gregory_historicalarticles" ("retracted");

-- gregory_historicaltrials
CREATE INDEX CONCURRENTLY IF NOT EXISTS "gregory_historicaltrials_last_updated_4cbdf43b" ON "gregory_historicaltrials" ("last_updated");
CREATE INDEX CONCURRENTLY IF NOT EXISTS "gregory_historicaltrials_published_date_ffc0f4e8" ON "gregory_historicaltrials" ("published_date");
CREATE INDEX CONCURRENTLY IF NOT EXISTS "gregory_historicaltrials_recruitment_status_5a4dcc14" ON "gregory_historicaltrials" ("recruitment_status");
CREATE INDEX CONCURRENTLY IF NOT EXISTS "gregory_historicaltrials_recruitment_status_5a4dcc14_like" ON "gregory_historicaltrials" ("recruitment_status" varchar_pattern_ops);

-- trials
CREATE INDEX CONCURRENTLY IF NOT EXISTS "trials_last_updated_87480860" ON "trials" ("last_updated");
CREATE INDEX CONCURRENTLY IF NOT EXISTS "trials_published_date_71c63f51" ON "trials" ("published_date");
CREATE INDEX CONCURRENTLY IF NOT EXISTS "trials_recruitment_status_0437da09" ON "trials" ("recruitment_status");
CREATE INDEX CONCURRENTLY IF NOT EXISTS "trials_recruitment_status_0437da09_like" ON "trials" ("recruitment_status" varchar_pattern_ops);

-- gregory_mlpredictions (composite)
CREATE INDEX CONCURRENTLY IF NOT EXISTS "mlpred_art_subj_date_idx" ON "gregory_mlpredictions" ("article_id", "subject_id", "created_date" DESC);
```

### B2. Drop the 3 redundant manual indexes (non-blocking)

```sql
DROP INDEX CONCURRENTLY IF EXISTS idx_articles_discovery_date;
DROP INDEX CONCURRENTLY IF EXISTS idx_articles_published_date;
DROP INDEX CONCURRENTLY IF EXISTS idx_trials_published_date;
```

### B3. Check for failed/INVALID index builds before faking

A `CONCURRENTLY` build that errored leaves an INVALID index. Re-run section 7 of
the inspection script (or):

```sql
SELECT i.relname AS invalid_index
FROM pg_index ix
JOIN pg_class i ON i.oid = ix.indexrelid
WHERE NOT ix.indisvalid;
```

If any show up: `DROP INDEX CONCURRENTLY <name>;` and re-create it before B4.

### B4. Mark the migration applied (no DDL runs)

```bash
docker exec gregory python manage.py migrate gregory 0050 --fake
```

### B5. Verify Django and DB agree

```bash
docker exec gregory python manage.py showmigrations gregory | tail -3   # 0050 = [X]
docker exec gregory python manage.py migrate --check                    # no pending
docker exec -i gregory python manage.py shell < docs/releases/v24/inspect_db_pre_0050.py
```

Expected end state: all 19 indexes present and valid, the three `idx_*`
duplicates gone, `idx_trials_discovery_date` + both `idx_*_covering` still
present, `unique_title_case_insensitive` untouched.

---

## Rollback

`0050` is reversible: `manage.py migrate gregory 0049` drops the new indexes and
recreates the three manual ones (via the migration's `reverse_sql`). If you
applied Option B with `--fake`, reverse it with `--fake` too and undo the DDL by
hand, since Django didn't run the forward DDL either.
