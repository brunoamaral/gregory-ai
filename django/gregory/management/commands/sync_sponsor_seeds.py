"""Apply the curated sponsor families in gregory.utils.sponsor_seeds.SPONSOR_SEEDS to the
Sponsor/SponsorAlias tables.

Idempotent and safe to re-run after every edit to sponsor_seeds.py: for each family it
get_or_create's the canonical Sponsor (marking sponsor_type_source="curated", which
_update_sponsor_type_from_trial() in gregory/models.py never overwrites automatically),
then for each variant string either creates a fresh alias pointing at that sponsor, or —
if the key was already auto-created as its own singleton Sponsor by a prior trial save —
folds that singleton into the canonical sponsor: repoints its trials' FK, moves its other
aliases across, and deletes the now-empty sponsor. This makes "seed the family, then run
the backfill" and "backfill first, then add a seed family later" converge to the same
end state.

See TRIALS-SPONSOR-CANONICALIZATION-PLAN.md PR 1 §5.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from gregory.models import Sponsor, SponsorAlias, _unique_sponsor_slug
from gregory.utils.sponsor_merge import merge_sponsors
from gregory.utils.sponsor_seeds import SPONSOR_SEEDS
from gregory.utils.trial_field_normalizers import normalize_sponsor_key


class Command(BaseCommand):
	help = (
		"Apply curated sponsor families (gregory.utils.sponsor_seeds.SPONSOR_SEEDS) to the "
		"Sponsor/SponsorAlias tables: get_or_create each canonical sponsor and its aliases, "
		"folding in any singleton sponsor a variant key was already auto-created under."
	)

	def add_arguments(self, parser):
		parser.add_argument(
			"--dry-run",
			action="store_true",
			help="Report what would change without saving.",
		)

	def handle(self, *args, **options):
		dry_run = options["dry_run"]
		verbosity = options.get("verbosity", 1)

		families_created = 0
		aliases_created = 0
		sponsors_folded = 0
		trials_repointed = 0
		# Keys already handled earlier in this run — a family can legitimately list two
		# variant strings that normalize to the same key (e.g. differing only in trailing
		# punctuation); on a --dry-run nothing is persisted between iterations, so without
		# this the second variant would be miscounted as "would create" again.
		seen_keys_this_run: set[str] = set()

		for canonical_name, (sponsor_type, variants) in SPONSOR_SEEDS.items():
			with transaction.atomic():
				sponsor, created = Sponsor.objects.get_or_create(
					name=canonical_name,
					defaults={
						"slug": _unique_sponsor_slug(canonical_name),
						"sponsor_type": sponsor_type,
						"sponsor_type_source": "curated",
					},
				)
				if created:
					families_created += 1
					if verbosity >= 2:
						self.stdout.write(f"Created canonical sponsor: {canonical_name}")
				elif (
					sponsor.sponsor_type != sponsor_type
					or sponsor.sponsor_type_source != "curated"
				):
					sponsor.sponsor_type = sponsor_type
					sponsor.sponsor_type_source = "curated"
					if not dry_run:
						sponsor.save(update_fields=["sponsor_type", "sponsor_type_source"])

				for variant in variants:
					key = normalize_sponsor_key(variant)
					if key is None or key in seen_keys_this_run:
						continue
					seen_keys_this_run.add(key)

					existing = SponsorAlias.objects.select_related("sponsor").filter(
						key=key
					).first()

					if existing is None:
						if verbosity >= 2:
							self.stdout.write(f"  + alias {key!r} -> {canonical_name}")
						if not dry_run:
							SponsorAlias.objects.create(
								sponsor=sponsor, key=key, raw_sample=variant
							)
						aliases_created += 1
						continue

					if existing.sponsor_id == sponsor.pk:
						continue  # already correct — idempotent no-op

					# The key was auto-created under a different (singleton) sponsor before
					# this family existed. Fold that sponsor into the canonical one: repoint
					# its trials, move its remaining aliases, delete it.
					stray_sponsor = existing.sponsor
					if verbosity >= 1:
						self.stdout.write(
							self.style.WARNING(
								f"  Folding stray sponsor {stray_sponsor.name!r} "
								f"(id={stray_sponsor.pk}) into {canonical_name!r}"
							)
						)
					if dry_run:
						trials_repointed += stray_sponsor.trials.count()
					else:
						trials_repointed_this, _ = merge_sponsors(sponsor, [stray_sponsor])
						trials_repointed += trials_repointed_this
					sponsors_folded += 1

				if dry_run:
					# Never persist anything from a dry run — the atomic block above may
					# have staged a get_or_create; roll it back.
					transaction.set_rollback(True)

		prefix = "Would create" if dry_run else "Created"
		self.stdout.write(
			self.style.SUCCESS(
				f"{prefix} {families_created} new canonical sponsor(s), "
				f"{aliases_created} new alias(es). Folded {sponsors_folded} stray "
				f"sponsor(s), repointing {trials_repointed} trial(s)."
			)
		)
