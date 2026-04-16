from django.core.management.base import BaseCommand
from django.db.models import Count, Q
from django.utils import timezone
from gregory.models import Authors, Team
from gregory.functions import normalize_orcid
from subscriptions.management.commands.utils.get_credentials import get_orcid_credentials
import orcid
from dotenv import load_dotenv
from simple_history.utils import update_change_reason
import requests
load_dotenv()

class Command(BaseCommand):
	help = 'Updates authors\' country information and ORCID check timestamp from the ORCID public API based on specific criteria.'

	def add_arguments(self, parser):
		parser.add_argument(
			'--team',
			type=str,
			default=None,
			help='Team slug to use for ORCID credentials. Falls back to organisation credentials then environment variables if not set.',
		)

	def handle(self, *args, **kwargs):
		team_slug = kwargs.get('team')
		team = None
		if team_slug:
			try:
				team = Team.objects.get(slug=team_slug)
			except Team.DoesNotExist:
				self.stderr.write(self.style.ERROR(f"Team with slug '{team_slug}' not found."))
				return

		orcid_key, orcid_secret = get_orcid_credentials(team)
		if not orcid_key or not orcid_secret:
			self.stderr.write(self.style.ERROR(
				"ORCID credentials not found. Set ORCID_CLIENT_ID and ORCID_CLIENT_SECRET "
				"in the environment, or configure them on the team or organisation."
			))
			return

		orcid_api = orcid.PublicAPI(orcid_key, orcid_secret, sandbox=False)
		try:
			token = orcid_api.get_search_token_from_orcid()
		except requests.exceptions.HTTPError as e:
			print(f"Failed to retrieve token from ORCID API: {e}")
			return  # Stop execution if token retrieval fails

		three_months_ago = timezone.now() - timezone.timedelta(days=90)
		authors = Authors.objects.annotate(num_articles=Count('articles')).filter(
						Q(orcid_check__lte=three_months_ago) | Q(orcid_check__isnull=True),
						ORCID__isnull=False,
						country__isnull=True
		).order_by('-num_articles')[:1000]
		# Initialize counters for summary
		success_count = 0
		error_count = 0
		no_address_count = 0

		for author in authors:
			try:
				initial_country = author.country
				
				# Clean up ORCID ID to ensure proper format
				author_orcid_number = normalize_orcid(author.ORCID)
				print(f"Cleaned ORCID ID: {author_orcid_number}")  # Debug
				record = orcid_api.read_record_public(author_orcid_number, 'record', token)
				addresses = record.get('person', {}).get('addresses', {}).get('address', [])
				orcid_check = timezone.now()
				author.orcid_check = orcid_check

				if addresses:
					country_code = addresses[0].get('country', {}).get('value')
					author.country = country_code
					change_reason = 'Updated country from ORCID API.'
					success_count += 1
				else:
					print(f"No address found for author with ORCID: {author_orcid_number}")
					change_reason = 'Attempted to update country from ORCID API but no address found.'
					no_address_count += 1

				author.save()
				if initial_country != author.country:
					update_change_reason(author, change_reason)
					
			except requests.exceptions.HTTPError as e:
				print(f"Failed to update author with ORCID: {author_orcid_number}. Error: {e}")
				# Print more detailed error information
				if hasattr(e, 'response') and e.response is not None:
					print(f"Response status code: {e.response.status_code}")
					print(f"Response content: {e.response.text}")
				error_count += 1
				continue

		# Print summary
		print(f"\nSummary:")
		print(f"Total authors processed: {len(authors)}")
		print(f"Successful updates: {success_count}")
		print(f"Authors with no address: {no_address_count}")
		print(f"Failed updates: {error_count}")