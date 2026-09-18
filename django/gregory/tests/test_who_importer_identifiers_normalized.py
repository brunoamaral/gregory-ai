"""
Importer regression test for Trials.identifiers_normalized.

Imports django/gregory/tests/fixtures/ictrp/NCT07758270_NCT07768761_DRKS00040782.xml
-- three real WHO ICTRP-sourced trials, trimmed from a live sample export --
through the real importWHOXML command, then asserts each trial's
identifiers_normalized holds ONLY its own registry id. Two of the three carry
a free-text Secondary_ID that is a genuine WHO ICTRP negative sample (not a
registry id: "1583821/ 17043-H33", "23-232 BO") -- see the fixture's own
header comment -- so this also guards against the normalizer
over-matching free text that merely looks numeric/alphanumeric.

Run:
  docker exec gregory python manage.py test gregory.tests.test_who_importer_identifiers_normalized
"""

from pathlib import Path

from django.test import TestCase

from gregory.models import Trials
from gregory.tests.test_who_importer import _run_import, _who_source

FIXTURE_PATH = (
	Path(__file__).resolve().parent
	/ "fixtures"
	/ "ictrp"
	/ "NCT07758270_NCT07768761_DRKS00040782.xml"
)


class WHOImportedTrialsIdentifiersNormalizedTest(TestCase):
	@classmethod
	def setUpTestData(cls):
		cls.source = _who_source()
		_run_import(FIXTURE_PATH.read_text(encoding="utf-8"), cls.source.source_id)

	def test_three_trials_imported(self):
		self.assertEqual(
			Trials.objects.filter(
				identifiers__nct__in=["NCT07758270", "NCT07768761"]
			).count()
			+ Trials.objects.filter(identifiers__drks="DRKS00040782").count(),
			3,
		)

	def test_nct07758270_identifiers_normalized_holds_only_its_own_id(self):
		"""Secondary_ID and Acronym are both "REDIFINE MS" for this trial --
		free text that isn't a registry id, and (unlike Secondary_ID)
		Acronym was never a source for identifiers_normalized at all."""
		trial = Trials.objects.get(identifiers__nct="NCT07758270")
		self.assertEqual(trial.identifiers, {"nct": "NCT07758270"})
		self.assertEqual(trial.secondary_id, "REDIFINE MS")
		self.assertEqual(trial.acronym, "REDIFINE MS")
		self.assertEqual(trial.identifiers_normalized, ["nct:NCT07758270"])

	def test_nct07768761_identifiers_normalized_holds_only_its_own_id(self):
		trial = Trials.objects.get(identifiers__nct="NCT07768761")
		self.assertEqual(trial.identifiers, {"nct": "NCT07768761"})
		self.assertEqual(trial.secondary_id, "1583821/ 17043-H33")
		self.assertEqual(trial.identifiers_normalized, ["nct:NCT07768761"])

	def test_drks00040782_identifiers_normalized_holds_only_its_own_id(self):
		trial = Trials.objects.get(identifiers__drks="DRKS00040782")
		self.assertEqual(trial.identifiers, {"drks": "DRKS00040782"})
		self.assertEqual(trial.secondary_id, "23-232 BO")
		self.assertEqual(trial.identifiers_normalized, ["drks:DRKS00040782"])

	def test_no_contact_fields_present(self):
		"""The fixture deliberately strips every contact field -- confirms the
		importer doesn't backfill them from anywhere else."""
		for nct in ("NCT07758270", "NCT07768761"):
			trial = Trials.objects.get(identifiers__nct=nct)
			self.assertFalse(trial.contact_firstname)
			self.assertFalse(trial.contact_lastname)
			self.assertFalse(trial.contact_email)
			self.assertFalse(trial.contact_tel)
		drks_trial = Trials.objects.get(identifiers__drks="DRKS00040782")
		self.assertFalse(drks_trial.contact_firstname)
		self.assertFalse(drks_trial.ethics_review_contact_name)
		self.assertFalse(drks_trial.ethics_review_contact_email)
