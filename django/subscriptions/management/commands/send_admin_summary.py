from django.conf import settings
from django.contrib.sites.models import Site
from django.core.management.base import BaseCommand
from django.template.loader import get_template
from django.utils.html import strip_tags
from gregory.models import Articles, Trials, Team, MLPredictions
from sitesettings.models import CustomSetting
from subscriptions.management.commands.utils.send_email import send_email
from django.db.models import Prefetch


class Command(BaseCommand):
	help = 'Sends an admin summary every 2 days.'

	def handle(self, *args, **options):
		site = Site.objects.get_current()
		customsettings = CustomSetting.objects.get(site=site)

		# Step 1: Fetch all teams
		teams = Team.objects.all()

		if not teams.exists():
			self.stdout.write(self.style.WARNING("No teams found. Skipping admin summary."))
			return

		for team in teams:
			members = team.members.all()  # Assuming `members` is a related field
			subjects = team.subjects.all()

			if not members.exists():
				self.stdout.write(self.style.WARNING(f"No members found in team {team.name}. Skipping."))
				continue

			if not subjects.exists():
				self.stdout.write(self.style.WARNING(f"No subjects associated with team {team.name}. Skipping."))
				continue

			for subject in subjects:
				# Step 2: Fetch unsent articles and trials for this team and subject
				articles = Articles.objects.filter(subjects=subject).exclude(sent_to_teams=team).prefetch_related(
					Prefetch('ml_predictions', queryset=MLPredictions.objects.select_related('subject'))
				)
				trials = Trials.objects.filter(subjects=subject).exclude(sent_to_teams=team)

				if not articles.exists() and not trials.exists():
					self.stdout.write(self.style.WARNING(f'No new articles or trials for team "{team.name}" and subject "{subject}". Skipping.'))
					continue

				for member in members:
					self.stdout.write(self.style.SUCCESS(f"Sending admin summary to {member.email}."))

					# Step 3: Prepare the summary context for the email
					summary_context = {
						"articles": articles,
						"trials": trials,
						"admin": member.email,
						"title": customsettings.title,
						"email_footer": customsettings.email_footer,
						"site": site,
					}

					# Render email content
					html_content = get_template('emails/admin_summary.html').render(summary_context)
					text_content = strip_tags(html_content)

					# Step 4: Send email
					result = send_email(
						to=member.email,
						subject=f'{subject} | Admin Summary',
						html=html_content,
						text=text_content,
						site=site,
						sender_name="GregoryAI"
					)

					# Step 5: Log email success/failure
					if result.status_code == 200:
						self.stdout.write(self.style.SUCCESS(f"Admin summary email sent to {member.email}."))
						# Step 6: Mark articles and trials as sent to this team
						for article in articles:
							article.sent_to_teams.add(team)
						for trial in trials:
							trial.sent_to_teams.add(team)
					else:
						self.stdout.write(self.style.ERROR(f"Failed to send email to {member.email}. Status: {result.status_code}"))