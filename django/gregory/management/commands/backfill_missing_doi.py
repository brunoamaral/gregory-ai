"""
One-time backfill for the ~747 science-paper articles ingested without a DOI
before the ingestion fix (articles-missing-doi). Investigation found three
buckets:

  - ~226 have the DOI literally embedded in the article URL (Springer, PNAS,
    doi.org/dx.doi.org redirects, ...).
  - ~166 are PubMed links whose DOI can be resolved via the PMID through NCBI
    E-utilities.
  - ~355 are repositories/theses with genuinely no DOI (out of scope; these
    land in the "not found" bucket and are left alone).

For each DOI-less ``kind="science paper"`` article, tries in order:
  1. ``extract_doi_from_url(article.link)``
  2. the PubMed PMID -> DOI API, for pubmed.ncbi.nlm.nih.gov links
  3. the existing CrossRef title search (``gregory.functions.get_doi``)

A found DOI is assigned through ``assign_doi_or_merge`` (the same
collision-safe guard ``find_doi`` uses) so a DOI that another article already
holds merges the two rows instead of tripping the unique constraint.

Selection deliberately ignores the ``doi_lookup_next_check`` backoff used by
``find_doi`` -- most of these 747 rows have already exhausted their CrossRef
title-search retries and would otherwise never be picked up again.

Run:
	docker exec gregory python manage.py backfill_missing_doi --dry-run
	docker exec gregory python manage.py backfill_missing_doi --limit 50
	docker exec gregory python manage.py backfill_missing_doi
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

import gregory.functions as greg
from gregory.models import Articles
from gregory.services.article_merge import assign_doi_or_merge
from gregory.utils.doi_utils import extract_doi_from_url, resolve_doi_from_pubmed_url
from gregory.utils.enrichment import clear_marker

METHOD_LABELS = {
	"url": "Resolved via URL",
	"pubmed": "Resolved via PubMed",
	"crossref": "Resolved via CrossRef",
	"not_found": "Not found",
}


class Command(BaseCommand):
	help = (
		"Backfill missing DOIs for science-paper articles, trying the article "
		"URL, the PubMed PMID API, and a CrossRef title search in that order."
	)

	def add_arguments(self, parser):
		parser.add_argument(
			"--dry-run",
			action="store_true",
			help="Report what would be assigned without writing to the database.",
		)
		parser.add_argument(
			"--limit",
			type=int,
			default=None,
			help="Only process the first N matching articles (useful for testing).",
		)

	def handle(self, *args, **options):
		dry_run = options["dry_run"]
		limit = options["limit"]

		# due_filter is deliberately NOT applied: most of these rows have
		# already exhausted their find_doi backoff and would never be
		# reselected otherwise.
		queryset = (
			Articles.objects.filter(kind="science paper")
			.filter(Q(doi__isnull=True) | Q(doi=""))
			.order_by("article_id")
		)
		if limit:
			queryset = queryset[:limit]

		articles = list(queryset)
		counts = {"url": 0, "pubmed": 0, "crossref": 0, "not_found": 0}

		for article in articles:
			doi, method = self._resolve_doi(article)

			if not doi:
				counts["not_found"] += 1
				self.log(f"[not found] article {article.article_id}: {article.title}")
				continue

			counts[method] += 1

			if dry_run:
				self.stdout.write(
					self.style.SUCCESS(
						f"[dry-run:{method}] article {article.article_id} "
						f"({article.title}) -> {doi}"
					)
				)
				continue

			with transaction.atomic():
				# save=False so the DOI and the cleared backoff marker persist
				# in a single save on the no-collision path, mirroring find_doi.
				survivor, merged = assign_doi_or_merge(article, doi, save=False)
				clear_marker(survivor, "doi_lookup", save=False)
				survivor.save()

			if merged:
				self.stdout.write(
					self.style.WARNING(
						f"[{method}] article {article.article_id}: DOI {doi} already "
						f"existed -- merged into article {survivor.article_id}."
					)
				)
			else:
				self.stdout.write(
					self.style.SUCCESS(
						f"[{method}] article {survivor.article_id}: assigned DOI {doi}."
					)
				)

		self._print_summary(counts, dry_run)

	def _resolve_doi(self, article):
		"""Try each resolution method in order. Returns (doi, method_key) or (None, None)."""
		doi = extract_doi_from_url(article.link)
		if doi:
			return doi, "url"

		if article.link and "pubmed.ncbi.nlm.nih.gov" in article.link.lower():
			doi = resolve_doi_from_pubmed_url(article.link)
			if doi:
				return doi, "pubmed"

		try:
			doi = greg.get_doi(article.title)
		except Exception as e:
			self.stdout.write(
				self.style.WARNING(
					f"CrossRef lookup failed for article {article.article_id} "
					f"('{article.title}'): {e}. Skipping."
				)
			)
			doi = None
		if doi:
			return doi, "crossref"

		return None, None

	def log(self, message):
		self.stdout.write(message)

	def _print_summary(self, counts, dry_run):
		self.stdout.write("")
		self.stdout.write(self.style.MIGRATE_HEADING("Summary"))
		for key in ("url", "pubmed", "crossref", "not_found"):
			self.stdout.write(f"  {METHOD_LABELS[key]}: {counts[key]}")
		total_resolved = counts["url"] + counts["pubmed"] + counts["crossref"]
		self.stdout.write(f"  Total resolved: {total_resolved}")
		self.stdout.write(f"  Total processed: {total_resolved + counts['not_found']}")
		if dry_run:
			self.stdout.write(
				self.style.WARNING("Dry run -- no changes were written to the database.")
			)
