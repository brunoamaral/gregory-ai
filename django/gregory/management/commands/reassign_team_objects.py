"""
Management command: reassign all objects from one team to another.

Usage:
    python manage.py reassign_team_objects \\
        --from-team OLD_SLUG \\
        --to-team   NEW_SLUG \\
        [--conflict skip|rename|merge] \\
        [--dry-run]
"""

from django.core.management.base import BaseCommand, CommandError

from gregory.models import Team
from gregory.services.team_reassignment import reassign_team


class Command(BaseCommand):
	help = (
		"Reassign all objects (subjects, sources, categories, lists, ML logs, "
		"articles, trials, model files) from one team to another. "
		"Both teams must belong to the same organisation."
	)

	def add_arguments(self, parser):
		parser.add_argument(
			"--from-team",
			required=True,
			metavar="SLUG",
			help="Slug of the team to reassign objects FROM (may be inactive).",
		)
		parser.add_argument(
			"--to-team",
			required=True,
			metavar="SLUG",
			help="Slug of the target team to reassign objects TO (must be active).",
		)
		parser.add_argument(
			"--conflict",
			choices=["skip", "rename", "merge"],
			default="skip",
			help=(
				"How to handle subject slug collisions. "
				"skip: leave conflicting subjects on the old team. "
				"rename: append a suffix to the slug and reassign. "
				"merge: move dependents to the existing subject, then delete the duplicate. "
				"Default: skip."
			),
		)
		parser.add_argument(
			"--dry-run",
			action="store_true",
			default=False,
			help="Preview what would change without making any modifications.",
		)

	def handle(self, *args, **options):
		from_slug = options["from_team"]
		to_slug = options["to_team"]
		conflict = options["conflict"]
		dry_run = options["dry_run"]

		# Fetch teams — use all_objects so inactive teams can be the source.
		try:
			from_team = Team.all_objects.get(slug=from_slug)
		except Team.DoesNotExist:
			raise CommandError(f"No team found with slug '{from_slug}'.")

		try:
			to_team = Team.all_objects.get(slug=to_slug)
		except Team.DoesNotExist:
			raise CommandError(f"No team found with slug '{to_slug}'.")

		if dry_run:
			self.stdout.write(
				self.style.WARNING("DRY RUN — no changes will be made.\n")
			)

		self.stdout.write(
			f"Reassigning from '{from_team}' → '{to_team}' "
			f"(conflict mode: {conflict})\n"
		)

		try:
			report = reassign_team(
				from_team=from_team,
				to_team=to_team,
				conflict=conflict,
				dry_run=dry_run,
			)
		except ValueError as exc:
			raise CommandError(str(exc))

		self.stdout.write("\n" + report.summary() + "\n")

		if report.errors:
			self.stdout.write(self.style.ERROR("Completed with errors — see above."))
		elif dry_run:
			self.stdout.write(
				self.style.WARNING("Dry run complete. Run without --dry-run to apply.")
			)
		else:
			self.stdout.write(self.style.SUCCESS("Reassignment complete."))
