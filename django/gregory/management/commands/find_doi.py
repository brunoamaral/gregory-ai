from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from gregory.models import Articles
from gregory.services.article_merge import assign_doi_or_merge
from gregory.utils.enrichment import clear_marker, due_filter, record_fruitless_attempt
import gregory.functions as greg


class Command(BaseCommand):
	help = "Searches the article DOI by its title"

	def handle(self, *args, **kwargs):
		# Update articles with DOI
		self.update_doi()

	def update_doi(self):
		# Materialise: a DOI collision may merge (and delete) rows mid-loop.
		articles = list(
			Articles.objects.filter(
				due_filter("doi_lookup_next_check"),
				kind="science paper",
			).filter(Q(doi__isnull=True) | Q(doi=""))
		)
		for article in articles:
			self.stdout.write(f"Processing article '{article.title}'.")
			try:
				doi = greg.get_doi(article.title)
			except Exception as e:
				# Network/API failure: not a completed attempt, so the backoff
				# marker must not advance — the next run retries immediately.
				self.stderr.write(
					self.style.WARNING(
						f"CrossRef lookup failed for '{article.title}': {e}. "
						"Will retry next run."
					)
				)
				continue
			if doi:
				self.stdout.write(self.style.SUCCESS(f"Found DOI: {doi}."))
				# Guard against two independently-created rows silently converging
				# on the same DOI: if another article already holds it, merge
				# rather than create a collision. assign_doi_or_merge returns the
				# survivor (which may differ from `article` if it was the loser).
				with transaction.atomic():
					# save=False so the DOI and the cleared backoff marker are
					# persisted in a single save (one history row) on the common
					# no-collision path.
					survivor, merged = assign_doi_or_merge(
						article, doi, save=False
					)
					clear_marker(survivor, "doi_lookup", save=False)
					survivor.save()
				if merged:
					self.stdout.write(
						self.style.WARNING(
							f"DOI {doi} already existed — merged into article "
							f"{survivor.article_id}."
						)
					)
				else:
					self.stdout.write(
						self.style.SUCCESS(
							f"Updated article {survivor.title} with DOI: {survivor.doi}."
						)
					)
			else:
				# CrossRef responded and had no match: back off before retrying.
				record_fruitless_attempt(article, "doi_lookup")