from django.test import TestCase

from gregory.models import Authors
from gregory.services.orcid_sync import apply_orcid_record_to_author


class ApplyOrcidRecordToAuthorTest(TestCase):
	def setUp(self):
		self.author = Authors.objects.create(
			given_name="Ada", family_name="Lovelace", ORCID="0000-0001-2345-6789"
		)

	def test_sets_country_and_biography_from_record(self):
		record = {
			"person": {
				"addresses": {"address": [{"country": {"value": "GB"}}]},
				"biography": {"content": "Mathematician and writer."},
			}
		}

		result = apply_orcid_record_to_author(self.author, record)

		self.author.refresh_from_db()
		self.assertEqual(self.author.country, "GB")
		self.assertEqual(self.author.biography, "Mathematician and writer.")
		self.assertIsNotNone(self.author.orcid_check)
		self.assertEqual(sorted(result.changed_fields), ["biography", "country"])
		self.assertTrue(result.has_address)
		self.assertTrue(result.has_biography)

	def test_missing_address_and_biography_leave_fields_untouched(self):
		self.author.country = "PT"
		self.author.biography = "Existing bio."
		self.author.save()

		record = {"person": {}}

		result = apply_orcid_record_to_author(self.author, record)

		self.author.refresh_from_db()
		self.assertEqual(self.author.country, "PT")
		self.assertEqual(self.author.biography, "Existing bio.")
		self.assertIsNotNone(self.author.orcid_check)
		self.assertEqual(result.changed_fields, [])
		self.assertFalse(result.has_address)
		self.assertFalse(result.has_biography)

	def test_change_reason_only_recorded_when_fields_change(self):
		record = {"person": {}}

		apply_orcid_record_to_author(self.author, record)

		latest_history = self.author.history.first()
		# orcid_check always changes, but history change_reason is only set
		# when apply_orcid_record_to_author records one explicitly.
		self.assertIsNone(latest_history.history_change_reason)

	def test_change_reason_set_when_country_changes(self):
		record = {
			"person": {"addresses": {"address": [{"country": {"value": "FR"}}]}}
		}

		apply_orcid_record_to_author(self.author, record)

		latest_history = self.author.history.first()
		self.assertEqual(
			latest_history.history_change_reason,
			"Updated country from ORCID API.",
		)

	def test_change_reason_suffix_is_appended(self):
		record = {
			"person": {"addresses": {"address": [{"country": {"value": "FR"}}]}}
		}

		apply_orcid_record_to_author(
			self.author, record, change_reason_suffix="(manual recheck)"
		)

		latest_history = self.author.history.first()
		self.assertEqual(
			latest_history.history_change_reason,
			"Updated country from ORCID API. (manual recheck)",
		)

	def test_only_first_address_country_is_used(self):
		record = {
			"person": {
				"addresses": {
					"address": [
						{"country": {"value": "US"}},
						{"country": {"value": "CA"}},
					]
				}
			}
		}

		apply_orcid_record_to_author(self.author, record)

		self.author.refresh_from_db()
		self.assertEqual(self.author.country, "US")
