-- ============================================================================
-- Pre-flight DB inspection for migration
--   0050_perf_add_indexes_drop_redundant_unique
--
-- READ-ONLY. Makes no changes. Safe to run on prod or local dev.
--
-- Purpose: confirm the database has no surprises before applying migration 0050
--   (a) indexes the migration wants to create do not already exist
--   (b) the unique constraint on trials.title (which 0050 drops) really exists
--   (c) table sizes / row counts so you can judge lock duration
--   (d) no left-over INVALID indexes from prior CONCURRENTLY attempts
--
-- Run with psql:
--   psql "$DATABASE_URL" -f inspect_db_pre_0050.sql
-- or:
--   psql -h <host> -U <user> -d <db> -f inspect_db_pre_0050.sql
--
-- Tables touched by 0050:
--   articles, trials, gregory_mlpredictions,
--   gregory_historicalarticles, gregory_historicaltrials
-- ============================================================================

\echo ''
\echo '================================================================'
\echo '1) ALL EXISTING INDEXES on the affected tables'
\echo '   (look here for any prior hand-rolled optimisation indexes)'
\echo '================================================================'
SELECT
    t.relname              AS table_name,
    i.relname              AS index_name,
    pg_size_pretty(pg_relation_size(i.oid)) AS index_size,
    ix.indisunique         AS is_unique,
    ix.indisvalid          AS is_valid,
    pg_get_indexdef(i.oid) AS definition
FROM pg_class t
JOIN pg_index ix     ON t.oid = ix.indrelid
JOIN pg_class i      ON i.oid = ix.indexrelid
JOIN pg_namespace n  ON n.oid = t.relnamespace
WHERE n.nspname = 'public'
  AND t.relname IN ('articles','trials','gregory_mlpredictions',
                    'gregory_historicalarticles','gregory_historicaltrials')
ORDER BY t.relname, i.relname;

\echo ''
\echo '================================================================'
\echo '2) DO THE TARGET COLUMNS ALREADY HAVE A LEADING-COLUMN INDEX?'
\echo '   If a row shows already_indexed = t, migration 0050 will add a'
\echo '   second (redundant) index on that column. Not an error, but'
\echo '   worth knowing before you create duplicates.'
\echo '================================================================'
WITH targets(table_name, column_name) AS (
    VALUES
        ('articles','discovery_date'),
        ('articles','doi'),
        ('articles','published_date'),
        ('articles','retracted'),
        ('trials','last_updated'),
        ('trials','published_date'),
        ('trials','recruitment_status'),
        ('gregory_historicalarticles','discovery_date'),
        ('gregory_historicalarticles','doi'),
        ('gregory_historicalarticles','published_date'),
        ('gregory_historicalarticles','retracted'),
        ('gregory_historicaltrials','last_updated'),
        ('gregory_historicaltrials','published_date'),
        ('gregory_historicaltrials','recruitment_status')
)
SELECT
    tg.table_name,
    tg.column_name,
    EXISTS (
        SELECT 1
        FROM pg_class t
        JOIN pg_index ix    ON t.oid = ix.indrelid
        JOIN pg_class i     ON i.oid = ix.indexrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        JOIN pg_attribute a ON a.attrelid = t.oid
                           AND a.attnum = ix.indkey[0]   -- leading column only
        WHERE n.nspname = 'public'
          AND t.relname = tg.table_name
          AND a.attname = tg.column_name
    ) AS already_indexed
FROM targets tg
ORDER BY tg.table_name, tg.column_name;

\echo ''
\echo '================================================================'
\echo '3) COMPOSITE INDEX mlpred_art_subj_date_idx -- already present?'
\echo '   Expected: 0 rows. If 1 row, migration 0050 will fail with'
\echo '   "relation already exists" -> fake-apply or drop it first.'
\echo '================================================================'
SELECT indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND indexname = 'mlpred_art_subj_date_idx';

\echo ''
\echo '================================================================'
\echo '4) UNIQUE backing on trials.title -- 0050 drops the PLAIN unique only'
\echo '   The first query finds a PLAIN UNIQUE(title) (real constraint or a'
\echo '   non-expression unique index on exactly the title column) -- that is'
\echo '   what removing field-level unique=True targets. The second query lists'
\echo '   expression-based unique indexes (e.g. lower(title)) which 0050 does'
\echo '   NOT touch. Typical prod state: 0 plain rows, 1 lower(title) row.'
\echo '================================================================'
\echo '-- (4a) PLAIN UNIQUE(title) -- 0050 WILL drop this if present:'
SELECT i.relname AS plain_unique_index, pg_get_indexdef(i.oid) AS definition
FROM pg_index ix
JOIN pg_class i      ON i.oid = ix.indexrelid
JOIN pg_class t      ON t.oid = ix.indrelid
JOIN pg_namespace n  ON n.oid = t.relnamespace
WHERE n.nspname = 'public'
  AND t.relname = 'trials'
  AND ix.indisunique
  AND ix.indexprs IS NULL                       -- exclude lower(title) expression index
  AND ix.indkey::text = (
        SELECT a.attnum::text FROM pg_attribute a
        WHERE a.attrelid = t.oid AND a.attname = 'title');

\echo '-- (4b) expression-based unique indexes (e.g. lower(title)) -- NOT touched by 0050:'
SELECT i.relname AS expr_unique_index, pg_get_indexdef(i.oid) AS definition
FROM pg_index ix
JOIN pg_class i      ON i.oid = ix.indexrelid
JOIN pg_class t      ON t.oid = ix.indrelid
JOIN pg_namespace n  ON n.oid = t.relnamespace
WHERE n.nspname = 'public'
  AND t.relname = 'trials'
  AND ix.indisunique
  AND ix.indexprs IS NOT NULL;

\echo ''
\echo '================================================================'
\echo '5) DUPLICATE trials.title values (informational)'
\echo '   Dropping the unique constraint is always safe, but this tells'
\echo '   you whether duplicates were being blocked. Expect 0 rows now.'
\echo '================================================================'
SELECT title, COUNT(*) AS n
FROM trials
GROUP BY title
HAVING COUNT(*) > 1
ORDER BY n DESC
LIMIT 20;

\echo ''
\echo '================================================================'
\echo '6) TABLE SIZE & ROW COUNT -- estimate lock / build time'
\echo '   Plain CREATE INDEX (what manage.py migrate does) takes a'
\echo '   SHARE lock that BLOCKS WRITES for the whole build. On big'
\echo '   tables consider building CONCURRENTLY by hand first, then'
\echo '   fake-applying 0050 (see runbook note below).'
\echo '================================================================'
SELECT
    c.relname AS table_name,
    to_char(c.reltuples, 'FM999,999,999')      AS est_rows,
    pg_size_pretty(pg_total_relation_size(c.oid)) AS total_size,
    pg_size_pretty(pg_relation_size(c.oid))       AS table_only_size
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relname IN ('articles','trials','gregory_mlpredictions',
                    'gregory_historicalarticles','gregory_historicaltrials')
ORDER BY pg_total_relation_size(c.oid) DESC;

\echo ''
\echo '================================================================'
\echo '7) ANY INVALID INDEXES (left over from a failed CONCURRENTLY)'
\echo '   Expect 0 rows. Invalid indexes still take space and must be'
\echo '   dropped + rebuilt.'
\echo '================================================================'
SELECT n.nspname AS schema, t.relname AS table_name, i.relname AS invalid_index
FROM pg_index ix
JOIN pg_class i     ON i.oid = ix.indexrelid
JOIN pg_class t     ON t.oid = ix.indrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
WHERE NOT ix.indisvalid
  AND n.nspname = 'public'
ORDER BY t.relname, i.relname;

\echo ''
\echo '================================================================'
\echo '8) MIGRATION STATE -- is 0050 already recorded as applied?'
\echo '   Expect 0 rows on a DB that has not run 0050 yet.'
\echo '================================================================'
SELECT name, applied
FROM django_migrations
WHERE app = 'gregory'
  AND name LIKE '0050_perf%';

\echo ''
\echo '== done =='
