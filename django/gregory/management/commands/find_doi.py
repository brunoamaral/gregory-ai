from django.core.management.base import BaseCommand
from gregory.models import Articles
import gregory.functions as greg


class Command(BaseCommand):
	help = "Searches the article DOI by its title"

	def handle(self, *args, **kwargs):
		# Update articles with DOI
		self.update_doi()

	def update_doi(self):
		articles = Articles.objects.filter(kind="science paper", doi__isnull=True)
		for article in articles:
			self.stdout.write(f"Processing article '{article.title}'.")
			doi = greg.get_doi(article.title)
			if doi:
				self.stdout.write(self.style.SUCCESS(f"Found DOI: {doi}."))
				article.doi = doi
				article.save()
				self.stdout.write(
					self.style.SUCCESS(
						f"Updated article {article.title} with DOI: {article.doi}."
					)
				)
