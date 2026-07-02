import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

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
		mock_ref.objects.all.return_value.delete.assert_called_once()


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
