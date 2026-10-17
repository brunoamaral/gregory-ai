"""
Fill Articles.pmid from PubMed URLs already on file, with no API calls.

Parses ``link`` and every URL in ``links`` with ``extract_pmid_from_url``. A PMID
is set only where the article has none and the PMID is unused. A PMID found on
two or more articles (or already held by another article) is set on none of
them: those articles are duplicates, and are listed so ``merge_duplicate_articles``
can deal with them first. An article whose URLs name two different PMIDs is
skipped too.

Run:
	docker exec gregory python manage.py backfill_article_pmids --dry-run
	docker exec gregory python manage.py backfill_article_pmids
"""

from collections import defaultdict

from django.core.management.base import BaseCommand

from gregory.models import Articles
from gregory.utils.doi_utils import extract_pmid_from_url

BATCH_SIZE = 1000


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

		held = set(
			Articles.objects.filter(pmid__isnull=False).values_list("pmid", flat=True)
		)

		by_pmid = defaultdict(list)
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
			elif pmids:
				by_pmid[pmids.pop()].append(article_id)

		to_set = {}
		duplicates = {}
		for pmid, article_ids in by_pmid.items():
			if pmid in held or len(article_ids) > 1:
				duplicates[pmid] = article_ids
			else:
				to_set[pmid] = article_ids[0]

		if not dry_run:
			self._write(to_set)

		verb = "Would set" if dry_run else "Set"
		self.stdout.write(f"Scanned {scanned} articles without a PMID.")
		self.stdout.write(f"{verb} {len(to_set)} PMIDs.")
		self.stdout.write(
			f"Left alone: {len(duplicates)} PMIDs shared by several articles or already "
			f"held by another one, {len(ambiguous)} articles naming several PMIDs."
		)
		for pmid, article_ids in sorted(duplicates.items()):
			holder = " (+ one already holding it)" if pmid in held else ""
			self.stdout.write(f"  duplicate PMID {pmid}: articles {article_ids}{holder}")
		for article_id, pmids in ambiguous:
			self.stdout.write(f"  ambiguous: article {article_id} names PMIDs {pmids}")

	def _write(self, to_set):
		items = list(to_set.items())
		for start in range(0, len(items), BATCH_SIZE):
			chunk = items[start : start + BATCH_SIZE]
			articles = Articles.objects.in_bulk([article_id for _, article_id in chunk])
			batch = []
			for pmid, article_id in chunk:
				article = articles[article_id]
				article.pmid = pmid
				batch.append(article)
			Articles.objects.bulk_update(batch, ["pmid"])
