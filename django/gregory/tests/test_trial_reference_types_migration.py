"""
Data migration 0134: auto-detected article-trial links carrying an identifier
type the current patterns never produce (dev has `irctn` and `euct`) are
relabelled, so a full detection scan does not add a second row per pair.

pytest runs with --nomigrations, so the migration's function is called
directly against the test schema.
"""

import importlib

from django.apps import apps
from django.db import connection
from django.test import TestCase

from gregory.models import ArticleTrialReference, Articles, Trials

migration = importlib.import_module(
	"gregory.migrations.0134_normalise_trial_reference_types"
)


def _run():
	with connection.schema_editor() as schema_editor:
		migration.normalise_trial_reference_types(apps, schema_editor)


class NormaliseTrialReferenceTypesTest(TestCase):
	def setUp(self):
		self.trial = Trials.objects.create(title="T", link="https://t.test/1")
		self.article = Articles.objects.create(title="A", link="https://a.test/1")

	def _row(self, identifier_type, identifier_value, **extra):
		return ArticleTrialReference.objects.create(
			article=self.article,
			trial=self.trial,
			identifier_type=identifier_type,
			identifier_value=identifier_value,
			**extra,
		)

	def test_old_label_is_relabelled_when_no_row_has_the_current_label(self):
		row = self._row("irctn", "irct2017081235678n1")

		_run()

		row.refresh_from_db()
		self.assertEqual(row.identifier_type, "irct")
		self.assertEqual(row.identifier_value, "IRCT2017081235678N1")

	def test_old_label_row_is_deleted_when_the_current_label_exists(self):
		current = self._row("ctis", "2020-001205-23-01")
		old = self._row("euct", "2020-001205-23-01")

		_run()

		self.assertTrue(ArticleTrialReference.objects.filter(pk=current.pk).exists())
		self.assertFalse(ArticleTrialReference.objects.filter(pk=old.pk).exists())

	def test_suppressed_old_label_row_is_kept_when_the_current_label_exists(self):
		self._row("ctis", "2020-001205-23-01")
		old = self._row("euct", "2020-001205-23-01", suppressed=True)

		_run()

		old.refresh_from_db()
		self.assertEqual(old.identifier_type, "euct")
		self.assertTrue(old.suppressed)

	def test_values_with_zero_or_several_identifiers_are_left_alone(self):
		none = self._row("odd_label", "not an identifier")
		several = self._row("other_label", "NCT04578639 and ISRCTN12345678")

		_run()

		none.refresh_from_db()
		several.refresh_from_db()
		self.assertEqual(none.identifier_type, "odd_label")
		self.assertEqual(several.identifier_type, "other_label")

	def test_current_labels_and_manual_rows_are_untouched(self):
		nct = self._row("nct", "NCT04578639")
		other_trial = Trials.objects.create(title="T2", link="https://t.test/2")
		manual = ArticleTrialReference.objects.create(
			article=self.article,
			trial=other_trial,
			identifier_type="manual",
			identifier_value="NCT01234567",
			source="manual",
		)

		_run()

		nct.refresh_from_db()
		manual.refresh_from_db()
		self.assertEqual(nct.identifier_type, "nct")
		self.assertEqual(manual.identifier_type, "manual")
		self.assertEqual(manual.identifier_value, "NCT01234567")
