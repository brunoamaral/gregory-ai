from django.core.management.base import BaseCommand
from django.utils.text import slugify
from gregory.models import Categories, TeamCategory, Articles, Trials

class Command(BaseCommand):
	help = 'Migrate existing categories to the new TeamCategory model'

	def handle(self, *args, **kwargs):
			self.stdout.write('Starting migration of categories to TeamCategory...')

			# Migrate each category to TeamCategory
			for category in Categories.objects.all():
					category_slug = slugify(category.category_name)
					
					# Check if the TeamCategory already exists
					team_category, created = TeamCategory.objects.get_or_create(
							team=category.team,
							category_slug=category_slug,
							defaults={
									'category_name': category.category_name,
									'category_description': category.category_description,
									'category_terms': category.category_terms
							}
					)

					if created:
							self.stdout.write(f'Created TeamCategory: {team_category}')
					else:
							self.stdout.write(f'Found existing TeamCategory: {team_category}')

					# Migrate associations from Articles
					for article in category.articles_set.all():
							article.team_categories.add(team_category)
							article.save()
							self.stdout.write(f'Added TeamCategory {team_category} to Article {article}')

					# Migrate associations from Trials
					for trial in category.trials_set.all():
							trial.team_categories.add(team_category)
							trial.save()
							self.stdout.write(f'Added TeamCategory {team_category} to Trial {trial}')

			self.stdout.write('Migration completed successfully.')
