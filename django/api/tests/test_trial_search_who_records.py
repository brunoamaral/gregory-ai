"""
End-to-end regression test for a real WHO ICTRP-sourced trial: its registry
name lives only in ``scientific_title`` (the ``title`` is the registry's lay
public title, and WHO-only trials rarely have a ``summary``), so it must be
findable through ``?search=`` once TrialFilter reads scientific_title too.

Imports django/gregory/tests/fixtures/ictrp/ISRCTN14048364.xml -- the OCTOPUS
trial (ISRCTN14048364, dev/prod trial 519) reconstructed from its database
record in the shape of a real ICTRP export -- through the real importWHOXML
command, then searches for it through the public API exactly as a caller
would.
"""

from pathlib import Path

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from api.tests.visibility_helpers import publish_subjects
from gregory.models import OrganizationApiSettings, Trials
from gregory.tests.test_who_importer import _run_import, _who_source

FIXTURE_PATH = (
	Path(__file__).resolve().parent.parent.parent
	/ "gregory"
	/ "tests"
	/ "fixtures"
	/ "ictrp"
	/ "ISRCTN14048364.xml"
)


class WHOImportedTrialSearchTests(TestCase):
	@classmethod
	def setUpTestData(cls):
		cls.source = _who_source()
		# Make the imported trial visible to an anonymous caller -- same
		# visibility setup TrialSearchViewTests uses in test_trial_search.py.
		# Both halves are required: make_api_public covers the org/team-level
		# check TrialSearchView.get_queryset runs against visible_org_ids,
		# and publish_subjects covers the subject-level check every content
		# endpoint runs against visible_subject_ids (gregory/visibility.py).
		OrganizationApiSettings.objects.filter(
			organization=cls.source.team.organization
		).update(make_api_public=True)
		publish_subjects(
			cls.source.subject, organization=cls.source.team.organization
		)
		_run_import(FIXTURE_PATH.read_text(encoding="utf-8"), cls.source.source_id)
		cls.trial = Trials.objects.get(identifiers__isrctn="ISRCTN14048364")
		cls.client = APIClient()

	def test_fixture_imports_with_scientific_title_and_no_summary(self):
		"""Sanity check on the import itself, isolating a fixture/importer
		regression from a search-filter regression if this ever fails."""
		self.assertEqual(
			self.trial.scientific_title,
			"OCTOPUS - Optimal Clinical Trials Platform for Progressive Multiple Sclerosis",
		)
		self.assertIn(
			"Testing and comparing multiple drugs at once", self.trial.title
		)
		self.assertNotIn("OCTOPUS", self.trial.title)
		self.assertFalse(self.trial.summary)
		self.assertIsNone(self.trial.acronym)

	def test_search_octopus_via_trials_list_endpoint(self):
		# GET /trials/ -- TrialViewSet, the main list/filter endpoint.
		response = self.client.get("/trials/", {"search": "OCTOPUS"})
		self.assertEqual(response.status_code, 200)
		ids = {r["trial_id"] for r in response.data["results"]}
		self.assertIn(self.trial.trial_id, ids)

	def test_search_octopus_via_trials_search_endpoint(self):
		# GET /trials/search/ -- TrialSearchView, the team/subject-scoped
		# search endpoint used by search_trials's MCP tool.
		response = self.client.get(
			reverse("trial-search"),
			{
				"team_id": self.source.team.id,
				"subject_id": self.source.subject.id,
				"search": "OCTOPUS",
			},
		)
		self.assertEqual(response.status_code, 200)
		titles = [t["title"] for t in response.data["results"]]
		self.assertIn(self.trial.title, titles)

	def test_search_full_scientific_title_phrase_matches(self):
		response = self.client.get(
			"/trials/", {"search": '"Optimal Clinical Trials Platform"'}
		)
		self.assertEqual(response.status_code, 200)
		ids = {r["trial_id"] for r in response.data["results"]}
		self.assertIn(self.trial.trial_id, ids)

	def test_title_only_search_still_misses_it(self):
		# title= stays single-field: OCTOPUS isn't in the public title.
		response = self.client.get("/trials/", {"title": "OCTOPUS"})
		self.assertEqual(response.status_code, 200)
		ids = {r["trial_id"] for r in response.data["results"]}
		self.assertNotIn(self.trial.trial_id, ids)
