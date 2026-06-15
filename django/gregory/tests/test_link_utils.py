"""
Unit tests for gregory.utils.registry_utils — identifiers_conflict truth table.

These tests have no database dependency.

Run:
  docker exec gregory python manage.py test gregory.tests.test_registry_utils
"""

import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from django.test import SimpleTestCase
from gregory.utils.registry_utils import _norm, identifiers_conflict


class NormTest(SimpleTestCase):
	def test_strips_and_uppercases(self):
		self.assertEqual(_norm(" nct001 "), "NCT001")
		self.assertEqual(_norm("NCT001"), "NCT001")
		self.assertEqual(_norm("nct001"), "NCT001")

	def test_handles_non_string(self):
		self.assertEqual(_norm(123), "123")


class IdentifiersConflictTest(SimpleTestCase):
	# ------------------------------------------------------------------ #
	# Same key, different value → conflict
	# ------------------------------------------------------------------ #

	def test_same_key_different_value_is_conflict(self):
		self.assertTrue(identifiers_conflict({"nct": "NCT001"}, {"nct": "NCT002"}))

	def test_same_key_different_value_case_insensitive_no_conflict(self):
		"""Values that normalise to the same string must NOT conflict."""
		self.assertFalse(identifiers_conflict({"nct": " nct001 "}, {"nct": "NCT001"}))

	def test_same_key_same_value_no_conflict(self):
		self.assertFalse(identifiers_conflict({"nct": "NCT001"}, {"nct": "NCT001"}))

	# ------------------------------------------------------------------ #
	# Disjoint keys → no conflict (cross-registry same study)
	# ------------------------------------------------------------------ #

	def test_disjoint_keys_no_conflict(self):
		"""nct vs euctr — different registries, could be the same study."""
		self.assertFalse(identifiers_conflict({"nct": "NCT001"}, {"euctr": "EUCTR001"}))

	def test_multiple_keys_no_shared_conflict(self):
		self.assertFalse(
			identifiers_conflict(
				{"nct": "NCT001", "euctr": "EUCTR001"},
				{"eudract": "2024-000001-01"},
			)
		)

	# ------------------------------------------------------------------ #
	# Missing / None values → no conflict
	# ------------------------------------------------------------------ #

	def test_none_existing_no_conflict(self):
		self.assertFalse(identifiers_conflict(None, {"nct": "NCT001"}))

	def test_none_incoming_no_conflict(self):
		self.assertFalse(identifiers_conflict({"nct": "NCT001"}, None))

	def test_both_none_no_conflict(self):
		self.assertFalse(identifiers_conflict(None, None))

	def test_empty_dicts_no_conflict(self):
		self.assertFalse(identifiers_conflict({}, {}))

	def test_existing_key_value_is_none_no_conflict(self):
		"""An existing key whose value is None is treated as absent."""
		self.assertFalse(identifiers_conflict({"nct": None}, {"nct": "NCT001"}))

	def test_incoming_key_value_is_none_no_conflict(self):
		self.assertFalse(identifiers_conflict({"nct": "NCT001"}, {"nct": None}))

	# ------------------------------------------------------------------ #
	# Mixed scenarios (multiple keys, some matching)
	# ------------------------------------------------------------------ #

	def test_conflict_on_one_key_overrides_agreement_on_other(self):
		"""One conflicting key is enough to trigger a conflict."""
		self.assertTrue(
			identifiers_conflict(
				{"nct": "NCT001", "euctr": "EUCTR001"},
				{"nct": "NCT002", "euctr": "EUCTR001"},
			)
		)

	def test_agreement_on_all_shared_keys_no_conflict(self):
		self.assertFalse(
			identifiers_conflict(
				{"nct": "NCT001", "euctr": "EUCTR001"},
				{"nct": "NCT001", "euctr": "EUCTR001"},
			)
		)

	# ------------------------------------------------------------------ #
	# The three real-world example trials from the design note
	# ------------------------------------------------------------------ #

	def test_example_379244_conflict(self):
		"""trial_id 379244: two different NCTs with the same title → conflict."""
		existing = {"nct": "NCT05529498"}
		incoming = {"nct": "NCT05560880"}
		self.assertTrue(identifiers_conflict(existing, incoming))

	def test_example_542983_conflict(self):
		"""trial_id 542983: WHO feed NCT ≠ CT.gov NCT → conflict."""
		existing = {"nct": "NCT06122415"}
		incoming = {"nct": "NCT05926167"}
		self.assertTrue(identifiers_conflict(existing, incoming))

	def test_example_504121_no_conflict(self):
		"""trial_id 504121: cross-registered (nct + euctr), disjoint keys → merge."""
		existing = {"nct": "NCT03268902"}
		incoming = {"euctr": "EUCTR2018-000123-42-GB"}
		self.assertFalse(identifiers_conflict(existing, incoming))
