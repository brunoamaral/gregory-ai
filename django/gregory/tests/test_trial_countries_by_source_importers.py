"""Importer integration tests for Trials.countries_by_source (Layer 1 of the
country-normalization design — see docs/TRIAL-COUNTRY-NORMALIZATION-PLAN.md).

Mirrors the structure of test_trial_links.py: each importer must write ONLY its own
key ("ctgov" for feedreader_trials_ctgov.py, "ictrp" for importWHOXML.py), never
touching a key written by another source, exactly like the existing `links` field.

Run:
  docker exec gregory python manage.py test gregory.tests.test_trial_countries_by_source_importers
"""

import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from django.test import TestCase
from organizations.models import Organization

from gregory.classes import ClinicalTrial
from gregory.management.commands.feedreader_trials_ctgov import Command as CTGovCommand
from gregory.management.commands.importWHOXML import Command as WHOCommand
from gregory.models import Sources, Subject, Team, Trials
from gregory.utils.trial_field_normalizers import TrialRegion

TITLE = "A Cross-registered Study of Country Provenance"
CTGOV_LINK = "https://clinicaltrials.gov/study/NCT00000002"


def _make_source(org, method="rss", name="Test Source"):
	team = Team.objects.create(
		organization=org, name=f"Country Team {name}", slug=f"country-team-{method}"
	)
	subject = Subject.objects.create(
		subject_name=f"Country Subject {name}", subject_slug=f"country-subject-{method}"
	)
	return Sources.objects.create(
		name=name,
		source_for="trials",
		method=method,
		subject=subject,
		team=team,
	)


class CTGovCountriesBySourceTest(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name="Country Test Org CTGov")
		self.source = _make_source(self.org, method="ctgov_api", name="CTGov API")

	def _cmd(self):
		cmd = CTGovCommand()
		cmd.verbosity = 0
		return cmd

	def test_create_new_trial_seeds_ctgov_key(self):
		incoming = ClinicalTrial(
			title=TITLE,
			summary="s",
			link=CTGOV_LINK,
			published_date=None,
			identifiers={"nct": "NCT00000002"},
			extra_fields={"countries": "France, United States"},
		)
		trial = self._cmd().create_new_trial(incoming, self.source)
		self.assertEqual(
			trial.countries_by_source, {"ctgov": "France, United States"}
		)
		self.assertEqual(
			sorted(tc.country.code for tc in trial.trial_countries.all()), ["FR", "US"]
		)

	def test_update_existing_trial_refreshes_ctgov_key_without_touching_others(self):
		trial = Trials.objects.create(
			title=TITLE,
			link=CTGOV_LINK,
			identifiers={"nct": "NCT00000002"},
			countries_by_source={"ictrp": "France;Germany"},
			countries="France",
		)
		incoming = ClinicalTrial(
			title=TITLE,
			summary="s",
			link=CTGOV_LINK,
			published_date=None,
			identifiers={"nct": "NCT00000002"},
			extra_fields={"countries": "France, Spain"},
		)
		self._cmd().update_existing_trial(trial, incoming, self.source)
		trial.refresh_from_db()
		self.assertEqual(
			trial.countries_by_source,
			{"ictrp": "France;Germany", "ctgov": "France, Spain"},
		)

	def test_reimporting_same_value_is_idempotent(self):
		trial = Trials.objects.create(
			title=TITLE,
			link=CTGOV_LINK,
			identifiers={"nct": "NCT00000002"},
			countries_by_source={"ctgov": "France"},
		)
		incoming = ClinicalTrial(
			title=TITLE,
			summary="s",
			link=CTGOV_LINK,
			published_date=None,
			identifiers={"nct": "NCT00000002"},
			extra_fields={"countries": "France"},
		)
		self._cmd().update_existing_trial(trial, incoming, self.source)
		trial.refresh_from_db()
		self.assertEqual(trial.countries_by_source, {"ctgov": "France"})


class WHOCountriesBySourceTest(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name="Country Test Org WHO")
		self.source = _make_source(self.org, method="rss", name="WHO ICTRP")
		self.subject = self.source.subject

	def test_create_new_trial_seeds_ictrp_key(self):
		trial_data = {
			"identifiers": {"who": "WHO000002"},
			"title": TITLE,
			"link": "https://trialsearch.who.int/Trial2.aspx?TrialID=WHO000002",
			"countries": "France;Iran (Islamic Republic of)",
			"countries_by_source": {"ictrp": "France;Iran (Islamic Republic of)"},
		}
		trial = WHOCommand().create_new_trial(trial_data, self.source, self.subject)
		self.assertEqual(
			trial.countries_by_source, {"ictrp": "France;Iran (Islamic Republic of)"}
		)
		self.assertEqual(
			sorted(tc.country.code for tc in trial.trial_countries.all()), ["FR", "IR"]
		)

	def test_update_existing_trial_refreshes_ictrp_key_without_touching_others(self):
		trial = Trials.objects.create(
			title=TITLE,
			link="https://trialsearch.who.int/Trial2.aspx?TrialID=WHO000002",
			identifiers={"who": "WHO000002"},
			countries_by_source={"ctgov": "France, United States"},
			countries="France",
		)
		trial_data = {
			"identifiers": {"who": "WHO000002"},
			"title": TITLE,
			"link": "https://trialsearch.who.int/Trial2.aspx?TrialID=WHO000002",
			"countries": "Germany;Spain",
			"countries_by_source": {"ictrp": "Germany;Spain"},
		}
		WHOCommand().update_existing_trial(trial, trial_data, self.source, self.subject)
		trial.refresh_from_db()
		self.assertEqual(
			trial.countries_by_source,
			{"ctgov": "France, United States", "ictrp": "Germany;Spain"},
		)

	def test_missing_countries_key_does_not_touch_countries_by_source(self):
		trial = Trials.objects.create(
			title=TITLE,
			link="https://trialsearch.who.int/Trial2.aspx?TrialID=WHO000002",
			identifiers={"who": "WHO000002"},
			countries_by_source={"ctgov": "France"},
		)
		trial_data = {
			"identifiers": {"who": "WHO000002"},
			"title": TITLE,
			"link": "https://trialsearch.who.int/Trial2.aspx?TrialID=WHO000002",
		}
		WHOCommand().update_existing_trial(trial, trial_data, self.source, self.subject)
		trial.refresh_from_db()
		self.assertEqual(trial.countries_by_source, {"ctgov": "France"})


class CrossSourceCountriesMergeTest(TestCase):
	"""A trial cross-registered in ClinicalTrials.gov and WHO ICTRP ends up with both
	sources' raw country data preserved side by side, and the normalized country/region
	layers reflect the union of both."""

	def setUp(self):
		self.org = Organization.objects.create(name="Country Test Org Cross")
		self.ctgov_source = _make_source(self.org, method="ctgov_api", name="CTGov API 2")
		self.who_source = _make_source(self.org, method="rss", name="WHO ICTRP 2")

	def test_ctgov_then_who_unions_countries_and_regions(self):
		cmd = CTGovCommand()
		cmd.verbosity = 0
		incoming = ClinicalTrial(
			title=TITLE,
			summary="s",
			link=CTGOV_LINK,
			published_date=None,
			identifiers={"nct": "NCT00000003"},
			extra_fields={"countries": "United States"},
		)
		trial = cmd.create_new_trial(incoming, self.ctgov_source)

		trial_data = {
			"identifiers": {"nct": "NCT00000003"},
			"title": TITLE,
			"link": CTGOV_LINK,
			"countries": "Japan",
			"countries_by_source": {"ictrp": "Japan"},
		}
		WHOCommand().update_existing_trial(
			trial, trial_data, self.who_source, self.who_source.subject
		)

		trial.refresh_from_db()
		self.assertEqual(
			trial.countries_by_source, {"ctgov": "United States", "ictrp": "Japan"}
		)
		codes = sorted(tc.country.code for tc in trial.trial_countries.all())
		self.assertEqual(codes, ["JP", "US"])
		self.assertEqual(
			sorted(trial.regions_normalized),
			sorted([TrialRegion.ASIA, TrialRegion.NORTH_AMERICA]),
		)
