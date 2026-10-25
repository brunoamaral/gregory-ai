"""
Link EMA medicines to the team categories they belong to.

Re-matches every automatic category against every medicine on each run (about 2,400
products against a few hundred categories takes seconds, so there is no incremental
window). The rule lives in gregory/services/medicine_matching.py.

Only automatic, non-suppressed links are managed here:

- a missing link is created (``source=automatic``, ``matched_on`` says why);
- an automatic link that no longer matches is deleted;
- ``matched_on`` is refreshed when it changed.

Manual links are never touched and skip every rule. A suppressed link (an editor removed
an automatic link) is never recreated, changed or restored. Manual categories are skipped
entirely: they only ever get links an editor adds by hand.

These are pipeline writes, so no editor is recorded, as with rebuild_categories.

Run:
	docker exec gregory python manage.py match_medicines
	docker exec gregory python manage.py match_medicines --category 49 --dry-run
"""

from django.core.management.base import CommandError
from django.db import transaction

from gregory.management.base import GregoryBaseCommand
from gregory.models import (
	CategoryAssignmentSource,
	CategoryType,
	Medicine,
	MedicineCategoryAssignment,
	TeamCategory,
)
from gregory.services.medicine_matching import category_areas, product_matches

BATCH_SIZE = 500


class Command(GregoryBaseCommand):
	help = "Link EMA medicines to the automatic team categories they match."

	def add_arguments(self, parser):
		parser.add_argument(
			"--category",
			type=int,
			metavar="ID",
			help="Match one category only.",
		)
		parser.add_argument(
			"--dry-run",
			action="store_true",
			help="Compute and report the changes; write nothing.",
		)

	def handle(self, *args, **options):
		dry_run = options["dry_run"]
		categories = TeamCategory.objects.select_related("team").prefetch_related("subjects")
		if options["category"] is not None:
			try:
				target = categories.get(pk=options["category"])
			except TeamCategory.DoesNotExist:
				raise CommandError(f"TeamCategory {options['category']} does not exist.")
			if target.category_type != CategoryType.AUTOMATIC:
				self.stdout.write(
					self.style.WARNING(
						f"Category '{target.category_name}' is manual; nothing to do."
					)
				)
				return
			categories = categories.filter(pk=target.pk)
		else:
			skipped = categories.filter(category_type=CategoryType.MANUAL).count()
			if skipped:
				self.stdout.write(f"Skipping {skipped} manual categories")
			categories = categories.exclude(category_type=CategoryType.MANUAL)

		# Only the fields the rule reads.
		medicines = list(
			Medicine.objects.only("id", "name", "substances", "is_combination", "therapeutic_areas")
		)
		prefix = "[dry run] " if dry_run else ""
		totals = {"created": 0, "removed": 0, "updated": 0, "kept": 0, "changed_categories": 0}

		for category in categories.order_by("pk"):
			expected = self._expected(category, medicines)
			counts = self._apply(category, expected, dry_run)
			for key in ("created", "removed", "updated", "kept"):
				totals[key] += counts[key]
			if counts["created"] or counts["removed"] or counts["updated"]:
				totals["changed_categories"] += 1
				self.stdout.write(
					f"{prefix}{category.category_name} (id {category.pk}, {category.team.name}): "
					f"+{counts['created']} -{counts['removed']} ~{counts['updated']} "
					f"({counts['kept']} unchanged)"
				)

		self.stdout.write(
			f"{prefix}Medicine links: {totals['created']} added, {totals['removed']} removed, "
			f"{totals['updated']} relabelled, {totals['kept']} unchanged, in "
			f"{totals['changed_categories']} categories."
		)
		if dry_run:
			self.stdout.write("Dry run: nothing was written.")

	def _expected(self, category, medicines):
		"""``{medicine id: matched_on}`` for the products the rule links to ``category``."""
		areas = category_areas(category)
		expected = {}
		for medicine in medicines:
			label = product_matches(category, medicine, areas)
			if label is not None:
				expected[medicine.pk] = label
		return expected

	def _apply(self, category, expected, dry_run):
		rows = list(
			MedicineCategoryAssignment.objects.filter(teamcategory=category).only(
				"id", "medicine_id", "source", "suppressed", "matched_on"
			)
		)
		# Any existing row occupies its medicine: a manual link is the editor's, a
		# suppressed one was removed by an editor, and either way the unique constraint
		# on (medicine, category) leaves no room for a second row.
		by_medicine = {row.medicine_id: row for row in rows}

		to_create = [
			MedicineCategoryAssignment(
				medicine_id=medicine_id,
				teamcategory=category,
				source=CategoryAssignmentSource.AUTOMATIC,
				matched_on=label,
			)
			for medicine_id, label in expected.items()
			if medicine_id not in by_medicine
		]
		to_remove = []
		to_update = []
		kept = 0
		for row in rows:
			if row.source != CategoryAssignmentSource.AUTOMATIC or row.suppressed:
				continue
			label = expected.get(row.medicine_id)
			if label is None:
				to_remove.append(row.pk)
			elif row.matched_on != label:
				row.matched_on = label
				to_update.append(row)
			else:
				kept += 1

		if not dry_run and (to_create or to_remove or to_update):
			with transaction.atomic():
				if to_remove:
					MedicineCategoryAssignment.objects.filter(
						pk__in=to_remove,
						source=CategoryAssignmentSource.AUTOMATIC,
						suppressed=False,
					).delete()
				MedicineCategoryAssignment.objects.bulk_update(
					to_update, ["matched_on"], batch_size=BATCH_SIZE
				)
				# ignore_conflicts: an editor may have added the same medicine by hand since
				# the rows were read; the unique constraint keeps theirs.
				MedicineCategoryAssignment.objects.bulk_create(
					to_create, batch_size=BATCH_SIZE, ignore_conflicts=True
				)
		return {
			"created": len(to_create),
			"removed": len(to_remove),
			"updated": len(to_update),
			"kept": kept,
		}
