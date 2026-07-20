"""Apply the curated modality mapping in
gregory.utils.category_modality_seeds.CATEGORY_MODALITY_SEEDS to TeamCategory.modality.

Idempotent and safe to re-run after every edit to category_modality_seeds.py: by
default a category's modality is only ever set when it is currently null — once
someone has curated (or corrected) a value in the admin, the seed file no longer
touches it. Pass --force to overwrite everything from the seed file regardless.

See CATEGORY-MODALITY-PLAN.md.
"""

from django.core.management.base import BaseCommand

from gregory.models import TeamCategory
from gregory.utils.category_modality_seeds import CATEGORY_MODALITY_SEEDS


class Command(BaseCommand):
	help = (
		"Apply curated intervention-modality classifications "
		"(gregory.utils.category_modality_seeds.CATEGORY_MODALITY_SEEDS) to "
		"TeamCategory.modality. Never overwrites a value already set unless --force "
		"is passed."
	)

	def add_arguments(self, parser):
		parser.add_argument(
			"--dry-run",
			action="store_true",
			help="Report what would change without saving.",
		)
		parser.add_argument(
			"--force",
			action="store_true",
			help="Overwrite modality even when a category already has a value set.",
		)

	def handle(self, *args, **options):
		dry_run = options["dry_run"]
		force = options["force"]
		verbosity = options.get("verbosity", 1)

		categories = list(
			TeamCategory.objects.filter(category_slug__in=CATEGORY_MODALITY_SEEDS.keys())
		)
		by_slug = {c.category_slug: c for c in categories}

		assigned = []
		skipped_already_set = []
		stale_seeds = []

		for slug, modality in CATEGORY_MODALITY_SEEDS.items():
			category = by_slug.get(slug)
			if category is None:
				stale_seeds.append(slug)
				continue
			if category.modality and not force:
				skipped_already_set.append(category)
				continue
			if category.modality != modality:
				category.modality = modality
				assigned.append(category)

		if assigned and not dry_run:
			TeamCategory.objects.bulk_update(assigned, ["modality"], batch_size=200)

		backlog = list(
			TeamCategory.objects.filter(modality__isnull=True)
			.exclude(pk__in=[c.pk for c in assigned])
			.order_by("category_name")
			.values_list("category_name", flat=True)
		)

		prefix = "Would assign" if dry_run else "Assigned"
		self.stdout.write(
			self.style.SUCCESS(
				f"{prefix} modality to {len(assigned)} categor{'y' if len(assigned) == 1 else 'ies'}, "
				f"skipped {len(skipped_already_set)} already-curated categor"
				f"{'y' if len(skipped_already_set) == 1 else 'ies'}."
			)
		)

		if stale_seeds:
			self.stdout.write(
				self.style.WARNING(
					f"{len(stale_seeds)} seed slug(s) had no matching category: "
					f"{', '.join(sorted(stale_seeds))}"
				)
			)

		if verbosity >= 2 and assigned:
			for category in assigned:
				self.stdout.write(f"  + {category.category_slug} -> {category.modality}")

		if backlog:
			self.stdout.write(
				self.style.WARNING(
					f"{len(backlog)} categor{'y' if len(backlog) == 1 else 'ies'} still "
					f"uncurated (modality is null): {', '.join(backlog)}"
				)
			)
		else:
			self.stdout.write(self.style.SUCCESS("No uncurated categories remain."))
