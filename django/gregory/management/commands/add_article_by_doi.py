"""
Django management command to import a single article by DOI.

This command fetches article metadata from CrossRef, creates the article in the database,
and runs through the enrichment pipeline steps (authors, categories, predictions).
"""
from django.core.management.base import BaseCommand, CommandError
from django.core.management import call_command
from django.db import transaction
from django.utils import timezone

from gregory.models import Articles, Sources, Authors, Subject, Team
from gregory.classes import SciencePaper
from gregory.functions import normalize_orcid


class Command(BaseCommand):
	help = 'Import a single article by DOI and run it through the pipeline.'

	def add_arguments(self, parser):
		parser.add_argument(
			'doi',
			type=str,
			help='The DOI of the article to import (e.g., 10.1234/example.doi)'
		)
		parser.add_argument(
			'--source-id',
			type=int,
			required=True,
			help='The source ID to associate with the article (required)'
		)
		parser.add_argument(
			'--skip-authors',
			action='store_true',
			help='Skip fetching authors from CrossRef'
		)
		parser.add_argument(
			'--skip-categories',
			action='store_true',
			help='Skip category assignment'
		)
		parser.add_argument(
			'--skip-predictions',
			action='store_true',
			help='Skip ML predictions'
		)
		parser.add_argument(
			'--skip-takeaways',
			action='store_true',
			help='Skip generating takeaways'
		)
		parser.add_argument(
			'--dry-run',
			action='store_true',
			help='Show what would be imported without making changes'
		)

	def handle(self, *args, **options):
		doi = options['doi']
		source_id = options['source_id']
		dry_run = options.get('dry_run', False)
		
		# Normalize DOI (remove URL prefix if present)
		doi = self.normalize_doi(doi)
		
		self.stdout.write(f"Importing article with DOI: {doi}")
		
		# Check if article already exists
		existing = Articles.objects.filter(doi=doi).first()
		if existing:
			self.stdout.write(self.style.WARNING(
				f"Article already exists (ID: {existing.article_id}): {existing.title}"
			))
			# Optionally add source association if different
			try:
				source = Sources.objects.get(pk=source_id)
				if source not in existing.sources.all():
					if not dry_run:
						existing.sources.add(source)
						if source.team:
							existing.teams.add(source.team)
						if source.subject:
							existing.subjects.add(source.subject)
					self.stdout.write(self.style.SUCCESS(
						f"Added source '{source.name}' to existing article"
					))
			except Sources.DoesNotExist:
				raise CommandError(f"Source with ID {source_id} not found")
			return
		
		# Validate source exists
		try:
			source = Sources.objects.get(pk=source_id)
		except Sources.DoesNotExist:
			raise CommandError(f"Source with ID {source_id} not found")
		
		self.stdout.write(f"Using source: {source.name}")
		
		# Fetch article metadata from CrossRef
		self.stdout.write("Fetching metadata from CrossRef...")
		paper = SciencePaper(doi=doi)
		result = paper.refresh()
		
		if result and 'error' in str(result).lower():
			raise CommandError(f"Failed to fetch article data: {result}")
		
		if not paper.title:
			raise CommandError(
				f"Could not retrieve article title from CrossRef for DOI: {doi}"
			)
		
		# Check if article with same title exists (case-insensitive)
		existing_by_title = Articles.objects.filter(title__iexact=paper.title).first()
		if existing_by_title:
			self.stdout.write(self.style.WARNING(
				f"Article with same title already exists (ID: {existing_by_title.article_id})"
			))
			if existing_by_title.doi != doi:
				self.stdout.write(self.style.WARNING(
					f"Existing article has different DOI: {existing_by_title.doi}"
				))
			return
		
		if dry_run:
			self.stdout.write(self.style.WARNING("DRY RUN - Would create article:"))
			self.stdout.write(f"  Title: {paper.title}")
			self.stdout.write(f"  DOI: {paper.doi}")
			self.stdout.write(f"  Link: {paper.link}")
			self.stdout.write(f"  Journal: {paper.journal}")
			self.stdout.write(f"  Publisher: {paper.publisher}")
			self.stdout.write(f"  Published: {paper.published_date}")
			self.stdout.write(f"  Access: {paper.access}")
			self.stdout.write(f"  Abstract: {paper.abstract[:200] if paper.abstract else 'N/A'}...")
			return
		
		# Create the article
		self.stdout.write("Creating article...")
		with transaction.atomic():
			article = Articles.objects.create(
				title=paper.title,
				doi=paper.doi,
				link=paper.link or f"https://doi.org/{paper.doi}",
				summary=paper.clean_abstract() if paper.abstract else None,
				published_date=paper.published_date,
				access=paper.access,
				publisher=paper.publisher,
				container_title=paper.journal,
				kind='science paper',
				crossref_check=timezone.now(),
			)
			
			# Add source association
			article.sources.add(source)
			
			# Add team and subject from source
			if source.team:
				article.teams.add(source.team)
			if source.subject:
				article.subjects.add(source.subject)
			
			self.stdout.write(self.style.SUCCESS(
				f"Created article (ID: {article.article_id}): {article.title}"
			))
		
		# Step 2: Get authors from CrossRef data
		if not options.get('skip_authors') and paper.authors:
			self.stdout.write("Processing authors...")
			self.process_authors(article, paper.authors)
		
		# Step 3: Assign categories
		if not options.get('skip_categories'):
			self.stdout.write("Assigning categories...")
			try:
				# Run rebuild_categories just for this article
				call_command('rebuild_categories', articles_only=True, verbosity=0)
				self.stdout.write(self.style.SUCCESS("Categories assigned"))
			except Exception as e:
				self.stdout.write(self.style.WARNING(f"Category assignment failed: {e}"))
		
		# Step 4: Generate takeaways
		if not options.get('skip_takeaways'):
			self.stdout.write("Generating takeaways...")
			try:
				call_command('get_takeaways')
				self.stdout.write(self.style.SUCCESS("Takeaways generated"))
			except Exception as e:
				self.stdout.write(self.style.WARNING(f"Takeaway generation failed: {e}"))
		
		# Step 5: Run ML predictions
		if not options.get('skip_predictions'):
			self.stdout.write("Running ML predictions...")
			try:
				call_command('predict_articles', all_teams=True, verbosity=0)
				self.stdout.write(self.style.SUCCESS("ML predictions completed"))
			except Exception as e:
				self.stdout.write(self.style.WARNING(f"ML prediction failed: {e}"))
		
		self.stdout.write(self.style.SUCCESS(
			f"\nArticle import complete! Article ID: {article.article_id}"
		))

	def normalize_doi(self, doi):
		"""
		Normalize DOI by removing common URL prefixes.
		"""
		prefixes = [
			'https://doi.org/',
			'http://doi.org/',
			'https://dx.doi.org/',
			'http://dx.doi.org/',
			'doi:',
		]
		doi = doi.strip()
		for prefix in prefixes:
			if doi.lower().startswith(prefix.lower()):
				doi = doi[len(prefix):]
				break
		return doi.strip()

	def process_authors(self, article, authors_data):
		"""
		Process authors from CrossRef data and associate them with the article.
		"""
		added_count = 0
		for author_data in authors_data:
			given_name = author_data.get('given')
			family_name = author_data.get('family')
			raw_orcid = author_data.get('ORCID')
			orcid = normalize_orcid(raw_orcid) if raw_orcid else None
			
			if not given_name or not family_name:
				self.stdout.write(self.style.WARNING(
					f"  Skipping author with missing name: {author_data}"
				))
				continue
			
			# Try to find or create author
			author_obj = None
			
			if orcid:
				# First try to match by ORCID
				author_obj, created = Authors.objects.get_or_create(
					ORCID=orcid,
					defaults={'given_name': given_name, 'family_name': family_name}
				)
				if not created:
					# Update name if different
					if author_obj.given_name != given_name or author_obj.family_name != family_name:
						author_obj.given_name = given_name
						author_obj.family_name = family_name
						author_obj.save()
			else:
				# Try to find by name
				try:
					author_obj = Authors.objects.get(
						given_name=given_name,
						family_name=family_name
					)
				except Authors.DoesNotExist:
					author_obj = Authors.objects.create(
						given_name=given_name,
						family_name=family_name,
						ORCID=orcid
					)
				except Authors.MultipleObjectsReturned:
					self.stdout.write(self.style.WARNING(
						f"  Multiple authors found for {given_name} {family_name}, skipping"
					))
					continue
			
			if author_obj:
				article.authors.add(author_obj)
				added_count += 1
		
		self.stdout.write(self.style.SUCCESS(f"  Added {added_count} authors"))
