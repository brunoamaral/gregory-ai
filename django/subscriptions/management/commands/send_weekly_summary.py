from datetime import timedelta
from django.conf import settings
from django.contrib.sites.models import Site
from django.core.management.base import BaseCommand
from django.template.loader import get_template
from django.utils.html import strip_tags
from django.utils.timezone import now
from gregory.models import Articles, Trials, Subject
from sitesettings.models import CustomSetting
from subscriptions.models import (
	Lists,
	Subscribers,
	SentArticleNotification,
	SentTrialNotification
)
from subscriptions.management.commands.utils.send_email import send_email


class Command(BaseCommand):
	help = 'Sends a weekly digest email for all weekly digest lists.'

	def handle(self, *args, **options):
		site = Site.objects.get_current()
		customsettings = CustomSetting.objects.get(site=site)

		# Step 1: Find all lists that are a weekly digest
		weekly_digest_lists = Lists.objects.filter(weekly_digest=True, subjects__isnull=False).distinct()

		if not weekly_digest_lists.exists():
			self.stdout.write(self.style.WARNING('No lists marked as weekly digest with subjects found.'))
			return

		for digest_list in weekly_digest_lists:
			# Step 2: Get the subjects for this list
			list_subjects = digest_list.subjects.all()

			if not list_subjects.exists():
				self.stdout.write(self.style.WARNING(f'The list "{digest_list.list_name}" is marked as weekly digest but has no subjects.'))
				continue

			# Step 3: Gather clinical trials and articles for the subjects of this list within the last 30 days
			articles = Articles.objects.filter(
				subjects__in=list_subjects,
				discovery_date__gte=now() - timedelta(days=30)
			).distinct()

			trials = Trials.objects.filter(
				subjects__in=list_subjects,
				discovery_date__gte=now() - timedelta(days=30)
			).distinct()

			# Check if there are any articles or trials before proceeding
			if not articles.exists() and not trials.exists():
				self.stdout.write(self.style.WARNING(f'No articles or trials found for the weekly digest list "{digest_list.list_name}". Skipping.'))
				continue

			# Step 4: Send the digest to the subscribers of this list
			subscribers = Subscribers.objects.filter(
				active=True,
				subscriptions=digest_list
			).distinct()

			if not subscribers.exists():
				self.stdout.write(self.style.WARNING(f'No active subscribers found for the weekly digest list "{digest_list.list_name}".'))
				continue

			for subscriber in subscribers:
				# Filter out articles already sent to this subscriber for this list
				sent_article_ids = SentArticleNotification.objects.filter(
					article__in=articles,
					list=digest_list,
					subscriber=subscriber
				).values_list('article_id', flat=True)

				unsent_articles = articles.exclude(pk__in=sent_article_ids)

				# Filter out trials already sent to this subscriber for this list
				sent_trial_ids = SentTrialNotification.objects.filter(
					trial__in=trials,
					list=digest_list,
					subscriber=subscriber
				).values_list('trial_id', flat=True)

				unsent_trials = trials.exclude(pk__in=sent_trial_ids)

				# If there's nothing new, skip this subscriber
				if not unsent_articles.exists() and not unsent_trials.exists():
					self.stdout.write(
						self.style.WARNING(f'No new articles or trials for {subscriber.email} in list "{digest_list.list_name}".')
					)
					continue

				# Prepare and send the email
				summary_context = {
					"articles": unsent_articles,
					"trials": unsent_trials,
					"title": customsettings.title,
					"email_footer": customsettings.email_footer,
					"site": site,
				}

				html_content = get_template('emails/weekly_summary.html').render(summary_context)
				text_content = strip_tags(html_content)

				# Use the shared email utility here
				result = send_email(
					to=subscriber.email,
					subject=f'Your Weekly Digest: {digest_list.list_name}',
					html=html_content,
					text=text_content,
					site=site,
					sender_name="GregoryAI"
				)

				if result.status_code == 200:
					self.stdout.write(
						self.style.SUCCESS(f'Weekly digest email sent to {subscriber.email} for list "{digest_list.list_name}".')
					)
					# Step 5: Record that these emails were already sent
					for article in unsent_articles:
						SentArticleNotification.objects.get_or_create(
							article=article,
							list=digest_list,
							subscriber=subscriber
						)
					for trial in unsent_trials:
						SentTrialNotification.objects.get_or_create(
							trial=trial,
							list=digest_list,
							subscriber=subscriber
						)
				else:
					self.stdout.write(
						self.style.ERROR(f'Failed to send weekly digest email to {subscriber.email} for list "{digest_list.list_name}". Status: {result.status_code}')
					)