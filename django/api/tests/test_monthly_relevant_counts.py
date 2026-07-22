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

from gregory.models import (
	Articles,
	MLPredictions,
	OrganizationApiSettings,
	Subject,
	Team,
	TeamCategory,
)

MARCH = datetime(2025, 3, 15, 12, 0, tzinfo=dt_timezone.utc)
APRIL = datetime(2025, 4, 10, 12, 0, tzinfo=dt_timezone.utc)
MAY = datetime(2025, 5, 15, 12, 0, tzinfo=dt_timezone.utc)


class MonthlyRelevantCountsTest(TestCase):
	maxDiff = None

	@classmethod
	def setUpTestData(cls):
		org = Organization.objects.create(name="Relevant Org", slug="relevant-org")
		OrganizationApiSettings.objects.filter(organization=org).update(
			make_api_public=True
		)
		cls.team = Team.objects.create(
			organization=org, name="Relevant Team", slug="relevant-team"
		)
		cls.subject = Subject.objects.create(
			team=cls.team, subject_name="Relevant Subj", subject_slug="relevant-subj"
		)
		cls.category = TeamCategory.objects.create(
			team=cls.team,
			category_name="Relevant Category",
			category_slug="relevant-category",
		)
		cls.category.subjects.add(cls.subject)

		# April: three relevant articles flagged by overlapping models plus one
		# without predictions. The naive per-model sum is 5 (lstm 2 + pubmed_bert 2
		# + lgbm_tfidf 1) while only 4 articles exist and only 3 are relevant.
		a_multi = cls._article("Multi-model overlap", APRIL)
		cls._predict(a_multi, "lstm", 0.9)
		cls._predict(a_multi, "pubmed_bert", 0.85)

		a_multi2 = cls._article("Multi-model overlap low scores", APRIL)
		cls._predict(a_multi2, "lstm", 0.55)
		cls._predict(a_multi2, "lgbm_tfidf", 0.6)

		a_single = cls._article("Single model", APRIL.replace(day=12))
		cls._predict(a_single, "pubmed_bert", 0.95)

		cls._article("No predictions April", APRIL.replace(day=22))

		# March: one article relevant at the default threshold but not at 0.8.
		a_march = cls._article("March borderline", MARCH)
		cls._predict(a_march, "lgbm_tfidf", 0.7)

		# May: nothing relevant — a below-threshold article, one with no
		# predictions, and one whose latest prediction dropped below threshold.
		a_low = cls._article("Below threshold", MAY)
		cls._predict(a_low, "lstm", 0.2)

		cls._article("No predictions May", MAY.replace(day=18))

		a_superseded = cls._article("Superseded prediction", MAY.replace(day=20))
		old = cls._predict(a_superseded, "lgbm_tfidf", 0.9, model_version="v1")
		MLPredictions.objects.filter(pk=old.pk).update(
			created_date=now() - timedelta(days=10)
		)
		cls._predict(a_superseded, "lgbm_tfidf", 0.2, model_version="v2")

		# Relevant but without a publication date: must not produce a null month
		# bucket in the relevant series.
		a_nodate = cls._article("No published date", None)
		cls._predict(a_nodate, "lstm", 0.99)

	def setUp(self):
		self.client = APIClient()

	@classmethod
	def _article(cls, title, published_date):
		article = Articles.objects.create(
			title=title,
			link=f"https://example.com/{title.lower().replace(' ', '-')}",
			published_date=published_date,
		)
		article.team_categories.add(cls.category)
		return article

	@classmethod
	def _predict(cls, article, algorithm, score, model_version="v1"):
		return MLPredictions.objects.create(
			article=article,
			subject=cls.subject,
			algorithm=algorithm,
			model_version=model_version,
			probability_score=score,
			predicted_relevant=score >= 0.5,
		)

	def _get_monthly_counts(self, **params):
		query = {
			"team_id": self.team.id,
			"category_id": self.category.id,
			"monthly_counts": "true",
			"include_authors": "false",
		}
		query.update(params)
		resp = self.client.get("/categories/", query)
		self.assertEqual(resp.status_code, 200)
		results = resp.json()["results"]
		self.assertEqual(len(results), 1)
		return results[0]["monthly_counts"]

	@staticmethod
	def _by_month(series):
		return {entry["month"]: entry["count"] for entry in series}

	@staticmethod
	def _month_key(by_month, prefix):
		keys = [k for k in by_month if k is not None and k.startswith(prefix)]
		return keys[0] if keys else None

	def test_relevant_series_deduplicates_across_models(self):
		counts = self._get_monthly_counts()
		relevant = self._by_month(counts["monthly_relevant_article_counts"])
		totals = self._by_month(counts["monthly_article_counts"])

		april = self._month_key(relevant, "2025-04")
		self.assertIsNotNone(april)
		# Naive sum of the per-model series double-counts the overlapping
		# articles and exceeds the month's article total.
		naive_april_sum = sum(
			self._by_month(series).get(april, 0)
			for series in counts["monthly_ml_article_counts_by_model"].values()
		)
		self.assertEqual(naive_april_sum, 5)
		self.assertEqual(totals[april], 4)
		self.assertGreater(naive_april_sum, totals[april])
		# The deduplicated series counts each article once.
		self.assertEqual(relevant[april], 3)

		march = self._month_key(relevant, "2025-03")
		self.assertIsNotNone(march)
		self.assertEqual(relevant[march], 1)

		# Nothing relevant in May: no bucket at all.
		self.assertIsNone(self._month_key(relevant, "2025-05"))

	def test_relevant_never_exceeds_total_per_month(self):
		counts = self._get_monthly_counts()
		relevant = self._by_month(counts["monthly_relevant_article_counts"])
		totals = self._by_month(counts["monthly_article_counts"])

		self.assertTrue(relevant)
		for month, count in relevant.items():
			self.assertIsNotNone(month)
			self.assertIn(month, totals)
			self.assertLessEqual(count, totals[month])

	def test_relevant_series_excludes_null_months(self):
		counts = self._get_monthly_counts()
		relevant_months = [
			entry["month"] for entry in counts["monthly_relevant_article_counts"]
		]
		self.assertNotIn(None, relevant_months)
		# The article without a publication date is still in the totals' null bucket.
		totals = self._by_month(counts["monthly_article_counts"])
		self.assertIn(None, totals)

	def test_latest_prediction_decides_relevance(self):
		# The superseded May article had a 0.9 prediction replaced by a 0.2 one
		# from the same algorithm: its latest prediction is below threshold, so
		# it is not relevant — consistent with the per-model series.
		counts = self._get_monthly_counts()
		relevant = self._by_month(counts["monthly_relevant_article_counts"])
		self.assertIsNone(self._month_key(relevant, "2025-05"))
		lgbm = self._by_month(
			counts["monthly_ml_article_counts_by_model"].get("lgbm_tfidf", [])
		)
		self.assertIsNone(self._month_key(lgbm, "2025-05"))

	def test_threshold_parameter_is_respected(self):
		counts = self._get_monthly_counts(ml_threshold="0.8")
		self.assertEqual(counts["ml_threshold"], 0.8)
		relevant = self._by_month(counts["monthly_relevant_article_counts"])
		# The 0.55/0.6 overlap article drops out at 0.8.
		april = self._month_key(relevant, "2025-04")
		self.assertIsNotNone(april)
		self.assertEqual(relevant[april], 2)
		# The 0.7 March article is no longer relevant.
		self.assertIsNone(self._month_key(relevant, "2025-03"))

	def test_existing_payload_fields_unchanged(self):
		counts = self._get_monthly_counts()
		for key in (
			"ml_threshold",
			"available_models",
			"monthly_article_counts",
			"monthly_ml_article_counts_by_model",
			"monthly_relevant_article_counts",
			"monthly_trial_counts",
		):
			self.assertIn(key, counts)
		self.assertEqual(counts["ml_threshold"], 0.5)
		self.assertEqual(
			sorted(counts["available_models"]),
			["lgbm_tfidf", "lstm", "pubmed_bert"],
		)


class MonthlyCountsPerModelLatestPredictionTest(TestCase):
	"""
	Regression coverage for the perf/monthly-counts-rewrite N+1 fix in
	CategorySerializer.get_monthly_counts.

	The per-model series in monthly_ml_article_counts_by_model must reflect
	only the LATEST prediction for a given (article, algorithm) pair: an
	article whose latest prediction for that algorithm dropped below
	threshold must not be counted even if an older prediction cleared it, and
	an article whose latest prediction rose above threshold must be counted
	even if an older one didn't.
	"""

	def setUp(self):
		self.client = APIClient()

		org = Organization.objects.create(
			name="Latest Pred Org", slug="latest-pred-org"
		)
		OrganizationApiSettings.objects.filter(organization=org).update(
			make_api_public=True
		)
		self.team = Team.objects.create(
			organization=org, name="Latest Pred Team", slug="latest-pred-team"
		)
		self.subject = Subject.objects.create(
			team=self.team,
			subject_name="Latest Pred Subj",
			subject_slug="latest-pred-subj",
		)
		self.category = TeamCategory.objects.create(
			team=self.team,
			category_name="Latest Pred Category",
			category_slug="latest-pred-category",
		)
		self.category.subjects.add(self.subject)

		# Superseded DOWN: an old lstm prediction cleared the threshold, but the
		# newest lstm prediction for the same article dropped below it. Must NOT
		# appear in the lstm series.
		self.article_down = self._article("Superseded down", APRIL)
		old_high = self._predict(self.article_down, "lstm", 0.9, model_version="v1")
		MLPredictions.objects.filter(pk=old_high.pk).update(
			created_date=now() - timedelta(days=10)
		)
		self._predict(self.article_down, "lstm", 0.1, model_version="v2")

		# Superseded UP: an old lstm prediction was below threshold, but the
		# newest lstm prediction for the same article cleared it. Must appear in
		# the lstm series.
		self.article_up = self._article("Superseded up", APRIL.replace(day=20))
		old_low = self._predict(self.article_up, "lstm", 0.1, model_version="v1")
		MLPredictions.objects.filter(pk=old_low.pk).update(
			created_date=now() - timedelta(days=10)
		)
		self._predict(self.article_up, "lstm", 0.9, model_version="v2")

	def _article(self, title, published_date):
		article = Articles.objects.create(
			title=title,
			link=f"https://example.com/{title.lower().replace(' ', '-')}",
			published_date=published_date,
		)
		article.team_categories.add(self.category)
		return article

	def _predict(self, article, algorithm, score, model_version="v1"):
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
			"team_id": self.team.id,
			"category_id": self.category.id,
			"monthly_counts": "true",
			"include_authors": "false",
		}
		query.update(params)
		resp = self.client.get("/categories/", query)
		self.assertEqual(resp.status_code, 200)
		results = resp.json()["results"]
		self.assertEqual(len(results), 1)
		return results[0]["monthly_counts"]

	@staticmethod
	def _by_month(series):
		return {entry["month"]: entry["count"] for entry in series}

	def test_only_the_currently_qualifying_article_is_counted(self):
		counts = self._get_monthly_counts()
		lstm = self._by_month(counts["monthly_ml_article_counts_by_model"]["lstm"])
		april_total = sum(v for k, v in lstm.items() if k and k.startswith("2025-04"))
		# Two articles exist in April; only "Superseded up" currently qualifies
		# for lstm (latest prediction 0.9 >= 0.5). "Superseded down" does not
		# (latest prediction 0.1 < 0.5), even though its older prediction was 0.9.
		self.assertEqual(april_total, 1)

		totals = self._by_month(counts["monthly_article_counts"])
		april_articles_total = sum(
			v for k, v in totals.items() if k and k.startswith("2025-04")
		)
		self.assertEqual(april_articles_total, 2)


class MonthlyCountsAvailableModelsTest(TestCase):
	"""
	available_models must reflect every algorithm present among the category's
	predictions, independent of whether that algorithm's latest score clears
	ml_threshold -- available_models is not threshold-filtered.
	"""

	def setUp(self):
		self.client = APIClient()

		org = Organization.objects.create(
			name="Avail Models Org", slug="avail-models-org"
		)
		OrganizationApiSettings.objects.filter(organization=org).update(
			make_api_public=True
		)
		self.team = Team.objects.create(
			organization=org, name="Avail Models Team", slug="avail-models-team"
		)
		self.subject = Subject.objects.create(
			team=self.team,
			subject_name="Avail Models Subj",
			subject_slug="avail-models-subj",
		)
		self.category = TeamCategory.objects.create(
			team=self.team,
			category_name="Avail Models Category",
			category_slug="avail-models-category",
		)
		self.category.subjects.add(self.subject)

		article = Articles.objects.create(
			title="Below threshold algo",
			link="https://example.com/below-threshold-algo",
			published_date=APRIL,
		)
		article.team_categories.add(self.category)
		MLPredictions.objects.create(
			article=article,
			subject=self.subject,
			algorithm="lgbm_tfidf",
			model_version="v1",
			probability_score=0.1,
			predicted_relevant=False,
		)

	def _get_monthly_counts(self, **params):
		query = {
			"team_id": self.team.id,
			"category_id": self.category.id,
			"monthly_counts": "true",
			"include_authors": "false",
		}
		query.update(params)
		resp = self.client.get("/categories/", query)
		self.assertEqual(resp.status_code, 200)
		results = resp.json()["results"]
		self.assertEqual(len(results), 1)
		return results[0]["monthly_counts"]

	def test_available_models_includes_below_threshold_algorithm(self):
		counts = self._get_monthly_counts()
		self.assertIn("lgbm_tfidf", counts["available_models"])
		# But its per-model series has no qualifying months: available_models is
		# not threshold-filtered even though the per-model series is.
		self.assertEqual(counts["monthly_ml_article_counts_by_model"]["lgbm_tfidf"], [])


class MonthlyCountsQueryBudgetTest(TestCase):
	"""
	Query-count regression guard for CategorySerializer.get_monthly_counts.

	Before the perf/monthly-counts-rewrite fix, this method issued one query
	per (article, algorithm) pair in two separate Python loops -- tens of
	thousands of queries for a real category (measured: ~22s / 9000+ queries
	for a 7,364-article category against the dev DB). After the fix the method
	issues a small, roughly-constant number of queries independent of article
	count.
	"""

	def setUp(self):
		self.client = APIClient()

		org = Organization.objects.create(name="Budget Org", slug="budget-org")
		OrganizationApiSettings.objects.filter(organization=org).update(
			make_api_public=True
		)
		self.team = Team.objects.create(
			organization=org, name="Budget Team", slug="budget-team"
		)
		self.subject = Subject.objects.create(
			team=self.team, subject_name="Budget Subj", subject_slug="budget-subj"
		)
		self.category = TeamCategory.objects.create(
			team=self.team,
			category_name="Budget Category",
			category_slug="budget-category",
		)
		self.category.subjects.add(self.subject)

		algorithms = ["lstm", "pubmed_bert", "lgbm_tfidf"]
		for i in range(30):
			article = Articles.objects.create(
				title=f"Budget article {i}",
				link=f"https://example.com/budget-{i}",
				published_date=APRIL.replace(day=1) + timedelta(days=i % 27),
			)
			article.team_categories.add(self.category)
			for algo in algorithms:
				MLPredictions.objects.create(
					article=article,
					subject=self.subject,
					algorithm=algo,
					model_version="v1",
					probability_score=0.9,
					predicted_relevant=True,
				)

	def test_query_budget_stays_small_regardless_of_article_count(self):
		"""
		Query budget for get_monthly_counts itself against this fixture (30
		articles x 3 algorithms), metered at the serializer method rather than
		the full HTTP request so unrelated view/middleware/queryset changes
		can't trip the guard. Empirically 7 queries: 1 monthly_article_counts +
		1 available_models + 3 (one aggregate query per model in
		ml_counts_by_model) + 1 relevant_counts + 1 trial_counts. Under the old
		N+1 implementation this fixture would have issued 30 + 90 = 120+ extra
		per-pair `.first()` queries. Assert a bound with a little slack so a
		regression toward per-article or per-pair querying can't creep back in
		silently.
		"""
		from django.db import connection
		from django.test.utils import CaptureQueriesContext

		from api.serializers import CategorySerializer

		serializer = CategorySerializer(
			self.category,
			context={
				"monthly_counts_params": {
					"include_monthly_counts": True,
					"ml_threshold": 0.5,
				},
				"author_params": {"include_authors": False},
			},
		)
		with CaptureQueriesContext(connection) as ctx:
			payload = serializer.get_monthly_counts(self.category)
		self.assertIsNotNone(payload)
		self.assertEqual(
			sorted(payload["available_models"]), payload["available_models"],
			msg="available_models must be deterministically ordered",
		)
		self.assertLessEqual(
			len(ctx.captured_queries),
			9,
			msg=(
				"get_monthly_counts exceeded the query budget: "
				f"{len(ctx.captured_queries)} queries"
			),
		)
