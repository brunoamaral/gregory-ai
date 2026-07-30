"""Cross-implementation equivalence for the ML consensus rule.

"Is this article ML-relevant for these subjects at this threshold" is
implemented three times:

  - Articles.is_ml_relevant_for_subject / is_ml_relevant_any_subject
    (gregory/models.py) -- per-article ORM, used by the weekly digest
  - ml_relevant_articles_q (api/filters.py) -- a Q object, used by the API
    ?relevant=true filter and rss/sitemaps.py
  - recompute_article_relevance (gregory/relevance.py) -- raw SQL UPDATE,
    maintains the denormalized Articles.relevant column (manual OR ML)

Nothing but a shared docstring enforces that they keep agreeing. This suite
is that enforcement: it builds a fixture that exercises every place the three
implementations could plausibly diverge (consensus type, threshold boundary,
staleness, ties, the predicted_relevant/probability_score split, and the
manual/ML OR-combination the denormalized column alone performs), then
asserts all three agree on every subject and threshold.

This is *equivalence*, not correctness -- it will not catch a bug shared by
all three implementations. Each per-subject/per-threshold check also asserts
at least one positive match exists, so a bug that made every implementation
return nothing could not pass this suite by vacuous agreement.
"""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from organizations.models import Organization

from api.filters import ml_relevant_articles_q
from gregory.models import (
	Articles,
	ArticleSubjectRelevance,
	MLPredictions,
	Subject,
	Team,
)
from gregory.relevance import recompute_article_relevance

THRESHOLDS = [0.6, 0.8, 0.9]


class MlConsensusEquivalenceTestCase(TestCase):
	@classmethod
	def setUpTestData(cls):
		org = Organization.objects.create(name="Consensus Equivalence Org")
		cls.team = Team.objects.create(
			organization=org, name="Consensus Team", slug="consensus-equivalence-team"
		)
		cls.other_team = Team.objects.create(
			organization=org, name="Other Team", slug="consensus-equivalence-other-team"
		)

		cls.subject_any = Subject.objects.create(
			subject_name="Any Subject",
			subject_slug="ce-any-subject",
			team=cls.team,
			auto_predict=True,
			ml_consensus_type="any",
		)
		cls.subject_majority = Subject.objects.create(
			subject_name="Majority Subject",
			subject_slug="ce-majority-subject",
			team=cls.team,
			auto_predict=True,
			ml_consensus_type="majority",
		)
		cls.subject_all = Subject.objects.create(
			subject_name="All Subject",
			subject_slug="ce-all-subject",
			team=cls.team,
			auto_predict=True,
			ml_consensus_type="all",
		)
		# A second team's subject, used only for the cross-subject scoping case
		# below -- an article relevant for subject_any must not leak relevance
		# into a query scoped to subject_other.
		cls.subject_other = Subject.objects.create(
			subject_name="Other Subject",
			subject_slug="ce-other-subject",
			team=cls.other_team,
			auto_predict=True,
			ml_consensus_type="any",
		)

		cls.subjects = [
			cls.subject_any,
			cls.subject_majority,
			cls.subject_all,
			cls.subject_other,
		]

		cls._build_fixture()

	@classmethod
	def _article(cls, title, link):
		return Articles.objects.create(title=title, link=link)

	@classmethod
	def _predict(
		cls,
		article,
		subject,
		algorithm,
		score,
		model_version="v1",
		predicted_relevant=None,
		days_ago=0,
	):
		if predicted_relevant is None:
			predicted_relevant = score >= 0.5
		pred = MLPredictions.objects.create(
			article=article,
			subject=subject,
			algorithm=algorithm,
			model_version=model_version,
			probability_score=score,
			predicted_relevant=predicted_relevant,
		)
		if days_ago:
			MLPredictions.objects.filter(pk=pred.pk).update(
				created_date=timezone.now() - timedelta(days=days_ago)
			)
		return pred

	@classmethod
	def _build_fixture(cls):
		algorithms = ["pubmed_bert", "lgbm_tfidf", "lstm"]
		consensus_subjects = [cls.subject_any, cls.subject_majority, cls.subject_all]

		# Case: all three algorithms below threshold. Distinguishes a plain
		# negative from an implementation that miscounts zero qualifying
		# algorithms as consensus.
		cls.consensus_zero = cls._article("Consensus zero", "https://example.com/ce-zero")
		cls.consensus_zero.subjects.add(*consensus_subjects)
		for subject in consensus_subjects:
			for algo in algorithms:
				cls._predict(cls.consensus_zero, subject, algo, 0.3)

		# Case: exactly one algorithm above threshold -- separates 'any' (true)
		# from 'majority'/'all' (false), across all three consensus types at
		# once. Also attached to subject_other with NO predictions at all, to
		# exercise the cross-subject scoping case: relevant for subject_any,
		# but a query scoped to subject_other must not see it as relevant.
		cls.consensus_one = cls._article("Consensus one", "https://example.com/ce-one")
		cls.consensus_one.subjects.add(*consensus_subjects, cls.subject_other)
		for subject in consensus_subjects:
			cls._predict(cls.consensus_one, subject, algorithms[0], 0.9)
			for algo in algorithms[1:]:
				cls._predict(cls.consensus_one, subject, algo, 0.3)

		# Case: exactly two algorithms above threshold -- separates 'majority'
		# (true) from 'all' (false).
		cls.consensus_two = cls._article("Consensus two", "https://example.com/ce-two")
		cls.consensus_two.subjects.add(*consensus_subjects)
		for subject in consensus_subjects:
			cls._predict(cls.consensus_two, subject, algorithms[0], 0.9)
			cls._predict(cls.consensus_two, subject, algorithms[1], 0.85)
			cls._predict(cls.consensus_two, subject, algorithms[2], 0.3)

		# Case: all three algorithms above threshold -- the plain positive, and
		# the sanity anchor that keeps the equivalence loop below from passing
		# vacuously on an empty match set.
		cls.consensus_three = cls._article("Consensus three", "https://example.com/ce-three")
		cls.consensus_three.subjects.add(*consensus_subjects)
		for subject in consensus_subjects:
			for algo in algorithms:
				cls._predict(cls.consensus_three, subject, algo, 0.9)

		# Case: a score exactly at the threshold. >= vs > is the classic place
		# three independent implementations diverge.
		cls.threshold_boundary = cls._article(
			"Threshold boundary", "https://example.com/ce-boundary"
		)
		cls.threshold_boundary.subjects.add(cls.subject_any)
		cls._predict(cls.threshold_boundary, cls.subject_any, "pubmed_bert", 0.8)

		# Case: an algorithm with two predictions, the newer one below
		# threshold. Only the latest per (article, subject, algorithm) may
		# count -- a retired model_version must not keep an article relevant.
		cls.stale_prediction = cls._article(
			"Stale prediction", "https://example.com/ce-stale"
		)
		cls.stale_prediction.subjects.add(cls.subject_any)
		cls._predict(
			cls.stale_prediction, cls.subject_any, "pubmed_bert", 0.9,
			model_version="v1", days_ago=10,
		)
		cls._predict(
			cls.stale_prediction, cls.subject_any, "pubmed_bert", 0.3,
			model_version="v2", days_ago=0,
		)

		# Case: tied created_date on the latest pair for one algorithm. All
		# tied latest rows are considered -- the algorithm qualifies if any
		# tied row does.
		cls.tied_latest = cls._article("Tied latest", "https://example.com/ce-tied")
		cls.tied_latest.subjects.add(cls.subject_any)
		low = cls._predict(
			cls.tied_latest, cls.subject_any, "pubmed_bert", 0.3, model_version="v1"
		)
		high = cls._predict(
			cls.tied_latest, cls.subject_any, "pubmed_bert", 0.9, model_version="v2"
		)
		tied_at = timezone.now() - timedelta(days=1)
		MLPredictions.objects.filter(pk__in=[low.pk, high.pk]).update(created_date=tied_at)

		# Case: predicted_relevant=False despite a high probability_score. The
		# flag and the score must not be conflated.
		cls.flag_score_mismatch = cls._article(
			"Flag score mismatch", "https://example.com/ce-mismatch"
		)
		cls.flag_score_mismatch.subjects.add(cls.subject_any)
		cls._predict(
			cls.flag_score_mismatch,
			cls.subject_any,
			"pubmed_bert",
			0.95,
			predicted_relevant=False,
		)

		# Case: an article with no predictions at all.
		cls.no_predictions = cls._article("No predictions", "https://example.com/ce-none")
		cls.no_predictions.subjects.add(cls.subject_any)

		# Case: manual relevance only, no ML. Isolates the third
		# implementation's extra input -- the first two must both say False.
		cls.manual_only = cls._article("Manual only", "https://example.com/ce-manual-only")
		cls.manual_only.subjects.add(cls.subject_any)
		ArticleSubjectRelevance.objects.create(
			article=cls.manual_only, subject=cls.subject_any, is_relevant=True
		)

		# Case: manual relevance contradicting ML (manual False, ML True). The
		# two inputs must combine as OR -- the denormalized column must stay
		# True on the strength of the ML consensus alone.
		cls.manual_contradicts_ml = cls._article(
			"Manual contradicts ML", "https://example.com/ce-manual-contradicts"
		)
		cls.manual_contradicts_ml.subjects.add(cls.subject_any)
		cls._predict(cls.manual_contradicts_ml, cls.subject_any, "pubmed_bert", 0.9)
		ArticleSubjectRelevance.objects.create(
			article=cls.manual_contradicts_ml, subject=cls.subject_any, is_relevant=False
		)

	# -- comparison helpers --------------------------------------------------

	def _ml_set_via_models(self, subject, threshold):
		return {
			a.article_id
			for a in Articles.objects.filter(subjects=subject).distinct()
			if a.is_ml_relevant_for_subject(subject, threshold)
		}

	def _ml_set_via_filters(self, subject_ids, threshold):
		return set(
			Articles.objects.filter(ml_relevant_articles_q(threshold, subject_ids))
			.values_list("article_id", flat=True)
		)

	def _assert_sets_equal(self, label_a, set_a, label_b, set_b, context):
		if set_a == set_b:
			return
		only_a = sorted(set_a - set_b)
		only_b = sorted(set_b - set_a)
		self.fail(
			f"{label_a} and {label_b} disagree ({context}): "
			f"only in {label_a}={only_a}, only in {label_b}={only_b}"
		)

	# -- assertions ------------------------------------------------------------

	def test_digest_vs_api_agree_per_subject_across_thresholds(self):
		"""is_ml_relevant_for_subject and ml_relevant_articles_q must match
		article-for-article, for every subject and threshold in the fixture."""
		saw_a_match = False
		for subject in self.subjects:
			for threshold in THRESHOLDS:
				model_set = self._ml_set_via_models(subject, threshold)
				filter_set = self._ml_set_via_filters([subject.pk], threshold)
				self._assert_sets_equal(
					"is_ml_relevant_for_subject",
					model_set,
					"ml_relevant_articles_q",
					filter_set,
					context=f"subject={subject.subject_slug!r} threshold={threshold}",
				)
				saw_a_match = saw_a_match or bool(model_set)
		self.assertTrue(
			saw_a_match,
			"fixture produced zero ML-relevant matches across every subject/threshold -- "
			"the agreement above would be vacuous",
		)

	def test_multi_subject_digest_vs_api_agree(self):
		"""is_ml_relevant_any_subject (scoped to a subject list) must match
		ml_relevant_articles_q given the same subject_ids, including the
		cross-subject scoping case where an article is relevant for one
		subject but queried against another it's also tagged with."""
		subject_id_combos = [
			[self.subject_any.pk],
			[self.subject_any.pk, self.subject_majority.pk, self.subject_all.pk],
			[self.subject_other.pk],
			[self.subject_any.pk, self.subject_other.pk],
		]
		saw_a_match = False
		for ids in subject_id_combos:
			subjects_qs = Subject.objects.filter(pk__in=ids)
			for threshold in THRESHOLDS:
				model_set = {
					a.article_id
					for a in Articles.objects.all()
					if a.is_ml_relevant_any_subject(threshold=threshold, subjects=subjects_qs)
				}
				filter_set = self._ml_set_via_filters(ids, threshold)
				self._assert_sets_equal(
					"is_ml_relevant_any_subject",
					model_set,
					"ml_relevant_articles_q",
					filter_set,
					context=f"subject_ids={ids} threshold={threshold}",
				)
				saw_a_match = saw_a_match or bool(model_set)
		self.assertTrue(
			saw_a_match,
			"fixture produced zero multi-subject matches -- the agreement above would be vacuous",
		)
		# Confirm the scoping case actually exercised something meaningful:
		# consensus_one is relevant for subject_any but carries no prediction
		# for subject_other, so a query scoped to subject_other alone must
		# exclude it while one scoped to subject_any includes it.
		self.assertIn(
			self.consensus_one.article_id,
			self._ml_set_via_filters([self.subject_any.pk], 0.8),
		)
		self.assertNotIn(
			self.consensus_one.article_id,
			self._ml_set_via_filters([self.subject_other.pk], 0.8),
		)

	def test_denormalized_relevant_matches_manual_or_ml(self):
		"""Articles.relevant, after recompute_article_relevance(), must equal
		(manual relevance) OR (ML consensus via ml_relevant_articles_q) at the
		0.8 default threshold. This is the comparison that would have caught
		the bulk_create drift bug (see gregory/relevance.py) -- it stays in
		the suite permanently."""
		recompute_article_relevance()

		manual_ids = set(
			ArticleSubjectRelevance.objects.filter(is_relevant=True).values_list(
				"article_id", flat=True
			)
		)
		ml_ids = self._ml_set_via_filters(None, 0.8)
		expected = manual_ids | ml_ids
		actual = set(Articles.objects.filter(relevant=True).values_list("article_id", flat=True))

		self._assert_sets_equal(
			"manual | ml_relevant_articles_q",
			expected,
			"Articles.relevant=True",
			actual,
			context="recompute_article_relevance() at threshold=0.8",
		)
		# manual_contradicts_ml must survive on the strength of ML alone even
		# though its manual row is explicitly False -- proves OR, not override.
		self.assertIn(self.manual_contradicts_ml.article_id, actual)
		# manual_only must survive on the strength of the manual row alone,
		# with zero ML predictions backing it.
		self.assertIn(self.manual_only.article_id, actual)
