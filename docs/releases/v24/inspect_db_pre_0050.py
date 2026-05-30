"""
Pre-flight DB inspection for migration 0050_perf_add_indexes_drop_redundant_unique.

READ-ONLY. Makes no changes. Safe on prod or local dev.

Use this when psql is not available (e.g. inside the `gregory` container).
Run:

    docker exec -i gregory python manage.py shell < docs/releases/v24/inspect_db_pre_0050.py

(If psql IS available, prefer inspect_db_pre_0050.sql -- same checks.)
"""
from django.db import connection

TABLES = [
	'articles', 'trials', 'gregory_mlpredictions',
	'gregory_historicalarticles', 'gregory_historicaltrials',
]

TARGETS = [
	('articles', 'discovery_date'), ('articles', 'doi'),
	('articles', 'published_date'), ('articles', 'retracted'),
	('trials', 'last_updated'), ('trials', 'published_date'),
	('trials', 'recruitment_status'),
	('gregory_historicalarticles', 'discovery_date'),
	('gregory_historicalarticles', 'doi'),
	('gregory_historicalarticles', 'published_date'),
	('gregory_historicalarticles', 'retracted'),
	('gregory_historicaltrials', 'last_updated'),
	('gregory_historicaltrials', 'published_date'),
	('gregory_historicaltrials', 'recruitment_status'),
]


def header(n, title):
	print('\n' + '=' * 70)
	print(f'{n}) {title}')
	print('=' * 70)


def run(sql, params=None):
	cur = connection.cursor()
	cur.execute(sql, params or [])
	return cur.fetchall()


# 1) all indexes on affected tables
header(1, 'ALL EXISTING INDEXES on affected tables (spot prior optimisations)')
for table, index, size, uniq, valid, definition in run("""
	SELECT t.relname, i.relname,
	       pg_size_pretty(pg_relation_size(i.oid)),
	       ix.indisunique, ix.indisvalid, pg_get_indexdef(i.oid)
	FROM pg_class t
	JOIN pg_index ix    ON t.oid = ix.indrelid
	JOIN pg_class i     ON i.oid = ix.indexrelid
	JOIN pg_namespace n ON n.oid = t.relnamespace
	WHERE n.nspname='public' AND t.relname = ANY(%s)
	ORDER BY t.relname, i.relname;
""", [TABLES]):
	flags = ('UNIQUE ' if uniq else '') + ('' if valid else 'INVALID!')
	print(f'  [{table}] {index} ({size}) {flags}')
	print(f'      {definition}')

# 2) target columns already indexed?
header(2, 'TARGET COLUMNS already have a leading-column index? (t = redundant add)')
for table, column in TARGETS:
	(already,) = run("""
		SELECT EXISTS (
			SELECT 1 FROM pg_class t
			JOIN pg_index ix    ON t.oid = ix.indrelid
			JOIN pg_class i     ON i.oid = ix.indexrelid
			JOIN pg_namespace n ON n.oid = t.relnamespace
			JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ix.indkey[0]
			WHERE n.nspname='public' AND t.relname=%s AND a.attname=%s
		);
	""", [table, column])[0]
	print(f'  already_indexed={"t" if already else "f"}   {table}.{column}')

# 3) composite index existence
header(3, 'mlpred_art_subj_date_idx already present? (want 0 rows)')
rows = run("""
	SELECT indexname, indexdef FROM pg_indexes
	WHERE schemaname='public' AND indexname='mlpred_art_subj_date_idx';
""")
print('  EXISTS ALREADY -> 0050 will fail, fake-apply or drop first:' if rows else '  not present (good)')
for name, definition in rows:
	print(f'      {definition}')

# 4) unique constraint on trials.title
header(4, 'UNIQUE backing on trials.title (0050 DROPS this)')
cons = run("""
	SELECT conname, pg_get_constraintdef(oid)
	FROM pg_constraint
	WHERE conrelid='public.trials'::regclass AND contype='u';
""")
for name, definition in cons:
	print(f'  constraint {name}: {definition}')
idx = run("""
	SELECT i.relname, pg_get_indexdef(i.oid)
	FROM pg_class t
	JOIN pg_index ix    ON t.oid=ix.indrelid
	JOIN pg_class i     ON i.oid=ix.indexrelid
	JOIN pg_namespace n ON n.oid=t.relnamespace
	WHERE n.nspname='public' AND t.relname='trials'
	  AND ix.indisunique AND pg_get_indexdef(i.oid) ILIKE '%%(title%%';
""")
for name, definition in idx:
	print(f'  unique index {name}: {definition}')
if not cons and not idx:
	print('  WARNING: no unique on trials.title found -- 0050 AlterField will be a no-op')

# 5) duplicate titles
header(5, 'DUPLICATE trials.title values (informational, want 0 rows)')
dups = run("""
	SELECT title, COUNT(*) FROM trials
	GROUP BY title HAVING COUNT(*)>1 ORDER BY COUNT(*) DESC LIMIT 20;
""")
print(f'  {len(dups)} duplicated title(s)' + (':' if dups else ''))
for title, n in dups:
	print(f'      {n}x  {title[:90]}')

# 6) sizes / row counts
header(6, 'TABLE SIZE & ROW COUNT (estimate lock/build time; plain CREATE INDEX blocks writes)')
for table, est_rows, total, table_only in run("""
	SELECT c.relname, to_char(c.reltuples,'FM999,999,999'),
	       pg_size_pretty(pg_total_relation_size(c.oid)),
	       pg_size_pretty(pg_relation_size(c.oid))
	FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
	WHERE n.nspname='public' AND c.relname = ANY(%s)
	ORDER BY pg_total_relation_size(c.oid) DESC;
""", [TABLES]):
	print(f'  {table:32s} ~{est_rows:>12s} rows   total {total:>10s}   table {table_only:>10s}')

# 7) invalid indexes
header(7, 'INVALID indexes from failed CONCURRENTLY (want 0 rows)')
inv = run("""
	SELECT n.nspname, t.relname, i.relname
	FROM pg_index ix
	JOIN pg_class i     ON i.oid=ix.indexrelid
	JOIN pg_class t     ON t.oid=ix.indrelid
	JOIN pg_namespace n ON n.oid=t.relnamespace
	WHERE NOT ix.indisvalid AND n.nspname='public'
	ORDER BY t.relname, i.relname;
""")
print('  none (good)' if not inv else '  INVALID indexes found:')
for schema, table, index in inv:
	print(f'      {schema}.{table} -> {index}')

# 8) migration state
header(8, 'Is 0050 already recorded as applied? (want 0 rows on a DB that has not run it)')
mig = run("""
	SELECT name, applied FROM django_migrations
	WHERE app='gregory' AND name LIKE '0050_perf%%';
""")
print('  not applied yet' if not mig else '  ALREADY APPLIED:')
for name, applied in mig:
	print(f'      {name}  @ {applied}')

print('\n== done ==')
