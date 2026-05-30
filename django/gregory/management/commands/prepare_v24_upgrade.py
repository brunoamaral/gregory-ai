"""
prepare_v24_upgrade
===================
Friendly, idempotent helper that de-risks the v24 upgrade. It handles the
**critical** and **moderate** items flagged in docs/releases/v24/MIGRATION_SAFETY.md
*before* you run ``python manage.py migrate``.

What it does
------------
1. **Pre-flight check** (default, read-only): reports whether the legacy
   takeaway columns still hold data, how much is already in the per-org
   content tables, and how big the tables that migration ``0022`` will index
   are (so you can judge the write-lock window).

2. **Backfill** (``--backfill``): copies the legacy
   ``articles.takeaways`` / ``articles.summary_plain_english`` and
   ``trials.summary_plain_english`` values into ``ArticleOrgContent`` /
   ``TrialOrgContent`` for a chosen organisation, so that migration
   ``0048`` (which *drops* those columns) cannot lose data.

The legacy columns are read with raw SQL on purpose: on the v24 code the model
fields no longer exist, so this command keeps working whether it runs against a
pre- or post-``0048`` database.

Usage
-----
Read-only pre-flight (safe to run any time)::

    python manage.py prepare_v24_upgrade

Dry-run the backfill (no writes)::

    python manage.py prepare_v24_upgrade --backfill --dry-run

Backfill into a chosen org (interactive picker)::

    python manage.py prepare_v24_upgrade --backfill

Scripted / CI::

    python manage.py prepare_v24_upgrade --backfill --org-id 3 --noinput
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from gregory.models import ArticleOrgContent, TrialOrgContent
from organizations.models import Organization


# (table, [legacy columns]) pairs that migration 0048 removes.
LEGACY = {
	'articles': ['takeaways', 'summary_plain_english'],
	'trials': ['summary_plain_english'],
}
# Tables whose write-lock during the 0022 index build is worth sizing up.
INDEXED_TABLES = ['articles', 'trials', 'articles_team_categories', 'articles_authors']


class Command(BaseCommand):
	help = (
		'De-risk the v24 upgrade: pre-flight check (default) or back up legacy '
		'takeaway data into per-org content tables before running migrate.'
	)

	def add_arguments(self, parser):
		parser.add_argument(
			'--backfill',
			action='store_true',
			help='Copy legacy takeaways/summaries into ArticleOrgContent/TrialOrgContent.',
		)
		parser.add_argument(
			'--org-id',
			type=int,
			dest='org_id',
			help='Target organisation ID for the backfill. Required with --noinput.',
		)
		parser.add_argument(
			'--dry-run',
			action='store_true',
			dest='dry_run',
			help='Report what the backfill would do without writing.',
		)
		parser.add_argument(
			'--noinput',
			'--no-input',
			action='store_true',
			dest='no_input',
			help='Run non-interactively. Requires --org-id.',
		)

	# ------------------------------------------------------------------
	# Low-level helpers
	# ------------------------------------------------------------------

	def _columns_present(self, table):
		"""Return the subset of LEGACY[table] columns that still exist in the DB."""
		with connection.cursor() as cur:
			cur.execute(
				"SELECT column_name FROM information_schema.columns "
				"WHERE table_name = %s AND column_name = ANY(%s)",
				[table, LEGACY[table]],
			)
			return {row[0] for row in cur.fetchall()}

	def _rows_with_data(self, table, columns):
		"""Count rows where any of *columns* is non-null and non-empty."""
		if not columns:
			return 0
		where = ' OR '.join(
			f"({c} IS NOT NULL AND {c} <> '')" for c in columns
		)
		with connection.cursor() as cur:
			cur.execute(f"SELECT count(*) FROM {table} WHERE {where}")
			return cur.fetchone()[0]

	def _table_size(self, table):
		with connection.cursor() as cur:
			cur.execute(
				"SELECT pg_size_pretty(pg_total_relation_size(%s)), "
				"       (SELECT reltuples::bigint FROM pg_class WHERE relname = %s)",
				[table, table],
			)
			row = cur.fetchone()
			return row if row else ('n/a', 0)

	def _legacy_state(self):
		"""Return {table: (present_columns, rows_with_data)}."""
		state = {}
		for table in LEGACY:
			present = self._columns_present(table)
			rows = self._rows_with_data(table, present)
			state[table] = (present, rows)
		return state

	# ------------------------------------------------------------------
	# Pre-flight report
	# ------------------------------------------------------------------

	def _preflight(self):
		self.stdout.write(self.style.MIGRATE_HEADING('\nv24 upgrade pre-flight\n'))

		state = self._legacy_state()
		any_legacy_cols = any(present for present, _ in state.values())

		# --- Critical: legacy takeaway data ---
		self.stdout.write('Critical — legacy takeaway/summary data (dropped by 0048):')
		if not any_legacy_cols:
			self.stdout.write(self.style.SUCCESS(
				'  ✓ Legacy columns already removed. 0048 has run (or DB post-dates it). '
				'Nothing to back up.'
			))
		else:
			pending = 0
			for table, (present, rows) in state.items():
				if not present:
					continue
				self.stdout.write(
					f'  • {table}: {rows} row(s) still hold data in '
					f'{", ".join(sorted(present))}'
				)
				pending += rows
			art_oc = ArticleOrgContent.objects.count()
			trial_oc = TrialOrgContent.objects.count()
			self.stdout.write(
				f'  • already migrated: ArticleOrgContent={art_oc}, TrialOrgContent={trial_oc}'
			)
			if pending:
				self.stdout.write(self.style.WARNING(
					f'  ⚠ {pending} row(s) carry legacy data. Run '
					f'`--backfill` BEFORE migrate, or 0048 will drop them.'
				))
			else:
				self.stdout.write(self.style.SUCCESS(
					'  ✓ Columns exist but hold no data — safe to migrate.'
				))

		# --- Moderate: index-build write lock (0022) ---
		self.stdout.write('\nModerate — 0022 builds indexes with a plain CREATE INDEX')
		self.stdout.write('(blocks writes on these tables for the build duration):')
		for table in INDEXED_TABLES:
			size, rows = self._table_size(table)
			self.stdout.write(f'  • {table}: {size} (~{rows:,} rows)')
		self.stdout.write(self.style.WARNING(
			'  ⚠ Run migrate inside a maintenance window with writers paused.'
		))

		# --- Verdict ---
		self.stdout.write('')
		if any_legacy_cols and any(rows for _, rows in state.values()):
			self.stdout.write(self.style.NOTICE(
				'Next: python manage.py prepare_v24_upgrade --backfill --org-id <id>'
			))
		else:
			self.stdout.write(self.style.SUCCESS(
				'Next: pause writers, then python manage.py migrate '
				'&& python manage.py createcachetable gregory_cache'
			))
		self.stdout.write('')

	# ------------------------------------------------------------------
	# Backfill
	# ------------------------------------------------------------------

	def _list_orgs(self):
		self.stdout.write('\nOrganisations:')
		self.stdout.write(f'  {"ID":>5}  {"Name":<40}')
		self.stdout.write('  ' + '-' * 48)
		for org in Organization.objects.order_by('id'):
			self.stdout.write(f'  {org.id:>5}  {str(org.name):<40}')
		self.stdout.write('')

	def _resolve_org(self, org_id, no_input):
		if org_id is None:
			if no_input:
				raise CommandError('--org-id is required with --noinput.')
			self._list_orgs()
			while True:
				raw = input('Target organisation ID: ').strip()
				if raw.isdigit():
					org_id = int(raw)
					break
				self.stderr.write('Please enter a numeric ID.\n')
		try:
			return Organization.objects.get(pk=org_id)
		except Organization.DoesNotExist:
			raise CommandError(f'Organisation id={org_id} does not exist.')

	def _fetch_legacy(self, table, pk, columns):
		"""Yield (pk_value, {col: value}) for rows with any non-empty legacy column."""
		where = ' OR '.join(f"({c} IS NOT NULL AND {c} <> '')" for c in columns)
		cols_sql = ', '.join(columns)
		with connection.cursor() as cur:
			cur.execute(f"SELECT {pk}, {cols_sql} FROM {table} WHERE {where}")
			for row in cur.fetchall():
				yield row[0], dict(zip(columns, row[1:]))

	def _backfill(self, org, dry_run):
		state = self._legacy_state()
		if not any(present for present, _ in state.values()):
			self.stdout.write(self.style.SUCCESS(
				'Legacy columns already removed — nothing to back up.'
			))
			return

		plan = [
			('articles', 'article_id', ArticleOrgContent, 'article_id'),
			('trials', 'trial_id', TrialOrgContent, 'trial_id'),
		]
		created = skipped = 0

		for table, pk, model, fk in plan:
			present = state[table][0]
			if not present:
				continue
			rows = list(self._fetch_legacy(table, pk, sorted(present)))
			existing = set(
				model.objects
				.filter(organization=org, **{f'{fk}__in': [r[0] for r in rows]})
				.values_list(fk, flat=True)
			)
			for obj_id, values in rows:
				if obj_id in existing:
					skipped += 1
					continue
				if dry_run:
					created += 1
					continue
				with transaction.atomic():
					model.objects.create(
						organization=org,
						**{fk: obj_id},
						**values,
					)
				created += 1

		verb = 'Would create' if dry_run else 'Created'
		style = self.style.WARNING if dry_run else self.style.SUCCESS
		self.stdout.write(style(
			f'{verb} {created} per-org content row(s); skipped {skipped} existing.'
		))
		if not dry_run:
			self.stdout.write(self.style.SUCCESS(
				'✓ Legacy data preserved. Safe to run migrate.'
			))

	# ------------------------------------------------------------------
	# Entry point
	# ------------------------------------------------------------------

	def handle(self, *args, **options):
		if not options['backfill']:
			self._preflight()
			return

		org = self._resolve_org(options['org_id'], options['no_input'])

		if not options['no_input'] and not options['dry_run']:
			confirm = input(
				f'\nBack up legacy takeaways/summaries into per-org content for '
				f'"{org.name}" (id={org.id})? [y/N] '
			).strip().lower()
			if confirm not in ('y', 'yes'):
				self.stdout.write('Aborted.')
				return

		self._backfill(org, options['dry_run'])
