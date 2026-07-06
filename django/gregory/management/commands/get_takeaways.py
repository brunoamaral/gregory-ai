"""
get_takeaways
=============
Generate ML takeaways for science papers and store them in ``ArticleOrgContent``
(one row per article × organisation).

A single summariser call is made per article (since the abstract is identical
regardless of organisation), and the resulting text is fanned out to every
organisation the article belongs to via teams that does not already have a
non-empty ``takeaways`` row.

Articles are processed oldest-first (``order_by("article_id")``) so a backlog
is worked down from the front instead of always favouring recent ingestion.
The candidate queryset is filtered up front to articles that still need work
(at least one linked organisation missing a non-empty ``takeaways`` row), so
already-filled articles are never fetched and never consume ``--limit``.

Usage
-----
Default (pipeline cron)::

    python manage.py get_takeaways

Scoped to one organisation::

    python manage.py get_takeaways --org-id 3

Bigger / smaller batch, dry run::

    python manage.py get_takeaways --limit 50
    python manage.py get_takeaways --dry-run
"""

from bs4 import BeautifulSoup
import html
import time

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Exists, OuterRef, Q
from django.db.models.functions import Length

from gregory.models import Articles, ArticleOrgContent
from transformers import pipeline

import logging


class Command(BaseCommand):
	help = (
		"Summarise article abstracts and store the result in ArticleOrgContent "
		"for every organisation the article belongs to (via teams)."
	)

	def add_arguments(self, parser):
		parser.add_argument(
			"--org-id",
			type=int,
			dest="org_id",
			help="Restrict generation to a single organisation.",
		)
		parser.add_argument(
			"--limit",
			type=int,
			default=20,
			dest="limit",
			help="Maximum number of articles to summarise in this run (default: 20).",
		)
		parser.add_argument(
			"--dry-run",
			action="store_true",
			dest="dry_run",
			help="Report what would be generated without loading the model or writing rows.",
		)

	# ------------------------------------------------------------------
	# Helpers
	# ------------------------------------------------------------------

	@staticmethod
	def clean_html(input_text):
		return BeautifulSoup(input_text, "html.parser").get_text()

	@staticmethod
	def get_summary_max_length(text):
		nr_words = len(text.split())
		max_length = 100
		return min(max_length, int(nr_words / 2))

	@staticmethod
	def summarize_abstract(article_id, abstract, summarizer, min_length=25):
		start = time.time()
		max_length = Command.get_summary_max_length(abstract)
		if max_length > min_length:
			logging.info(
				f"Summarizing abstract {article_id} with lengths [{min_length}, {max_length}]"
			)
			summary = summarizer(
				abstract, min_length=min_length, max_length=max_length, truncation=True
			)
			end = time.time()
			logging.info(f" => Elapsed time: {end - start} sec.")
			return summary[0]["summary_text"] if summary else ""
		return ""

	@staticmethod
	def _orgs_for_article(article, org_id=None):
		"""Return organisations linked to *article* via its teams, optionally scoped."""
		from django.apps import apps

		Organization = apps.get_model("organizations", "Organization")
		qs = Organization.objects.filter(teams__articles=article).distinct()
		if org_id is not None:
			qs = qs.filter(pk=org_id)
		return list(qs)

	@staticmethod
	def _orgs_missing_takeaways(article, orgs):
		"""Of *orgs*, return the ones whose ArticleOrgContent row is missing or has empty takeaways."""
		existing = (
			ArticleOrgContent.objects.filter(article=article, organization__in=orgs)
			.exclude(takeaways__isnull=True)
			.exclude(takeaways="")
			.values_list("organization_id", flat=True)
		)
		filled = set(existing)
		return [org for org in orgs if org.pk not in filled]

	@staticmethod
	def _needs_work_qs(qs, org_id=None):
		"""Restrict *qs* to articles that still need at least one takeaway.

		An article needs work when at least one organisation linked to it via
		teams (optionally scoped to *org_id*) has no ``ArticleOrgContent`` row,
		or has one with a NULL/empty ``takeaways``. Implemented as a single
		``Exists`` subquery so it composes cleanly with ``.iterator()``.
		"""
		from django.apps import apps

		Organization = apps.get_model("organizations", "Organization")

		outer_article = OuterRef("pk")
		orgs_for_article = Organization.objects.filter(teams__articles=outer_article)
		if org_id is not None:
			orgs_for_article = orgs_for_article.filter(pk=org_id)

		already_filled = (
			Q(article_contents__article=outer_article)
			& Q(article_contents__takeaways__isnull=False)
			& ~Q(article_contents__takeaways="")
		)
		orgs_missing = orgs_for_article.exclude(already_filled)

		return qs.filter(Exists(orgs_missing))

	# ------------------------------------------------------------------
	# Entry point
	# ------------------------------------------------------------------

	def handle(self, *args, **options):
		org_id = options.get("org_id")
		limit = options.get("limit")
		if limit is None:
			limit = 20
		dry_run = options.get("dry_run")

		if limit <= 0:
			raise CommandError("--limit must be a positive integer.")

		# Base candidate pool: science papers with an abstract in the usable length range.
		# We order oldest-first so a run works down a backlog instead of always
		# favouring the most recently ingested articles.
		base_qs = (
			Articles.objects.annotate(abstract_length=Length("summary"))
			.filter(
				abstract_length__gte=25,
				abstract_length__lte=3000,
				kind="science paper",
			)
			.order_by("article_id")
		)

		# Filter up front to articles that actually still need work, so
		# already-filled articles are never fetched and never consume --limit.
		base_qs = self._needs_work_qs(base_qs, org_id=org_id)

		summarizer = None
		processed = 0
		created_rows = 0
		updated_rows = 0
		scanned = 0
		orphans = 0

		for article in base_qs.iterator(chunk_size=100):
			if processed >= limit:
				break
			scanned += 1

			orgs = self._orgs_for_article(article, org_id=org_id)
			if not orgs:
				# Should be rare now that the queryset filters for pending work:
				# it can only happen if the article's teams/orgs changed between
				# the query running and this row being processed.
				if org_id is None:
					orphans += 1
					self.stderr.write(
						self.style.WARNING(
							f"Skipping orphan article {article.article_id}: no organisations via teams."
						)
					)
				continue

			# Cheap per-article re-check: the queryset annotation and this write
			# are not atomic, so re-derive the exact set of orgs still missing a
			# takeaway rather than trusting the upfront filter blindly.
			missing_orgs = self._orgs_missing_takeaways(article, orgs)
			if not missing_orgs:
				continue

			try:
				abstract = (
					self.clean_html(html.unescape(article.summary))
					.replace("\n", " ")
					.replace("\r", " ")
				)
			except Exception as e:
				self.stderr.write(
					self.style.WARNING(
						f"Skipping article {article.article_id}: failed to clean abstract: {e}"
					)
				)
				continue

			if dry_run:
				self.stdout.write(
					f"[DRY RUN] Would summarise article {article.article_id} and fill "
					f"{len(missing_orgs)} org row(s): {[o.pk for o in missing_orgs]}"
				)
				processed += 1
				continue

			if summarizer is None:
				self.stdout.write("Loading the model")
				summarizer = pipeline(
					"summarization", model="philschmid/bart-large-cnn-samsum"
				)
				summarizer.tokenizer.model_max_length = 1024
				self.stdout.write("Summarizer model ready for use")

			try:
				takeaways = self.summarize_abstract(
					article.article_id, abstract, summarizer
				)
			except Exception as e:
				self.stderr.write(
					self.style.WARNING(
						f"Skipping article {article.article_id}: summariser failed: {e}"
					)
				)
				continue

			if not takeaways:
				continue

			for org in missing_orgs:
				aoc, created = ArticleOrgContent.objects.get_or_create(
					article=article,
					organization=org,
					defaults={"takeaways": takeaways},
				)
				if created:
					created_rows += 1
				else:
					aoc.takeaways = takeaways
					aoc.save(update_fields=["takeaways", "updated_at"])
					updated_rows += 1

			processed += 1

		summary_style = self.style.WARNING if dry_run else self.style.SUCCESS
		self.stdout.write(
			summary_style(
				f"{'[DRY RUN] ' if dry_run else ''}"
				f"Processed {processed} article(s) (examined {scanned} candidate(s), orphans skipped {orphans}). "
				f"Created {created_rows} new ArticleOrgContent row(s), "
				f"updated {updated_rows} existing row(s)."
			)
		)
