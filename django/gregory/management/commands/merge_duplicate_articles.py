"""
Merge duplicate ``Articles`` rows that describe the same paper.

Two rows converge on one paper in two shapes:

  * **Same DOI** — the common case. A DOI-less row (typically a BASE-search
    entry whose title differs only in punctuation/whitespace) is created weeks
    after the original, then ``find_doi`` fills in a DOI another row already
    holds. Use ``--scan`` to find every such group, or ``--doi`` to target
    specific DOIs.

  * **Different DOI, same paper** — preprint vs. published version. These can't
    be found automatically; pass them explicitly with ``--keep``/``--remove``.

For each group a survivor is chosen (manual relevance decision > has ML
predictions > earliest discovery_date > lowest id) and every other row is merged
into it and deleted. See ``gregory.services.article_merge`` for what "merge"
moves.

Runs as a **dry run by default** — nothing is committed unless ``--commit`` is
passed. Every merge logs a line (survivor id, removed id, doi) for audit.

Usage:
  # Preview every same-DOI duplicate group in the database:
  docker exec gregory python manage.py merge_duplicate_articles --scan

  # Commit the cleanup:
  docker exec gregory python manage.py merge_duplicate_articles --scan --commit

  # Specific DOIs:
  docker exec gregory python manage.py merge_duplicate_articles \
      --doi 10.1038/s41467-026-73802-w --commit

  # Preprint vs. published (different DOIs, same paper):
  docker exec gregory python manage.py merge_duplicate_articles \
      --keep 306723 --remove 317465 --commit
"""

from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count
from django.db.models.functions import Lower

from gregory.models import Articles
from gregory.services.article_merge import merge_articles, pick_survivor


class Command(BaseCommand):
	help = (
		"Merge duplicate Articles rows (same DOI, or explicitly listed) into one "
		"survivor, moving all relations, then deleting the duplicates. Dry run "
		"unless --commit is given."
	)

	def add_arguments(self, parser):
		parser.add_argument(
			"--scan",
			action="store_true",
			help="find and merge every group of articles sharing a DOI.",
		)
		parser.add_argument(
			"--doi",
			nargs="+",
			default=None,
			help="one or more DOIs whose articles should be merged.",
		)
		parser.add_argument(
			"--keep",
			type=int,
			default=None,
			help="article_id to keep (used with --remove for different-DOI pairs).",
		)
		parser.add_argument(
			"--remove",
			type=int,
			nargs="+",
			default=None,
			help="article_ids to merge into --keep and delete.",
		)
		parser.add_argument(
			"--commit",
			action="store_true",
			help="persist the merges (default is a dry run that rolls back).",
		)

	def handle(self, *args, **options):
		scan = options["scan"]
		dois = options["doi"]
		keep_id = options["keep"]
		remove_ids = options["remove"]
		commit = options["commit"]

		modes = [bool(scan), bool(dois), bool(keep_id or remove_ids)]
		if sum(modes) != 1:
			raise CommandError(
				"Choose exactly one of: --scan, --doi, or --keep/--remove."
			)

		with transaction.atomic():
			if keep_id or remove_ids:
				groups = self._explicit_group(keep_id, remove_ids)
			elif dois:
				groups = self._groups_for_dois(dois)
			else:
				groups = self._scan_groups()

			merged_count = 0
			for survivor, losers in groups:
				# Snapshot loser ids before the merge — delete() nulls their pk.
				loser_ids = [a.article_id for a in losers]
				survivor = merge_articles(survivor, losers, stdout=self.stdout)
				merged_count += len(losers)
				self.stdout.write(
					self.style.SUCCESS(
						f"Group kept article {survivor.article_id} "
						f"(doi={survivor.doi}); removed {loser_ids}."
					)
				)

			if not groups:
				self.stdout.write(self.style.WARNING("No duplicate groups found."))

			self.stdout.write(
				self.style.SUCCESS(
					f"Done. {merged_count} article(s) merged across "
					f"{len(groups)} group(s)."
				)
			)

			if not commit:
				transaction.set_rollback(True)
				self.stdout.write(
					self.style.WARNING(
						"DRY RUN — all changes rolled back. Re-run with --commit "
						"to persist."
					)
				)

	def _explicit_group(self, keep_id, remove_ids):
		if not keep_id or not remove_ids:
			raise CommandError("--keep and --remove must be given together.")
		remove_ids = [r for r in remove_ids if r != keep_id]
		if not remove_ids:
			raise CommandError(
				"--remove must contain at least one id different from --keep."
			)
		try:
			keep = Articles.objects.get(pk=keep_id)
		except Articles.DoesNotExist:
			raise CommandError(f"--keep article {keep_id} does not exist.")
		losers = []
		for rid in remove_ids:
			try:
				losers.append(Articles.objects.get(pk=rid))
			except Articles.DoesNotExist:
				self.stderr.write(
					self.style.WARNING(f"skip: article {rid} does not exist.")
				)
		return [(keep, losers)] if losers else []

	def _groups_for_dois(self, dois):
		groups = []
		for doi in dois:
			articles = list(Articles.objects.filter(doi__iexact=doi))
			if len(articles) < 2:
				self.stderr.write(
					self.style.WARNING(
						f"skip: DOI {doi} matches {len(articles)} article(s), "
						"nothing to merge."
					)
				)
				continue
			survivor = pick_survivor(articles)
			losers = [a for a in articles if a.article_id != survivor.article_id]
			groups.append((survivor, losers))
		return groups

	def _scan_groups(self):
		# Group case-insensitively on non-empty DOIs; only DOIs held by >1 row.
		dup_dois = set(
			Articles.objects.exclude(doi__isnull=True)
			.exclude(doi="")
			.annotate(ldoi=Lower("doi"))
			.values("ldoi")
			.annotate(n=Count("article_id"))
			.filter(n__gt=1)
			.values_list("ldoi", flat=True)
		)
		buckets = defaultdict(list)
		for article in Articles.objects.filter(
			doi__isnull=False
		).exclude(doi=""):
			key = article.doi.lower()
			if key in dup_dois:
				buckets[key].append(article)

		groups = []
		for articles in buckets.values():
			if len(articles) < 2:
				continue
			survivor = pick_survivor(articles)
			losers = [a for a in articles if a.article_id != survivor.article_id]
			groups.append((survivor, losers))
		return groups
