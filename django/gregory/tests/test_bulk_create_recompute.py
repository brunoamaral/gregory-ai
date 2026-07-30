"""Tests for the pipeline's bulk_create path: MLPredictions.objects.bulk_create
bypasses post_save, so ml_score and relevant only update when the caller
explicitly recomputes. This is the failure mode predict_articles hit in
production — see docs/ml-prediction-signal-bypass-plan.md."""

from django.test import TestCase
from organizations.models import Organization

from gregory.models import Articles, MLPredictions, Subject, Team
from gregory.relevance import recompute_article_ml_scores, recompute_article_relevance
from gregory.signals import _recompute_article_ml_score


class BulkCreateDoesNotTriggerSignalsTestCase(TestCase):
	"""Documents the bug: bulk_create leaves both denormalized fields stale
	until something explicitly recomputes them."""

	@classmethod
	def setUpTestData(cls):
		org = Organization.objects.create(name="Bulk Create Org")
		cls.team = Team.objects.create(organization=org, slug="bulk-create-team")
		cls.subject = Subject.objects.create(
			subject_name="Bulk Subject",
			subject_slug="bulk-subject",
			team=cls.team,
			auto_predict=True,
			ml_consensus_type="any",
		)
		cls.article = Articles.objects.create(
			title="Bulk create article",
			link="https://example.com/bulk1",
		)
		cls.article.subjects.add(cls.subject)

	def test_bulk_create_alone_leaves_fields_stale(self):
		MLPredictions.objects.bulk_create(
			[
				MLPredictions(
					article=self.article,
					subject=self.subject,
					algorithm="pubmed_bert",
					model_version="v1",
					probability_score=0.9,
					predicted_relevant=True,
				)
			]
		)
		self.article.refresh_from_db()
		self.assertIsNone(self.article.ml_score, "bulk_create must not fire post_save")
		self.assertFalse(self.article.relevant, "bulk_create must not fire post_save")

	def test_recompute_after_bulk_create_updates_both_fields(self):
		"""The fix: an explicit recompute call after bulk_create brings both
		denormalized fields in line with what the signal would have produced."""
		MLPredictions.objects.bulk_create(
			[
				MLPredictions(
					article=self.article,
					subject=self.subject,
					algorithm="pubmed_bert",
					model_version="v1",
					probability_score=0.9,
					predicted_relevant=True,
				)
			]
		)
		recompute_article_ml_scores(article_ids=[self.article.article_id])
		recompute_article_relevance(article_ids=[self.article.article_id])

		self.article.refresh_from_db()
		self.assertAlmostEqual(self.article.ml_score, 0.9, places=5)
		self.assertTrue(self.article.relevant)

	def test_relevant_clears_when_bulk_created_latest_drops_below_threshold(self):
		"""A retrained model_version's bulk-created prediction can move the flag
		in either direction, not just set it."""
		MLPredictions.objects.bulk_create(
			[
				MLPredictions(
					article=self.article,
					subject=self.subject,
					algorithm="pubmed_bert",
					model_version="v1",
					probability_score=0.9,
					predicted_relevant=True,
				)
			]
		)
		recompute_article_ml_scores(article_ids=[self.article.article_id])
		recompute_article_relevance(article_ids=[self.article.article_id])
		self.article.refresh_from_db()
		self.assertTrue(self.article.relevant)

		MLPredictions.objects.bulk_create(
			[
				MLPredictions(
					article=self.article,
					subject=self.subject,
					algorithm="pubmed_bert",
					model_version="v2",
					probability_score=0.2,
					predicted_relevant=False,
				)
			]
		)
		recompute_article_ml_scores(article_ids=[self.article.article_id])
		recompute_article_relevance(article_ids=[self.article.article_id])
		self.article.refresh_from_db()
		self.assertAlmostEqual(self.article.ml_score, 0.2, places=5)
		self.assertFalse(
			self.article.relevant,
			"a superseded high score must not keep the flag set after recompute",
		)


class RecomputeArticleMlScoresScopingTestCase(TestCase):
	"""Scoped vs. unscoped recompute_article_ml_scores must agree, and the
	single-article signal path must not drift from the bulk path."""

	@classmethod
	def setUpTestData(cls):
		org = Organization.objects.create(name="Scoping Org")
		cls.team = Team.objects.create(organization=org, slug="scoping-team")
		cls.subject = Subject.objects.create(
			subject_name="Scoping Subject",
			subject_slug="scoping-subject",
			team=cls.team,
		)
		cls.article_a = Articles.objects.create(
			title="Scoped ml_score A", link="https://example.com/scoped-a"
		)
		cls.article_b = Articles.objects.create(
			title="Scoped ml_score B", link="https://example.com/scoped-b"
		)
		MLPredictions.objects.bulk_create(
			[
				MLPredictions(
					article=cls.article_a,
					subject=cls.subject,
					algorithm="pubmed_bert",
					model_version="v1",
					probability_score=0.7,
					predicted_relevant=False,
				),
				MLPredictions(
					article=cls.article_b,
					subject=cls.subject,
					algorithm="pubmed_bert",
					model_version="v1",
					probability_score=0.4,
					predicted_relevant=False,
				),
			]
		)

	def test_scoped_recompute_only_touches_requested_articles(self):
		changed = recompute_article_ml_scores(article_ids=[self.article_a.article_id])
		self.assertEqual(changed, 1)
		self.article_a.refresh_from_db()
		self.article_b.refresh_from_db()
		self.assertAlmostEqual(self.article_a.ml_score, 0.7, places=5)
		self.assertIsNone(
			self.article_b.ml_score, "unscoped article must not be touched"
		)

	def test_scoped_and_unscoped_agree_for_same_article_set(self):
		recompute_article_ml_scores(
			article_ids=[self.article_a.article_id, self.article_b.article_id]
		)
		self.article_a.refresh_from_db()
		self.article_b.refresh_from_db()
		scoped_a, scoped_b = self.article_a.ml_score, self.article_b.ml_score

		Articles.objects.filter(
			article_id__in=[self.article_a.article_id, self.article_b.article_id]
		).update(ml_score=None)

		recompute_article_ml_scores()
		self.article_a.refresh_from_db()
		self.article_b.refresh_from_db()
		self.assertAlmostEqual(self.article_a.ml_score, scoped_a, places=5)
		self.assertAlmostEqual(self.article_b.ml_score, scoped_b, places=5)

	def test_empty_article_ids_list_is_a_noop(self):
		changed = recompute_article_ml_scores(article_ids=[])
		self.assertEqual(changed, 0)

	def test_signal_path_matches_bulk_path(self):
		"""_recompute_article_ml_score (used by the post_save signal) must
		produce the same result as calling recompute_article_ml_scores directly,
		so the per-article and batch paths can never drift apart."""
		_recompute_article_ml_score(self.article_a.article_id)
		self.article_a.refresh_from_db()
		signal_score = self.article_a.ml_score

		Articles.objects.filter(article_id=self.article_a.article_id).update(
			ml_score=None
		)
		recompute_article_ml_scores(article_ids=[self.article_a.article_id])
		self.article_a.refresh_from_db()
		self.assertAlmostEqual(self.article_a.ml_score, signal_score, places=5)
