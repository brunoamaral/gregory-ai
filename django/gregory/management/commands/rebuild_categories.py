from django.core.management.base import BaseCommand
from django.db.models import Q
from gregory.models import Articles, Trials, TeamCategory, Articles, Trials

class Command(BaseCommand):
	help = 'Rebuilds category associations for articles and trials.'

	def handle(self, *args, **options):
		self.rebuild_cats_articles()
		self.rebuild_cats_trials()
		self.stdout.write(self.style.SUCCESS('Successfully rebuilt category associations for articles and trials.'))

	def rebuild_cats_articles(self):
		# Clear existing relationships
		Articles.team_categories.through.objects.all().delete()

		# Get all team categories
		categories = TeamCategory.objects.all()

		for cat in categories:
			terms = cat.category_terms
			team_id = cat.team_id

			# Build the Q object for filtering articles
			query = Q()
			for term in terms:
				query |= Q(title__icontains=term)

			# Filter articles based on the team and the terms
			articles = Articles.objects.filter(query, teams__id=team_id)

			# Associate articles with the team category
			for article in articles:
				article.team_categories.add(cat)

	def rebuild_cats_trials(self):
		# Clear existing relationships
		Trials.team_categories.through.objects.all().delete()

		# Get all team categories
		categories = TeamCategory.objects.all()

		for cat in categories:
			terms = cat.category_terms
			team_id = cat.team_id

			# Build the Q object for filtering trials
			query = Q()
			for term in terms:
				query |= Q(title__icontains=term)

			# Filter trials based on the team and the terms
			trials = Trials.objects.filter(query, teams__id=team_id)

			# Associate trials with the team category
			for trial in trials:
				trial.team_categories.add(cat)