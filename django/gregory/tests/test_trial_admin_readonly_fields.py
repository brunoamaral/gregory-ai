"""
Tests for TrialAdmin's read-only field policy: every field populated by the
automatic importers (WHO ICTRP, ClinicalTrials.gov, EU CTIS) is locked read-only in
the admin, except a small allowlist (ADMIN_EDITABLE_FIELDS) the admin is expected to
curate by hand — the canonical link, registry identifiers, and the three
relationship fields (sources/teams/subjects). Editorial content and category
assignments are edited via their own always-editable inlines, not fields on this
form.

Run:
  docker exec gregory python manage.py test gregory.tests.test_trial_admin_readonly_fields
"""

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from gregory.admin import TrialAdmin
from gregory.models import Trials

User = get_user_model()


class GetReadonlyFieldsTests(TestCase):
	def setUp(self):
		self.trial_admin = TrialAdmin(Trials, AdminSite())

	def _fieldset_fields(self):
		fields = set()
		for _, options in self.trial_admin.fieldsets:
			fields.update(options.get("fields", ()))
		return fields

	def test_admin_editable_fields_are_not_readonly(self):
		readonly = set(self.trial_admin.get_readonly_fields(None))
		for field in self.trial_admin.ADMIN_EDITABLE_FIELDS:
			self.assertNotIn(field, readonly)

	def test_every_other_fieldset_field_is_readonly(self):
		readonly = set(self.trial_admin.get_readonly_fields(None))
		fieldset_fields = self._fieldset_fields()
		expected_readonly = fieldset_fields - self.trial_admin.ADMIN_EDITABLE_FIELDS
		self.assertEqual(readonly, expected_readonly)

	def test_derived_and_importer_managed_fields_are_readonly(self):
		"""Spot-check a few fields that must stay locked for specific reasons:
		editable=False derived fields, and importer-managed JSON blobs."""
		readonly = set(self.trial_admin.get_readonly_fields(None))
		for field in (
			"phase_normalized",
			"recruitment_status_normalized",
			"regions_normalized",
			"primary_sponsor_normalized",
			"countries_by_source",
			"links",
			"last_updated",
			"title",
			"primary_sponsor",
			"inclusion_criteria",
		):
			self.assertIn(field, readonly)

	def test_relationship_fields_stay_editable(self):
		readonly = set(self.trial_admin.get_readonly_fields(None))
		for field in ("sources", "teams", "subjects"):
			self.assertNotIn(field, readonly)

	def test_link_and_identifiers_stay_editable(self):
		readonly = set(self.trial_admin.get_readonly_fields(None))
		self.assertNotIn("link", readonly)
		self.assertNotIn("identifiers", readonly)


class ChangeFormRenderingTests(TestCase):
	"""Live HTTP-level smoke test — catches template-level crashes (e.g. a readonly
	widget that doesn't know how to render a JSONField) that a pure unit test on
	get_readonly_fields() wouldn't."""

	def setUp(self):
		self.superuser = User.objects.create_superuser(
			username="trial-admin-root", email="root@example.com", password="pw"
		)
		self.client.force_login(self.superuser)
		self.trial = Trials.objects.create(
			title="Readonly Fields Test Trial",
			link="https://example.com/readonly-fields-test",
			identifiers={"nct": "NCT00000000"},
			primary_sponsor="Test Sponsor",
			countries_by_source={"ctgov": "France"},
		)

	def test_change_form_renders_without_error(self):
		url = reverse("admin:gregory_trials_change", args=[self.trial.pk])
		response = self.client.get(url)
		self.assertEqual(response.status_code, 200)

	def test_readonly_field_is_excluded_from_the_form(self):
		"""Django excludes readonly fields from ModelForm.base_fields entirely —
		this is what actually prevents a POST from changing them."""
		admin_site_form = TrialAdmin(Trials, AdminSite())
		request = type("_Req", (), {"user": self.superuser})()
		form_class = admin_site_form.get_form(request, self.trial)
		self.assertNotIn("title", form_class.base_fields)
		self.assertNotIn("primary_sponsor", form_class.base_fields)

	def test_editable_allowlist_field_is_present_in_the_form(self):
		admin_site_form = TrialAdmin(Trials, AdminSite())
		request = type("_Req", (), {"user": self.superuser})()
		form_class = admin_site_form.get_form(request, self.trial)
		self.assertIn("link", form_class.base_fields)
		self.assertIn("identifiers", form_class.base_fields)

	def test_posting_a_change_to_a_readonly_field_does_not_alter_it(self):
		"""Defense in depth: even if someone crafted a POST body including a
		readonly field's name, Django's ModelForm ignores it since it was never
		part of the form — the stored value must survive unchanged."""
		url = reverse("admin:gregory_trials_change", args=[self.trial.pk])
		get_response = self.client.get(url)
		self.assertEqual(get_response.status_code, 200)

		post_data = {
			"title": "Hacked Title",
			"link": self.trial.link,
			"identifiers": '{"nct": "NCT00000000"}',
			"_save": "Save",
		}
		# Inline management forms are required by Django's formset machinery for a
		# POST to validate; empty-but-present is enough to exercise the readonly
		# guard without needing real inline data.
		for prefix in (
			"trialorgcontent_set",
			"articletrialreference_set",
			"trialcategoryassignment_set",
			"trialcountry_set",
		):
			post_data[f"{prefix}-TOTAL_FORMS"] = "0"
			post_data[f"{prefix}-INITIAL_FORMS"] = "0"
			post_data[f"{prefix}-MIN_NUM_FORMS"] = "0"
			post_data[f"{prefix}-MAX_NUM_FORMS"] = "1000"

		self.client.post(url, post_data)
		self.trial.refresh_from_db()
		self.assertEqual(self.trial.title, "Readonly Fields Test Trial")
