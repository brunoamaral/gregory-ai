from django.core.management.base import BaseCommand
from django.db.models import Q
from gregory.models import Articles
from gregory.utils.enrichment import clear_marker, due_filter, record_fruitless_attempt
import gregory.functions as greg


class Command(BaseCommand):
	help = "Searches the article DOI by its title"

	def handle(self, *args, **kwargs):
		# Update articles with DOI
		self.update_doi()

	def update_doi(self):
		articles = Articles.objects.filter(
			due_filter("doi_lookup_next_check"),
			kind="science paper",
		).filter(Q(doi__isnull=True) | Q(doi=""))
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
				article.doi = doi
				clear_marker(article, "doi_lookup", save=False)
				article.save()
				self.stdout.write(
					self.style.SUCCESS(
						f"Updated article {article.title} with DOI: {article.doi}."
					)
				)
			else:
				# CrossRef responded and had no match: back off before retrying.
				record_fruitless_attempt(article, "doi_lookup")