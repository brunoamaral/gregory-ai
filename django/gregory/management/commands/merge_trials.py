"""
Merge duplicate ``Trials`` rows into one.

Used to resolve pre-existing duplicates before applying migration 0054 (the partial
unique indexes on registry identifiers) — see docs/trials-identity-dedup.md, Phase 0. The
guarded-title matching added in this PR stops *new* duplicates, but rows that were already
merged-by-title-then-split (or manually entered twice) must be reconciled by hand.

For each removed trial it:
  * adds the removed trial's M2M links (sources, teams, subjects, team_categories,
    ml_predictions) to the kept trial,
  * repoints every reverse-FK child (org_contents, article_references, sent notifications)
    to the kept trial — dropping a child if the kept trial already has the equivalent row
    (so a unique constraint isn't violated; nothing is CASCADE-deleted by accident),
  * unions ``identifiers`` (the kept trial's values win on shared keys),
  * deletes the removed trial.

All work happens in one transaction; ``--dry-run`` rolls back.

Usage:
  docker exec gregory python manage.py merge_trials --keep 643 --remove 699
  docker exec gregory python manage.py merge_trials --keep 3913 --remove 11915 --dry-run
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

from gregory.models import Trials
from gregory.utils.trial_utils import canonical_link, merge_links


class Command(BaseCommand):
	help = 'Merge duplicate Trials rows into a kept trial, moving all relations, then deleting the duplicates.'

	def add_arguments(self, parser):
		parser.add_argument('--keep', type=int, required=True, help='trial_id to keep.')
		parser.add_argument('--remove', type=int, nargs='+', required=True,
							help='one or more trial_ids to merge into --keep and delete.')
		parser.add_argument('--dry-run', action='store_true', help='roll back instead of committing.')

	def handle(self, *args, **options):
		keep_id = options['keep']
		remove_ids = [r for r in options['remove'] if r != keep_id]
		dry_run = options['dry_run']

		if not remove_ids:
			raise CommandError('--remove must contain at least one id different from --keep.')

		try:
			keep = Trials.objects.get(pk=keep_id)
		except Trials.DoesNotExist:
			raise CommandError(f'--keep trial {keep_id} does not exist.')

		m2m_fields = [f.name for f in Trials._meta.get_fields()
					  if f.many_to_many and not f.auto_created]
		reverse_fks = [f for f in Trials._meta.get_fields() if f.one_to_many]

		with transaction.atomic():
			for rid in remove_ids:
				try:
					rem = Trials.objects.get(pk=rid)
				except Trials.DoesNotExist:
					self.stderr.write(self.style.WARNING(f'skip: trial {rid} does not exist.'))
					continue

				self.stdout.write(f'Merging trial {rid} into {keep_id} …')

				# 1. M2M links → add removed trial's to the kept trial.
				for fname in m2m_fields:
					related = list(getattr(rem, fname).all())
					if related:
						getattr(keep, fname).add(*related)

				# 2. Reverse-FK children → repoint to kept trial; drop on unique collision.
				#    A nested atomic() per child gives a savepoint we can roll back cleanly
				#    after an IntegrityError and keep using the outer transaction.
				for rel in reverse_fks:
					accessor = rel.get_accessor_name()
					fk_name = rel.field.name
					for child in list(getattr(rem, accessor).all()):
						try:
							with transaction.atomic():
								setattr(child, fk_name, keep)
								child.save(update_fields=[fk_name])
						except IntegrityError:
							dropped_pk = child.pk
							child.delete()
							self.stdout.write(
								f'   dropped duplicate {rel.related_model.__name__} #{dropped_pk} '
								f'(kept trial already has the equivalent row)'
							)

				# 3. Union identifiers (conservative: kept trial's values win, add missing keys).
				merged = dict(keep.identifiers or {})
				for k, v in (rem.identifiers or {}).items():
					if v and (k not in merged or merged[k] is None):
						merged[k] = v

				# Union registry links the same way (kept trial's entries win). The
				# kept trial's canonical link stays — registries are not ranked —
				# unless it is an aggregator URL that can be upgraded (canonical_link).
				merged_links = dict(keep.links or {})
				for k, v in (rem.links or {}).items():
					if v and not merged_links.get(k):
						merged_links[k] = v
				merged_links = merge_links(merged_links, rem.link)

				# 4. Delete the removed trial FIRST so its registry ids are freed, THEN adopt
				#    the unioned identifiers — otherwise both rows briefly share an id and trip
				#    the partial unique indexes added in migration 0054.
				rem.delete()
				update_fields = []
				if merged != (keep.identifiers or {}):
					keep.identifiers = merged
					update_fields.append('identifiers')
				if merged_links != (keep.links or {}):
					keep.links = merged_links
					update_fields.append('links')
				new_link = canonical_link(keep.links, keep.link)
				if new_link and new_link != keep.link:
					keep.link = new_link
					update_fields.append('link')
				if update_fields:
					keep.save(update_fields=update_fields)
				self.stdout.write(self.style.SUCCESS(f'   merged and deleted trial {rid}.'))

			keep.refresh_from_db()
			self.stdout.write(self.style.SUCCESS(
				f'Done. Kept trial {keep_id} identifiers: {keep.identifiers}'
			))

			if dry_run:
				transaction.set_rollback(True)
				self.stdout.write(self.style.WARNING('DRY RUN — all changes rolled back.'))
