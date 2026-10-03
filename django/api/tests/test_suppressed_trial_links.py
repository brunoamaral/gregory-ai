"""
A suppressed ArticleTrialReference is a link an editor removed (D6/D10): it is
hidden from every read, while the row stays so detect_trial_references doesn't
recreate it.
"""

from django.test import TestCase
from organizations.models import Organization
from rest_framework.test import APIClient

from api.tests.visibility_helpers import publish_subjects
from gregory.models import (
	ArticleTrialReference,
	Articles,
	Subject,
	Team,
	Trials,
)


class SuppressedTrialLinkReadsTest(TestCase):
	def setUp(self):
		org = Organization.objects.create(name="Suppressed Org", slug="suppressed-org")
		team = Team.objects.create(name="Suppressed Team", slug="suppressed-team", organization=org)
		subject = Subject.objects.create(
			subject_name="Suppressed Subject", subject_slug="suppressed-subject", team=team
		)
		publish_subjects(subject, organization=org)

		self.trial = Trials.objects.create(title="Visible trial", link="https://t.test/v")
		self.hidden_trial = Trials.objects.create(title="Hidden trial", link="https://t.test/h")
		for trial in (self.trial, self.hidden_trial):
			trial.subjects.add(subject)
			trial.teams.add(team)

		self.article = Articles.objects.create(title="Linked article", link="https://a.test/1")
		self.unlinked = Articles.objects.create(title="Only suppressed", link="https://a.test/2")
		for article in (self.article, self.unlinked):
			article.subjects.add(subject)
			article.teams.add(team)

		ArticleTrialReference.objects.create(
			article=self.article,
			trial=self.trial,
			identifier_type="nct_id",
			identifier_value="NCT00000001",
		)
		ArticleTrialReference.objects.create(
			article=self.article,
			trial=self.hidden_trial,
			identifier_type="nct_id",
			identifier_value="NCT00000002",
			suppressed=True,
		)
		ArticleTrialReference.objects.create(
			article=self.unlinked,
			trial=self.hidden_trial,
			identifier_type="nct_id",
			identifier_value="NCT00000002",
			suppressed=True,
		)
		self.client = APIClient()

	@staticmethod
	def _by_title(resp, title):
		return next(r for r in resp.data["results"] if r["title"] == title)

	def test_article_list_omits_suppressed_trials(self):
		resp = self.client.get("/articles/")
		row = self._by_title(resp, "Linked article")
		self.assertEqual([t["title"] for t in row["clinical_trials"]], ["Visible trial"])
		self.assertEqual(self._by_title(resp, "Only suppressed")["clinical_trials"], [])

	def test_article_detail_omits_suppressed_trials(self):
		resp = self.client.get(f"/articles/{self.article.article_id}/")
		self.assertEqual([t["title"] for t in resp.data["clinical_trials"]], ["Visible trial"])

	def test_trial_detail_omits_suppressed_articles(self):
		resp = self.client.get(f"/trials/{self.hidden_trial.trial_id}/")
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp.data["articles"], [])
		resp = self.client.get(f"/trials/{self.trial.trial_id}/")
		self.assertEqual([a["title"] for a in resp.data["articles"]], ["Linked article"])

	def test_has_clinical_trials_ignores_suppressed_links(self):
		linked = self.client.get("/articles/?has_clinical_trials=true")
		self.assertEqual([r["title"] for r in linked.data["results"]], ["Linked article"])
		unlinked = self.client.get("/articles/?has_clinical_trials=false")
		self.assertEqual([r["title"] for r in unlinked.data["results"]], ["Only suppressed"])
