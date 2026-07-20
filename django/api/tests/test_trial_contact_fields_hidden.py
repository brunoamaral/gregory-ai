"""
Regression test: the Trials API must never expose the trial's public-contact
fields (contact_firstname, contact_lastname, contact_address, contact_email,
contact_tel, contact_affiliation) — personal contact details of a real person,
scraped from WHO ICTRP/ClinicalTrials.gov. The model fields themselves are kept
(used internally / by the admin), only their API exposure is removed.

Run:
  docker exec gregory python manage.py test api.tests.test_trial_contact_fields_hidden
"""

from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from api.serializers import TrialSerializer
from gregory.models import Organization, OrganizationApiSettings, Subject, Team, Trials

CONTACT_FIELDS = [
	"contact_firstname",
	"contact_lastname",
	"contact_address",
	"contact_email",
	"contact_tel",
	"contact_affiliation",
]


class TrialContactFieldsHiddenTests(TestCase):
	def setUp(self):
		self.organization = Organization.objects.create(
			name="Contact Fields Org", slug="contact-fields-org"
		)
		OrganizationApiSettings.objects.filter(organization=self.organization).update(
			make_api_public=True
		)
		self.team = Team.objects.create(
			name="Contact Fields Team",
			slug="contact-fields-team",
			organization=self.organization,
		)
		self.subject = Subject.objects.create(
			subject_name="Contact Fields Subject",
			subject_slug="contact-fields-subject",
			team=self.team,
		)
		self.trial = Trials.objects.create(
			title="Contact Fields Trial",
			link="https://example.com/contact-fields",
			published_date=timezone.now(),
			contact_firstname="Jane",
			contact_lastname="Doe",
			contact_address="123 Main St",
			contact_email="jane.doe@example.com",
			contact_tel="+1-555-0100",
			contact_affiliation="Example University Hospital",
		)
		self.trial.teams.add(self.team)
		self.trial.subjects.add(self.subject)
		self.client = APIClient()

	def test_serializer_meta_fields_excludes_contact_fields(self):
		for field in CONTACT_FIELDS:
			self.assertNotIn(field, TrialSerializer.Meta.fields)

	def test_serializer_instance_has_no_contact_field(self):
		serializer = TrialSerializer(self.trial)
		for field in CONTACT_FIELDS:
			self.assertNotIn(field, serializer.data)

	def test_live_response_does_not_include_contact_fields(self):
		response = self.client.get(f"/trials/?trial_id={self.trial.trial_id}")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		result = response.data["results"][0]
		for field in CONTACT_FIELDS:
			self.assertNotIn(field, result)

	def test_live_response_does_not_leak_contact_values_anywhere(self):
		"""Belt-and-braces: none of the actual contact values appear anywhere in
		the serialized response, even under an unexpected key."""
		response = self.client.get(f"/trials/?trial_id={self.trial.trial_id}")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		body = str(response.data)
		for value in (
			"Jane",
			"Doe",
			"123 Main St",
			"jane.doe@example.com",
			"+1-555-0100",
			"Example University Hospital",
		):
			self.assertNotIn(value, body)
