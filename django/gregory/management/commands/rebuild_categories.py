from django.core.management.base import BaseCommand
from django.db.models import Q
from gregory.models import Articles, Trials, TeamCategory


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
		categories = TeamCategory.objects.prefetch_related('subjects').all()

		for cat in categories:
			terms = cat.category_terms
			# Loop through each subject associated with the category
			for subject in cat.subjects.all():
				subject_id = subject.id

				# Build the Q object for filtering articles
				query = Q()
				for term in terms:
					query |= Q(title__icontains=term) | Q(summary__icontains=term)  # Include abstract field

				# Filter articles based on the subject and terms
				articles = Articles.objects.filter(query, subjects__id=subject_id)

				# Get IDs of the filtered articles
				article_ids = articles.values_list('article_id', flat=True)

				# Bulk add articles to the category
				cat.articles.add(*article_ids)

	def rebuild_cats_trials(self):
		# Clear existing relationships
		Trials.team_categories.through.objects.all().delete()

		# Get all team categories
		categories = TeamCategory.objects.prefetch_related('subjects').all()

		for cat in categories:
			terms = cat.category_terms

			# Loop through each subject associated with the category
			for subject in cat.subjects.all():
				subject_id = subject.id

				# Build the Q object for filtering trials
				query = Q()
				for term in terms:
					query |= Q(title__icontains=term) | Q(summary__icontains=term)

				# Filter trials based on the subject and terms
				trials = Trials.objects.filter(query, subjects__id=subject_id)

				# Get IDs of the filtered trials
				trial_ids = trials.values_list('trial_id', flat=True)

				# Bulk add trials to the category
				cat.trials.add(*trial_ids)