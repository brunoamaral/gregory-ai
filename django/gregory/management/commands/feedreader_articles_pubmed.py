"""
Run each active PubMed API source's search and keep the papers created since the last run.

A `pubmed_api` source stores a PubMed search (`pubmed_query`) and publication types to
leave out. Every run builds `(<query>) NOT (<types>) AND <start>:<end>[crdt]` (see
gregory.utils.pubmed_search), searches PubMed's E-utilities, fetches the records, and
applies each one to an article (gregory.services.pubmed_records). Unlike the PubMed RSS
sources it sees every paper created in the window, not just the newest 100, and gets the
full structured abstract, authors, publication types and MeSH headings.

The window ends today and starts 2 days before `last_successful_fetch_at` (30 days back on
the first run, or `--days`). PubMed returns at most 9,999 IDs per search, so a window that
reaches that is halved until each half fits; a single day that still does fails the source.
The anchor only moves after a run in which every search and fetch succeeded and no record
failed, so a failed run is re-covered by the next one.

RSS sources are untouched: feedreader_articles only reads `method="rss"` sources.

Run:
	docker exec gregory python manage.py feedreader_articles_pubmed
	docker exec gregory python manage.py feedreader_articles_pubmed --source-id 12 --days 90 --dry-run
"""

from datetime import date, timedelta

from django.db import transaction
from django.utils import timezone

from gregory.classes import SciencePaper
from gregory.management.base import GregoryBaseCommand
from gregory.management.commands.feedreader_articles import Command as RssCommand
from gregory.models import Articles, Sources
from gregory.services.pubmed_records import apply_pubmed_record, pubmed_link
from gregory.utils.ncbi import NcbiClient, NcbiResultCapError
from gregory.utils.pubmed_search import build_pubmed_term
from gregory.utils.pubmed_xml import parse_pubmed_xml
from gregory.utils.registry_utils import merge_links

FETCH_BATCH_SIZE = 200
OVERLAP_DAYS = 2
DEFAULT_FIRST_RUN_DAYS = 30


class WindowTooBroad(Exception):
	"""One day of results still reaches PubMed's 9,999 ID cap."""


class Command(GregoryBaseCommand):
	help = "Fetches new papers for active PubMed API sources."

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		# Reused for its article lookup (PMID, DOI, link, title) and for the source,
		# subject and team links, so a PubMed paper is matched and tagged exactly as
		# an RSS one is.
		self.rss = RssCommand()
		self.fetch_errors = []

	def add_arguments(self, parser):
		parser.add_argument("--source-id", type=int, help="Only this source.")
		parser.add_argument(
			"--days",
			type=int,
			default=DEFAULT_FIRST_RUN_DAYS,
			help=f"How far back a source with no earlier run looks (default {DEFAULT_FIRST_RUN_DAYS}).",
		)
		parser.add_argument(
			"--dry-run",
			action="store_true",
			help="Search and report what would be fetched; write nothing and keep the anchor.",
		)

	def handle(self, *args, **options):
		self.rss.verbosity = self.verbosity
		self.rss.stdout, self.rss.stderr, self.rss.style = self.stdout, self.stderr, self.style
		sources = Sources.objects.filter(
			method="pubmed_api", source_for="science paper", active=True
		).select_related("team__organization")
		if options.get("source_id"):
			sources = sources.filter(pk=options["source_id"])
		for source in sources:
			self.log(f"# Processing PubMed source {source}", level=1)
			# One broken source must not stop the ones after it.
			try:
				self.process_source(source, options["days"], options["dry_run"])
			except Exception as exc:
				self.fetch_errors.append(f"{source.name}: {exc}")
				self.log(
					f"Source '{source.name}' failed: {exc}. Anchor not moved.",
					level=1,
					style_func=self.style.ERROR,
				)

	# -- one source -------------------------------------------------------

	def process_source(self, source, first_run_days, dry_run):
		query = (source.pubmed_query or "").strip()
		if not query:
			self.log(f"Source '{source.name}' has no PubMed search; skipping.", level=1)
			return
		fetch_started = timezone.now()
		today = fetch_started.date()
		if source.last_successful_fetch_at:
			start = (source.last_successful_fetch_at - timedelta(days=OVERLAP_DAYS)).date()
		else:
			start = today - timedelta(days=first_run_days)

		client = (
			NcbiClient.for_organization(source.team.organization) if source.team else NcbiClient()
		)
		excluded = list(source.excluded_publication_types.all())
		excluded_names = [t.name for t in excluded]
		excluded_uis = {t.ui for t in excluded}

		translations = []
		pmids = self._search(client, query, excluded_names, start, today, translations)
		on_source = set(
			Articles.objects.filter(sources=source, pmid__in=pmids).values_list("pmid", flat=True)
		)
		todo = [p for p in pmids if p not in on_source]
		self.log(
			f"  Window {start} to {today}: PubMed read the search as "
			f"{translations[0] if translations else '(no result)'}; {len(pmids)} found, "
			f"{len(on_source)} already on this source.",
			level=1,
		)
		if dry_run:
			self.log(f"  Dry run: would fetch {len(todo)} records; nothing written.", level=1)
			return

		counts = dict(new=0, merged=0, excluded=0, books=0, failed=0)
		for index in range(0, len(todo), FETCH_BATCH_SIZE):
			batch = todo[index : index + FETCH_BATCH_SIZE]
			parsed = parse_pubmed_xml(client.efetch_pubmed(batch))
			counts["books"] += parsed.books_skipped
			for record in parsed.records:
				# Safety net behind the NOT clause in the search term.
				if excluded_uis and any(ui in excluded_uis for ui, _ in record.publication_types):
					counts["excluded"] += 1
					continue
				try:
					counts["new" if self.process_record(source, record) else "merged"] += 1
				except Exception as exc:
					counts["failed"] += 1
					self.log(
						f"  PMID {record.pmid} failed: {exc}", level=1, style_func=self.style.WARNING
					)

		if counts["failed"]:
			self.log(
				f"  {counts['failed']} record(s) failed; not moving the anchor.",
				level=1,
				style_func=self.style.WARNING,
			)
		else:
			source.last_successful_fetch_at = fetch_started
			source.save(update_fields=["last_successful_fetch_at"])
		self.log(
			f"Finished {source.name}: {counts['new']} new, {counts['merged']} merged, "
			f"{len(on_source)} skipped (already on the source), {counts['excluded']} excluded, "
			f"{counts['failed']} failed.",
			level=1,
			style_func=self.style.SUCCESS,
		)

	# -- search -----------------------------------------------------------

	def _search(self, client, query, excluded_names, start: date, end: date, translations):
		"""IDs of papers created from ``start`` to ``end`` (inclusive), halving the window
		whenever PubMed's result cap is reached. Raises WindowTooBroad for a single day."""
		term = build_pubmed_term(query, excluded_names, start, end)
		try:
			result = client.esearch(term, retmax=9999)
		except NcbiResultCapError:
			if start >= end:
				raise WindowTooBroad(
					f"{start} alone has 9,999 or more results; narrow the search"
				)
			middle = start + (end - start) // 2
			first = self._search(client, query, excluded_names, start, middle, translations)
			second = self._search(
				client, query, excluded_names, middle + timedelta(days=1), end, translations
			)
			return list(dict.fromkeys(first + second))
		if result.querytranslation:
			translations.append(result.querytranslation)
		return result.ids

	# -- one record -------------------------------------------------------

	def process_record(self, source, record):
		"""Apply a record to its article, creating it if needed. True when created."""
		link = pubmed_link(record.pmid)
		existing = self.rss.find_existing_article(
			record.doi, record.title, link, pmid=record.pmid
		)
		if existing is not None:
			with transaction.atomic():
				apply_pubmed_record(existing, record, is_new=False)
				merged = merge_links(existing.links, link)
				if merged != (existing.links or {}):
					existing.links = merged
					existing.save(update_fields=["links"])
				self.rss.add_article_relationships(existing, source)
			return False

		# CrossRef first, outside the transaction: no database transaction is held
		# open across an HTTP call.
		details = self._crossref_details(record)
		with transaction.atomic():
			article = Articles.objects.create(
				title=record.title or f"PubMed {record.pmid}",
				link=link,
				links=merge_links(None, link),
			)
			apply_pubmed_record(article, record, is_new=True)
			for name, value in details.items():
				setattr(article, name, value)
			article.save(update_fields=list(details))
			self.rss.add_article_relationships(article, source)
		return True

	@staticmethod
	def _crossref_details(record):
		"""Journal, publisher, access and PDF link for a new article. CrossRef's title and
		abstract never replace PubMed's; when CrossRef has no journal, PubMed's fills the gap."""
		details = {
			"container_title": (record.journal or None) and record.journal[:150],
			"publisher": None,
			"access": "unknown",
			"pdf_link": None,
			"crossref_check": None,
		}
		if record.doi:
			paper = SciencePaper(doi=record.doi)
			if not SciencePaper.is_crossref_failed(paper.refresh()):
				journal = paper.journal or record.journal
				details.update(
					container_title=journal[:150] if journal else None,
					publisher=paper.publisher[:150] if paper.publisher else None,
					access=paper.access or "unknown",
					pdf_link=paper.pdf_link,
					crossref_check=timezone.now(),
				)
		return details
