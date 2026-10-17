"""
Fill Articles.pmid from PubMed URLs already on file, with no API calls.

Parses ``link`` and every URL in ``links`` with ``extract_pmid_from_url``. A PMID
is set only where the article has none and the PMID is unused. A PMID found on
two or more articles (or already held by another article) is set on none of
them: those articles are duplicates, and are listed so ``merge_duplicate_articles``
can deal with them first. An article whose URLs name two different PMIDs is
skipped too, and counts towards the duplicate check of each PMID it names.

Writes use ``update()``, which records no article history rows. Run it outside
the pipeline window: each row is only set while its pmid is still empty, so a
concurrent importer cannot be overwritten, but it can make a row get skipped.

Run:
	docker exec gregory python manage.py backfill_article_pmids --dry-run
	docker exec gregory python manage.py backfill_article_pmids
"""

from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import IntegrityError, transaction

from gregory.models import Articles
from gregory.utils.doi_utils import extract_pmid_from_url


def pmids_in_article_urls(link, links):
	"""Every distinct PMID named by an article's ``link`` and ``links`` URLs."""
	urls = [link]
	if isinstance(links, dict):
		urls.extend(links.values())
	found = set()
	for url in urls:
		if isinstance(url, str):
			pmid = extract_pmid_from_url(url)
			if pmid:
				found.add(pmid)
	return found


class Command(BaseCommand):
	help = "Set Articles.pmid from PubMed URLs in link/links (no API calls)."

	def add_arguments(self, parser):
		parser.add_argument(
			"--dry-run",
			action="store_true",
			help="Report what would be set without writing to the database.",
		)

	def handle(self, *args, **options):
		dry_run = options["dry_run"]

		held = dict(
			Articles.objects.filter(pmid__isnull=False).values_list("pmid", "article_id")
		)

		by_pmid = defaultdict(list)
		named_by_ambiguous = defaultdict(list)
		ambiguous = []
		scanned = 0
		for article_id, link, links in (
			Articles.objects.filter(pmid__isnull=True)
			.values_list("article_id", "link", "links")
			.iterator(chunk_size=2000)
		):
			scanned += 1
			pmids = pmids_in_article_urls(link, links)
			if len(pmids) > 1:
				ambiguous.append((article_id, sorted(pmids)))
				for pmid in pmids:
					# Never set on this article, but it may be the same paper as another
					# one naming one of these PMIDs: keep that one out of the backfill too.
					named_by_ambiguous[pmid].append(article_id)
			elif pmids:
				by_pmid[pmids.pop()].append(article_id)

		to_set = {}
		duplicates = {}
		for pmid, article_ids in by_pmid.items():
			article_ids = sorted(article_ids + named_by_ambiguous.get(pmid, []))
			if pmid in held or len(article_ids) > 1:
				duplicates[pmid] = article_ids
			else:
				to_set[pmid] = article_ids[0]

		skipped = [] if dry_run else self._write(to_set)
		set_count = len(to_set) - len(skipped)

		verb = "Would set" if dry_run else "Set"
		self.stdout.write(f"Scanned {scanned} articles without a PMID.")
		self.stdout.write(f"{verb} {set_count if not dry_run else len(to_set)} PMIDs.")
		if skipped:
			self.stdout.write(
				f"Skipped {len(skipped)} rows that changed since the scan: "
				+ ", ".join(f"article {a} (PMID {p})" for p, a in skipped)
			)
		self.stdout.write(
			f"Left alone: {len(duplicates)} PMIDs shared by several articles or already "
			f"held by another one, {len(ambiguous)} articles naming several PMIDs."
		)
		for pmid, article_ids in sorted(duplicates.items()):
			ids = ([held[pmid]] if pmid in held else []) + article_ids
			self.stdout.write(
				f"  duplicate PMID {pmid}: articles {ids}"
				+ (f" ({held[pmid]} already holds it)" if pmid in held else "")
			)
		for article_id, pmids in ambiguous:
			self.stdout.write(f"  ambiguous: article {article_id} names PMIDs {pmids}")

	def _write(self, to_set):
		"""Set each PMID where the article still has none. Returns the (pmid, article_id)
		pairs that could not be set because the row changed since the scan."""
		skipped = []
		for pmid, article_id in to_set.items():
			try:
				with transaction.atomic():
					updated = Articles.objects.filter(
						pk=article_id, pmid__isnull=True
					).update(pmid=pmid)
			except IntegrityError:
				updated = 0
			if not updated:
				skipped.append((pmid, article_id))
		return skipped
