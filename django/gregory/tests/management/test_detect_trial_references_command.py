import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from io import StringIO

from django.core.management import call_command
from django.test import TestCase


class DetectTrialReferencesCommandTest(TestCase):
	"""Options and the full-scan behaviour, against the real database."""

	def setUp(self):
		from gregory.models import Articles, Trials

		self.trial = Trials.objects.create(
			title="Scan trial",
			identifiers={"nct": "NCT04578639"},
			link="https://clinicaltrials.gov/study/NCT04578639",
		)
		self.other_trial = Trials.objects.create(
			title="Other trial",
			identifiers={"nct": "NCT01234567"},
			link="https://clinicaltrials.gov/study/NCT01234567",
		)
		self.article = Articles.objects.create(
			title="Cites one trial",
			summary="Registered as NCT04578639.",
			link="https://pubmed.ncbi.nlm.nih.gov/10/",
		)
		self.other_article = Articles.objects.create(
			title="Cites the other trial",
			summary="Registered as NCT01234567.",
			link="https://pubmed.ncbi.nlm.nih.gov/11/",
		)

	def _pairs(self):
		from gregory.models import ArticleTrialReference

		return set(ArticleTrialReference.objects.values_list("article_id", "trial_id"))

	def test_article_and_trial_id_options_restrict_the_scan(self):
		call_command(
			"detect_trial_references",
			"--article-id",
			str(self.article.pk),
			"--trial-id",
			str(self.trial.pk),
			stdout=StringIO(),
		)
		self.assertEqual(self._pairs(), {(self.article.pk, self.trial.pk)})

	def test_limit_scans_only_that_many_articles(self):
		call_command("detect_trial_references", "--limit", "1", stdout=StringIO())
		self.assertEqual(len(self._pairs()), 1)

	def test_dry_run_with_options_creates_nothing(self):
		out = StringIO()
		call_command(
			"detect_trial_references",
			"--article-id",
			str(self.article.pk),
			"--trial-id",
			str(self.trial.pk),
			"--limit",
			"5",
			"--dry-run",
			stdout=out,
		)
		self.assertEqual(self._pairs(), set())
		self.assertIn("Would have created 1 article-trial links", out.getvalue())

	def test_reset_rebuilds_the_auto_rows(self):
		from gregory.models import ArticleTrialReference

		call_command("detect_trial_references", stdout=StringIO())
		self.assertEqual(len(self._pairs()), 2)
		call_command("detect_trial_references", "--reset", stdout=StringIO())
		# Deleted, then rebuilt by the scan that follows.
		self.assertEqual(ArticleTrialReference.objects.count(), 2)

	def test_recent_limits_the_scan_to_the_window(self):
		from datetime import timedelta

		from django.utils import timezone

		from gregory.models import Articles

		Articles.objects.filter(pk=self.other_article.pk).update(
			discovery_date=timezone.now() - timedelta(days=90)
		)
		call_command("detect_trial_references", "--recent", stdout=StringIO())
		self.assertEqual(self._pairs(), {(self.article.pk, self.trial.pk)})


class DetectTrialReferencesFullScanTest(TestCase):
	"""No window: every article is scanned, with or without a summary."""

	def setUp(self):
		from datetime import timedelta

		from django.utils import timezone

		from gregory.models import Articles, Trials

		self.trial = Trials.objects.create(
			title="New trial",
			identifiers={"nct": "NCT04578639"},
			link="https://clinicaltrials.gov/study/NCT04578639",
		)
		self.old_article = Articles.objects.create(
			title="An old paper",
			summary="Registered as NCT04578639.",
			link="https://pubmed.ncbi.nlm.nih.gov/20/",
		)
		Articles.objects.filter(pk=self.old_article.pk).update(
			discovery_date=timezone.now() - timedelta(days=90)
		)
		self.title_only = Articles.objects.create(
			title="Results of NCT04578639",
			summary=None,
			link="https://pubmed.ncbi.nlm.nih.gov/21/",
		)

	def test_old_article_is_linked_to_a_new_trial_with_no_options(self):
		from gregory.models import ArticleTrialReference

		call_command("detect_trial_references", stdout=StringIO())
		self.assertTrue(
			ArticleTrialReference.objects.filter(
				article=self.old_article, trial=self.trial, identifier_type="nct"
			).exists()
		)

	def test_id_in_the_title_with_no_summary_is_linked(self):
		from gregory.models import ArticleTrialReference

		call_command("detect_trial_references", stdout=StringIO())
		self.assertTrue(
			ArticleTrialReference.objects.filter(
				article=self.title_only, trial=self.trial
			).exists()
		)

	def test_second_run_creates_nothing_and_reports_the_pairs_as_linked(self):
		from gregory.models import ArticleTrialReference

		call_command("detect_trial_references", stdout=StringIO())
		self.assertEqual(ArticleTrialReference.objects.count(), 2)

		out = StringIO()
		call_command("detect_trial_references", stdout=out)
		self.assertEqual(ArticleTrialReference.objects.count(), 2)
		self.assertIn("Created 0 article-trial links", out.getvalue())
		self.assertIn("2 pairs already linked", out.getvalue())

	def test_summary_reports_skipped_pairs(self):
		from gregory.models import ArticleTrialReference

		ArticleTrialReference.objects.create(
			article=self.old_article,
			trial=self.trial,
			identifier_type="nct",
			identifier_value="NCT04578639",
			suppressed=True,
		)
		out = StringIO()
		call_command("detect_trial_references", stdout=out)
		self.assertIn("1 pairs skipped (suppressed or manual)", out.getvalue())

	def test_per_link_lines_only_at_verbosity_2(self):
		quiet = StringIO()
		call_command("detect_trial_references", "--dry-run", stdout=quiet)
		self.assertNotIn("Would create:", quiet.getvalue())

		loud = StringIO()
		call_command("detect_trial_references", "--dry-run", verbosity=2, stdout=loud)
		self.assertIn("Would create: Article", loud.getvalue())


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
