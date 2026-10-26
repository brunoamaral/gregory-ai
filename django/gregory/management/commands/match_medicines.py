"""
Link EMA medicines to the team categories and subjects they belong to.

Three passes, in this order, each re-derived from scratch on every run (about 2,400
products against a few hundred categories takes seconds, so there is no incremental
window). The rules live in gregory/services/medicine_matching.py and the database writes
in gregory/services/medicine_linking.py.

1. Groups. Products that share their substances and their indication are copies of one
   medicine. One leads (an authorised product first, then the earliest authorisation) and
   the others get ``grouped_under`` set to it. Generics, biosimilars and products never
   authorised aren't grouped.
2. Categories. Every automatic category is matched against every product (a diagnostic
   never matches).
3. Subjects. Every subject with ``ema_therapeutic_areas`` is linked to the non-diagnostic
   products EMA approved for one of its areas, with no category needed. A subject whose
   areas were cleared loses its links.

Only derived rows are managed here:

- a missing link is created (a category link gets ``source=automatic``; ``matched_on``
  says why);
- a derived link that no longer matches is deleted;
- ``matched_on`` is refreshed when it changed.

Manual category links are never touched and skip every rule. A suppressed link (an editor
removed an automatic category link, or took a medicine off a subject) is never recreated,
changed or restored. Manual categories are skipped entirely: they only ever get links an
editor adds by hand.

These are pipeline writes, so no editor is recorded, as with rebuild_categories.

Run:
	docker exec gregory python manage.py match_medicines
	docker exec gregory python manage.py match_medicines --category 49 --dry-run
	docker exec gregory python manage.py match_medicines --subject 13 --dry-run
"""

from django.core.management.base import CommandError
from django.db.models import Exists, OuterRef, Q

from gregory.management.base import GregoryBaseCommand
from gregory.models import (
	CategoryType,
	MedicineSubjectLink,
	Subject,
	TeamCategory,
)
from gregory.services.medicine_linking import (
	group_medicines,
	link_category,
	link_subject,
	load_medicines,
)


class Command(GregoryBaseCommand):
	help = "Group EMA medicines and link them to the automatic team categories and subjects they match."

	def add_arguments(self, parser):
		parser.add_argument(
			"--category",
			type=int,
			metavar="ID",
			help="Match one category only (skips the other passes).",
		)
		parser.add_argument(
			"--subject",
			type=int,
			metavar="ID",
			help="Match one subject only (skips the other passes).",
		)
		parser.add_argument(
			"--dry-run",
			action="store_true",
			help="Compute and report the changes; write nothing.",
		)

	def handle(self, *args, **options):
		dry_run = options["dry_run"]
		category_id = options["category"]
		subject_id = options["subject"]
		if category_id is not None and subject_id is not None:
			raise CommandError("Use --category or --subject, not both.")
		prefix = "[dry run] " if dry_run else ""

		medicines = load_medicines()
		if category_id is None and subject_id is None:
			self._group_pass(medicines, dry_run, prefix)
		if subject_id is None:
			self._category_pass(category_id, medicines, dry_run, prefix)
		if category_id is None:
			self._subject_pass(subject_id, medicines, dry_run, prefix)

		if dry_run:
			self.stdout.write("Dry run: nothing was written.")

	def _group_pass(self, medicines, dry_run, prefix):
		counts = group_medicines(medicines, dry_run=dry_run)
		self.stdout.write(
			f"{prefix}Groups: {counts['grouped']} products grouped under a lead, in "
			f"{counts['groups']} groups; {counts['changed']} changed."
		)

	def _category_pass(self, category_id, medicines, dry_run, prefix):
		categories = TeamCategory.objects.select_related("team").prefetch_related("subjects")
		if category_id is not None:
			try:
				target = categories.get(pk=category_id)
			except TeamCategory.DoesNotExist:
				raise CommandError(f"TeamCategory {category_id} does not exist.")
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

		totals = self._new_totals()
		for category in categories.order_by("pk"):
			counts = link_category(category, medicines, dry_run=dry_run)
			self._tally(totals, counts)
			if self._changed(counts):
				self._report_owner(
					prefix, category.category_name, category.pk, category.team.name, counts
				)
		self.stdout.write(
			f"{prefix}Medicine links: {totals['created']} added, {totals['removed']} removed, "
			f"{totals['updated']} relabelled, {totals['kept']} unchanged, in "
			f"{totals['changed']} categories."
		)

	def _subject_pass(self, subject_id, medicines, dry_run, prefix):
		subjects = Subject.objects.select_related("team")
		if subject_id is not None:
			subjects = subjects.filter(pk=subject_id)
			if not subjects.exists():
				raise CommandError(f"Subject {subject_id} does not exist.")
		else:
			# A subject with areas gets links. One without gets none, but a subject whose
			# areas were cleared still has links to remove, so it is included too.
			has_links = MedicineSubjectLink.objects.filter(
				subject=OuterRef("pk"), suppressed=False
			)
			subjects = subjects.filter(~Q(ema_therapeutic_areas=[]) | Exists(has_links))

		totals = self._new_totals()
		for subject in subjects.order_by("pk"):
			counts = link_subject(subject, medicines, dry_run=dry_run)
			self._tally(totals, counts)
			if self._changed(counts):
				team = subject.team.name if subject.team else "no team"
				self._report_owner(prefix, subject.subject_name, subject.pk, team, counts)
		self.stdout.write(
			f"{prefix}Subject links: {totals['created']} added, {totals['removed']} removed, "
			f"{totals['updated']} relabelled, {totals['kept']} unchanged, in "
			f"{totals['changed']} subjects."
		)

	def _new_totals(self):
		return {"created": 0, "removed": 0, "updated": 0, "kept": 0, "changed": 0}

	def _changed(self, counts):
		return bool(counts["created"] or counts["removed"] or counts["updated"])

	def _tally(self, totals, counts):
		for key in ("created", "removed", "updated", "kept"):
			totals[key] += counts[key]
		if self._changed(counts):
			totals["changed"] += 1

	def _report_owner(self, prefix, name, pk, team, counts):
		self.stdout.write(
			f"{prefix}{name} (id {pk}, {team}): "
			f"+{counts['created']} -{counts['removed']} ~{counts['updated']} "
			f"({counts['kept']} unchanged)"
		)
