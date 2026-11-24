"""The two relevance rules: the API's and the editorial one.

api.filters.relevant_articles_q has two modes:

- API rule (``/articles/?relevant=true|false``, editor_verdict_wins=False):
  relevant by a human OR by AI. An editor's "not relevant" does not hide an
  ML "yes" -- deliberate, the frontend decides how to use the API.
- Editorial rule (default, used by relevant-only sitemaps and RSS feeds): for
  each subject an editor's is_relevant=True or False wins, and ML consensus
  decides only when there is no verdict.

Either way an article is relevant when it is relevant for at least one subject
in play, and ``relevant=false`` is the exact complement of ``relevant=true``.
"""

from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from api.filters import ml_relevant_articles_q, relevant_articles_q
from api.tests.visibility_helpers import publish_subjects
from gregory.models import (
	Articles,
	ArticleSubjectRelevance,
	MLPredictions,
	Organization,
	OrganizationApiSettings,
	Subject,
	Team,
)


class RelevanceRulesTests(TestCase):
	@classmethod
	def setUpTestData(cls):
		cls.org = Organization.objects.create(
			name="Override Org", slug="override-org"
		)
		OrganizationApiSettings.objects.filter(organization=cls.org).update(
			make_api_public=True
		)
		cls.team = Team.objects.create(
			name="Override Team", slug="override-team", organization=cls.org
		)
		# "any": one model reaching the threshold is enough.
		cls.s1 = Subject.objects.create(
			subject_name="Override S1",
			subject_slug="override-s1",
			team=cls.team,
			auto_predict=True,
			ml_consensus_type="any",
		)
		cls.s2 = Subject.objects.create(
			subject_name="Override S2",
			subject_slug="override-s2",
			team=cls.team,
			auto_predict=True,
			ml_consensus_type="any",
		)
		publish_subjects(cls.s1, cls.s2, organization=cls.org)

		def make(title, ml_for=(), verdicts=()):
			article = Articles.objects.create(
				title=title, link=f"https://example.com/override/{title}"
			)
			article.subjects.add(cls.s1, cls.s2)
			article.teams.add(cls.team)
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

		cls.ml_only = make("ml-only", ml_for=[cls.s1])
		cls.ml_yes_editor_no = make(
			"ml-yes-editor-no", ml_for=[cls.s1], verdicts=[(cls.s1, False)]
		)
		cls.ml_yes_s1_editor_no_s1_yes_s2 = make(
			"ml-yes-editor-no-s1-yes-s2",
			ml_for=[cls.s1],
			verdicts=[(cls.s1, False), (cls.s2, True)],
		)
		cls.ml_yes_s1_editor_no_s2 = make(
			"ml-yes-s1-editor-no-s2", ml_for=[cls.s1], verdicts=[(cls.s2, False)]
		)
		cls.editor_yes_ml_no = make("editor-yes-ml-no", verdicts=[(cls.s1, True)])
		cls.editor_no_ml_no = make("editor-no-ml-no", verdicts=[(cls.s1, False)])
		# A reviewed-but-undecided row (NULL) is "no verdict": ML decides.
		cls.ml_yes_unreviewed = make(
			"ml-yes-unreviewed", ml_for=[cls.s1], verdicts=[(cls.s1, None)]
		)
		cls.nothing = make("nothing")

		cls.all_articles = [
			cls.ml_only,
			cls.ml_yes_editor_no,
			cls.ml_yes_s1_editor_no_s1_yes_s2,
			cls.ml_yes_s1_editor_no_s2,
			cls.editor_yes_ml_no,
			cls.editor_no_ml_no,
			cls.ml_yes_unreviewed,
			cls.nothing,
		]

	def setUp(self):
		self.client = APIClient()

	def _ids(self, query):
		response = self.client.get(f"/articles/?{query}")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		return {r["article_id"] for r in response.data["results"]}

	def _expect(self, query, included):
		"""API: relevant=true returns exactly `included`; relevant=false returns
		the rest of the fixture (the exact complement)."""
		everything = {a.article_id for a in self.all_articles}
		wanted = {a.article_id for a in included}
		sep = "&" if query else ""
		self.assertEqual(self._ids(f"{query}{sep}relevant=true") & everything, wanted)
		self.assertEqual(
			self._ids(f"{query}{sep}relevant=false") & everything, everything - wanted
		)

	def _expect_editorial(self, subject_ids, included):
		"""Editorial rule via relevant_articles_q's default mode."""
		everything = {a.article_id for a in self.all_articles}
		ids = set(
			Articles.objects.filter(
				relevant_articles_q(subject_ids=subject_ids)
			).values_list("article_id", flat=True)
		)
		self.assertEqual(ids & everything, {a.article_id for a in included})

	# --- API rule: human OR AI ---

	def test_api_unscoped(self):
		self._expect(
			"",
			[
				self.ml_only,
				self.ml_yes_editor_no,  # ML yes is enough; the editor's no doesn't hide it
				self.ml_yes_s1_editor_no_s1_yes_s2,
				self.ml_yes_s1_editor_no_s2,
				self.editor_yes_ml_no,
				self.ml_yes_unreviewed,
			],
		)

	def test_api_scoped_to_s1(self):
		self._expect(
			f"subject_id={self.s1.id}",
			[
				self.ml_only,
				self.ml_yes_editor_no,
				self.ml_yes_s1_editor_no_s1_yes_s2,
				self.ml_yes_s1_editor_no_s2,
				self.editor_yes_ml_no,
				self.ml_yes_unreviewed,
			],
		)

	def test_api_scoped_to_s2(self):
		self._expect(
			f"subject_id={self.s2.id}",
			[self.ml_yes_s1_editor_no_s1_yes_s2],  # only an editor yes for S2
		)

	def test_api_scoped_via_subjects_any(self):
		self._expect(
			f"subjects_any={self.s1.id},{self.s2.id}",
			[
				self.ml_only,
				self.ml_yes_editor_no,
				self.ml_yes_s1_editor_no_s1_yes_s2,
				self.ml_yes_s1_editor_no_s2,
				self.editor_yes_ml_no,
				self.ml_yes_unreviewed,
			],
		)

	def test_api_editor_no_does_not_hide_ml_yes(self):
		self.assertIn(self.ml_yes_editor_no.article_id, self._ids("relevant=true"))
		self.assertNotIn(
			self.ml_yes_editor_no.article_id, self._ids("relevant=false")
		)

	# --- Editorial rule: the editor's verdict wins per subject ---

	def test_editorial_unscoped(self):
		self._expect_editorial(
			None,
			[
				self.ml_only,  # ML yes S1, no verdict
				self.ml_yes_s1_editor_no_s1_yes_s2,  # editor yes S2 carries it
				self.ml_yes_s1_editor_no_s2,  # editor no S2 is irrelevant to S1's ML
				self.editor_yes_ml_no,
				self.ml_yes_unreviewed,
			],
		)

	def test_editorial_scoped_to_s1(self):
		self._expect_editorial(
			[self.s1.id],
			[
				self.ml_only,
				self.ml_yes_s1_editor_no_s2,  # editor no for S2 only
				self.editor_yes_ml_no,
				self.ml_yes_unreviewed,
			],
		)

	def test_editorial_scoped_to_s2(self):
		self._expect_editorial(
			[self.s2.id], [self.ml_yes_s1_editor_no_s1_yes_s2]
		)

	def test_editorial_scoped_to_both(self):
		self._expect_editorial(
			[self.s1.id, self.s2.id],
			[
				self.ml_only,
				self.ml_yes_s1_editor_no_s1_yes_s2,
				self.ml_yes_s1_editor_no_s2,
				self.editor_yes_ml_no,
				self.ml_yes_unreviewed,
			],
		)

	def test_ml_threshold_still_reports_what_ml_says(self):
		"""ml_threshold is "what the models say", not "relevant": editor
		verdicts never apply to it."""
		self.assertIn(
			self.ml_yes_editor_no.article_id, self._ids("ml_threshold=0.5")
		)

	def test_ml_relevant_articles_q_default_ignores_editor_verdicts(self):
		"""The pure ML rule, shared with Articles.is_ml_relevant_* and the
		stored-column recompute, is unchanged by default."""
		ids = set(
			Articles.objects.filter(
				ml_relevant_articles_q(subject_ids=[self.s1.id])
			).values_list("article_id", flat=True)
		)
		self.assertIn(self.ml_yes_editor_no.article_id, ids)
		ids_override = set(
			Articles.objects.filter(
				ml_relevant_articles_q(
					subject_ids=[self.s1.id], exclude_manual_irrelevant=True
				)
			).values_list("article_id", flat=True)
		)
		self.assertNotIn(self.ml_yes_editor_no.article_id, ids_override)
		self.assertIn(self.ml_only.article_id, ids_override)

	def test_relevant_articles_q_does_not_duplicate_rows(self):
		"""An article with verdicts on both subjects must appear once."""
		for editor_verdict_wins in (True, False):
			pks = list(
				Articles.objects.filter(
					relevant_articles_q(editor_verdict_wins=editor_verdict_wins)
				).values_list("article_id", flat=True)
			)
			self.assertEqual(len(pks), len(set(pks)))

	# --- relevance_rule=editorial: the editorial rule, opted into on the API ---

	def _editorial_ids(self, subject_ids):
		return {
			a
			for a in Articles.objects.filter(
				relevant_articles_q(subject_ids=subject_ids)
			).values_list("article_id", flat=True)
		} & {a.article_id for a in self.all_articles}

	def test_relevance_rule_editorial_matches_the_editorial_rule(self):
		everything = {a.article_id for a in self.all_articles}
		cases = (
			("", None),
			(f"subject_id={self.s1.id}", [self.s1.id]),
			(f"subject_id={self.s2.id}", [self.s2.id]),
			(f"subjects_any={self.s1.id},{self.s2.id}", [self.s1.id, self.s2.id]),
		)
		for scope, subject_ids in cases:
			with self.subTest(scope=scope):
				sep = "&" if scope else ""
				wanted = self._editorial_ids(subject_ids)
				got = self._ids(f"{scope}{sep}relevant=true&relevance_rule=editorial")
				self.assertEqual(got & everything, wanted)
				# relevant=false stays the exact complement under the same rule.
				rest = self._ids(f"{scope}{sep}relevant=false&relevance_rule=editorial")
				self.assertEqual(rest & everything, everything - wanted)

	def test_relevance_rule_editorial_hides_an_editor_rejection(self):
		query = f"subject_id={self.s1.id}&relevant=true"
		self.assertIn(self.ml_yes_editor_no.article_id, self._ids(query))
		self.assertNotIn(
			self.ml_yes_editor_no.article_id,
			self._ids(f"{query}&relevance_rule=editorial"),
		)

	def test_relevance_rule_api_is_the_default(self):
		for scope in ("", f"subject_id={self.s1.id}&"):
			with self.subTest(scope=scope):
				self.assertEqual(
					self._ids(f"{scope}relevant=true&relevance_rule=api"),
					self._ids(f"{scope}relevant=true"),
				)

	def test_relevance_rule_alone_filters_nothing(self):
		self.assertEqual(self._ids("relevance_rule=editorial"), self._ids(""))

	def test_relevance_rule_invalid_value_is_400(self):
		for path in ("/articles/", "/articles/stats/"):
			for value in ("strict", "EDITORIAL", "true"):
				with self.subTest(path=path, value=value):
					response = self.client.get(
						path, {"relevant": "true", "relevance_rule": value}
					)
					self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
					self.assertIn("relevance_rule", response.data)

	# A real cache, so a stats cache key that ignored relevance_rule would
	# hand the editorial request the api count and fail here.
	@override_settings(
		CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
	)
	def test_relevance_rule_sets_the_stats_relevant_count(self):
		scope = {"subject_id": self.s1.id}
		api = self.client.get("/articles/stats/", scope)
		editorial = self.client.get(
			"/articles/stats/", {**scope, "relevance_rule": "editorial"}
		)
		self.assertEqual(api.status_code, status.HTTP_200_OK)
		self.assertEqual(editorial.status_code, status.HTTP_200_OK)
		self.assertEqual(
			api.data["relevant"], len(self._ids(f"subject_id={self.s1.id}&relevant=true"))
		)
		self.assertEqual(editorial.data["relevant"], len(self._editorial_ids([self.s1.id])))
		self.assertLess(editorial.data["relevant"], api.data["relevant"])

	def test_relevance_rule_in_a_post_search_body(self):
		response = self.client.post(
			"/articles/search/",
			{
				"team_id": self.team.id,
				"subject_id": self.s1.id,
				"relevant": True,
				"relevance_rule": "editorial",
			},
			format="json",
		)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		got = {r["article_id"] for r in response.data["results"]}
		self.assertEqual(got, self._editorial_ids([self.s1.id]))

	def test_relevance_rule_in_a_get_search(self):
		response = self.client.get(
			"/articles/search/",
			{
				"team_id": self.team.id,
				"subject_id": self.s1.id,
				"relevant": "true",
				"relevance_rule": "editorial",
			},
		)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		got = {r["article_id"] for r in response.data["results"]}
		self.assertEqual(got, self._editorial_ids([self.s1.id]))

	def test_relevance_rule_invalid_value_is_400_on_search(self):
		params = {
			"team_id": self.team.id,
			"subject_id": self.s1.id,
			"relevant": "true",
			"relevance_rule": "strict",
		}
		get = self.client.get("/articles/search/", params)
		post = self.client.post("/articles/search/", params, format="json")
		self.assertEqual(get.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertEqual(post.status_code, status.HTTP_400_BAD_REQUEST)

	def test_ml_threshold_still_filters_on_the_models_under_editorial(self):
		"""Documented: ml_threshold is its own filter on what the models say,
		so with it an editor-only yes drops out under either rule."""
		query = f"subject_id={self.s1.id}&relevant=true&relevance_rule=editorial"
		self.assertIn(self.editor_yes_ml_no.article_id, self._ids(query))
		with_threshold = self._ids(f"{query}&ml_threshold=0.8")
		self.assertNotIn(self.editor_yes_ml_no.article_id, with_threshold)
		# The editor's no still wins over the ML yes.
		self.assertNotIn(self.ml_yes_editor_no.article_id, with_threshold)
		self.assertIn(self.ml_only.article_id, with_threshold)
