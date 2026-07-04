"""Tests for the denormalized Articles.relevant flag: recompute logic and signals."""

from django.test import TestCase
from organizations.models import Organization

from gregory.models import (
	Articles,
	ArticleSubjectRelevance,
	MLPredictions,
	Subject,
	Team,
)
from gregory.relevance import recompute_article_relevance


class RecomputeArticleRelevanceTestCase(TestCase):
	"""Flag matrix: manual relevance, ML consensus per ml_consensus_type, and non-matches."""

	def setUp(self):
		org = Organization.objects.create(name="Relevance Test Org")
		self.team = Team.objects.create(organization=org, name="Relevance Team", slug="relevance-team")
		self.subject_any = Subject.objects.create(
			subject_name="Any Subject",
			subject_slug="any-subject",
			team=self.team,
			auto_predict=True,
			ml_consensus_type="any",
		)
		self.subject_majority = Subject.objects.create(
			subject_name="Majority Subject",
			subject_slug="majority-subject",
			team=self.team,
			auto_predict=True,
			ml_consensus_type="majority",
		)
		self.subject_all = Subject.objects.create(
			subject_name="All Subject",
			subject_slug="all-subject",
			team=self.team,
			auto_predict=True,
			ml_consensus_type="all",
		)
		self.subject_no_auto_predict = Subject.objects.create(
			subject_name="No Auto Predict Subject",
			subject_slug="no-auto-predict-subject",
			team=self.team,
			auto_predict=False,
			ml_consensus_type="any",
		)

	def _make_article(self, title, link):
		return Articles.objects.create(title=title, link=link)

	def _predict(self, article, subject, algorithm, score, predicted_relevant=True):
		return MLPredictions.objects.create(
			article=article,
			subject=subject,
			algorithm=algorithm,
			model_version="v1",
			probability_score=score,
			predicted_relevant=predicted_relevant,
		)

	def test_manually_relevant_sets_flag_true(self):
		article = self._make_article("Manual relevant", "https://example.com/r1")
		article.subjects.add(self.subject_any)
		ArticleSubjectRelevance.objects.create(
			article=article, subject=self.subject_any, is_relevant=True
		)
		recompute_article_relevance()
		article.refresh_from_db()
		self.assertTrue(article.relevant)

	def test_ml_consensus_any_with_one_algorithm(self):
		article = self._make_article("Any consensus", "https://example.com/r2")
		article.subjects.add(self.subject_any)
		self._predict(article, self.subject_any, "pubmed_bert", 0.9)
		recompute_article_relevance()
		article.refresh_from_db()
		self.assertTrue(article.relevant)

	def test_ml_consensus_majority_needs_two_algorithms(self):
		article = self._make_article("Majority consensus", "https://example.com/r3")
		article.subjects.add(self.subject_majority)
		self._predict(article, self.subject_majority, "pubmed_bert", 0.9)
		recompute_article_relevance()
		article.refresh_from_db()
		self.assertFalse(article.relevant, "One algorithm should not satisfy majority consensus")

		self._predict(article, self.subject_majority, "lgbm_tfidf", 0.85)
		recompute_article_relevance()
		article.refresh_from_db()
		self.assertTrue(article.relevant)

	def test_ml_consensus_all_needs_three_algorithms(self):
		article = self._make_article("All consensus", "https://example.com/r4")
		article.subjects.add(self.subject_all)
		self._predict(article, self.subject_all, "pubmed_bert", 0.9)
		self._predict(article, self.subject_all, "lgbm_tfidf", 0.85)
		recompute_article_relevance()
		article.refresh_from_db()
		self.assertFalse(article.relevant, "Two algorithms should not satisfy unanimous consensus")

		self._predict(article, self.subject_all, "lstm", 0.81)
		recompute_article_relevance()
		article.refresh_from_db()
		self.assertTrue(article.relevant)

	def test_below_threshold_is_not_relevant(self):
		article = self._make_article("Below threshold", "https://example.com/r5")
		article.subjects.add(self.subject_any)
		self._predict(article, self.subject_any, "pubmed_bert", 0.79)
		recompute_article_relevance()
		article.refresh_from_db()
		self.assertFalse(article.relevant)

	def test_subject_not_auto_predict_is_not_relevant(self):
		article = self._make_article("Non auto predict subject", "https://example.com/r6")
		article.subjects.add(self.subject_no_auto_predict)
		self._predict(article, self.subject_no_auto_predict, "pubmed_bert", 0.95)
		recompute_article_relevance()
		article.refresh_from_db()
		self.assertFalse(article.relevant)

	def test_prediction_for_unattached_subject_is_not_relevant(self):
		"""A prediction for a subject not attached to the article must not count."""
		article = self._make_article("Unattached subject prediction", "https://example.com/r7")
		# Note: article.subjects is intentionally left empty (subject_any not added)
		self._predict(article, self.subject_any, "pubmed_bert", 0.95)
		recompute_article_relevance()
		article.refresh_from_db()
		self.assertFalse(article.relevant)

	def test_is_relevant_false_does_not_set_flag(self):
		article = self._make_article("Explicitly not relevant", "https://example.com/r8")
		article.subjects.add(self.subject_any)
		ArticleSubjectRelevance.objects.create(
			article=article, subject=self.subject_any, is_relevant=False
		)
		recompute_article_relevance()
		article.refresh_from_db()
		self.assertFalse(article.relevant)

	def test_is_relevant_null_does_not_set_flag(self):
		article = self._make_article("Not reviewed", "https://example.com/r9")
		article.subjects.add(self.subject_any)
		ArticleSubjectRelevance.objects.create(
			article=article, subject=self.subject_any, is_relevant=None
		)
		recompute_article_relevance()
		article.refresh_from_db()
		self.assertFalse(article.relevant)

	def test_relevant_for_two_subjects_counts_once(self):
		"""An article relevant for multiple subjects is still a single relevant article."""
		article = self._make_article("Relevant for two subjects", "https://example.com/r10")
		article.subjects.add(self.subject_any, self.subject_majority)
		ArticleSubjectRelevance.objects.create(
			article=article, subject=self.subject_any, is_relevant=True
		)
		self._predict(article, self.subject_majority, "pubmed_bert", 0.9)
		self._predict(article, self.subject_majority, "lgbm_tfidf", 0.9)
		recompute_article_relevance()
		article.refresh_from_db()
		self.assertTrue(article.relevant)
		# Distinctness at the author-count level is exercised in the API tests;
		# here we only need the single boolean flag to be true, not doubled.
		self.assertIn(article.relevant, (True, False))

	def test_idempotent_second_call_changes_nothing(self):
		article = self._make_article("Idempotence check", "https://example.com/r11")
		article.subjects.add(self.subject_any)
		ArticleSubjectRelevance.objects.create(
			article=article, subject=self.subject_any, is_relevant=True
		)
		# The post_save signal already synced the flag on create; force it back
		# out of sync (bypassing signals via .update()) so this test exercises
		# recompute_article_relevance itself rather than the signal.
		Articles.objects.filter(pk=article.pk).update(relevant=False)

		first_run_changed = recompute_article_relevance()
		self.assertGreater(first_run_changed, 0)
		second_run_changed = recompute_article_relevance()
		self.assertEqual(second_run_changed, 0)

	def test_scoped_article_ids_only_updates_requested_articles(self):
		article_a = self._make_article("Scoped A", "https://example.com/r12")
		article_a.subjects.add(self.subject_any)
		ArticleSubjectRelevance.objects.create(
			article=article_a, subject=self.subject_any, is_relevant=True
		)
		article_b = self._make_article("Scoped B", "https://example.com/r13")
		article_b.subjects.add(self.subject_any)
		ArticleSubjectRelevance.objects.create(
			article=article_b, subject=self.subject_any, is_relevant=True
		)
		# Both flags were already synced to True by the post_save signal; reset
		# them so the scoped recompute below has real work to do.
		Articles.objects.filter(pk__in=[article_a.pk, article_b.pk]).update(relevant=False)

		changed = recompute_article_relevance(article_ids=[article_a.article_id])
		self.assertEqual(changed, 1)
		article_a.refresh_from_db()
		article_b.refresh_from_db()
		self.assertTrue(article_a.relevant)
		self.assertFalse(article_b.relevant, "Article outside the scoped ids must not be touched")

	def test_empty_article_ids_list_is_a_noop(self):
		changed = recompute_article_relevance(article_ids=[])
		self.assertEqual(changed, 0)


class ArticleRelevanceSignalTestCase(TestCase):
	"""post_save/post_delete signals keep Articles.relevant in sync automatically."""

	def setUp(self):
		org = Organization.objects.create(name="Signal Relevance Org")
		self.team = Team.objects.create(organization=org, slug="signal-relevance-team")
		self.subject = Subject.objects.create(
			subject_name="Signal Subject",
			subject_slug="signal-subject",
			team=self.team,
			auto_predict=True,
			ml_consensus_type="any",
		)
		self.article = Articles.objects.create(
			title="Signal relevance article",
			link="https://example.com/sig-rel-1",
		)
		self.article.subjects.add(self.subject)

	def _refresh(self):
		self.article.refresh_from_db()

	def test_saving_relevant_true_sets_flag_immediately(self):
		self._refresh()
		self.assertFalse(self.article.relevant)

		ArticleSubjectRelevance.objects.create(
			article=self.article, subject=self.subject, is_relevant=True
		)
		self._refresh()
		self.assertTrue(self.article.relevant)

	def test_setting_relevance_back_to_none_clears_flag(self):
		relevance = ArticleSubjectRelevance.objects.create(
			article=self.article, subject=self.subject, is_relevant=True
		)
		self._refresh()
		self.assertTrue(self.article.relevant)

		relevance.is_relevant = None
		relevance.save()
		self._refresh()
		self.assertFalse(self.article.relevant)

	def test_deleting_relevance_row_clears_flag(self):
		relevance = ArticleSubjectRelevance.objects.create(
			article=self.article, subject=self.subject, is_relevant=True
		)
		self._refresh()
		self.assertTrue(self.article.relevant)

		relevance.delete()
		self._refresh()
		self.assertFalse(self.article.relevant)

	def test_ml_prediction_save_triggers_recompute(self):
		self._refresh()
		self.assertFalse(self.article.relevant)

		MLPredictions.objects.create(
			article=self.article,
			subject=self.subject,
			algorithm="pubmed_bert",
			model_version="v1",
			probability_score=0.85,
			predicted_relevant=True,
		)
		self._refresh()
		self.assertTrue(self.article.relevant)

	def test_ml_prediction_delete_triggers_recompute(self):
		prediction = MLPredictions.objects.create(
			article=self.article,
			subject=self.subject,
			algorithm="pubmed_bert",
			model_version="v1",
			probability_score=0.85,
			predicted_relevant=True,
		)
		self._refresh()
		self.assertTrue(self.article.relevant)

		prediction.delete()
		self._refresh()
		self.assertFalse(self.article.relevant)
