"""
Tests for the deduplicated monthly_relevant_article_counts series in the
categories endpoint's monthly_counts payload.

An article is relevant in a month when the latest prediction of at least one
ML model meets ml_threshold; it is counted once per month no matter how many
models flagged it. The series must therefore never exceed the same month's
monthly_article_counts — which a naive sum of the per-model series does as
soon as two models flag the same article.

Run with:
    docker exec gregory python manage.py test api.tests.test_monthly_relevant_counts
"""
from datetime import datetime, timedelta, timezone as dt_timezone

from django.test import TestCase
from django.utils.timezone import now
from organizations.models import Organization
from rest_framework.test import APIClient

from gregory.models import Articles, MLPredictions, OrganizationApiSettings, Subject, Team, TeamCategory

MARCH = datetime(2025, 3, 15, 12, 0, tzinfo=dt_timezone.utc)
APRIL = datetime(2025, 4, 10, 12, 0, tzinfo=dt_timezone.utc)
MAY = datetime(2025, 5, 15, 12, 0, tzinfo=dt_timezone.utc)


class MonthlyRelevantCountsTest(TestCase):
	maxDiff = None

	def setUp(self):
		self.client = APIClient()

		org = Organization.objects.create(name='Relevant Org', slug='relevant-org')
		OrganizationApiSettings.objects.filter(organization=org).update(make_api_public=True)
		self.team = Team.objects.create(organization=org, name='Relevant Team', slug='relevant-team')
		self.subject = Subject.objects.create(team=self.team, subject_name='Relevant Subj', subject_slug='relevant-subj')
		self.category = TeamCategory.objects.create(
			team=self.team,
			category_name='Relevant Category',
			category_slug='relevant-category',
		)
		self.category.subjects.add(self.subject)

		# April: three relevant articles flagged by overlapping models plus one
		# without predictions. The naive per-model sum is 5 (lstm 2 + pubmed_bert 2
		# + lgbm_tfidf 1) while only 4 articles exist and only 3 are relevant.
		a_multi = self._article('Multi-model overlap', APRIL)
		self._predict(a_multi, 'lstm', 0.9)
		self._predict(a_multi, 'pubmed_bert', 0.85)

		a_multi2 = self._article('Multi-model overlap low scores', APRIL)
		self._predict(a_multi2, 'lstm', 0.55)
		self._predict(a_multi2, 'lgbm_tfidf', 0.6)

		a_single = self._article('Single model', APRIL.replace(day=12))
		self._predict(a_single, 'pubmed_bert', 0.95)

		self._article('No predictions April', APRIL.replace(day=22))

		# March: one article relevant at the default threshold but not at 0.8.
		a_march = self._article('March borderline', MARCH)
		self._predict(a_march, 'lgbm_tfidf', 0.7)

		# May: nothing relevant — a below-threshold article, one with no
		# predictions, and one whose latest prediction dropped below threshold.
		a_low = self._article('Below threshold', MAY)
		self._predict(a_low, 'lstm', 0.2)

		self._article('No predictions May', MAY.replace(day=18))

		a_superseded = self._article('Superseded prediction', MAY.replace(day=20))
		old = self._predict(a_superseded, 'lgbm_tfidf', 0.9, model_version='v1')
		MLPredictions.objects.filter(pk=old.pk).update(created_date=now() - timedelta(days=10))
		self._predict(a_superseded, 'lgbm_tfidf', 0.2, model_version='v2')

		# Relevant but without a publication date: must not produce a null month
		# bucket in the relevant series.
		a_nodate = self._article('No published date', None)
		self._predict(a_nodate, 'lstm', 0.99)

	def _article(self, title, published_date):
		article = Articles.objects.create(
			title=title,
			link=f'https://example.com/{title.lower().replace(" ", "-")}',
			published_date=published_date,
		)
		article.team_categories.add(self.category)
		return article

	def _predict(self, article, algorithm, score, model_version='v1'):
		return MLPredictions.objects.create(
			article=article,
			subject=self.subject,
			algorithm=algorithm,
			model_version=model_version,
			probability_score=score,
			predicted_relevant=score >= 0.5,
		)

	def _get_monthly_counts(self, **params):
		query = {
			'team_id': self.team.id,
			'category_id': self.category.id,
			'monthly_counts': 'true',
			'include_authors': 'false',
		}
		query.update(params)
		resp = self.client.get('/categories/', query)
		self.assertEqual(resp.status_code, 200)
		results = resp.json()['results']
		self.assertEqual(len(results), 1)
		return results[0]['monthly_counts']

	@staticmethod
	def _by_month(series):
		return {entry['month']: entry['count'] for entry in series}

	@staticmethod
	def _month_key(by_month, prefix):
		keys = [k for k in by_month if k is not None and k.startswith(prefix)]
		return keys[0] if keys else None

	def test_relevant_series_deduplicates_across_models(self):
		counts = self._get_monthly_counts()
		relevant = self._by_month(counts['monthly_relevant_article_counts'])
		totals = self._by_month(counts['monthly_article_counts'])

		april = self._month_key(relevant, '2025-04')
		self.assertIsNotNone(april)
		# Naive sum of the per-model series double-counts the overlapping
		# articles and exceeds the month's article total.
		naive_april_sum = sum(
			self._by_month(series).get(april, 0)
			for series in counts['monthly_ml_article_counts_by_model'].values()
		)
		self.assertEqual(naive_april_sum, 5)
		self.assertEqual(totals[april], 4)
		self.assertGreater(naive_april_sum, totals[april])
		# The deduplicated series counts each article once.
		self.assertEqual(relevant[april], 3)

		march = self._month_key(relevant, '2025-03')
		self.assertIsNotNone(march)
		self.assertEqual(relevant[march], 1)

		# Nothing relevant in May: no bucket at all.
		self.assertIsNone(self._month_key(relevant, '2025-05'))

	def test_relevant_never_exceeds_total_per_month(self):
		counts = self._get_monthly_counts()
		relevant = self._by_month(counts['monthly_relevant_article_counts'])
		totals = self._by_month(counts['monthly_article_counts'])

		self.assertTrue(relevant)
		for month, count in relevant.items():
			self.assertIsNotNone(month)
			self.assertIn(month, totals)
			self.assertLessEqual(count, totals[month])

	def test_relevant_series_excludes_null_months(self):
		counts = self._get_monthly_counts()
		relevant_months = [entry['month'] for entry in counts['monthly_relevant_article_counts']]
		self.assertNotIn(None, relevant_months)
		# The article without a publication date is still in the totals' null bucket.
		totals = self._by_month(counts['monthly_article_counts'])
		self.assertIn(None, totals)

	def test_latest_prediction_decides_relevance(self):
		# The superseded May article had a 0.9 prediction replaced by a 0.2 one
		# from the same algorithm: its latest prediction is below threshold, so
		# it is not relevant — consistent with the per-model series.
		counts = self._get_monthly_counts()
		relevant = self._by_month(counts['monthly_relevant_article_counts'])
		self.assertIsNone(self._month_key(relevant, '2025-05'))
		lgbm = self._by_month(counts['monthly_ml_article_counts_by_model'].get('lgbm_tfidf', []))
		self.assertIsNone(self._month_key(lgbm, '2025-05'))

	def test_threshold_parameter_is_respected(self):
		counts = self._get_monthly_counts(ml_threshold='0.8')
		self.assertEqual(counts['ml_threshold'], 0.8)
		relevant = self._by_month(counts['monthly_relevant_article_counts'])
		# The 0.55/0.6 overlap article drops out at 0.8.
		april = self._month_key(relevant, '2025-04')
		self.assertIsNotNone(april)
		self.assertEqual(relevant[april], 2)
		# The 0.7 March article is no longer relevant.
		self.assertIsNone(self._month_key(relevant, '2025-03'))

	def test_existing_payload_fields_unchanged(self):
		counts = self._get_monthly_counts()
		for key in (
			'ml_threshold',
			'available_models',
			'monthly_article_counts',
			'monthly_ml_article_counts_by_model',
			'monthly_relevant_article_counts',
			'monthly_trial_counts',
		):
			self.assertIn(key, counts)
		self.assertEqual(counts['ml_threshold'], 0.5)
		self.assertEqual(
			sorted(counts['available_models']),
			['lgbm_tfidf', 'lstm', 'pubmed_bert'],
		)
