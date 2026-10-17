"""
Fetch PubMed's record for articles that have a PMID and apply it.

Selects articles with a ``pmid`` where:
  - ``pubmed_checked_at`` is null (from RSS, the backfill or the DOI lookup), or
  - the record was still being indexed (In-Process, In-Data-Review, Publisher)
    and the last check was over 30 days ago, or
  - the last check was over a year ago.
Never-checked articles go first. EFetch takes 200 PMIDs per call.

A PMID that EFetch doesn't return (deleted or merged at NCBI) gets
``pubmed_checked_at`` set and a log line, so it isn't retried until the yearly pass.

Run:
	docker exec gregory python manage.py refresh_pubmed_records --dry-run
	docker exec gregory python manage.py refresh_pubmed_records --limit 50000
"""

from datetime import timedelta

from django.db.models import F, Q
from django.utils import timezone
from organizations.models import Organization

from gregory.management.base import GregoryBaseCommand
from gregory.models import Articles
from gregory.services.pubmed_records import apply_pubmed_record
from gregory.utils.ncbi import NcbiClient
from gregory.utils.pubmed_xml import parse_pubmed_xml

BATCH_SIZE = 200
DEFAULT_LIMIT = 2000
IN_PROGRESS_STATUSES = ("In-Process", "In-Data-Review", "Publisher")
IN_PROGRESS_RECHECK_DAYS = 30
RECHECK_DAYS = 365


def articles_due_for_refresh(now=None):
	"""Articles with a PMID whose PubMed record should be fetched, never-checked first."""
	now = now or timezone.now()
	return (
		Articles.objects.filter(pmid__isnull=False)
		.filter(
			Q(pubmed_checked_at__isnull=True)
			| Q(
				pubmed_status__in=IN_PROGRESS_STATUSES,
				pubmed_checked_at__lt=now - timedelta(days=IN_PROGRESS_RECHECK_DAYS),
			)
			| Q(pubmed_checked_at__lt=now - timedelta(days=RECHECK_DAYS))
		)
		.order_by(F("pubmed_checked_at").asc(nulls_first=True), "article_id")
	)


class Command(GregoryBaseCommand):
	help = "Fetch and apply PubMed records for articles that have a PMID."

	def add_arguments(self, parser):
		parser.add_argument(
			"--limit",
			type=int,
			default=DEFAULT_LIMIT,
			help=f"Most articles to refresh in one run (default {DEFAULT_LIMIT}).",
		)
		parser.add_argument(
			"--organization",
			type=int,
			help="Organization ID whose NCBI credentials to use. Default: keyless.",
		)
		parser.add_argument(
			"--dry-run",
			action="store_true",
			help="Report how many articles are due without calling NCBI.",
		)

	def handle(self, *args, **options):
		due = articles_due_for_refresh()
		articles = list(due[: options["limit"]])
		self.log(f"{len(articles)} articles due for a PubMed refresh.", level=1)
		if options["dry_run"] or not articles:
			return

		client = self._client(options.get("organization"))
		totals = dict(fetched=0, updated=0, summaries_replaced=0, retracted=0, missing=0, failed_batches=0)
		for start in range(0, len(articles), BATCH_SIZE):
			self._refresh_batch(client, articles[start : start + BATCH_SIZE], totals)

		self.log(
			"PubMed refresh: {fetched} fetched, {updated} updated, {summaries_replaced} "
			"summaries replaced, {retracted} retracted, {missing} missing at NCBI, "
			"{failed_batches} failed batches.".format(**totals),
			level=1,
		)

	def _client(self, organization_id):
		if not organization_id:
			return NcbiClient()
		try:
			organization = Organization.objects.get(pk=organization_id)
		except Organization.DoesNotExist:
			from django.core.management.base import CommandError

			raise CommandError(f"No organization with id {organization_id}.")
		return NcbiClient.for_organization(organization)

	def _refresh_batch(self, client, batch, totals):
		by_pmid = {a.pmid: a for a in batch}
		try:
			parsed = parse_pubmed_xml(client.efetch_pubmed(list(by_pmid)))
		except Exception as exc:  # NcbiError, XML parse errors: skip the batch, keep going
			totals["failed_batches"] += 1
			self.log(f"Batch of {len(batch)} failed: {exc}", level=1, style_func=self.style.WARNING)
			return

		seen = set()
		for record in parsed.records:
			article = by_pmid.get(record.pmid)
			if article is None:
				continue
			seen.add(record.pmid)
			totals["fetched"] += 1
			try:
				result = apply_pubmed_record(article, record, is_new=False)
			except Exception as exc:
				self.log(
					f"Article {article.article_id} (PMID {record.pmid}) failed: {exc}",
					level=1,
					style_func=self.style.WARNING,
				)
				continue
			totals["updated"] += 1
			totals["summaries_replaced"] += result.summary_replaced
			totals["retracted"] += result.retracted_set

		missing = [a for pmid, a in by_pmid.items() if pmid not in seen]
		for article in missing:
			self.log(
				f"PMID {article.pmid} (article {article.article_id}) not returned by NCBI.",
				level=1,
			)
		if missing:
			Articles.objects.filter(pk__in=[a.pk for a in missing]).update(
				pubmed_checked_at=timezone.now()
			)
			totals["missing"] += len(missing)
