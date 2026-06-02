"""
Read-only management command: list suspected false-merges in the Trials table.

Reports:
  1. Rows where the link's NCT ID ≠ identifiers['nct']  (link/NCT mismatch)
  2. Duplicate NCT/EUCTR/EUDRACT/CTIS values across rows  (constraint pre-flight check)

Usage:
  python manage.py audit_trial_merges
  python manage.py audit_trial_merges --check-dupes  # also run per-key dupe scan
  python manage.py audit_trial_merges --json          # machine-readable output

Run this BEFORE applying the migration that adds the partial unique indexes to
confirm no existing rows would collide on the new constraints.
"""

import json
import re

from django.core.management.base import BaseCommand
from django.db import connection
from django.db.models import Count

from gregory.models import Trials


# Pattern to extract an NCT ID from a ClinicalTrials.gov URL
_NCT_FROM_LINK_RE = re.compile(r'/(NCT\d{8})', re.IGNORECASE)


def _nct_from_link(link: str | None) -> str | None:
	"""Extract a normalised NCT ID from a CT.gov URL, or None."""
	if not link:
		return None
	m = _NCT_FROM_LINK_RE.search(link)
	return m.group(1).upper() if m else None


class Command(BaseCommand):
	help = (
		'Read-only audit: list Trials rows with suspected link/NCT mismatches '
		'and (optionally) per-registry-key duplicate values.'
	)

	def add_arguments(self, parser):
		parser.add_argument(
			'--check-dupes',
			action='store_true',
			default=False,
			help='Also scan for rows that would collide under the new partial unique indexes.',
		)
		parser.add_argument(
			'--json',
			dest='output_json',
			action='store_true',
			default=False,
			help='Emit results as a JSON object instead of human-readable text.',
		)

	def handle(self, *args, **options):
		check_dupes = options['check_dupes']
		output_json = options['output_json']

		report = {}

		# ------------------------------------------------------------------
		# 1. Link/NCT mismatch: stored link points to a different NCT than
		#    identifiers['nct'].
		# ------------------------------------------------------------------
		mismatches = []
		qs = Trials.objects.exclude(identifiers__has_key='nct').union(
			Trials.objects.filter(identifiers__has_key='nct')
		)
		# We need the full queryset; use filter to narrow to rows that have an
		# NCT-looking link so we're not scanning all 19 k rows in Python.
		nct_rows = Trials.objects.filter(
			identifiers__has_key='nct',
			link__icontains='NCT',
		).values('trial_id', 'link', 'identifiers')

		for row in nct_rows:
			stored_nct = (row['identifiers'].get('nct') or '').strip().upper()
			link_nct = _nct_from_link(row['link'])
			if link_nct and stored_nct and link_nct != stored_nct:
				mismatches.append({
					'trial_id': row['trial_id'],
					'identifiers_nct': stored_nct,
					'link_nct': link_nct,
					'link': row['link'],
				})

		report['link_nct_mismatches'] = mismatches

		# ------------------------------------------------------------------
		# 2. Per-registry duplicate scan (pre-flight for the migration).
		# ------------------------------------------------------------------
		dupes_report = {}
		if check_dupes:
			registry_keys = ['nct', 'euctr', 'eudract', 'ctis']
			for key in registry_keys:
				with connection.cursor() as cursor:
					cursor.execute(
						"""
						SELECT upper(identifiers->>'%s') AS val, count(*) AS cnt
						FROM trials
						WHERE identifiers ? '%s'
						GROUP BY upper(identifiers->>'%s')
						HAVING count(*) > 1
						ORDER BY cnt DESC
						""" % (key, key, key)  # noqa: S608 – read-only, no user input
					)
					rows = cursor.fetchall()
				if rows:
					dupes_report[key] = [{'value': r[0], 'count': r[1]} for r in rows]
			report['duplicate_registry_ids'] = dupes_report

		# ------------------------------------------------------------------
		# Output
		# ------------------------------------------------------------------
		if output_json:
			self.stdout.write(json.dumps(report, indent=2))
			return

		# Human-readable
		self.stdout.write(self.style.MIGRATE_HEADING('\n=== Link / NCT mismatch ==='))
		if mismatches:
			self.stdout.write(
				self.style.WARNING(f'{len(mismatches)} row(s) where link NCT ≠ identifiers[\'nct\']:')
			)
			for m in mismatches:
				self.stdout.write(
					f"  trial_id={m['trial_id']}  "
					f"identifiers.nct={m['identifiers_nct']}  "
					f"link_nct={m['link_nct']}  "
					f"link={m['link']}"
				)
		else:
			self.stdout.write(self.style.SUCCESS('No link/NCT mismatches found.'))

		if check_dupes:
			self.stdout.write(self.style.MIGRATE_HEADING('\n=== Duplicate registry IDs ==='))
			if dupes_report:
				for key, entries in dupes_report.items():
					self.stdout.write(self.style.WARNING(f'{key}:'))
					for e in entries:
						self.stdout.write(f"  {e['value']}  ({e['count']} rows)")
			else:
				self.stdout.write(
					self.style.SUCCESS(
						'No duplicate registry IDs found — safe to apply the partial unique indexes.'
					)
				)
