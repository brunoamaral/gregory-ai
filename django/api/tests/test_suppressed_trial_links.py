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


class TrialLinkConfirmedReadsTest(TestCase):
	"""One entry per linked record, with `link_confirmed` and `link_type`."""

	def setUp(self):
		org = Organization.objects.create(name="Confirmed Org", slug="confirmed-org")
		team = Team.objects.create(name="Confirmed Team", slug="confirmed-team", organization=org)
		subject = Subject.objects.create(
			subject_name="Confirmed Subject", subject_slug="confirmed-subject", team=team
		)
		publish_subjects(subject, organization=org)
		self.article = Articles.objects.create(title="Article", link="https://a.test/c1")
		self.article.subjects.add(subject)
		self.trials = {}
		for key in ("pair", "manual", "ident", "acro"):
			trial = Trials.objects.create(title=f"Trial {key}", link=f"https://t.test/{key}")
			trial.subjects.add(subject)
			self.trials[key] = trial
		self.client = APIClient()

	def _ref(self, key, identifier_type, **extra):
		return ArticleTrialReference.objects.create(
			article=self.article,
			trial=self.trials[key],
			identifier_type=identifier_type,
			identifier_value=f"{identifier_type}-value",
			**extra,
		)

	def _entries(self):
		resp = self.client.get(f"/articles/{self.article.article_id}/")
		self.assertEqual(resp.status_code, 200)
		return {t["title"]: t for t in resp.data["clinical_trials"]}

	def test_a_pair_with_two_rows_is_listed_once(self):
		self._ref("pair", "nct")
		self._ref("pair", "eudract")
		entries = self.client.get(f"/articles/{self.article.article_id}/").data["clinical_trials"]
		self.assertEqual([t["title"] for t in entries], ["Trial pair"])

	def test_the_trial_side_lists_an_article_once_too(self):
		self._ref("pair", "nct")
		self._ref("pair", "eudract")
		resp = self.client.get(f"/trials/{self.trials['pair'].trial_id}/")
		self.assertEqual([a["title"] for a in resp.data["articles"]], ["Article"])

	def test_link_type_and_confirmed(self):
		self._ref("pair", "nct")
		self._ref("pair", "eudract", confirmed=True)
		self._ref("manual", "manual", source="manual", confirmed=True)
		self._ref("ident", "nct")
		self._ref("acro", "acronym")

		entries = self._entries()

		self.assertEqual(
			{k: (v["link_type"], v["link_confirmed"]) for k, v in entries.items()},
			{
				"Trial pair": ("identifier", True),
				"Trial manual": ("manual", True),
				"Trial ident": ("identifier", False),
				"Trial acro": ("acronym", False),
			},
		)

	def test_manual_wins_over_identifier_and_identifier_over_acronym(self):
		self._ref("pair", "nct")
		self._ref("pair", "manual", source="manual", confirmed=True)
		self._ref("acro", "acronym")
		self._ref("acro", "nct")
		entries = self._entries()
		self.assertEqual(entries["Trial pair"]["link_type"], "manual")
		self.assertEqual(entries["Trial acro"]["link_type"], "identifier")

	def test_trial_side_carries_the_fields(self):
		self._ref("acro", "acronym", confirmed=True)
		resp = self.client.get(f"/trials/{self.trials['acro'].trial_id}/")
		entry = resp.data["articles"][0]
		self.assertEqual((entry["link_type"], entry["link_confirmed"]), ("acronym", True))

	def test_a_hidden_row_does_not_count_towards_the_pair(self):
		self._ref("pair", "nct", suppressed=True, confirmed=True)
		self._ref("pair", "eudract")
		entry = self._entries()["Trial pair"]
		self.assertEqual((entry["link_type"], entry["link_confirmed"]), ("identifier", False))

	def test_list_query_count_does_not_grow_with_links(self):
		from django.db import connection
		from django.test.utils import CaptureQueriesContext

		self._ref("pair", "nct")
		self.client.get("/articles/")  # warm the process-level caches
		with CaptureQueriesContext(connection) as before:
			self.client.get("/articles/")
		self._ref("pair", "eudract")
		self._ref("ident", "nct")
		self._ref("acro", "acronym")
		with CaptureQueriesContext(connection) as after:
			self.client.get("/articles/")
		self.assertEqual(len(before), len(after))
