import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from io import StringIO

from django.core.management import call_command
from django.test import TestCase


class DetectTrialAcronymsTest(TestCase):
	"""Acronym detection: an adjacent mention, one subject-sharing trial, no existing row."""

	def setUp(self):
		from organizations.models import Organization

		from gregory.models import Subject, Team

		org = Organization.objects.create(name="Acr Org", slug="acr-org")
		team = Team.objects.create(organization=org, name="Acr Team", slug="acr-team")
		self.ms = Subject.objects.create(subject_name="MS", subject_slug="ms", team=team)
		self.pd = Subject.objects.create(subject_name="PD", subject_slug="pd", team=team)
		self._n = 0

	def _trial(self, acronym, subject, title="A trial", **extra):
		from gregory.models import Trials

		trial = Trials.objects.create(
			title=title, acronym=acronym, link="https://example.org/t", **extra
		)
		trial.subjects.add(subject)
		return trial

	def _article(self, title, subject, summary=""):
		from gregory.models import Articles

		self._n += 1
		article = Articles.objects.create(
			title=title, summary=summary, link=f"https://example.org/a/{self._n}"
		)
		article.subjects.add(subject)
		return article

	def _rows(self, **filters):
		from gregory.models import ArticleTrialReference

		return ArticleTrialReference.objects.filter(**filters)

	def _run(self, *args, **kwargs):
		out = StringIO()
		call_command("detect_trial_references", *args, stdout=out, **kwargs)
		return out.getvalue()

	def test_adjacent_mention_with_shared_subject_creates_one_unconfirmed_row(self):
		trial = self._trial("CLARITY", self.ms)
		article = self._article("Results of the CLARITY trial", self.ms)
		self._run()
		row = self._rows(article=article, trial=trial).get()
		self.assertEqual(row.identifier_type, "acronym")
		self.assertEqual(row.identifier_value, "CLARITY")
		self.assertEqual(row.source, "auto")
		self.assertFalse(row.confirmed)
		self.assertFalse(row.suppressed)

	def test_no_link_when_the_mention_is_not_adjacent(self):
		self._trial("CLARITY", self.ms)
		self._article("CLARITY was measured in patients", self.ms)
		self._run()
		self.assertEqual(self._rows().count(), 0)

	def test_no_link_when_no_subject_is_shared(self):
		self._trial("CLARITY", self.pd)
		self._article("Results of the CLARITY trial", self.ms)
		self._run()
		self.assertEqual(self._rows().count(), 0)

	def test_no_link_when_the_acronym_is_too_common(self):
		self._trial("CLARITY", self.ms)
		first = self._article("Results of the CLARITY trial", self.ms)
		self._article("Another CLARITY study", self.ms)
		out = self._run("--acronym-max-articles", "1")
		self.assertEqual(self._rows().count(), 0)
		self.assertIn("1 acronyms skipped as too common", out)
		# Raising the cap links both.
		self._run("--acronym-max-articles", "2")
		self.assertEqual(self._rows().count(), 2)
		self.assertTrue(self._rows(article=first).exists())

	def test_the_count_comes_from_the_whole_corpus_even_with_article_id(self):
		self._trial("CLARITY", self.ms)
		first = self._article("Results of the CLARITY trial", self.ms)
		self._article("Another CLARITY study", self.ms)
		self._run("--article-id", str(first.pk), "--acronym-max-articles", "1")
		self.assertEqual(self._rows().count(), 0)
		self._run("--article-id", str(first.pk), "--acronym-max-articles", "2")
		self.assertEqual(set(self._rows().values_list("article_id", flat=True)), {first.pk})

	def test_two_trials_with_the_acronym_both_sharing_the_subject_is_ambiguous(self):
		self._trial("CLARITY", self.ms)
		self._trial("CLARITY", self.ms)
		self._article("Results of the CLARITY trial", self.ms)
		out = self._run()
		self.assertEqual(self._rows().count(), 0)
		self.assertIn("1 mentions skipped as ambiguous", out)

	def test_only_the_trial_sharing_the_subject_is_linked(self):
		right = self._trial("CLARITY", self.ms)
		self._trial("CLARITY", self.pd)
		article = self._article("Results of the CLARITY trial", self.ms)
		self._run()
		self.assertEqual(
			set(self._rows().values_list("article_id", "trial_id")), {(article.pk, right.pk)}
		)

	def test_a_pair_already_linked_by_identifier_gets_no_acronym_row(self):
		trial = self._trial("CLARITY", self.ms, identifiers={"nct": "NCT04578639"})
		article = self._article("The CLARITY trial NCT04578639", self.ms)
		self._run()
		types = list(
			self._rows(article=article, trial=trial).values_list("identifier_type", flat=True)
		)
		self.assertEqual(types, ["nct"])

	def test_existing_rows_of_any_kind_block_an_acronym_row(self):
		from gregory.models import ArticleTrialReference

		for kwargs in ({"suppressed": True}, {"source": "manual"}, {"identifier_type": "nct"}):
			with self.subTest(kwargs=kwargs):
				ArticleTrialReference.objects.all().delete()
				trial = self._trial("CLARITY", self.ms)
				article = self._article("The CLARITY trial", self.ms)
				fields = {
					"article": article,
					"trial": trial,
					"identifier_type": "nct",
					"identifier_value": "NCT04578639",
				}
				fields.update(kwargs)
				ArticleTrialReference.objects.create(**fields)
				self._run()
				self.assertEqual(self._rows(article=article, trial=trial).count(), 1)
				self.assertFalse(self._rows(identifier_type="acronym").exists())

	def test_second_run_creates_nothing(self):
		self._trial("CLARITY", self.ms)
		self._article("Results of the CLARITY trial", self.ms)
		self._run()
		out = self._run()
		self.assertEqual(self._rows().count(), 1)
		self.assertIn("Created 0 article-trial links", out)

	def test_dry_run_writes_nothing_and_reports_the_same_count(self):
		self._trial("CLARITY", self.ms)
		self._article("Results of the CLARITY trial", self.ms)
		out = self._run("--dry-run")
		self.assertEqual(self._rows().count(), 0)
		self.assertIn("Would have created 1 article-trial links", out)
		self.assertIn("Acronym links: 1", out)

	def test_skip_acronyms_creates_none(self):
		self._trial("CLARITY", self.ms)
		self._article("Results of the CLARITY trial", self.ms)
		self._run("--skip-acronyms")
		self.assertEqual(self._rows().count(), 0)

	def test_limit_skips_the_acronym_pass_and_says_so(self):
		self._trial("CLARITY", self.ms)
		self._article("Results of the CLARITY trial", self.ms)
		out = self._run("--limit", "5")
		self.assertEqual(self._rows().count(), 0)
		self.assertIn("Acronym pass skipped", out)

	def test_trial_id_writes_only_that_trials_acronym(self):
		wanted = self._trial("CLARITY", self.ms)
		self._trial("TEMSO", self.ms)
		self._article("Results of the CLARITY trial", self.ms)
		self._article("The TEMSO study", self.ms)
		self._run("--trial-id", str(wanted.pk))
		self.assertEqual(set(self._rows().values_list("trial_id", flat=True)), {wanted.pk})

	def test_reset_removes_an_unconfirmed_acronym_row_and_keeps_a_confirmed_one(self):
		self._trial("CLARITY", self.ms)
		self._trial("TEMSO", self.ms)
		self._article("Results of the CLARITY trial", self.ms)
		kept_article = self._article("The TEMSO study", self.ms)
		self._run()
		self.assertEqual(self._rows().count(), 2)
		self._rows(article=kept_article).update(confirmed=True)

		# A dry-run reset previews the same outcome: the removable row is not
		# counted as existing, so it would be created again.
		out = self._run("--reset", "--dry-run")
		self.assertIn("Would delete 1 existing references", out)
		self.assertIn("Would have created 1 article-trial links", out)
		self.assertEqual(self._rows().count(), 2)

		self._run("--reset")
		self.assertEqual(self._rows().count(), 2)
		self.assertTrue(self._rows(article=kept_article, confirmed=True).exists())

	def test_an_ordinary_run_never_deletes_an_acronym_row(self):
		trial = self._trial("CLARITY", self.ms)
		self._article("Results of the CLARITY trial", self.ms)
		self._run()
		trial.acronym = "RENAMED"
		trial.save()
		self._run("--acronym-max-articles", "0")
		self.assertEqual(self._rows(identifier_type="acronym").count(), 1)
