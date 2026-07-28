"""Regression tests for Articles.is_ml_relevant_any_subject subject scoping.

Without a `subjects` argument, is_ml_relevant_any_subject checks every
auto_predict subject the article belongs to, regardless of which team owns
it. A digest list must only be able to credit an article as ML-relevant on
the strength of predictions for its *own* subjects -- an unrelated team's
auto_predict subject should not leak relevance into a list that has nothing
to do with it.
"""

from django.test import TestCase
from organizations.models import Organization

from gregory.models import Articles, MLPredictions, Subject, Team


class IsMlRelevantAnySubjectScopingTestCase(TestCase):
	def setUp(self):
		org = Organization.objects.create(name="Scoping Test Org")
		self.team_a = Team.objects.create(
			organization=org, name="Team A", slug="scoping-team-a"
		)
		self.team_b = Team.objects.create(
			organization=org, name="Team B", slug="scoping-team-b"
		)
		self.subject_a = Subject.objects.create(
			subject_name="Subject A",
			subject_slug="scoping-subject-a",
			team=self.team_a,
			auto_predict=True,
			ml_consensus_type="any",
		)
		self.subject_b = Subject.objects.create(
			subject_name="Subject B",
			subject_slug="scoping-subject-b",
			team=self.team_b,
			auto_predict=True,
			ml_consensus_type="any",
		)

	def test_unrelated_subject_prediction_excluded_when_scoped(self):
		"""Article belongs to both subject_a and subject_b. Only subject_b has
		a high-confidence ML prediction. A caller scoped to subject_a's own
		list must not see this article as ML-relevant."""
		article = Articles.objects.create(
			title="Cross-team leak", link="https://example.com/scoping-1"
		)
		article.subjects.add(self.subject_a, self.subject_b)
		MLPredictions.objects.create(
			article=article,
			subject=self.subject_b,
			algorithm="pubmed_bert",
			model_version="v1",
			probability_score=0.95,
			predicted_relevant=True,
		)

		# Scoped to subject_a only (e.g. a digest list owned by team A):
		# subject_b's prediction must not leak in.
		self.assertFalse(
			article.is_ml_relevant_any_subject(threshold=0.8, subjects=[self.subject_a])
		)

		# Scoped to subject_b: the prediction is in scope, so it counts.
		self.assertTrue(
			article.is_ml_relevant_any_subject(threshold=0.8, subjects=[self.subject_b])
		)

		# Unscoped (default/back-compat behaviour): checks every auto_predict
		# subject the article belongs to, so it's still relevant.
		self.assertTrue(article.is_ml_relevant_any_subject(threshold=0.8))

	def test_scoping_accepts_queryset(self):
		"""subjects may be passed as a queryset (e.g. digest_list.subjects.all()),
		not just a list of instances."""
		article = Articles.objects.create(
			title="Queryset scoping", link="https://example.com/scoping-2"
		)
		article.subjects.add(self.subject_a, self.subject_b)
		MLPredictions.objects.create(
			article=article,
			subject=self.subject_b,
			algorithm="pubmed_bert",
			model_version="v1",
			probability_score=0.95,
			predicted_relevant=True,
		)

		team_a_subjects = Subject.objects.filter(team=self.team_a)
		self.assertFalse(
			article.is_ml_relevant_any_subject(threshold=0.8, subjects=team_a_subjects)
		)

		team_b_subjects = Subject.objects.filter(team=self.team_b)
		self.assertTrue(
			article.is_ml_relevant_any_subject(threshold=0.8, subjects=team_b_subjects)
		)
