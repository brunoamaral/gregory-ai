"""Tests for the ?ml_score_min= article filter, and how it differs from ?ml_threshold=.

``ml_score_min`` is a plain ``ml_score >= value`` on the stored column the
results already show. ``ml_threshold`` is the consensus rule behind
``relevant=true``: it only counts a prediction whose model classed the article
relevant (``predicted_relevant``), and the prediction pipeline sets that flag at
its cutoff (``predict_articles --prob-threshold``, 0.8 by default). So for
predictions made at the default, an ``ml_threshold`` below 0.8 behaves like
0.8, and a
subject whose consensus type is ``all`` drops an article as soon as one model
sits just under the cutoff -- even when the average score is high. The
regression tests here pin that documented difference so a change to either
filter shows up as a failing test rather than a surprise in a dossier.

Run with:
    docker exec gregory python manage.py test api.tests.test_ml_score_min_filter
"""

from django.core.cache import cache
from django.test import TestCase
from organizations.models import Organization
from rest_framework.test import APIClient

from api.tests.visibility_helpers import publish_subjects
from gregory.models import (
	Articles,
	MLPredictions,
	OrganizationApiSettings,
	Subject,
	Team,
)
from gregory.relevance import recompute_article_ml_scores

# The prediction pipeline's default cutoff for predicted_relevant
# (predict_articles.py DEFAULT_THRESHOLD). A run can override it with
# --prob-threshold; these fixtures model predictions made at the default.
PIPELINE_CUTOFF = 0.8


class MlScoreMinFilterTestCase(TestCase):
	def setUp(self):
		# /articles/stats/ answers from a DB cache that persists across tests.
		cache.clear()
		self.client = APIClient()

		org = Organization.objects.create(name="ML Score Min Org", slug="ml-score-min-org")
		OrganizationApiSettings.objects.filter(organization=org).update(
			make_api_public=True
		)
		self.team = Team.objects.create(
			organization=org, name="ML Score Min Team", slug="ml-score-min-team"
		)
		# Consensus "all" is what Multiple Sclerosis uses in production, and the
		# setting that makes ml_threshold and ml_score_min diverge the most.
		self.subject = Subject.objects.create(
			team=self.team,
			subject_name="Consensus All Subj",
			subject_slug="ml-score-min-all-subj",
			auto_predict=True,
			ml_consensus_type="all",
		)
		publish_subjects(self.subject, organization=org)

	def _article(self, title):
		article = Articles.objects.create(
			title=title,
			link=f"https://example.com/{title.lower().replace(' ', '-')}",
		)
		article.subjects.add(self.subject)
		article.teams.add(self.team)
		return article

	def _predict(self, article, algorithm, score):
		# predicted_relevant follows the pipeline's rule, not a free choice, so
		# the fixtures describe data the pipeline could really have written.
		return MLPredictions.objects.create(
			article=article,
			subject=self.subject,
			algorithm=algorithm,
			model_version="v1",
			probability_score=score,
			predicted_relevant=score >= PIPELINE_CUTOFF,
		)

	def _scored_article(self, title, score):
		"""An article whose single latest prediction (and so ml_score) is `score`."""
		article = self._article(title)
		self._predict(article, "pubmed_bert", score)
		# The post_save signal already does this; calling it keeps the fixture
		# honest if that signal ever moves to a batch job.
		recompute_article_ml_scores(article_ids=[article.article_id])
		return article

	def _three_models(self, title, scores):
		article = self._article(title)
		for algorithm, score in zip(("pubmed_bert", "lgbm_tfidf", "lstm"), scores):
			self._predict(article, algorithm, score)
		recompute_article_ml_scores(article_ids=[article.article_id])
		return article

	def _ids(self, params):
		resp = self.client.get("/articles/", params)
		self.assertEqual(resp.status_code, 200)
		return {r["article_id"] for r in resp.data["results"]}

	# ------------------------------------------------------------------
	# The filter itself.
	# ------------------------------------------------------------------

	def test_keeps_scores_at_or_above_value_and_drops_below_and_null(self):
		high = self._scored_article("High score", 0.92)
		at_value = self._scored_article("Exactly at value", 0.7)
		below = self._scored_article("Just below", 0.69)
		unscored = self._article("Never scored")

		ids = self._ids({"ml_score_min": "0.7"})

		self.assertIn(high.article_id, ids)
		self.assertIn(at_value.article_id, ids)
		self.assertNotIn(below.article_id, ids)
		self.assertNotIn(unscored.article_id, ids)

	def test_boundary_is_inclusive(self):
		at_value = self._scored_article("Exactly at value", 0.7)

		self.assertIn(at_value.article_id, self._ids({"ml_score_min": "0.7"}))
		self.assertNotIn(at_value.article_id, self._ids({"ml_score_min": "0.71"}))

	def test_zero_and_one_are_valid_bounds(self):
		low = self._scored_article("Low", 0.05)
		top = self._scored_article("Top", 1.0)

		self.assertEqual(
			self._ids({"ml_score_min": "0"}), {low.article_id, top.article_id}
		)
		self.assertEqual(self._ids({"ml_score_min": "1"}), {top.article_id})

	def test_out_of_range_and_non_numeric_values_return_400(self):
		self._scored_article("Any article", 0.9)

		for bad in ("1.5", "-0.1", "abc"):
			with self.subTest(value=bad):
				resp = self.client.get("/articles/", {"ml_score_min": bad})
				# 400, not the silent empty page ml_threshold gives for 1.5.
				self.assertEqual(resp.status_code, 400)

	def test_combines_with_ordering(self):
		best = self._scored_article("Best", 0.95)
		middle = self._scored_article("Middle", 0.8)
		self._scored_article("Out", 0.2)

		resp = self.client.get(
			"/articles/",
			{"subject_id": self.subject.id, "ml_score_min": "0.7", "ordering": "-ml_score"},
		)
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(
			[r["article_id"] for r in resp.data["results"]],
			[best.article_id, middle.article_id],
		)

	# ------------------------------------------------------------------
	# ml_threshold is a different filter. These pin the difference the
	# documentation describes.
	# ------------------------------------------------------------------

	def test_dihydroartemisinin_case_ml_score_min_includes_what_ml_threshold_drops(self):
		"""One model just under the cutoff drops the paper from a consensus-all
		ml_threshold match, while the displayed score is well above 0.7."""
		article = self._three_models("Near miss", (0.78, 0.97, 1.0))
		article.refresh_from_db()
		self.assertAlmostEqual(article.ml_score, (0.78 + 0.97 + 1.0) / 3)

		scoped = {"subject_id": self.subject.id}
		self.assertIn(article.article_id, self._ids({**scoped, "ml_score_min": "0.7"}))
		self.assertNotIn(article.article_id, self._ids({**scoped, "ml_threshold": "0.7"}))
		# Not a quirk of 0.7: the lgbm-style 0.78 misses the cutoff at any value.
		self.assertNotIn(article.article_id, self._ids({**scoped, "ml_threshold": "0.5"}))

	def test_ml_threshold_below_the_pipeline_cutoff_acts_as_the_cutoff(self):
		everyone_agrees = self._three_models("Everyone agrees", (0.95, 0.9, 0.85))
		on_the_cutoff = self._three_models("On the cutoff", (0.8, 0.8, 0.8))
		near_miss = self._three_models("Near miss", (0.78, 0.97, 1.0))
		all_just_under = self._three_models("All just under", (0.72, 0.72, 0.72))
		scoped = {"subject_id": self.subject.id}

		at_cutoff = self._ids({**scoped, "ml_threshold": "0.8"})
		below_cutoff = self._ids({**scoped, "ml_threshold": "0.7"})

		self.assertEqual(below_cutoff, at_cutoff)
		self.assertEqual(
			at_cutoff, {everyone_agrees.article_id, on_the_cutoff.article_id}
		)
		# ml_score_min at the same number is a much wider net.
		self.assertEqual(
			self._ids({**scoped, "ml_score_min": "0.7"}),
			{
				everyone_agrees.article_id,
				on_the_cutoff.article_id,
				near_miss.article_id,
				all_just_under.article_id,
			},
		)

	def test_ml_threshold_above_the_cutoff_still_narrows(self):
		"""The floor only affects values below the cutoff: 0.9 is a real, stricter rule."""
		strong = self._three_models("Strong", (0.95, 0.92, 0.91))
		weak = self._three_models("Weak", (0.85, 0.84, 0.83))
		scoped = {"subject_id": self.subject.id}

		self.assertEqual(
			self._ids({**scoped, "ml_threshold": "0.8"}),
			{strong.article_id, weak.article_id},
		)
		self.assertEqual(
			self._ids({**scoped, "ml_threshold": "0.9"}), {strong.article_id}
		)

	# ------------------------------------------------------------------
	# /articles/stats/ reuses ArticleFilter.
	# ------------------------------------------------------------------

	def test_stats_total_agrees_with_list_count(self):
		self._scored_article("High score", 0.92)
		self._scored_article("At value", 0.7)
		self._scored_article("Below", 0.69)
		self._article("Never scored")

		listing = self.client.get("/articles/", {"ml_score_min": "0.7"})
		stats = self.client.get("/articles/stats/", {"ml_score_min": "0.7"})

		self.assertEqual(listing.status_code, 200)
		self.assertEqual(stats.status_code, 200)
		self.assertEqual(listing.data["count"], 2)
		self.assertEqual(stats.data["total"], listing.data["count"])
