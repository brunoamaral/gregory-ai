"""Relevance on /articles/ only counts for the subjects the caller can see.

An article can carry subjects from two sites. Under site-scoped visibility it
is visible on both, but an editor verdict or ML consensus on the OTHER site's
subject must not make it relevant here. Before this was fixed, a request that
named no subject passed ``subject_ids=None`` to ``relevant_articles_q`` /
``ml_relevant_articles_q``, which read every subject in the database.

Covers both relevance rules (``relevance_rule=api`` and ``editorial``), the
list, ``relevant=false`` as the complement, ``ml_threshold``, the stats count,
and naming a subject outside the caller's scope.
"""

from types import SimpleNamespace

from django.http import QueryDict
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from api.filters import ArticleFilter
from api.tests.visibility_helpers import publish_subjects
from gregory.models import (
	Articles,
	ArticleSubjectRelevance,
	MLPredictions,
	Organization,
	Subject,
	Team,
)


class RelevanceVisibleScopeTests(TestCase):
	@classmethod
	def setUpTestData(cls):
		def tenant(slug):
			org = Organization.objects.create(name=f"Org {slug}", slug=f"org-{slug}")
			team = Team.objects.create(
				name=f"Team {slug}", slug=f"team-{slug}", organization=org
			)
			subject = Subject.objects.create(
				subject_name=f"Subject {slug}",
				subject_slug=f"subject-{slug}",
				team=team,
				auto_predict=True,
				ml_consensus_type="any",
			)
			site = publish_subjects(subject, organization=org)
			return team, subject, site

		cls.team_a, cls.sa, cls.site_a = tenant("a")
		cls.team_b, cls.sb, cls.site_b = tenant("b")

		def make(title, ml_for=(), verdicts=()):
			# Every article carries both subjects, so it is visible on both
			# sites; only where its relevance comes from differs.
			article = Articles.objects.create(
				title=title, link=f"https://example.com/visible-scope/{title}"
			)
			article.subjects.add(cls.sa, cls.sb)
			article.teams.add(cls.team_a, cls.team_b)
			for subject in ml_for:
				MLPredictions.objects.create(
					article=article,
					subject=subject,
					algorithm="pubmed_bert",
					probability_score=0.95,
					predicted_relevant=True,
				)
			for subject, verdict in verdicts:
				ArticleSubjectRelevance.objects.create(
					article=article, subject=subject, is_relevant=verdict
				)
			return article

		cls.ml_a = make("ml-a", ml_for=[cls.sa])
		cls.editor_a = make("editor-a", verdicts=[(cls.sa, True)])
		cls.ml_b = make("ml-b", ml_for=[cls.sb])
		cls.editor_b = make("editor-b", verdicts=[(cls.sb, True)])
		# ML yes on A, editor no on A, editor yes on B. The API rule keeps it on
		# A (ML yes); the editorial rule drops it on A -- only B's yes would
		# carry it, and B is not A's to read.
		cls.mixed = make(
			"mixed", ml_for=[cls.sa], verdicts=[(cls.sa, False), (cls.sb, True)]
		)
		cls.nothing = make("nothing")
		cls.all_articles = [
			cls.ml_a,
			cls.editor_a,
			cls.ml_b,
			cls.editor_b,
			cls.mixed,
			cls.nothing,
		]
		cls.everything = {a.article_id for a in cls.all_articles}

	def setUp(self):
		self.client = APIClient()

	def _ids(self, site, **params):
		response = self.client.get("/articles/", {"site_id": site.id, **params})
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		return {r["article_id"] for r in response.data["results"]} & self.everything

	def _expected(self):
		"""What relevant=true returns, per (site, rule)."""
		return {
			(self.site_a, "api"): {self.ml_a, self.editor_a, self.mixed},
			(self.site_a, "editorial"): {self.ml_a, self.editor_a},
			(self.site_b, "api"): {self.ml_b, self.editor_b, self.mixed},
			(self.site_b, "editorial"): {self.ml_b, self.editor_b, self.mixed},
		}

	def test_every_article_is_visible_on_both_sites(self):
		for site in (self.site_a, self.site_b):
			with self.subTest(site=site.domain):
				self.assertEqual(self._ids(site), self.everything)

	def test_list_counts_only_visible_subjects(self):
		for (site, rule), wanted in self._expected().items():
			wanted_ids = {a.article_id for a in wanted}
			with self.subTest(site=site.domain, rule=rule):
				self.assertEqual(
					self._ids(site, relevant="true", relevance_rule=rule), wanted_ids
				)
				# relevant=false stays the exact complement.
				self.assertEqual(
					self._ids(site, relevant="false", relevance_rule=rule),
					self.everything - wanted_ids,
				)

	def test_default_rule_is_api_and_scoped(self):
		self.assertEqual(
			self._ids(self.site_a, relevant="true"),
			{a.article_id for a in self._expected()[(self.site_a, "api")]},
		)

	def test_other_sites_verdict_and_consensus_do_not_leak(self):
		for rule in ("api", "editorial"):
			with self.subTest(rule=rule):
				got = self._ids(self.site_a, relevant="true", relevance_rule=rule)
				self.assertNotIn(self.ml_b.article_id, got)
				self.assertNotIn(self.editor_b.article_id, got)

	def test_ml_threshold_counts_only_visible_subjects(self):
		self.assertEqual(
			self._ids(self.site_a, ml_threshold="0.8"),
			{self.ml_a.article_id, self.mixed.article_id},
		)
		self.assertEqual(
			self._ids(self.site_b, ml_threshold="0.8"), {self.ml_b.article_id}
		)

	def test_naming_a_visible_subject_still_scopes(self):
		self.assertEqual(
			self._ids(self.site_a, subject_id=self.sa.id, relevant="true"),
			{a.article_id for a in self._expected()[(self.site_a, "api")]},
		)

	def test_naming_a_subject_outside_the_scope_does_not_widen_it(self):
		"""subject_id=B on site A still lists A's articles tagged with B, but
		B's verdicts and consensus are not A's to read: nothing is relevant."""
		self.assertEqual(self._ids(self.site_a, subject_id=self.sb.id), self.everything)
		for rule in ("api", "editorial"):
			with self.subTest(rule=rule):
				self.assertEqual(
					self._ids(
						self.site_a, subject_id=self.sb.id, relevant="true", relevance_rule=rule
					),
					set(),
				)
				self.assertEqual(
					self._ids(
						self.site_a, subjects_any=f"{self.sa.id},{self.sb.id}",
						relevant="true", relevance_rule=rule,
					),
					{a.article_id for a in self._expected()[(self.site_a, rule)]},
				)

	# A real cache, so a stats cache key that ignored the site would hand one
	# site the other's count and fail here.
	@override_settings(
		CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
	)
	def test_stats_relevant_count_counts_only_visible_subjects(self):
		for (site, rule), wanted in self._expected().items():
			with self.subTest(site=site.domain, rule=rule):
				response = self.client.get(
					"/articles/stats/", {"site_id": site.id, "relevance_rule": rule}
				)
				self.assertEqual(response.status_code, status.HTTP_200_OK)
				self.assertEqual(response.data["total"], len(self.everything))
				self.assertEqual(response.data["relevant"], len(wanted))

	def test_without_a_visibility_scope_relevance_stays_unscoped(self):
		"""Code paths that bypass the middleware (no visible_subject_ids on
		the request) keep the old behaviour, as SubjectVisibilityMixin does."""
		request = SimpleNamespace(GET=QueryDict("relevant=true"))
		article_filter = ArticleFilter(
			request.GET, queryset=Articles.objects.all(), request=request
		)
		self.assertIsNone(article_filter._relevance_subject_ids())
		got = set(
			article_filter.filter_relevant(Articles.objects.all(), "relevant", True)
			.values_list("article_id", flat=True)
		) & self.everything
		self.assertEqual(
			got,
			{
				a.article_id
				for a in (self.ml_a, self.editor_a, self.ml_b, self.editor_b, self.mixed)
			},
		)

	def test_relevance_subject_ids_intersects_named_with_visible(self):
		cases = (
			("", {self.sa.id}, [self.sa.id]),
			(f"subject_id={self.sb.id}", {self.sa.id}, []),
			(f"subjects_any={self.sa.id},{self.sb.id}", {self.sa.id}, [self.sa.id]),
			(f"subject_id={self.sa.id}", {self.sa.id, self.sb.id}, [self.sa.id]),
			("", set(), []),
		)
		for query, visible, expected in cases:
			with self.subTest(query=query, visible=visible):
				request = SimpleNamespace(GET=QueryDict(query), visible_subject_ids=visible)
				article_filter = ArticleFilter(
					request.GET, queryset=Articles.objects.none(), request=request
				)
				self.assertEqual(article_filter._relevance_subject_ids(), expected)
