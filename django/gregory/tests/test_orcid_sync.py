from django.test import TestCase

from gregory.models import Authors
from gregory.services.orcid_sync import (
	apply_orcid_record_to_author,
	select_current_affiliation,
)


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

	def test_null_addresses_section_does_not_raise(self):
		record = {"person": {"addresses": None}}

		result = apply_orcid_record_to_author(self.author, record)

		self.author.refresh_from_db()
		self.assertFalse(result.has_address)

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

	def test_credit_name_set_from_record(self):
		record = {"person": {"name": {"credit-name": {"value": "A. Lovelace"}}}}

		apply_orcid_record_to_author(self.author, record)

		self.author.refresh_from_db()
		self.assertEqual(self.author.credit_name, "A. Lovelace")

	def test_credit_name_absent_leaves_field_untouched(self):
		self.author.credit_name = "Existing Name"
		self.author.save()

		result = apply_orcid_record_to_author(self.author, {"person": {}})

		self.author.refresh_from_db()
		self.assertEqual(self.author.credit_name, "Existing Name")
		self.assertEqual(result.changed_fields, [])

	def test_orcid_keywords_populated_from_multi_keyword_record(self):
		record = {
			"person": {
				"keywords": {
					"keyword": [
						{"content": "computing"},
						{"content": "mathematics"},
					]
				}
			}
		}

		apply_orcid_record_to_author(self.author, record)

		self.author.refresh_from_db()
		self.assertEqual(self.author.orcid_keywords, ["computing", "mathematics"])

	def test_empty_keywords_section_yields_empty_list_no_spurious_history(self):
		record = {"person": {"keywords": {"keyword": []}}}

		result = apply_orcid_record_to_author(self.author, record)

		self.author.refresh_from_db()
		self.assertEqual(self.author.orcid_keywords, [])
		self.assertEqual(result.changed_fields, [])
		latest_history = self.author.history.first()
		self.assertIsNone(latest_history.history_change_reason)

	def test_external_ids_mapped_to_type_value_url_shape(self):
		record = {
			"person": {
				"external-identifiers": {
					"external-identifier": [
						{
							"external-id-type": "Scopus Author ID",
							"external-id-value": "6602258586",
							"external-id-url": {"value": "http://example.com/scopus"},
						},
						{
							"external-id-type": "ISNI",
							"external-id-value": "0000000138352317",
						},
					]
				}
			}
		}

		apply_orcid_record_to_author(self.author, record)

		self.author.refresh_from_db()
		self.assertEqual(
			self.author.external_ids,
			[
				{
					"type": "Scopus Author ID",
					"value": "6602258586",
					"url": "http://example.com/scopus",
				},
				{"type": "ISNI", "value": "0000000138352317", "url": None},
			],
		)

	def test_researcher_urls_mapped_to_name_url_shape(self):
		record = {
			"person": {
				"researcher-urls": {
					"researcher-url": [
						{
							"url-name": "LinkedIn",
							"url": {"value": "https://linkedin.com/in/ada"},
						}
					]
				}
			}
		}

		apply_orcid_record_to_author(self.author, record)

		self.author.refresh_from_db()
		self.assertEqual(
			self.author.researcher_urls,
			[{"name": "LinkedIn", "url": "https://linkedin.com/in/ada"}],
		)

	def test_orcid_claimed_and_verified_email_from_history(self):
		record = {"history": {"claimed": True, "verified-email": False}}

		apply_orcid_record_to_author(self.author, record)

		self.author.refresh_from_db()
		self.assertTrue(self.author.orcid_claimed)
		self.assertFalse(self.author.orcid_verified_email)

	def test_idempotency_applying_same_record_twice_records_change_once(self):
		record = {
			"person": {"addresses": {"address": [{"country": {"value": "FR"}}]}}
		}

		apply_orcid_record_to_author(self.author, record)
		result_second = apply_orcid_record_to_author(self.author, record)

		self.assertEqual(result_second.changed_fields, [])
		# Only one history row should carry the change reason.
		reasoned_rows = [
			h for h in self.author.history.all() if h.history_change_reason
		]
		self.assertEqual(len(reasoned_rows), 1)


class SelectCurrentAffiliationTest(TestCase):
	def test_picks_ongoing_over_ended(self):
		activities_summary = {
			"employments": {
				"affiliation-group": [
					{
						"summaries": [
							{
								"employment-summary": {
									"start-date": {"year": {"value": "2018"}},
									"end-date": {"year": {"value": "2020"}},
									"organization": {"name": "Old Org"},
								}
							}
						]
					},
					{
						"summaries": [
							{
								"employment-summary": {
									"start-date": {"year": {"value": "2015"}},
									"end-date": None,
									"organization": {"name": "Current Org"},
								}
							}
						]
					},
				]
			}
		}

		self.assertEqual(
			select_current_affiliation(activities_summary), "Current Org"
		)

	def test_picks_latest_start_date_among_multiple_ongoing(self):
		activities_summary = {
			"employments": {
				"affiliation-group": [
					{
						"summaries": [
							{
								"employment-summary": {
									"start-date": {
										"year": {"value": "2020"},
										"month": {"value": "04"},
									},
									"end-date": None,
									"organization": {"name": "Ronin Institute"},
								}
							}
						]
					},
					{
						"summaries": [
							{
								"employment-summary": {
									"start-date": {
										"year": {"value": "2020"},
										"month": {"value": "06"},
										"day": {"value": "02"},
									},
									"end-date": None,
									"organization": {"name": "Mighty Red Barn"},
								}
							}
						]
					},
				]
			}
		}

		self.assertEqual(
			select_current_affiliation(activities_summary), "Mighty Red Barn"
		)

	def test_empty_employments_returns_none(self):
		self.assertIsNone(select_current_affiliation({}))
		self.assertIsNone(
			select_current_affiliation({"employments": {"affiliation-group": []}})
		)


class ApplyOrcidRecordFullRealisticFixtureTest(TestCase):
	"""Shape-fidelity test using a trimmed version of a real ORCID v3.0 record
	(0000-0001-5109-3700, Laurel Haak) fetched from pub.orcid.org."""

	def setUp(self):
		self.author = Authors.objects.create(
			given_name="Laurel", family_name="Haak", ORCID="0000-0001-5109-3700"
		)

	def test_full_record_populates_all_tier1_fields(self):
		record = {
			"history": {
				"claimed": True,
				"verified-email": True,
			},
			"person": {
				"name": {"credit-name": {"value": "Laurel L Haak"}},
				"addresses": {"address": [{"country": {"value": "US"}}]},
				"biography": {"content": "Entrepreneur, strategist, researcher."},
				"keywords": {
					"keyword": [
						{"content": "future of work"},
						{"content": "persistent identifiers"},
						{"content": "research policy"},
					]
				},
				"external-identifiers": {
					"external-identifier": [
						{
							"external-id-type": "Scopus Author ID",
							"external-id-value": "6602258586",
							"external-id-url": {
								"value": "http://www.scopus.com/inward/authorDetails.url?authorID=6602258586"
							},
						},
						{
							"external-id-type": "ISNI",
							"external-id-value": "0000000138352317",
							"external-id-url": {
								"value": "http://isni.org/isni/0000000138352317"
							},
						},
					]
				},
				"researcher-urls": {
					"researcher-url": [
						{
							"url-name": "LinkedIn",
							"url": {"value": "https://www.linkedin.com/in/laurellhaak/"},
						},
						{
							"url-name": "Google Scholar",
							"url": {
								"value": "https://scholar.google.com/citations?hl=en&user=fFjR4DoAAAAJ"
							},
						},
					]
				},
				"emails": {"email": []},
			},
			"activities-summary": {
				"employments": {
					"affiliation-group": [
						{
							"summaries": [
								{
									"employment-summary": {
										"role-title": "Founder and CEO",
										"start-date": {
											"year": {"value": "2020"},
											"month": {"value": "06"},
											"day": {"value": "02"},
										},
										"end-date": None,
										"organization": {"name": "Mighty Red Barn"},
									}
								}
							]
						},
						{
							"summaries": [
								{
									"employment-summary": {
										"role-title": "Research Scholar",
										"start-date": {
											"year": {"value": "2020"},
											"month": {"value": "04"},
											"day": None,
										},
										"end-date": None,
										"organization": {"name": "Ronin Institute"},
									}
								}
							]
						},
						{
							"summaries": [
								{
									"employment-summary": {
										"role-title": "Executive Director",
										"start-date": {
											"year": {"value": "2012"},
											"month": {"value": "04"},
											"day": {"value": "09"},
										},
										"end-date": {
											"year": {"value": "2020"},
											"month": {"value": "06"},
											"day": {"value": "01"},
										},
										"organization": {"name": "ORCID"},
									}
								}
							]
						},
					]
				}
			},
		}

		result = apply_orcid_record_to_author(self.author, record)

		self.author.refresh_from_db()
		self.assertEqual(self.author.country, "US")
		self.assertEqual(
			self.author.biography, "Entrepreneur, strategist, researcher."
		)
		self.assertEqual(self.author.credit_name, "Laurel L Haak")
		self.assertEqual(
			self.author.orcid_keywords,
			["future of work", "persistent identifiers", "research policy"],
		)
		self.assertEqual(len(self.author.external_ids), 2)
		self.assertEqual(self.author.external_ids[0]["type"], "Scopus Author ID")
		self.assertEqual(len(self.author.researcher_urls), 2)
		self.assertEqual(self.author.researcher_urls[0]["name"], "LinkedIn")
		self.assertEqual(self.author.emails, [])
		self.assertEqual(self.author.current_affiliation, "Mighty Red Barn")
		self.assertTrue(self.author.orcid_claimed)
		self.assertTrue(self.author.orcid_verified_email)
		self.assertTrue(result.has_keywords)
		self.assertTrue(result.has_affiliation)
