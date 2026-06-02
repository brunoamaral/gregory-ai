# Generated manually for trial identity de-duplication – Phase 1.
#
# Replaces the case-insensitive title unique constraint with per-registry-key
# partial unique indexes, and adds a non-unique lower(title) index to keep
# title lookups fast.
#
# See docs/trials-identity-dedup.md for the full design rationale.

from django.db import migrations, models
from django.db.models import Q
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Upper, Lower


def check_registry_duplicates(apps, schema_editor):
	"""Pre-flight: fail loudly if any duplicate registry IDs exist.

	The partial unique indexes will fail to create if duplicates are present.
	Running this check first gives an actionable error message instead of a
	cryptic DB error, and surfaces the offending values so they can be resolved
	before the migration is re-run.
	"""
	from django.db import connection

	registry_keys = ['nct', 'euctr', 'eudract', 'ctis']
	duplicates_found = {}

	with connection.cursor() as cursor:
		for key in registry_keys:
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
				duplicates_found[key] = [(r[0], r[1]) for r in rows]

	if duplicates_found:
		lines = [
			'Cannot add partial unique indexes: duplicate registry IDs found.',
			'Resolve these collisions before re-running the migration:',
		]
		for key, rows in duplicates_found.items():
			for val, cnt in rows:
				lines.append(f'  {key} = {val!r}  ({cnt} rows)')
		raise Exception('\n'.join(lines))


class Migration(migrations.Migration):

	dependencies = [
		('gregory', '0053_add_subject_history'),
	]

	operations = [
		# 0. Pre-flight duplicate scan — aborts loudly before touching indexes.
		migrations.RunPython(check_registry_duplicates, migrations.RunPython.noop),

		# 1. Drop the old case-insensitive title unique constraint.
		migrations.RemoveConstraint(
			model_name='trials',
			name='unique_title_case_insensitive',
		),

		# 2. Non-unique lower(title) index to keep title lookups fast.
		migrations.AddIndex(
			model_name='trials',
			index=models.Index(
				Lower('title'),
				name='trials_lower_title_idx',
			),
		),

		# 3. Partial unique constraint on upper(identifiers->>'nct')
		#    WHERE identifiers ? 'nct'
		migrations.AddConstraint(
			model_name='trials',
			constraint=models.UniqueConstraint(
				Upper(KeyTextTransform('nct', 'identifiers')),
				condition=Q(identifiers__has_key='nct'),
				name='uniq_trial_nct',
			),
		),

		# 4. Partial unique constraint on upper(identifiers->>'euctr')
		migrations.AddConstraint(
			model_name='trials',
			constraint=models.UniqueConstraint(
				Upper(KeyTextTransform('euctr', 'identifiers')),
				condition=Q(identifiers__has_key='euctr'),
				name='uniq_trial_euctr',
			),
		),

		# 5. Partial unique constraint on upper(identifiers->>'eudract')
		migrations.AddConstraint(
			model_name='trials',
			constraint=models.UniqueConstraint(
				Upper(KeyTextTransform('eudract', 'identifiers')),
				condition=Q(identifiers__has_key='eudract'),
				name='uniq_trial_eudract',
			),
		),

		# 6. Partial unique constraint on upper(identifiers->>'ctis')
		migrations.AddConstraint(
			model_name='trials',
			constraint=models.UniqueConstraint(
				Upper(KeyTextTransform('ctis', 'identifiers')),
				condition=Q(identifiers__has_key='ctis'),
				name='uniq_trial_ctis',
			),
		),
	]
