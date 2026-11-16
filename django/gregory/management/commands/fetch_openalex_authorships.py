"""
Fetch author positions and institutions from OpenAlex and store them as ArticleAuthorship rows.

Selects articles that have a DOI and at least one linked author, have never been fetched
(``openalex_checked_at`` empty) and are due by the back-off marker. Newest first. OpenAlex
takes 100 DOIs per request. Each returned authorship is matched to one of the article's
authors (ORCID, then family name plus initial); authors are never created from OpenAlex.

A DOI OpenAlex doesn't know yet records a fruitless attempt, so the article is retried
later with back-off (new papers can take days to reach OpenAlex). A budget or rate-limit
response (429 or another 4xx) stops the run without marking anything; a network failure
skips one batch and carries on.

Optional key: OPENALEX_API_KEY. Without one the daily budget is smaller and shared with
everyone on the same IP address. This command never sends an email address.

Run:
	docker exec gregory python manage.py fetch_openalex_authorships --dry-run
	docker exec gregory python manage.py fetch_openalex_authorships --limit 60000
"""

import fcntl
import os
import tempfile

from django.core.management.base import CommandError
from django.db.models import Exists, F, OuterRef
from django.utils import timezone

from gregory.management.base import GregoryBaseCommand
from gregory.models import Articles
from gregory.services.openalex_authorships import apply_openalex_work, recompute_author_openalex_ids
from gregory.utils.enrichment import backoff_delta, due_filter
from gregory.utils.openalex import (
	MAX_DOIS_PER_REQUEST,
	OpenAlexBudgetError,
	OpenAlexClient,
	OpenAlexError,
	bare_doi,
	parse_work,
	usable_doi,
)

DEFAULT_LIMIT = 2000
LOCK_PATH = os.path.join(tempfile.gettempdir(), "fetch_openalex_authorships.lock")


def articles_to_fetch(*, refresh=False, ignore_backoff=False, article_id=None):
	"""Articles with a DOI and at least one author whose authorships should be fetched."""
	has_authors = Exists(Articles.authors.through.objects.filter(articles_id=OuterRef("pk")))
	qs = Articles.objects.filter(doi__isnull=False).exclude(doi="").filter(has_authors)
	if article_id is not None:
		return qs.filter(pk=article_id).order_by("pk")
	if refresh:
		return qs.filter(openalex_checked_at__isnull=False).order_by("openalex_checked_at", "pk")
	qs = qs.filter(openalex_checked_at__isnull=True)
	if not ignore_backoff:
		qs = qs.filter(due_filter("openalex_next_check"))
	return qs.order_by(F("published_date").desc(nulls_last=True), "-pk")


def record_fruitless_attempt(article):
	"""OpenAlex answered but doesn't know the paper: push the next check out.

	Through update(), not save(): the marker changes on every attempt and a save would add
	an article history row each time.
	"""
	attempts = article.openalex_attempts + 1
	Articles.objects.filter(pk=article.pk).update(
		openalex_attempts=attempts,
		openalex_next_check=timezone.now() + backoff_delta(attempts),
	)


class Command(GregoryBaseCommand):
	help = "Fetch author positions and institutions from OpenAlex for articles with a DOI."

	def add_arguments(self, parser):
		parser.add_argument(
			"--limit",
			type=int,
			default=DEFAULT_LIMIT,
			help=f"Most articles to fetch in one run (default {DEFAULT_LIMIT}).",
		)
		parser.add_argument(
			"--all",
			action="store_true",
			dest="ignore_backoff",
			help="Ignore the back-off marker: retry articles OpenAlex didn't know yet.",
		)
		parser.add_argument(
			"--refresh",
			action="store_true",
			help="Re-fetch articles that were already checked, oldest check first.",
		)
		parser.add_argument("--article-id", type=int, help="Fetch this one article, whatever its state.")
		parser.add_argument(
			"--dry-run",
			action="store_true",
			help="Report how many articles are due without calling OpenAlex or writing anything.",
		)

	def handle(self, *args, **options):
		limit = options["limit"]
		if limit < 1:
			raise CommandError("--limit must be at least 1.")
		queryset = articles_to_fetch(
			refresh=options["refresh"],
			ignore_backoff=options["ignore_backoff"],
			article_id=options["article_id"],
		)
		pks = list(queryset.values_list("pk", flat=True)[:limit])
		self.log(f"{len(pks)} articles due for an OpenAlex fetch (limit {limit}).", level=1)
		if options["dry_run"] or not pks:
			return

		with open(LOCK_PATH, "a") as lock:
			try:
				fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
			except BlockingIOError:
				self.log("Another fetch_openalex_authorships run is in progress; stopping.", level=1)
				return
			self._run(OpenAlexClient(), pks)

	def _run(self, client, pks):
		totals = dict(
			fetched=0, matched=0, unmatched=0, not_found=0, unusable=0, institutions=0, failed_batches=0
		)
		touched = set()
		stopped = False
		try:
			for start in range(0, len(pks), MAX_DOIS_PER_REQUEST):
				batch = list(Articles.objects.filter(pk__in=pks[start : start + MAX_DOIS_PER_REQUEST]))
				if not self._process(client, batch, totals, touched):
					stopped = True
					break
		finally:
			if touched:
				recompute_author_openalex_ids(touched)

		self.log(
			"OpenAlex authorships: {fetched} articles fetched, {matched} author links matched, "
			"{unmatched} authorships unmatched, {not_found} DOIs not found, {unusable} unusable DOIs "
			"skipped, {institutions} institutions created, {failed_batches} failed batches.".format(
				**totals
			),
			level=1,
		)
		self.log(f"OpenAlex cost this run: ${client.total_cost_usd:.4f}.", level=1)
		if stopped:
			self.log(
				"Stopped: OpenAlex refused a request (daily budget spent or rate limit). "
				"Nothing was marked for the rest; run again later.",
				level=1,
				style_func=self.style.WARNING,
			)

	def _process(self, client, batch, totals, touched):
		"""Fetch and apply one batch. Returns False when the run must stop."""
		by_doi = {}
		for article in batch:
			if not usable_doi(article.doi):
				totals["unusable"] += 1
				if article.openalex_checked_at is None:
					record_fruitless_attempt(article)
				continue
			by_doi[bare_doi(article.doi)] = article
		if not by_doi:
			return True

		try:
			works = client.works_by_doi(list(by_doi))
		except OpenAlexBudgetError as exc:
			self.log(str(exc), level=1, style_func=self.style.WARNING)
			return False
		except OpenAlexError as exc:
			totals["failed_batches"] += 1
			self.log(f"OpenAlex batch failed, skipping it: {exc}", level=1, style_func=self.style.WARNING)
			return True

		authors_by_article = self._authors_by_article(a.pk for a in by_doi.values())
		for doi, article in by_doi.items():
			raw = works.get(doi)
			if raw is None:
				totals["not_found"] += 1
				if article.openalex_checked_at is None:
					record_fruitless_attempt(article)
				continue
			try:
				result = apply_openalex_work(
					article, parse_work(raw), authors=authors_by_article.get(article.pk, [])
				)
			except Exception as exc:
				self.log(
					f"Article {article.article_id} failed: {type(exc).__name__}: {exc}",
					level=1,
					style_func=self.style.WARNING,
				)
				continue
			totals["fetched"] += 1
			totals["matched"] += result.matched
			totals["unmatched"] += result.unmatched
			totals["institutions"] += result.institutions_created
			touched.update(result.author_ids)
		return True

	def _authors_by_article(self, article_ids):
		"""Every batch article's authors, in one query."""
		through = Articles.authors.through
		by_article = {}
		for link in through.objects.filter(articles_id__in=list(article_ids)).select_related("authors"):
			by_article.setdefault(link.articles_id, []).append(link.authors)
		return by_article
