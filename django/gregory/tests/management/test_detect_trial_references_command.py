import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from unittest.mock import patch, MagicMock


class DetectTrialReferencesCommandTest(TestCase):
	@patch("gregory.management.commands.detect_trial_references.ArticleTrialReference")
	@patch("gregory.management.commands.detect_trial_references.Trials")
	@patch("gregory.management.commands.detect_trial_references.Articles")
	def test_handle_respects_options(self, mock_articles, mock_trials, mock_ref):
		qs = MagicMock()
		qs.filter.return_value = qs
		qs.exclude.return_value = qs
		qs.count.return_value = 0
		qs.__iter__.return_value = []
		mock_articles.objects.filter.return_value = qs
		mock_trials.objects.filter.return_value = qs
		mock_ref.objects.count.return_value = 0
		call_command(
			"detect_trial_references",
			"--article-id",
			"1",
			"--trial-id",
			"2",
			"--limit",
			"5",
			"--dry-run",
		)
		mock_articles.objects.filter.assert_any_call(article_id=1)
		mock_trials.objects.filter.assert_any_call(trial_id=2)

	@patch("gregory.management.commands.detect_trial_references.ArticleTrialReference")
	@patch("gregory.management.commands.detect_trial_references.Trials")
	@patch("gregory.management.commands.detect_trial_references.Articles")
	def test_reset_option_deletes_existing(self, mock_articles, mock_trials, mock_ref):
		qs = MagicMock()
		qs.filter.return_value = qs
		qs.exclude.return_value = qs
		qs.count.return_value = 0
		qs.__iter__.return_value = []
		mock_articles.objects.filter.return_value = qs
		mock_trials.objects.filter.return_value = qs
		mock_ref.objects.count.return_value = 0
		call_command("detect_trial_references", "--reset")
		# Only auto-detected, unsuppressed rows are reset (manual links and
		# unlinked ones are an editor's work).
		mock_ref.objects.filter.return_value.delete.assert_called_once()


class DetectTrialReferencesIntegrationTest(TestCase):
	"""Real-DB tests for the canonical-identifier matching path, covering the
	case that motivated the rewrite: an article citing a bare EudraCT number
	while the trial stores it as EUCTR<id>-<country>."""

	def setUp(self):
		from gregory.models import Articles, Trials

		self.trial = Trials.objects.create(
			title="OVERLORD-MS",
			identifiers={
				"nct": "NCT04578639",
				"euctr": "EUCTR2020-001205-23-NO",
			},
			link="https://clinicaltrials.gov/study/NCT04578639",
		)
		self.article = Articles.objects.create(
			title="Rituximab versus Ocrelizumab in Multiple Sclerosis",
			summary=(
				"OVERLORD-MS ClinicalTrials.gov number, NCT04578639; "
				"EudraCT number, 2020-001205-23."
			),
			link="https://pubmed.ncbi.nlm.nih.gov/1/",
		)

	def test_matches_nct_and_eudract_and_is_idempotent(self):
		from gregory.models import ArticleTrialReference

		call_command("detect_trial_references")
		refs = ArticleTrialReference.objects.filter(
			article=self.article, trial=self.trial
		)
		self.assertEqual(
			set(refs.values_list("identifier_type", "identifier_value")),
			{("nct", "NCT04578639"), ("eudract", "2020-001205-23")},
		)

		# Re-running must not create duplicates.
		call_command("detect_trial_references")
		self.assertEqual(
			ArticleTrialReference.objects.filter(
				article=self.article, trial=self.trial
			).count(),
			2,
		)

	def test_eudract_only_article_matches_via_euctr_key(self):
		"""Without the NCT sentence, the euctr-stored value must still match
		a bare EudraCT number in the article text."""
		from gregory.models import ArticleTrialReference

		self.article.summary = "EudraCT number, 2020-001205-23."
		self.article.save()

		call_command("detect_trial_references")
		refs = ArticleTrialReference.objects.filter(
			article=self.article, trial=self.trial
		)
		self.assertEqual(
			list(refs.values_list("identifier_type", "identifier_value")),
			[("eudract", "2020-001205-23")],
		)

	def test_dry_run_creates_nothing(self):
		from gregory.models import ArticleTrialReference

		call_command("detect_trial_references", "--dry-run")
		self.assertEqual(
			ArticleTrialReference.objects.filter(
				article=self.article, trial=self.trial
			).count(),
			0,
		)


class DetectTrialReferencesEditorLinksTest(TestCase):
	"""Links an editor added or removed survive detection and --reset."""

	def setUp(self):
		from django.contrib.auth import get_user_model

		from gregory.models import Articles, Trials

		self.user = get_user_model().objects.create_user("linker", "l@example.com", "x")
		self.trial = Trials.objects.create(
			title="Editor trial",
			identifiers={"nct": "NCT04578639", "euctr": "EUCTR2020-001205-23-NO"},
			link="https://clinicaltrials.gov/study/NCT04578639",
		)
		self.other_trial = Trials.objects.create(
			title="Unrelated trial", identifiers={"nct": "NCT01234567"}, link="https://t.test/2"
		)
		self.article = Articles.objects.create(
			title="Cites the trial",
			summary="ClinicalTrials.gov number, NCT04578639; EudraCT, 2020-001205-23.",
			link="https://pubmed.ncbi.nlm.nih.gov/2/",
		)

	def _manual(self, trial):
		from gregory.models import ArticleTrialReference

		return ArticleTrialReference.objects.create(
			article=self.article,
			trial=trial,
			identifier_type="manual",
			identifier_value="NCT01234567",
			source="manual",
			created_by=self.user,
		)

	def test_reset_keeps_manual_links(self):
		from gregory.models import ArticleTrialReference

		manual = self._manual(self.other_trial)
		call_command("detect_trial_references", stdout=StringIO())
		self.assertTrue(
			ArticleTrialReference.objects.filter(source="auto", trial=self.trial).exists()
		)

		call_command("detect_trial_references", "--reset", stdout=StringIO())

		self.assertTrue(ArticleTrialReference.objects.filter(pk=manual.pk).exists())
		# ...and the auto links were rebuilt from scratch.
		self.assertEqual(
			ArticleTrialReference.objects.filter(source="auto", trial=self.trial).count(), 2
		)

	def test_reset_removes_only_unsuppressed_auto_rows(self):
		from gregory.models import ArticleTrialReference

		auto = ArticleTrialReference.objects.create(
			article=self.article,
			trial=self.other_trial,
			identifier_type="nct_id",
			identifier_value="NCT01234567",
		)
		suppressed = ArticleTrialReference.objects.create(
			article=self.article,
			trial=self.trial,
			identifier_type="nct",
			identifier_value="NCT04578639",
			suppressed=True,
		)

		call_command("detect_trial_references", "--reset", "--dry-run", stdout=StringIO())
		self.assertTrue(ArticleTrialReference.objects.filter(pk=auto.pk).exists())

		call_command("detect_trial_references", "--reset", stdout=StringIO())

		self.assertFalse(ArticleTrialReference.objects.filter(pk=auto.pk).exists())
		self.assertTrue(ArticleTrialReference.objects.filter(pk=suppressed.pk).exists())

	def test_dry_run_reset_counts_only_what_it_would_delete(self):
		from gregory.models import ArticleTrialReference

		self._manual(self.other_trial)
		out = StringIO()
		call_command("detect_trial_references", "--reset", "--dry-run", stdout=out)
		self.assertIn("Would delete 0 existing references", out.getvalue())
		self.assertEqual(ArticleTrialReference.objects.count(), 1)

	def test_detection_respects_a_suppressed_pair_under_every_identifier(self):
		from gregory.models import ArticleTrialReference

		ArticleTrialReference.objects.create(
			article=self.article,
			trial=self.trial,
			identifier_type="nct",
			identifier_value="NCT04578639",
			suppressed=True,
		)

		call_command("detect_trial_references", stdout=StringIO())
		call_command("detect_trial_references", "--reset", stdout=StringIO())
		call_command("detect_trial_references", stdout=StringIO())

		rows = ArticleTrialReference.objects.filter(article=self.article, trial=self.trial)
		self.assertEqual(rows.count(), 1)
		self.assertTrue(rows.get().suppressed)

	def test_detection_does_not_duplicate_a_manual_link(self):
		from gregory.models import ArticleTrialReference

		self._manual(self.trial)

		call_command("detect_trial_references", stdout=StringIO())

		rows = ArticleTrialReference.objects.filter(article=self.article, trial=self.trial)
		self.assertEqual([r.source for r in rows], ["manual"])

	def test_new_rows_default_to_auto_and_visible(self):
		from gregory.models import ArticleTrialReference

		call_command("detect_trial_references", stdout=StringIO())

		for row in ArticleTrialReference.objects.all():
			self.assertEqual(row.source, "auto")
			self.assertFalse(row.suppressed)
			self.assertIsNone(row.created_by)
