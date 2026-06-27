"""
Django management command to import a single article by DOI.

Metadata is fetched from CrossRef; enrichment (categories, takeaways,
predictions) is optionally run afterwards.
"""

from django.core.management.base import BaseCommand, CommandError
from django.core.management import call_command

from gregory.models import Sources
from gregory.services.doi_import import (
	ImportStatus,
	create_article_from_doi,
	normalize_doi,
)


class Command(BaseCommand):
	help = "Import a single article by DOI and optionally run it through the pipeline."

	def add_arguments(self, parser):
		parser.add_argument(
			"doi",
			type=str,
			help="The DOI of the article to import (e.g., 10.1234/example.doi)",
		)
		parser.add_argument(
			"--source-id",
			type=int,
			required=True,
			help="The source ID to associate with the article (required)",
		)
		parser.add_argument(
			"--skip-authors",
			action="store_true",
			help="Skip fetching authors from CrossRef",
		)
		parser.add_argument(
			"--skip-categories", action="store_true", help="Skip category assignment"
		)
		parser.add_argument(
			"--skip-predictions", action="store_true", help="Skip ML predictions"
		)
		parser.add_argument(
			"--skip-takeaways", action="store_true", help="Skip generating takeaways"
		)
		parser.add_argument(
			"--dry-run",
			action="store_true",
			help="Show what would be imported without making changes",
		)

	def handle(self, *args, **options):
		from gregory.classes import SciencePaper

		doi = normalize_doi(options["doi"])
		source_id = options["source_id"]
		dry_run = options.get("dry_run", False)

		try:
			source = Sources.objects.get(pk=source_id)
		except Sources.DoesNotExist:
			raise CommandError(f"Source with ID {source_id} not found")

		self.stdout.write(f"Importing DOI: {doi}")
		self.stdout.write(f"Using source: {source.name}")

		if dry_run:
			self.stdout.write(self.style.WARNING("DRY RUN — fetching metadata only, no changes saved"))
			paper = SciencePaper(doi=doi)
			paper.refresh()
			if not paper.title:
				raise CommandError(f"Could not retrieve metadata from CrossRef for DOI: {doi}")
			self.stdout.write(f"  Title:     {paper.title}")
			self.stdout.write(f"  DOI:       {paper.doi}")
			self.stdout.write(f"  Link:      {paper.link}")
			self.stdout.write(f"  Journal:   {paper.journal}")
			self.stdout.write(f"  Publisher: {paper.publisher}")
			self.stdout.write(f"  Published: {paper.published_date}")
			self.stdout.write(f"  Access:    {paper.access}")
			self.stdout.write(f"  PDF link:  {paper.pdf_link or 'N/A'}")
			self.stdout.write(
				f"  Abstract:  {paper.abstract[:200] if paper.abstract else 'N/A'}..."
			)
			return

		result = create_article_from_doi(
			doi, source, skip_authors=options.get("skip_authors", False)
		)

		if result.status == ImportStatus.CROSSREF_FAILURE:
			raise CommandError(f"Failed to fetch article data: {result.message}")

		if result.status in (ImportStatus.EXISTS_BY_DOI, ImportStatus.EXISTS_BY_TITLE):
			self.stdout.write(self.style.WARNING(result.message))
			return

		self.stdout.write(self.style.SUCCESS(result.message))
		if result.authors_added:
			self.stdout.write(self.style.SUCCESS(f"  Added {result.authors_added} authors"))

		# Optional enrichment steps (heavy — skip in admin flow)
		if not options.get("skip_categories"):
			self.stdout.write("Assigning categories...")
			try:
				call_command("rebuild_categories", articles_only=True, verbosity=0)
				self.stdout.write(self.style.SUCCESS("Categories assigned"))
			except Exception as e:
				self.stdout.write(self.style.WARNING(f"Category assignment failed: {e}"))

		if not options.get("skip_takeaways"):
			self.stdout.write("Generating takeaways...")
			try:
				call_command("get_takeaways")
				self.stdout.write(self.style.SUCCESS("Takeaways generated"))
			except Exception as e:
				self.stdout.write(self.style.WARNING(f"Takeaway generation failed: {e}"))

		if not options.get("skip_predictions"):
			self.stdout.write("Running ML predictions...")
			try:
				call_command("predict_articles", all_teams=True, verbosity=0)
				self.stdout.write(self.style.SUCCESS("ML predictions completed"))
			except Exception as e:
				self.stdout.write(self.style.WARNING(f"ML prediction failed: {e}"))

		self.stdout.write(
			self.style.SUCCESS(
				f"\nArticle import complete! Article ID: {result.article.article_id}"
			)
		)
