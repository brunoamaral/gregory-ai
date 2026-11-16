"""
Group authors into labs: a head (3+ last-author papers) plus the people who share 2+
papers with them, computed from the OpenAlex authorships.

Never touches a manual membership or a manual lab. Writes only what changed, so a second
run with the same data writes nothing. Refuses to delete over half of the existing labs
(an empty or half-loaded authorship table) unless --force is given.

Run:
	docker exec gregory python manage.py rebuild_labs --dry-run
	docker exec gregory python manage.py rebuild_labs
"""

from django.core.management.base import CommandError
from django.utils import timezone

from gregory.management.base import GregoryBaseCommand
from gregory.services.labs import LabRebuildAborted, apply_labs, compute_labs, load_inputs


class Command(GregoryBaseCommand):
	help = "Group authors into labs from their OpenAlex authorships."

	def add_arguments(self, parser):
		parser.add_argument(
			"--dry-run",
			action="store_true",
			help="Print the counts of what would change and write nothing.",
		)
		parser.add_argument(
			"--force",
			action="store_true",
			help="Go ahead even when the run would delete more than half of the existing labs.",
		)

	def handle(self, *args, **options):
		dry_run = options["dry_run"]
		inputs = load_inputs()
		self.log(
			f"Loaded {len(inputs.rows)} authorships for {len(inputs.authors)} authors.", level=1
		)
		result = compute_labs(inputs.rows, today=timezone.now().date(), **inputs.as_kwargs())
		self.log(
			f"Computed {len(result.labs)} labs placing {len(result.assignments)} author rows.",
			level=1,
		)
		try:
			stats = apply_labs(result, dry_run=dry_run, force=options["force"])
		except LabRebuildAborted as exc:
			raise CommandError(str(exc))
		prefix = "Would change" if dry_run else "Changed"
		self.log(
			f"{prefix}: labs {stats.labs_created} created, {stats.labs_updated} updated, "
			f"{stats.labs_deleted} deleted, {stats.labs_flagged} flagged for review; "
			f"memberships {stats.memberships_created} created, {stats.memberships_updated} "
			f"updated, {stats.memberships_deleted} deleted. Left alone: "
			f"{stats.manual_memberships_left} manual memberships, {stats.manual_labs_left} "
			"manual labs.",
			level=1,
		)
