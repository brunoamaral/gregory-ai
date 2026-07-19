"""Merge one or more duplicate Sponsor entities into a single canonical one.

Modeled on merge_authors (see docs/merge-authors-command.md): repoints every merged
sponsor's trials and aliases onto the target, carries sponsor_type over to the target
only when the target doesn't already have one, deletes the emptied sponsors, and prints
a summary. This is the scripted equivalent of what sync_sponsor_seeds does automatically
when a seed family's variant key was already auto-created as its own singleton sponsor —
use this command for ad-hoc merges the seed table doesn't (yet) cover.

Usage:
    python manage.py merge_sponsors --into <target_id> <id> [<id> ...]
    python manage.py merge_sponsors --into <target_id> <id> --dry-run
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from gregory.models import Sponsor, SponsorAlias


class Command(BaseCommand):
	help = (
		"Merge one or more Sponsor entities into a single canonical one: repoints "
		"trials and aliases, carries sponsor_type over when the target has none, and "
		"deletes the merged sponsors."
	)

	def add_arguments(self, parser):
		parser.add_argument(
			"--into",
			type=int,
			required=True,
			metavar="SPONSOR_ID",
			help="Sponsor id to keep (the merge target).",
		)
		parser.add_argument(
			"sponsor_ids",
			nargs="+",
			type=int,
			metavar="SPONSOR_ID",
			help="Sponsor id(s) to merge into --into and delete.",
		)
		parser.add_argument(
			"--dry-run",
			action="store_true",
			help="Show what would be merged without making changes.",
		)
		parser.add_argument(
			"--force",
			action="store_true",
			help="Skip the confirmation prompt.",
		)

	def handle(self, *args, **options):
		target_id = options["into"]
		source_ids = list(dict.fromkeys(options["sponsor_ids"]))  # dedupe, keep order
		dry_run = options["dry_run"]
		force = options["force"]

		if target_id in source_ids:
			raise CommandError("--into sponsor cannot also appear in the merge list.")

		try:
			target = Sponsor.objects.get(pk=target_id)
		except Sponsor.DoesNotExist:
			raise CommandError(f"Sponsor {target_id} (--into) does not exist.")

		sources = list(Sponsor.objects.filter(pk__in=source_ids))
		found_ids = {s.pk for s in sources}
		missing = set(source_ids) - found_ids
		if missing:
			raise CommandError(f"Sponsor id(s) not found: {', '.join(map(str, sorted(missing)))}")

		self.stdout.write(f"KEEPING: {target.name} (id={target.pk}, trials={target.trials.count()})")
		for source in sources:
			self.stdout.write(
				f"  MERGING: {source.name} (id={source.pk}, "
				f"trials={source.trials.count()}, aliases={source.aliases.count()}, "
				f"sponsor_type={source.sponsor_type})"
			)

		if dry_run:
			self.stdout.write(self.style.WARNING("DRY RUN — no changes made."))
			return

		if not force:
			confirmation = input(
				'Proceed with this merge? This cannot be undone. Type "yes" to continue: '
			)
			if confirmation.lower() != "yes":
				self.stdout.write(self.style.WARNING("Merge cancelled."))
				return

		total_trials = 0
		total_aliases = 0
		with transaction.atomic():
			target_changed = False
			for source in sources:
				total_trials += source.trials.update(primary_sponsor_normalized=target)
				total_aliases += SponsorAlias.objects.filter(sponsor=source).update(
					sponsor=target
				)
				if not target.sponsor_type and source.sponsor_type:
					target.sponsor_type = source.sponsor_type
					target.sponsor_type_source = source.sponsor_type_source
					target_changed = True
				source.delete()
			if target_changed:
				target.save(update_fields=["sponsor_type", "sponsor_type_source"])

		self.stdout.write(
			self.style.SUCCESS(
				f"Merged {len(sources)} sponsor(s) into {target.name!r} (id={target.pk}): "
				f"{total_trials} trial(s) repointed, {total_aliases} alias(es) moved."
			)
		)
