"""An editor's verdict for a subject beats the ML consensus for that subject.

Rule under test (api.filters.relevant_articles_q, used by
``/articles/?relevant=true|false``): for each subject, an editor's
is_relevant=True or False wins; ML consensus decides only when there is no
verdict. An article is relevant when it is relevant for at least one subject
in play, and ``relevant=false`` is the exact complement.
"""

from django.test import TestCase
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


class RelevanceManualOverrideTests(TestCase):
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
		"""relevant=true returns exactly `included`; relevant=false returns the
		rest of the fixture (the exact complement)."""
		everything = {a.article_id for a in self.all_articles}
		wanted = {a.article_id for a in included}
		sep = "&" if query else ""
		self.assertEqual(self._ids(f"{query}{sep}relevant=true") & everything, wanted)
		self.assertEqual(
			self._ids(f"{query}{sep}relevant=false") & everything, everything - wanted
		)

	def test_unscoped(self):
		self._expect(
			"",
			[
				self.ml_only,  # ML yes S1, no verdict
				self.ml_yes_s1_editor_no_s1_yes_s2,  # editor yes S2 carries it
				self.ml_yes_s1_editor_no_s2,  # editor no S2 is irrelevant to S1's ML
				self.editor_yes_ml_no,
				self.ml_yes_unreviewed,
			],
		)

	def test_scoped_to_s1(self):
		self._expect(
			f"subject_id={self.s1.id}",
			[
				self.ml_only,
				self.ml_yes_s1_editor_no_s2,  # editor no for S2 only
				self.editor_yes_ml_no,
				self.ml_yes_unreviewed,
			],
		)

	def test_scoped_to_s2(self):
		self._expect(
			f"subject_id={self.s2.id}",
			[self.ml_yes_s1_editor_no_s1_yes_s2],  # only editor yes for S2
		)

	def test_scoped_via_subjects_any(self):
		self._expect(
			f"subjects_any={self.s1.id},{self.s2.id}",
			[
				self.ml_only,
				self.ml_yes_s1_editor_no_s1_yes_s2,
				self.ml_yes_s1_editor_no_s2,
				self.editor_yes_ml_no,
				self.ml_yes_unreviewed,
			],
		)

	def test_editor_no_beats_ml_yes_explicitly(self):
		self.assertNotIn(
			self.ml_yes_editor_no.article_id, self._ids("relevant=true")
		)
		self.assertIn(self.ml_yes_editor_no.article_id, self._ids("relevant=false"))

	def test_ml_threshold_still_reports_what_ml_says(self):
		"""ml_threshold is "what the models say", not "relevant": the override
		deliberately doesn't apply to it."""
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
		pks = list(
			Articles.objects.filter(relevant_articles_q()).values_list(
				"article_id", flat=True
			)
		)
		self.assertEqual(len(pks), len(set(pks)))
