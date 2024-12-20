from django.core.management.base import BaseCommand
from gregory.models import Articles, Trials
from subscriptions.models import Subscribers, SentArticleNotification, SentTrialNotification

class Command(BaseCommand):
	help = 'Marks all articles and trials as already sent to all subscribers for all their subscribed lists.'

	def handle(self, *args, **options):
		subscribers = Subscribers.objects.filter(active=True).prefetch_related('subscriptions')
		articles = list(Articles.objects.all())
		trials = list(Trials.objects.all())

		if not subscribers:
			self.stdout.write(self.style.WARNING('No active subscribers found.'))
			return

		if not articles and not trials:
			self.stdout.write(self.style.WARNING('No articles or trials found.'))
			return

		for subscriber in subscribers:
			lists_subscribed = subscriber.subscriptions.all()
			if not lists_subscribed.exists():
				self.stdout.write(self.style.WARNING(f'Subscriber {subscriber.email} has no subscriptions.'))
				continue

			for lst in lists_subscribed:
				# Mark all articles as sent
				for article in articles:
					_, created = SentArticleNotification.objects.get_or_create(
						article=article,
						list=lst,
						subscriber=subscriber
					)
					if created:
						self.stdout.write(self.style.SUCCESS(
							f'Article {article.pk} marked as sent to {subscriber.email} for list "{lst.list_name}".'
						))

				# Mark all trials as sent
				for trial in trials:
					_, created = SentTrialNotification.objects.get_or_create(
						trial=trial,
						list=lst,
						subscriber=subscriber
					)
					if created:
						self.stdout.write(self.style.SUCCESS(
							f'Trial {trial.pk} marked as sent to {subscriber.email} for list "{lst.list_name}".'
						))

		self.stdout.write(self.style.SUCCESS('All articles and trials have been marked as sent to all subscribers.'))