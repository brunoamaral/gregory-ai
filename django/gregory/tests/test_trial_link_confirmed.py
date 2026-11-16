"""
The `confirmed` flag on an article-trial link: the service rules
(gregory.services.trial_links) and migration 0135.

pytest runs with --nomigrations, so the migration's function is called directly.
"""

import importlib

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase

from gregory.models import ArticleTrialReference, Articles, Trials
from gregory.services.trial_links import link_trial, remove_link, set_confirmed, unlink_trial

User = get_user_model()

migration = importlib.import_module("gregory.migrations.0135_article_trial_reference_confirmed")


class ConfirmedServiceTest(TestCase):
	def setUp(self):
		self.user = User.objects.create_user("editor", "e@example.com", "x")
		self.article = Articles.objects.create(title="A", link="https://a.test/1")
		self.trial = Trials.objects.create(
			title="T", link="https://t.test/1", identifiers={"nct": "NCT01234567"}
		)

	def _auto(self, identifier_type="nct", **extra):
		return ArticleTrialReference.objects.create(
			article=self.article,
			trial=self.trial,
			identifier_type=identifier_type,
			identifier_value="X",
			**extra,
		)

	def test_a_new_auto_row_is_unconfirmed(self):
		self.assertFalse(self._auto().confirmed)

	def test_manual_link_is_created_confirmed(self):
		reference, created = link_trial(self.article, self.trial, self.user)
		self.assertTrue(created)
		self.assertTrue(reference.confirmed)

	def test_linking_an_auto_pair_confirms_every_visible_row_and_creates_nothing(self):
		first, second = self._auto("nct"), self._auto("eudract")
		hidden = self._auto("isrctn", suppressed=True)

		reference, created = link_trial(self.article, self.trial, self.user)

		self.assertFalse(created)
		self.assertTrue(reference.confirmed)
		self.assertEqual(ArticleTrialReference.objects.count(), 3)
		for row in (first, second):
			row.refresh_from_db()
			self.assertTrue(row.confirmed)
		hidden.refresh_from_db()
		self.assertFalse(hidden.confirmed)

	def test_confirming_writes_a_history_row_only_for_a_changed_row(self):
		row = self._auto()
		before = ArticleTrialReference.history.filter(id=row.id).count()
		link_trial(self.article, self.trial, self.user)
		self.assertEqual(ArticleTrialReference.history.filter(id=row.id).count(), before + 1)
		# Already confirmed: nothing more to record.
		link_trial(self.article, self.trial, self.user)
		self.assertEqual(ArticleTrialReference.history.filter(id=row.id).count(), before + 1)

	def test_set_confirmed_both_ways_returns_the_rows_changed(self):
		first, second = self._auto("nct"), self._auto("eudract", confirmed=True)

		changed = set_confirmed(self.article, self.trial, True)
		self.assertEqual([r.pk for r in changed], [first.pk])

		changed = set_confirmed(self.article, self.trial, False)
		self.assertEqual({r.pk for r in changed}, {first.pk, second.pk})
		self.assertFalse(ArticleTrialReference.objects.filter(confirmed=True).exists())

		self.assertEqual(set_confirmed(self.article, self.trial, False), [])

	def test_set_confirmed_ignores_hidden_rows(self):
		hidden = self._auto(suppressed=True)
		self.assertEqual(set_confirmed(self.article, self.trial, True), [])
		hidden.refresh_from_db()
		self.assertFalse(hidden.confirmed)

	def test_a_manual_row_cannot_be_unconfirmed(self):
		reference, _ = link_trial(self.article, self.trial, self.user)
		self.assertEqual(set_confirmed(self.article, self.trial, False), [])
		reference.refresh_from_db()
		self.assertTrue(reference.confirmed)

	def test_hiding_an_auto_row_clears_the_flag(self):
		row = self._auto(confirmed=True)
		remove_link(row)
		row.refresh_from_db()
		self.assertTrue(row.suppressed)
		self.assertFalse(row.confirmed)

	def test_unlink_trial_clears_the_flag_on_every_auto_row(self):
		self._auto("nct", confirmed=True)
		self._auto("eudract", confirmed=True)
		unlink_trial(self.article, self.trial)
		self.assertFalse(ArticleTrialReference.objects.filter(confirmed=True).exists())
		self.assertEqual(ArticleTrialReference.objects.filter(suppressed=True).count(), 2)


class ConfirmManualLinksMigrationTest(TestCase):
	def test_manual_rows_end_up_confirmed_and_auto_rows_do_not(self):
		article = Articles.objects.create(title="A", link="https://a.test/m")
		trials = [Trials.objects.create(title=f"T{i}", link=f"https://t.test/m{i}") for i in range(3)]
		manual = ArticleTrialReference.objects.create(
			article=article, trial=trials[0], identifier_type="manual", identifier_value="X", source="manual"
		)
		auto = ArticleTrialReference.objects.create(
			article=article, trial=trials[1], identifier_type="nct", identifier_value="X"
		)
		hidden = ArticleTrialReference.objects.create(
			article=article, trial=trials[2], identifier_type="nct", identifier_value="X", suppressed=True
		)
		self.assertFalse(manual.confirmed)

		with connection.schema_editor() as schema_editor:
			migration.confirm_manual_links(apps, schema_editor)

		manual.refresh_from_db()
		auto.refresh_from_db()
		hidden.refresh_from_db()
		self.assertTrue(manual.confirmed)
		self.assertFalse(auto.confirmed)
		self.assertFalse(hidden.confirmed)
