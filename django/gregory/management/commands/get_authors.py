from django.core.management.base import BaseCommand
from django.db.models import Q
from crossref.restful import Works, Etiquette
from dotenv import load_dotenv
import os
from gregory.models import Articles, Authors
from gregory.functions import normalize_orcid
from gregory.utils.enrichment import clear_marker, due_filter, record_fruitless_attempt
from sitesettings.models import CustomSetting


class Command(BaseCommand):
	help = "Fetches authors from CrossRef and updates the database."

	def handle(self, *args, **kwargs):
		load_dotenv()
		SITE = CustomSetting.objects.get(site__domain=os.environ.get("DOMAIN_NAME"))
		CLIENT_WEBSITE = "https://" + SITE.site.domain + "/"
		my_etiquette = Etiquette(SITE.title, "v8", CLIENT_WEBSITE, SITE.admin_email)
		works = Works(etiquette=my_etiquette)

		# Articles without authors whose backoff marker is unset or due. The old
		# crossref_check window is gone: this command used to refresh
		# crossref_check on every article it touched, which kept zero-author
		# articles inside its own selection window forever.
		articles = Articles.objects.filter(
			due_filter("authors_next_check"),
			~Q(doi__isnull=True) & ~Q(doi=""),
			authors__isnull=True,
		)
		for article in articles:
			try:
				w = works.doi(article.doi)
			except Exception as e:
				# Network/API failure: not a completed attempt; the marker must
				# not advance — the next run retries immediately.
				self.stderr.write(
					self.style.WARNING(
						f"CrossRef lookup failed for DOI {article.doi}: {e}. "
						"Will retry next run."
					)
				)
				continue

			authors_added = False
			if w and "author" in w and w["author"]:
				for author_data in w["author"]:
					# Ensure we have the necessary information
					given_name = author_data.get("given")
					family_name = author_data.get("family")
					raw_orcid = author_data.get("ORCID")
					orcid = normalize_orcid(raw_orcid)

					if not given_name or not family_name:
						self.stderr.write(
							self.style.WARNING(
								f"Missing given name or family name, skipping this author. Article DOI: {article.doi}."
							)
						)
						continue

					# First, attempt to match or create by ORCID
					if orcid:
						author_obj, created = Authors.objects.get_or_create(
							ORCID=orcid,
							defaults={
								"given_name": given_name,
								"family_name": family_name,
							},
						)
						if not created:
							# Update the author name if it's different
							if (
								author_obj.given_name != given_name
								or author_obj.family_name != family_name
							):
								author_obj.given_name = given_name
								author_obj.family_name = family_name
								author_obj.save()
								self.stdout.write(
									self.style.SUCCESS(
										f"Updated author {author_obj.full_name} with ORCID: {orcid}."
									)
								)
					else:
						# Handle authors without ORCID or when ORCID isn't provided
						try:
							author_obj = Authors.objects.get(
								given_name=given_name, family_name=family_name
							)
						except Authors.DoesNotExist:
							# Create a new author if none found
							self.stdout.write(
								self.style.SUCCESS(
									f"Creating author: {given_name} {family_name} with ORCID: {orcid}"
								)
							)
							author_obj = Authors.objects.create(
								given_name=given_name,
								family_name=family_name,
								ORCID=orcid,
							)
						except Authors.MultipleObjectsReturned:
							self.stderr.write(
								self.style.WARNING(
									f"Multiple authors found for {given_name} {family_name}, unable to uniquely identify. Skipping."
								)
							)
							continue

					# Add author to article if an author object was successfully created or retrieved
					if author_obj:
						article.authors.add(author_obj)
						authors_added = True

			# NOTE: this command must never write crossref_check — that
			# timestamp records CrossRef *metadata* freshness and is owned by
			# the feedreader / update_articles_info.
			if authors_added:
				clear_marker(article, "authors")
			else:
				# CrossRef responded but the record has no usable authors:
				# back off before asking again.
				record_fruitless_attempt(article, "authors")

		self.stdout.write(
			self.style.SUCCESS("Successfully updated authors from CrossRef.")
		)