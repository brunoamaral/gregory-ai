from django.core.management.base import BaseCommand
from django.db.models import Count, Q
from django.utils import timezone
from gregory.models import Authors
from gregory.functions import normalize_orcid
from gregory.services.orcid_sync import apply_orcid_record_to_author
from subscriptions.management.commands.utils.get_credentials import (
	get_orcid_credentials,
)
import orcid
import requests


class Command(BaseCommand):
	help = "Updates authors' country and biography information and ORCID check timestamp from the ORCID public API based on specific criteria."

	def add_arguments(self, parser):
		parser.add_argument(
			"--organization",
			type=str,
			required=True,
			help="Organisation slug to use for ORCID credentials and author scoping.",
		)

	def handle(self, *args, **kwargs):
		from django.apps import apps

		Organization = apps.get_model("organizations", "Organization")

		org_slug = kwargs["organization"]
		try:
			org = Organization.objects.get(slug=org_slug)
		except Organization.DoesNotExist:
			self.stderr.write(
				self.style.ERROR(f"Organisation with slug '{org_slug}' not found.")
			)
			return

		orcid_key, orcid_secret = get_orcid_credentials(organization=org)
		if not orcid_key or not orcid_secret:
			self.stderr.write(
				self.style.ERROR(
					f"[{org_slug}] ORCID credentials not found. Configure orcid_client_id and "
					f"orcid_client_secret on the organisation via the admin."
				)
			)
			return

		orcid_api = orcid.PublicAPI(orcid_key, orcid_secret, sandbox=False)
		try:
			token = orcid_api.get_search_token_from_orcid()
		except requests.exceptions.HTTPError as e:
			self.stderr.write(
				self.style.ERROR(
					f"[{org_slug}] Failed to retrieve token from ORCID API: {e}"
				)
			)
			return  # Stop execution if token retrieval fails

		thirty_days_ago = timezone.now() - timezone.timedelta(days=30)
		authors = (
			Authors.objects.annotate(num_articles=Count("articles", distinct=True))
			.filter(
				Q(orcid_check__lte=thirty_days_ago) | Q(orcid_check__isnull=True),
				ORCID__isnull=False,
				articles__teams__organization=org,
			)
			.distinct()
			.order_by("-num_articles")[:1000]
		)
		# Initialize counters for summary
		success_count = 0
		error_count = 0
		no_address_count = 0
		no_biography_count = 0

		verbosity = kwargs.get("verbosity", 1)
		for author in authors:
			try:
				# Clean up ORCID ID to ensure proper format
				author_orcid_number = normalize_orcid(author.ORCID)
				if verbosity >= 2:
					self.stdout.write(
						f"[{org_slug}] Processing ORCID: {author_orcid_number}"
					)
				record = orcid_api.read_record_public(
					author_orcid_number, "record", token
				)
				result = apply_orcid_record_to_author(author, record)

				if result.has_address:
					success_count += 1
				else:
					if verbosity >= 2:
						self.stdout.write(
							f"[{org_slug}] No address found for author with ORCID: {author_orcid_number}"
						)
					no_address_count += 1

				if not result.has_biography:
					if verbosity >= 2:
						self.stdout.write(
							f"[{org_slug}] No biography found for author with ORCID: {author_orcid_number}"
						)
					no_biography_count += 1

			except requests.exceptions.HTTPError as e:
				if (
					hasattr(e, "response")
					and e.response is not None
					and e.response.status_code == 409
				):
					try:
						error_body = e.response.json()
						error_code = error_body.get("error-code")
					except Exception:
						error_code = None

					if error_code == 9044:
						reason = "deactivated"
					elif error_code == 9018:
						reason = "locked"
					else:
						reason = "unavailable (409)"

					self.stderr.write(
						self.style.WARNING(
							f"[{org_slug}] Skipping ORCID {author_orcid_number}: record is {reason}. "
							f"Suppressing for 30 days."
						)
					)
					author.orcid_check = timezone.now()
					author.save()
				else:
					self.stderr.write(
						self.style.ERROR(
							f"[{org_slug}] Failed to update author with ORCID: {author_orcid_number}. Error: {e}"
						)
					)
					if hasattr(e, "response") and e.response is not None:
						self.stderr.write(
							self.style.ERROR(
								f"Response status code: {e.response.status_code}"
							)
						)
						self.stderr.write(
							self.style.ERROR(f"Response content: {e.response.text}")
						)
				error_count += 1
				continue

		# Print summary
		self.stdout.write(f"\n[{org_slug}] Summary:")
		self.stdout.write(f"[{org_slug}] Total authors processed: {len(authors)}")
		self.stdout.write(f"[{org_slug}] Successful updates: {success_count}")
		self.stdout.write(f"[{org_slug}] Authors with no address: {no_address_count}")
		self.stdout.write(
			f"[{org_slug}] Authors with no biography: {no_biography_count}"
		)
		self.stdout.write(f"[{org_slug}] Failed updates: {error_count}")
