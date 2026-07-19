from django.core.management.base import BaseCommand
from django.core.management import call_command


class Command(BaseCommand):
	help = "Runs a list of specified management commands."

	def add_arguments(self, parser):
		parser.add_argument(
			"--recent-days",
			type=int,
			default=30,
			help="Number of days to look back when processing trial references (default: 30)",
		)
		parser.add_argument(
			"--categories-days",
			type=int,
			default=30,
			help=(
				"Incremental window in days for rebuild_categories (default: 30). "
				"Categories whose matching configuration changed are fully re-matched regardless."
			),
		)
		parser.add_argument(
			"--full-category-rebuild",
			action="store_true",
			help="Run rebuild_categories over all content instead of the incremental window",
		)

	def handle(self, *args, **options):
		rebuild_kwargs = {}
		if not options.get("full_category_rebuild"):
			rebuild_kwargs["days"] = options.get("categories_days", 30)

		# standard commands to run in the pipeline
		commands_to_run = [
			("feedreader_articles", {}),  # 1. Feedreader. Get articles
			("feedreader_trials", {}),  # 2. Feedreader. Get trials
			(
				"feedreader_trials_ctgov",
				{"max_results": 2000},
			),  # 3. ClinicalTrials.gov trials (incremental window; cap is a safety ceiling)
			(
				"feedreader_trials_ctis",
				{"limit": 2000},
			),  # 3b. CTIS public API trials (full result set per source; RSS stays active as fallback)
			("find_doi", {}),  # 4. Find missing DOI
			("update_articles_info", {}),  # 5. Find missing data
			("get_authors", {}),  # 6. Find missing authors
			(
				"rebuild_categories",
				rebuild_kwargs,
			),  # 7. Assign categories (incremental by default)
			(
				"get_takeaways",
				{"limit": 50},
			),  # 8. Get takeaways (50 > ~30 new articles/run, so the queue drains)
		]

		# First run all the standard commands
		for cmd, kwargs in commands_to_run:
			try:
				self.stdout.write(self.style.SUCCESS(f"Running command: {cmd}"))
				call_command(cmd, **kwargs)
				self.stdout.write(self.style.SUCCESS(f"Finished command: {cmd}"))
			except Exception as e:
				self.stderr.write(
					self.style.ERROR(f"Error running command {cmd}: {str(e)}")
				)
				self.stdout.write(self.style.WARNING(f"Skipping command: {cmd}"))

		# Run update_orcid per organisation — each org uses its own ORCID credentials
		from django.apps import apps
		from gregory.models import OrganizationCredentials

		Organization = apps.get_model("organizations", "Organization")
		for org in Organization.objects.all().order_by("slug"):
			try:
				creds = org.credentials
			except OrganizationCredentials.DoesNotExist:
				creds = None
			if not creds or not creds.orcid_client_id or not creds.orcid_client_secret:
				self.stdout.write(
					self.style.WARNING(
						f'Skipping update_orcid for organisation "{org.slug}": no ORCID credentials configured.'
					)
				)
				continue
			try:
				self.stdout.write(
					self.style.SUCCESS(
						f"Running update_orcid for organisation: {org.slug}"
					)
				)
				call_command("update_orcid", organization=org.slug)
			except Exception as e:
				self.stderr.write(
					self.style.ERROR(f"Error running update_orcid for {org.slug}: {e}")
				)

		# Now run predict_articles with the --all-teams flag
		try:
			self.stdout.write(
				self.style.SUCCESS("Running predict_articles with --all-teams flag")
			)
			call_command("predict_articles", all_teams=True)
			self.stdout.write(
				self.style.SUCCESS(
					"Finished running predict_articles with --all-teams flag"
				)
			)
		except Exception as e:
			self.stderr.write(
				self.style.ERROR(f"Error running predict_articles: {str(e)}")
			)

		# Refresh the denormalized relevant flag (predict_articles bulk_creates
		# MLPredictions, which fires no signals, so a full pass is required here)
		try:
			self.stdout.write(
				self.style.SUCCESS("Running refresh_article_relevance")
			)
			call_command("refresh_article_relevance")
			self.stdout.write(
				self.style.SUCCESS("Finished running refresh_article_relevance")
			)
		except Exception as e:
			self.stderr.write(
				self.style.ERROR(f"Error running refresh_article_relevance: {str(e)}")
			)

		# Run detect_trial_references with recent articles only
		try:
			days = options.get("recent_days", 30)
			self.stdout.write(
				self.style.SUCCESS(
					f"Running detect_trial_references for articles from the last {days} days"
				)
			)
			call_command("detect_trial_references", recent=True, days=days)
			self.stdout.write(
				self.style.SUCCESS("Finished running detect_trial_references")
			)
		except Exception as e:
			self.stderr.write(
				self.style.ERROR(f"Error running detect_trial_references: {str(e)}")
			)

		# Prune old sent notification records (keeps last 30 days by default)
		try:
			self.stdout.write(
				self.style.SUCCESS(
					"Running prune_sent_notifications to clean up old records"
				)
			)
			call_command("prune_sent_notifications", days=30)
			self.stdout.write(
				self.style.SUCCESS("Finished running prune_sent_notifications")
			)
		except Exception as e:
			self.stderr.write(
				self.style.ERROR(f"Error running prune_sent_notifications: {str(e)}")
			)
