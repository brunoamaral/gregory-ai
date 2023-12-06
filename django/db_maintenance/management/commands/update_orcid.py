from django.core.management.base import BaseCommand
from django.db.models import Count, Q
from django.utils import timezone
from gregory.models import Authors
import orcid
import os
from dotenv import load_dotenv
load_dotenv()

class Command(BaseCommand):
	orcid_key = os.environ.get('ORCID_ClientID')
	orcid_secret = os.environ.get('ORCID_ClientSecret')

	def handle(self, *args, **kwargs):
		orcid_api = orcid.PublicAPI(self.orcid_key, self.orcid_secret, sandbox=False)
		token = orcid_api.get_search_token_from_orcid()
		six_months_ago = timezone.now() - timezone.timedelta(days=180)
		authors = Authors.objects.annotate(num_articles=Count('articles')).filter(
				Q(orcid_check__lte=six_months_ago) | Q(orcid_check__isnull=True),
				ORCID__isnull=False,
				country__isnull=True
		).order_by('-num_articles')[:1000]

		for author in authors:
			author_orcid_number = author.ORCID.replace('http://orcid.org/', '')
			record = orcid_api.read_record_public(author_orcid_number, 'record', token)
			addresses = record.get('person', {}).get('addresses', {}).get('address', [])
			orcid_check = timezone.now()
			author.orcid_check = orcid_check
			print(author)
			if addresses:
				country_code = addresses[0].get('country', {}).get('value')
				# Assigning the country code to the author's country field and saving
				author.country = country_code  # CountryField will handle the country code correctly
			else:
				print(f"No address found for author with ORCID: {author_orcid_number}")
			author.save()
