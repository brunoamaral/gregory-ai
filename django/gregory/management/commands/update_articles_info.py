from django.core.management.base import BaseCommand
from gregory.models import Articles
from gregory.classes import SciencePaper
from django.utils import timezone
from django.db.models import Q
import requests
from datetime import timedelta
from django.utils import timezone
import time
import re

class Command(BaseCommand):
	help = 'Updates articles with DOI, access, publisher, journal, publish date, and abstracts with minimal API calls.'

	def handle(self, *args, **kwargs):
		# Fetch and update articles details with minimal API calls
		self.update_article_details()
		self.stdout.write(self.style.SUCCESS('Successfully updated articles information with minimal API calls.'))

	@staticmethod
	def is_summary_truncated(summary):
		"""
		Check if a summary appears to be truncated.
		Returns True if the summary ends with common truncation patterns.
		"""
		if not summary:
			return False
		
		summary = summary.strip()
		
		# Check for common truncation patterns at the end
		truncation_patterns = [
			r'\.\.\.$',      # Ends with ...
			r'\[\.\.\.\]$',  # Ends with [...]
			r'\[…\]$',       # Ends with [...] using ellipsis character
			r'…$',           # Ends with ellipsis character
		]
		
		for pattern in truncation_patterns:
			if re.search(pattern, summary):
				return True
		
		return False

	def try_refresh_paper(self, paper, retries=3, delay=5):
		"""Attempt to refresh the paper data with retries and a delay."""
		for attempt in range(retries):
			try:
				paper.refresh()
				return True  # Success
			except requests.exceptions.RequestException as e:
				self.stdout.write(f"Attempt {attempt + 1} failed: {e}")
				if attempt < retries - 1:
					self.stdout.write(f"Retrying in {delay} seconds...")
					time.sleep(delay)
				else:
					self.stdout.write("Max retries exceeded. Skipping this paper.")
		return False  # Failed after retries

	def update_article_details(self):
		# Select articles that need updating but have a DOI
		three_months_ago = timezone.now() - timedelta(days=90)
		
		# First, get articles with missing or 'not available' summaries
		articles_missing_data = Articles.objects.filter(
			Q(doi__isnull=False, doi__gt='') &
			(Q(crossref_check__isnull=True) | Q(access__isnull=True) | Q(publisher__isnull=True) | Q(published_date__isnull=True) | Q(summary__isnull=True) | Q(summary='not available')) &
			Q(kind='science paper') &
			Q(discovery_date__gte=three_months_ago)
		).distinct()
		
		# Also get articles with potentially truncated summaries (ending with ..., [...], etc.)
		articles_with_summaries = Articles.objects.filter(
			Q(doi__isnull=False, doi__gt='') &
			Q(summary__isnull=False) &
			~Q(summary='') &
			~Q(summary='not available') &
			Q(kind='science paper') &
			Q(discovery_date__gte=three_months_ago) &
			# Look for summaries ending with truncation patterns
			(Q(summary__endswith='...') | Q(summary__endswith='[...]') | Q(summary__endswith='…') | Q(summary__endswith='[…]'))
		).distinct()
		
		# Combine both querysets
		articles = (articles_missing_data | articles_with_summaries).distinct()
		
		total_articles = articles.count()
		self.stdout.write(f"Found {total_articles} articles to update (including {articles_with_summaries.count()} with truncated summaries)")
		
		for article in articles:
			if article.doi:
				paper = SciencePaper(doi=article.doi)
				# Refresh once per article
				if not self.try_refresh_paper(paper):
					# Handle the failure (e.g., skip this article, log the issue)
					self.stdout.write(f"Skipping article '{article.title}' due to connection issues.")
					continue  # Use continue instead of return to proceed with the next article
			else:
				self.stdout.write(f"Empty DOI for article_id {article.id}")

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

		# Update summary if it's missing, 'not available', or truncated
		should_update_summary = (
			article.summary is None or 
			article.summary == 'not available' or 
			self.is_summary_truncated(article.summary)
		)
		
		if should_update_summary and hasattr(paper, 'abstract'):
			if paper.abstract:
				# Only update if the new abstract is different and appears to be complete
				if article.summary != paper.abstract:
					if self.is_summary_truncated(article.summary):
						self.stdout.write(f"Replacing truncated summary for '{article.title}'")
						self.stdout.write(f"  Old (truncated): {article.summary[-50:] if article.summary else 'None'}")
					self.stdout.write(f"  New abstract ({len(paper.abstract)} chars)")
					article.summary = paper.abstract
					update_fields.append('summary')
					updated_info.append('abstract')
			else:
				self.stdout.write(f"No abstract found for article '{article.title}' with DOI {article.doi}.")

		if update_fields:
			article.crossref_check = timezone.now()
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