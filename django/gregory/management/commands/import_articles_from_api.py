import requests
from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_datetime
from django.utils import timezone
from organizations.models import Organization

from gregory.models import Articles, Authors, Sources, Team, Subject, ArticleSubjectRelevance, ArticleOrgContent

class Command(BaseCommand):
	help = 'Fetches articles from the API and imports them into the Django app.'

	def add_arguments(self, parser):
		parser.add_argument(
			'api_url',
			type=str,
			help='The API URL to fetch articles from.',
		)
		parser.add_argument(
			'--target-org',
			type=str,
			required=True,
			help='Organization slug or ID that owns the imported takeaways/summaries.',
		)

	def handle(self, *args, **options):
		api_url = options['api_url']
		target_org_arg = options['target_org']

		try:
			if target_org_arg.isdigit():
				target_org = Organization.objects.get(pk=int(target_org_arg))
			else:
				target_org = Organization.objects.get(slug=target_org_arg)
		except Organization.DoesNotExist:
			raise CommandError("Organization not found: %s" % target_org_arg)

		imported_count = 0
		self.stdout.write("Starting import from %s" % api_url)

		# Loop through pages until there is no 'next' URL
		while api_url:
			try:
				response = requests.get(api_url)
				response.raise_for_status()
			except Exception as e:
				raise CommandError("Error fetching data from API: %s" % e)
			data = response.json()
			results = data.get("results", [])

			for item in results:
				# Extract basic article fields
				title = item.get("title")
				link = item.get("link")
				doi = item.get("doi")
				summary = item.get("summary")
				published_date = parse_datetime(item.get("published_date")) if item.get("published_date") else None
				publisher = item.get("publisher")
				container_title = item.get("container_title")
				access = item.get("access")
				takeaways = item.get("takeaways")
				summary_plain_english = item.get("summary_plain_english")
				discovery_date = parse_datetime(item.get("discovery_date")) if item.get("discovery_date") else timezone.now()

				# Create or update the Article instance using title and link as unique identifiers
				article, created = Articles.objects.update_or_create(
					title=title,
					link=link,
					defaults={
						"doi": doi,
						"summary": summary,
						"published_date": published_date,
						"publisher": publisher,
						"container_title": container_title,
						"access": access,
						"discovery_date": discovery_date,
					}
				)

				# Upsert per-org editorial content if the upstream provided any
				if takeaways or summary_plain_english:
					org_defaults = {}
					if takeaways:
						org_defaults["takeaways"] = takeaways
					if summary_plain_english:
						org_defaults["summary_plain_english"] = summary_plain_english
					ArticleOrgContent.objects.update_or_create(
						article=article,
						organization=target_org,
						defaults=org_defaults,
					)

				# Process ManyToMany relationships

				# Authors
				for author_data in item.get("authors", []):
					author, _ = Authors.objects.update_or_create(
						author_id=author_data.get("author_id"),
						defaults={
							"given_name": author_data.get("given_name"),
							"family_name": author_data.get("family_name"),
							"ORCID": author_data.get("ORCID"),
							"country": author_data.get("country"),
						}
					)
					article.authors.add(author)

				# Sources (the API returns a list of source names)
				for source_name in item.get("sources", []):
					source, _ = Sources.objects.get_or_create(
						name=source_name,
						defaults={
							"source_for": "news article",  # adjust if necessary
						}
					)
					article.sources.add(source)

				# Teams
				for team_data in item.get("teams", []):
					team, _ = Team.objects.get_or_create(
						pk=team_data.get("id"),
						defaults={
							"name": team_data.get("name"),
						}
					)
					article.teams.add(team)

				# Subjects
				for subject_data in item.get("subjects", []):
					subject, _ = Subject.objects.get_or_create(
						pk=subject_data.get("id"),
						defaults={
							"subject_name": subject_data.get("subject_name"),
							"description": subject_data.get("description"),
							"team_id": subject_data.get("team_id"),
						}
					)
					article.subjects.add(subject)

				# Article Subject Relevances
				for relevance in item.get("article_subject_relevances", []):
					subj_data = relevance.get("subject")
					if subj_data:
						subject, _ = Subject.objects.get_or_create(
							pk=subj_data.get("id"),
							defaults={
								"subject_name": subj_data.get("subject_name"),
								"description": subj_data.get("description"),
								"team_id": subj_data.get("team_id"),
							}
						)
						is_relevant = relevance.get("is_relevant", False)
						asr, created_asr = ArticleSubjectRelevance.objects.get_or_create(
							article=article,
							subject=subject,
							defaults={
								"is_relevant": is_relevant
							}
						)
						if not created_asr and asr.is_relevant != is_relevant:
							asr.is_relevant = is_relevant
							asr.save()

				imported_count += 1
				self.stdout.write("Imported article: %s" % title)

			# Proceed to the next page if available
			api_url = data.get("next")

		self.stdout.write(self.style.SUCCESS("Successfully imported %d articles." % imported_count))