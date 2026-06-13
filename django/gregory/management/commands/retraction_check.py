
from django.core.management.base import BaseCommand
from gregory.models import Articles
from gregory.classes import SciencePaper
from django.utils import timezone
from django.db.models import Q
from datetime import timedelta


class Command(BaseCommand):
	"""Checks and updates the retraction status of articles."""
	help = "Checks and updates the retraction status of articles."
	def add_arguments(self, parser):
		parser.add_argument(
		"--doi",
		type=str,
		help="The DOI to search for (optional). If provided, only this DOI will be checked.",
	)
	def check_retraction_status(self, doi=None):
		# Select articles that have a DOI, are science papers, and crossref_retraction_check is in the last 24 months and retracted is False or Null
		two_years_ago = timezone.now() - timedelta(days=730)
		thirty_days_ago = timezone.now() - timedelta(days=30)
		if doi:
			articles_to_check = Articles.objects.filter(
				Q(doi=doi)
				& Q(kind="science paper")
				& (Q(retracted=False) | Q(retracted__isnull=True))
			).distinct()
		else:
			# First, get articles 
			articles_to_check = Articles.objects.filter(
				Q(doi__isnull=False, doi__gt="")
				& (Q(retracted=False)
				& Q(kind="science paper")
				& Q(published_date__lte=two_years_ago)
				& (Q(crossref_retraction_check__gt=thirty_days_ago) | Q(crossref_retraction_check__isnull=True))
			)).distinct()
			total_articles = articles_to_check.count()
			self.stdout.write(
				f"Found {total_articles} articles to update."
			)

		for article in articles_to_check:
			if article.doi:
				paper = SciencePaper(doi=article.doi)
				paper.refresh()  # Initial refresh to get the latest data
				self.stdout.write(f"Checking article '{article.title}' (DOI: {article.doi}) for retraction status...")
				# Refresh once per article
			else:
				self.stdout.write(f"Empty DOI for article_id {article.id}")
				continue  # Skip articles without a DOI

			# Update fields from the refreshed paper object
			self.compare_retraction(article, paper)

	def compare_retraction(self, article, paper):
		if paper.retracted == True and article.retracted != True:
			article.retracted = paper.retracted
			article.crossref_retraction_check = timezone.now()
			article.save(update_fields=["retracted", "crossref_retraction_check"])
			self.stdout.write(
				f"Updated retraction status for article '{article.title}' (DOI: {article.doi}) to {article.retracted}."
			)
		else:
			article.crossref_retraction_check = timezone.now()
			article.save(update_fields=["crossref_retraction_check"])
			self.stdout.write(
				f"No change in retraction status for article '{article.title}' (DOI: {article.doi}). Updated retraction check timestamp."
			)
	def handle(self, *args, **options):

		doi = options.get("doi", None)
		# Fetch and update articles retraction status
		self.stdout.write(
			self.style.SUCCESS(
				"Starting retraction status check for articles..."
			)
		)
		self.check_retraction_status(doi=doi)
		self.stdout.write(
			self.style.SUCCESS(
				"Successfully updated articles retraction status."
			)
		)
