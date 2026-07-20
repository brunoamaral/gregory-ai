"""Tests for the API surface of the intervention-modality field on TeamCategory:
the ``modality`` field on the nested category serializer, the ``category_modality``
filter on /trials/ and /articles/, and the ``by_modality`` facet on
/trials/stats/. See CATEGORY-MODALITY-PLAN.md.

Trials with no team are invisible to OrgVisibilityMixin, so every trial here is
attached to a public-org team — same fixture shape as
api/tests/test_sponsor_api_surface.py.

Run with:
    docker exec gregory python manage.py test api.tests.test_category_modality_api
"""

from django.test import TestCase
from organizations.models import Organization
from rest_framework.test import APIClient

from gregory.models import (
	Articles,
	CategoryModality,
	OrganizationApiSettings,
	Subject,
	Team,
	TeamCategory,
	Trials,
)


class CategoryModalityAPITestCase(TestCase):
	def setUp(self):
		self.client = APIClient()
		org = Organization.objects.create(
			name="Modality API Org", slug="modality-api-org"
		)
		OrganizationApiSettings.objects.filter(organization=org).update(
			make_api_public=True
		)
		self.team = Team.objects.create(
			organization=org, name="Modality API Org", slug="modality-api-org"
		)
		self.subject = Subject.objects.create(
			subject_name="Modality Subject", subject_slug="modality-subject", team=self.team
		)

	def _make_category(self, name, slug, modality=None):
		category = TeamCategory.objects.create(
			team=self.team, category_name=name, category_slug=slug, modality=modality
		)
		category.subjects.add(self.subject)
		return category

	def _make_trial(self, title, link, categories=(), subjects=()):
		trial = Trials.objects.create(title=title, link=link)
		trial.teams.add(self.team)
		for category in categories:
			trial.team_categories.add(category)
		for subject in subjects:
			trial.subjects.add(subject)
		return trial


class CategorySerializerModalityTests(CategoryModalityAPITestCase):
	def test_modality_present_on_trial_category_payload(self):
		category = self._make_category(
			"Antibody Category", "antibody-category", modality="biologic_antibody"
		)
		trial = self._make_trial(
			"Modality Serializer Trial",
			"https://example.com/modality-ser-1",
			categories=[category],
		)
		resp = self.client.get(f"/trials/{trial.trial_id}/")
		self.assertEqual(resp.status_code, 200)
		payload_category = resp.data["team_categories"][0]
		self.assertEqual(payload_category["modality"], "biologic_antibody")

	def test_modality_null_safe_on_uncurated_category(self):
		category = self._make_category("Uncurated Category", "uncurated-category")
		trial = self._make_trial(
			"Modality Null Trial",
			"https://example.com/modality-ser-2",
			categories=[category],
		)
		resp = self.client.get(f"/trials/{trial.trial_id}/")
		self.assertEqual(resp.status_code, 200)
		self.assertIsNone(resp.data["team_categories"][0]["modality"])

	def test_modality_present_on_article_category_payload(self):
		category = self._make_category(
			"Article Modality Category", "article-modality-category", modality="small_molecule"
		)
		article = Articles.objects.create(
			title="Modality Article", link="https://example.com/modality-article-1"
		)
		article.teams.add(self.team)
		article.team_categories.add(category)
		resp = self.client.get(f"/articles/{article.article_id}/")
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(
			resp.data["team_categories"][0]["modality"], "small_molecule"
		)


class CategoryModalityFilterTests(CategoryModalityAPITestCase):
	def setUp(self):
		super().setUp()
		self.antibody_category = self._make_category(
			"Antibody Filter Category", "antibody-filter-category", modality="biologic_antibody"
		)
		self.antibody_category_2 = self._make_category(
			"Second Antibody Filter Category",
			"antibody-filter-category-2",
			modality="biologic_antibody",
		)
		self.small_molecule_category = self._make_category(
			"Small Molecule Filter Category",
			"small-molecule-filter-category",
			modality="small_molecule",
		)
		self.other_subject = Subject.objects.create(
			subject_name="Other Subject", subject_slug="other-subject", team=self.team
		)

	def test_trial_filter_returns_only_matching_modality(self):
		matching = self._make_trial(
			"Antibody Trial",
			"https://example.com/modality-filter-1",
			categories=[self.antibody_category],
		)
		non_matching = self._make_trial(
			"Small Molecule Trial",
			"https://example.com/modality-filter-2",
			categories=[self.small_molecule_category],
		)
		resp = self.client.get("/trials/", {"category_modality": "biologic_antibody"})
		self.assertEqual(resp.status_code, 200)
		ids = [row["trial_id"] for row in resp.data["results"]]
		self.assertIn(matching.trial_id, ids)
		self.assertNotIn(non_matching.trial_id, ids)

	def test_trial_with_two_same_modality_categories_appears_once(self):
		trial = self._make_trial(
			"Double Antibody Trial",
			"https://example.com/modality-filter-3",
			categories=[self.antibody_category, self.antibody_category_2],
		)
		resp = self.client.get("/trials/", {"category_modality": "biologic_antibody"})
		self.assertEqual(resp.status_code, 200)
		ids = [row["trial_id"] for row in resp.data["results"]]
		self.assertEqual(ids.count(trial.trial_id), 1)

	def test_trial_filter_composes_with_subject_id(self):
		matching = self._make_trial(
			"Composed Trial",
			"https://example.com/modality-filter-4",
			categories=[self.antibody_category],
			subjects=[self.subject],
		)
		wrong_subject = self._make_trial(
			"Wrong Subject Trial",
			"https://example.com/modality-filter-5",
			categories=[self.antibody_category],
			subjects=[self.other_subject],
		)
		resp = self.client.get(
			"/trials/",
			{"category_modality": "biologic_antibody", "subject_id": self.subject.pk},
		)
		self.assertEqual(resp.status_code, 200)
		ids = [row["trial_id"] for row in resp.data["results"]]
		self.assertIn(matching.trial_id, ids)
		self.assertNotIn(wrong_subject.trial_id, ids)

	def test_article_filter_returns_only_matching_modality(self):
		matching = Articles.objects.create(
			title="Antibody Article", link="https://example.com/modality-article-filter-1"
		)
		matching.teams.add(self.team)
		matching.team_categories.add(self.antibody_category)
		non_matching = Articles.objects.create(
			title="Small Molecule Article",
			link="https://example.com/modality-article-filter-2",
		)
		non_matching.teams.add(self.team)
		non_matching.team_categories.add(self.small_molecule_category)

		resp = self.client.get("/articles/", {"category_modality": "biologic_antibody"})
		self.assertEqual(resp.status_code, 200)
		ids = [row["article_id"] for row in resp.data["results"]]
		self.assertIn(matching.article_id, ids)
		self.assertNotIn(non_matching.article_id, ids)


class CategoryModalityFacetTests(CategoryModalityAPITestCase):
	def test_all_enum_keys_present_with_zero_when_empty(self):
		resp = self.client.get("/trials/stats/")
		self.assertEqual(resp.status_code, 200)
		by_modality = resp.data["by_modality"]
		self.assertEqual(
			set(by_modality.keys()), set(CategoryModality.values) | {"no_modality"}
		)
		for value in CategoryModality.values:
			self.assertEqual(by_modality[value], 0)

	def test_no_modality_counts_category_less_trials(self):
		self._make_trial("No Category Trial", "https://example.com/modality-facet-1")
		resp = self.client.get("/trials/stats/")
		self.assertEqual(resp.status_code, 200)
		self.assertGreaterEqual(resp.data["by_modality"]["no_modality"], 1)

	def test_multi_modality_trial_counted_in_both_buckets(self):
		antibody_category = self._make_category(
			"Facet Antibody Category", "facet-antibody-category", modality="biologic_antibody"
		)
		small_molecule_category = self._make_category(
			"Facet Small Molecule Category",
			"facet-small-molecule-category",
			modality="small_molecule",
		)
		# Intentional: a trial carrying categories of two different modalities is
		# counted once per modality bucket in by_modality — this is documented
		# behavior (the facet does not partition `total`), not a bug.
		self._make_trial(
			"Dual Modality Trial",
			"https://example.com/modality-facet-2",
			categories=[antibody_category, small_molecule_category],
		)
		resp = self.client.get("/trials/stats/")
		self.assertEqual(resp.status_code, 200)
		by_modality = resp.data["by_modality"]
		self.assertGreaterEqual(by_modality["biologic_antibody"], 1)
		self.assertGreaterEqual(by_modality["small_molecule"], 1)

	def test_facet_respects_filters(self):
		antibody_category = self._make_category(
			"Scoped Antibody Category", "scoped-antibody-category", modality="biologic_antibody"
		)
		other_subject = Subject.objects.create(
			subject_name="Scoped Other Subject",
			subject_slug="scoped-other-subject",
			team=self.team,
		)
		self._make_trial(
			"Scoped In Trial",
			"https://example.com/modality-facet-3",
			categories=[antibody_category],
			subjects=[self.subject],
		)
		self._make_trial(
			"Scoped Out Trial",
			"https://example.com/modality-facet-4",
			categories=[antibody_category],
			subjects=[other_subject],
		)
		resp = self.client.get("/trials/stats/", {"subject_id": self.subject.pk})
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp.data["by_modality"]["biologic_antibody"], 1)
