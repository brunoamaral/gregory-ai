"""
Find PubMed IDs for articles that have a DOI but no PMID, and apply PubMed's record.

DOIs go to PubMed 50 at a time as one search (``"<doi>"[doi] OR …``), the matching
records are fetched, and a record is kept only when its DOI equals the article's DOI
after normalisation: a near-miss is never attached. A kept record is applied with
``apply_pubmed_record`` (fills the PMID and PMCID, replaces a truncated or empty summary,
adds publication types and MeSH), so ``refresh_pubmed_records`` won't fetch it again.

An article PubMed has no record for backs off like the other enrichment tasks
(``pmid_lookup_next_check``, 2, 4, 8, 16 then 30 days), so it isn't asked about on every
run. A network failure moves nothing: the next run retries.

By default only articles discovered in the last 30 days are looked at (``--days``); older
ones are handled once by hand with ``--all``.

Run:
	docker exec gregory python manage.py find_pmids --dry-run
	docker exec gregory python manage.py find_pmids --all --limit 50000
"""

import fcntl
import os
import re
import tempfile
from datetime import timedelta

from django.core.management.base import CommandError
from django.utils import timezone
from organizations.models import Organization

from gregory.management.base import GregoryBaseCommand
from gregory.models import Articles
from gregory.services.pubmed_records import apply_pubmed_record
from gregory.utils.doi_utils import normalize_doi
from gregory.utils.enrichment import backoff_delta, due_filter
from gregory.utils.ncbi import NcbiClient, NcbiError, NcbiQueryError, NcbiResultCapError
from gregory.utils.pubmed_xml import parse_pubmed_xml

DOIS_PER_SEARCH = 50
FETCH_BATCH_SIZE = 200
DEFAULT_DAYS = 30
DEFAULT_LIMIT = 2000
LOCK_PATH = os.path.join(tempfile.gettempdir(), "find_pmids.lock")
# This many searches in a row that PubMed rejects, with none accepted in between, means
# PubMed is having trouble, not that fifty DOIs are all unreadable: stop, move nothing.
ABORT_AFTER_CONSECUTIVE_REJECTIONS = 5

_RESOLVER_PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*|doi/)", re.IGNORECASE)
_NOT_A_DOI = re.compile(r"^(?:pmid|pmc|pmcid)\s*:", re.IGNORECASE)


def search_form(stored_doi):
	"""The DOI as it should be put to PubMed: resolver prefixes removed, lowercased, but not
	stripped of closing brackets that belong to it. None for a value that isn't a DOI."""
	doi = (stored_doi or "").strip()
	if not doi or _NOT_A_DOI.match(doi):
		return None
	doi = _RESOLVER_PREFIX.sub("", doi).strip().lower()
	return doi if doi.startswith("10.") else None


def build_doi_term(dois):
	"""``"<doi>"[doi] OR "<doi>"[doi] …`` with quotes stripped from the DOIs."""
	return " OR ".join(f'"{doi.replace(chr(34), "")}"[doi]' for doi in dois)


def articles_needing_pmid(days=DEFAULT_DAYS, everything=False, now=None):
	"""Articles with a DOI and no PMID whose lookup is due, newest discoveries first."""
	now = now or timezone.now()
	queryset = Articles.objects.filter(
		due_filter("pmid_lookup_next_check"),
		pmid__isnull=True,
		doi__isnull=False,
	).exclude(doi="")
	if not everything:
		queryset = queryset.filter(discovery_date__gte=now - timedelta(days=days))
	return queryset.order_by("-discovery_date", "-article_id")


class Command(GregoryBaseCommand):
	help = "Find PMIDs for articles with a DOI and no PMID, and apply PubMed's record."

	def add_arguments(self, parser):
		parser.add_argument(
			"--days",
			type=int,
			default=DEFAULT_DAYS,
			help=f"Only articles discovered in the last N days (default {DEFAULT_DAYS}).",
		)
		parser.add_argument("--all", action="store_true", help="Every article, whatever its age.")
		parser.add_argument(
			"--limit",
			type=int,
			default=DEFAULT_LIMIT,
			help=f"Most articles to look at in one run (default {DEFAULT_LIMIT}).",
		)
		parser.add_argument(
			"--organization",
			type=int,
			help="Organization ID whose NCBI credentials to use. Default: keyless.",
		)
		parser.add_argument(
			"--dry-run", action="store_true", help="Report how many are due without calling NCBI."
		)

	def handle(self, *args, **options):
		if options["limit"] < 1:
			raise CommandError("--limit must be at least 1.")
		pks = list(
			articles_needing_pmid(options["days"], options["all"]).values_list("pk", flat=True)[
				: options["limit"]
			]
		)
		self.log(f"{len(pks)} articles with a DOI and no PMID are due for a lookup.", level=1)
		if options["dry_run"] or not pks:
			return

		with open(LOCK_PATH, "a") as lock:
			try:
				fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
			except BlockingIOError:
				self.log("Another find_pmids run is in progress; stopping.", level=1)
				return
			client = self._client(options.get("organization"))
			totals = dict(
				matched=0, no_match=0, rejected=0, conflicts=0, failed=0, summaries_replaced=0,
				unreadable=0, searches_ok=0, rejections_in_a_row=0, aborted=False,
			)
			for start in range(0, len(pks), DOIS_PER_SEARCH):
				if totals["aborted"]:
					break
				batch = list(Articles.objects.filter(pk__in=pks[start : start + DOIS_PER_SEARCH]))
				self._process(client, batch, totals)
			if totals["aborted"]:
				self.log(
					"PubMed rejected "
					f"{ABORT_AFTER_CONSECUTIVE_REJECTIONS} searches in a row; stopping. Nothing "
					"further was backed off, so the next run retries.",
					level=1,
					style_func=self.style.WARNING,
				)

		self.log(
			"find_pmids: {matched} matched, {no_match} with no PubMed record (backed off), "
			"{rejected} near-miss records rejected, {unreadable} DOIs PubMed would not read, "
			"{conflicts} PMIDs already on another article, {failed} failed, "
			"{summaries_replaced} summaries replaced.".format(**totals),
			level=1,
		)

	def _client(self, organization_id):
		if not organization_id:
			return NcbiClient.for_site()
		try:
			organization = Organization.objects.get(pk=organization_id)
		except Organization.DoesNotExist:
			raise CommandError(f"No organization with id {organization_id}.")
		return NcbiClient.for_organization(organization)

	# -- one batch of up to 50 articles ----------------------------------------

	def _process(self, client, batch, totals):
		if totals["aborted"]:
			return
		by_doi, terms, unusable = {}, [], []
		for article in batch:
			asked = search_form(article.doi)
			doi = normalize_doi(asked) if asked else None
			if doi:
				by_doi[doi] = article
				terms.append(asked)
			else:
				unusable.append(article)  # empty once normalised, or a pmid:/pmc: value
		if unusable:
			self._back_off(unusable, totals)
		if not by_doi:
			return
		batch = list(by_doi.values())
		try:
			result = client.esearch(build_doi_term(terms), retmax=DOIS_PER_SEARCH * 4)
			if len(result.ids) < result.count:
				# The list was cut short: the PMIDs that fell off would look like "no record".
				raise NcbiQueryError(f"{result.count} results but {len(result.ids)} IDs listed")
		except (NcbiQueryError, NcbiResultCapError) as exc:
			self._rejected(client, batch, totals, exc)
			return
		except NcbiError as exc:
			totals["failed"] += len(batch)
			self.log(f"Search for {len(batch)} DOIs failed: {exc}", level=1, style_func=self.style.WARNING)
			return
		totals["searches_ok"] += 1
		totals["rejections_in_a_row"] = 0
		pmids = result.ids

		matched, returned = set(), set()
		for start in range(0, len(pmids), FETCH_BATCH_SIZE):
			try:
				parsed = parse_pubmed_xml(client.efetch_pubmed(pmids[start : start + FETCH_BATCH_SIZE]))
			except Exception as exc:
				totals["failed"] += len(batch)
				self.log(f"Fetching records failed: {exc}", level=1, style_func=self.style.WARNING)
				return  # nothing moves: the whole batch is retried next run
			for record in parsed.records:
				returned.add(record.pmid)
				article = next(
					(by_doi[d] for d in (record.dois or [record.doi]) if d and d in by_doi), None
				)
				if article is None:
					# PubMed's DOI isn't one of ours (or it has none): never attach a near-miss.
					totals["rejected"] += 1
					continue
				if article.pk in matched:
					continue
				matched.add(article.pk)
				self._apply(article, record, totals)

		rest = [a for a in batch if a.pk not in matched]
		if set(pmids) - returned:
			# EFetch gave back fewer records than the search found (a PMID merged or deleted
			# between the two calls, or a partial answer): don't read that as "no record".
			totals["failed"] += len(rest)
			return
		self._back_off(rest, totals)

	def _rejected(self, client, batch, totals, exc):
		"""PubMed wouldn't take this search. Halve to find the DOI it chokes on, unless it is
		rejecting everything, which means PubMed is struggling and nothing should move."""
		totals["rejections_in_a_row"] += 1
		if totals["rejections_in_a_row"] >= ABORT_AFTER_CONSECUTIVE_REJECTIONS:
			totals["aborted"] = True
			return
		if len(batch) > 1:
			middle = len(batch) // 2
			self._process(client, batch[:middle], totals)
			self._process(client, batch[middle:], totals)
		elif totals["searches_ok"]:
			self.log(f"PubMed rejected DOI {batch[0].doi}: {exc}", level=1, style_func=self.style.WARNING)
			totals["unreadable"] += 1
			self._back_off([batch[0]], totals, count=False)
		else:
			totals["failed"] += 1  # no search has worked yet this run: not evidence about this DOI

	def _apply(self, article, record, totals):
		try:
			result = apply_pubmed_record(article, record, is_new=False)
		except Exception as exc:
			totals["failed"] += 1
			self.log(
				f"Article {article.article_id} (PMID {record.pmid}) failed: {exc}",
				level=1,
				style_func=self.style.WARNING,
			)
			return
		if result.pmid_conflict:
			# Another article already holds this PMID: a duplicate pair for
			# merge_duplicate_articles. Not retried every run.
			totals["conflicts"] += 1
			self._back_off([article], totals, count=False)
			return
		totals["matched"] += 1
		totals["summaries_replaced"] += result.summary_replaced
		Articles.objects.filter(pk=article.pk).update(pmid_lookup_attempts=0, pmid_lookup_next_check=None)

	def _back_off(self, articles, totals, count=True):
		"""PubMed answered and has nothing for these: push the next check out. update()
		keeps the churn out of the article history."""
		now = timezone.now()
		for article in articles:
			attempts = article.pmid_lookup_attempts + 1
			Articles.objects.filter(pk=article.pk).update(
				pmid_lookup_attempts=attempts,
				pmid_lookup_next_check=now + backoff_delta(attempts),
			)
		if count:
			totals["no_match"] += len(articles)
