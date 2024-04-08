from django.core.management.base import BaseCommand
from gregory.models import Articles
import gregory.functions as greg
from gregory.classes import SciencePaper
from django.utils import timezone
from django.db.models import Q
import requests
import time

class Command(BaseCommand):
	help = 'Updates articles with DOI, access, publisher, journal, publish date, and abstracts with minimal API calls.'

	def handle(self, *args, **kwargs):
		# Update articles with DOI
		self.update_doi()

		# Fetch and update articles details with minimal API calls
		self.update_article_details()

		self.stdout.write(self.style.SUCCESS('Successfully updated articles information with minimal API calls.'))

	def update_doi(self):
		articles = Articles.objects.filter(kind='science paper', doi__isnull=True, crossref_check__lte=timezone.now()-timezone.timedelta(days=3), crossref_check__gt=timezone.now()-timezone.timedelta(days=30)) | Articles.objects.filter(kind='science paper', doi__isnull=True, crossref_check__isnull=True)
		for article in articles:
			self.stdout.write(f"Processing article '{article.title}'.")
			doi = greg.get_doi(article.title)
			article.crossref_check = timezone.now()
			article.save()
			if doi:
				self.stdout.write(f"Found DOI: {doi}.")
				article.doi = doi
				article.save()

	def try_refresh_paper(self, paper, retries=3, delay=5):
		"""Attempt to refresh the paper data with retries and a delay."""
		for attempt in range(retries):
			try:
				paper.refresh()
				return True  # Success
			except requests.exceptions.ConnectionError as e:
				self.stdout.write(f"Attempt {attempt + 1} failed: {e}")
				if attempt < retries - 1:
					self.stdout.write(f"Retrying in {delay} seconds...")
					time.sleep(delay)
				else:
					self.stdout.write("Max retries exceeded. Skipping this paper.")
		return False  # Failed after retries

	def update_article_details(self):
		# Select articles that need updating but have a DOI
		articles = Articles.objects.filter(
				Q(doi__isnull=False, doi__gt=''),
				(Q(crossref_check__isnull=True) | Q(access__isnull=True) | Q(publisher__isnull=True) | Q(published_date__isnull=True) | Q(summary=None) | Q(summary='not available')) &
				Q(kind='science paper')
		).distinct()

		for article in articles:
			if article.doi != None:
				paper = SciencePaper(doi=article.doi)
				# Refresh once per article
				if not self.try_refresh_paper(paper):
					# Handle the failure (e.g., skip this article, log the issue)
					self.stdout.write(f"Skipping article '{article.title}' due to connection issues.")
					continue  # Use continue instead of return to proceed with the next article
			else:
				print(f"empty DOI for article_id {article}")

			# Update fields from the refreshed paper object
			self.update_article_from_paper(article, paper)

	def update_article_from_paper(self, article, paper):
		update_fields = []
		updated_info = []  # To keep track of what information is updated

		# Fetch the most recent history record for comparison later
		last_history = article.history.first()

		if article.access is None and hasattr(paper, 'access'):
			article.access = paper.access
			update_fields.append('access')
			updated_info.append('access information')

		if article.publisher is None and hasattr(paper, 'publisher'):
			article.publisher = paper.publisher
			update_fields.append('publisher')
			updated_info.append('publisher')

		if article.container_title is None and hasattr(paper, 'journal'):
			article.container_title = paper.journal
			update_fields.append('container_title')
			updated_info.append('journal')

		if article.published_date is None and hasattr(paper, 'published_date'):
			article.published_date = paper.published_date
			update_fields.append('published_date')
			updated_info.append('published date')

		if (article.summary is None or article.summary == 'not available') and hasattr(paper, 'abstract'):
			article.summary = paper.abstract
			update_fields.append('summary')
			updated_info.append('abstract')

		if update_fields:
			article.save(update_fields=update_fields)
			
			# Log the changes using Django Simple History
			new_history = article.history.first()  # The new state after save
			if last_history and new_history:
				changes = new_history.diff_against(last_history)
				if changes.changes:
					self.stdout.write(f"Changes for '{article.title}':")
					for change in changes.changes:
						self.stdout.write(f" - {change.field}: from '{change.old}' to '{change.new}'")
				else:
					self.stdout.write(f"No changes detected for '{article.title}'.")
			else:
				self.stdout.write(f"History not available for '{article.title}'.")

			# Log the updated information
			self.stdout.write(f"Updated article '{article.title}' with {', '.join(updated_info)}.")