"""Tests for refresh_substance_keys and for the data migration that fills the column."""

import importlib
from io import StringIO

from django.apps import apps
from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from gregory.models import Medicine, MedicineEvent, Regulator


def make(name, substances, regulator=Regulator.EMA, **extra):
	if regulator == Regulator.EMA:
		extra.setdefault("ema_product_number", f"EMEA/H/C/{name}")
	else:
		extra.setdefault("fda_application_number", f"NDA{name}")
	return Medicine.objects.create(
		name=name, regulator=regulator, substances=substances, status="Authorised", **extra
	)


def refresh(**options):
	out = StringIO()
	call_command("refresh_substance_keys", stdout=out, **options)
	return out.getvalue()


class RefreshSubstanceKeysTests(TestCase):
	def test_fills_every_row_and_reports_the_counts(self):
		gilenya_ema = make("Gilenya", ["fingolimod"])
		gilenya_fda = make("GILENYA", ["fingolimod hydrochloride"], Regulator.FDA)
		nothing = make("Nothing", [])
		output = refresh()
		self.assertIn("3 checked, 3 substance keys changed", output)
		for medicine, expected in (
			(gilenya_ema, "fingolimod"),
			(gilenya_fda, "fingolimod"),
			(nothing, f"medicine-{nothing.pk}"),
		):
			medicine.refresh_from_db()
			self.assertEqual(medicine.substance_key, expected)

	def test_second_run_changes_nothing(self):
		make("Gilenya", ["fingolimod"])
		make("Nothing", [])
		refresh()
		synced = dict(Medicine.objects.values_list("name", "last_synced_at"))
		output = refresh()
		self.assertIn("2 checked, 0 substance keys changed, 2 already current", output)
		self.assertEqual(dict(Medicine.objects.values_list("name", "last_synced_at")), synced)

	def test_writes_only_the_rows_that_change(self):
		current = make("Current", ["fingolimod"], substance_key="fingolimod")
		stale = make("Stale", ["donepezil hydrochloride"], substance_key="donepezil hydrochloride")
		with CaptureQueriesContext(connection) as queries:
			refresh()
		# One read of the table and one UPDATE, for the stale row only.
		updates = [q["sql"] for q in queries if q["sql"].startswith("UPDATE")]
		self.assertEqual(len(updates), 1)
		self.assertIn(f"IN ({stale.pk})", updates[0])
		current.refresh_from_db()
		stale.refresh_from_db()
		self.assertEqual(current.substance_key, "fingolimod")
		self.assertEqual(stale.substance_key, "donepezil")

	def test_dry_run_reports_and_writes_nothing(self):
		medicine = make("Gilenya", ["fingolimod hydrochloride"])
		output = refresh(dry_run=True)
		self.assertIn("1 substance keys would change", output)
		self.assertIn("Dry run: nothing was written", output)
		medicine.refresh_from_db()
		self.assertEqual(medicine.substance_key, "")

	def test_a_new_rule_reaches_unchanged_rows_without_events_or_a_new_sync_time(self):
		medicine = make("Aricept", ["donepezil hydrochloride"], substance_key="old rule")
		synced = medicine.last_synced_at
		refresh()
		medicine.refresh_from_db()
		self.assertEqual(medicine.substance_key, "donepezil")
		self.assertEqual(medicine.last_synced_at, synced)
		self.assertFalse(MedicineEvent.objects.exists())

	def test_the_command_is_in_the_pipeline_after_the_medicine_syncs(self):
		from unittest.mock import patch

		called = []
		with patch(
			"gregory.management.commands.pipeline.call_command",
			side_effect=lambda command, **kwargs: called.append(command),
		):
			call_command("pipeline", stdout=StringIO(), stderr=StringIO())
		self.assertIn("refresh_substance_keys", called)
		self.assertGreater(called.index("refresh_substance_keys"), called.index("sync_ema_medicines"))
		self.assertGreater(called.index("refresh_substance_keys"), called.index("sync_fda_medicines"))


class FillKeysMigrationTests(TestCase):
	def test_the_data_migration_fills_the_keys(self):
		migration = importlib.import_module("gregory.migrations.0132_fill_medicine_substance_key")
		gilenya = make("Gilenya", ["fingolimod hydrochloride"])
		nothing = make("Nothing", [])
		current = make("Current", ["fingolimod"], substance_key="fingolimod")
		migration.fill_keys(apps, None)
		for medicine, expected in (
			(gilenya, "fingolimod"),
			(nothing, f"medicine-{nothing.pk}"),
			(current, "fingolimod"),
		):
			medicine.refresh_from_db()
			self.assertEqual(medicine.substance_key, expected)
