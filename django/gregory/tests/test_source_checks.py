"""
gregory/source_checks.py and gregory/utils/ctgov_search.py: the checks the admin form and
the editor API share, and the ClinicalTrials.gov search the importer and the preview
share. The editor API itself is tested in api/tests/test_editor_sources.py.

Run:
  docker exec gregory pytest gregory/tests/test_source_checks.py
"""

from datetime import datetime, timezone as dt_timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import RequestFactory, SimpleTestCase, TestCase
from organizations.models import Organization

from gregory import source_checks
from gregory.admin import SourceAdminForm
from gregory.management.commands.feedreader_trials_ctgov import Command as CtgovCommand
from gregory.models import Sources, Subject, Team
from gregory.utils import ctgov_search
from gregory.utils.ncbi import ESearchResult


class CheckSettingsTests(SimpleTestCase):
	def test_a_source_that_passes_has_no_problems(self):
		self.assertEqual(source_checks.check_settings("rss_papers", {"link": "https://x.org/f"}), {})
		self.assertEqual(source_checks.check_settings("ctgov", {"ctgov_search_term": "ms"}), {})
		self.assertEqual(source_checks.check_settings("pubmed", {"pubmed_query": "remyelination"}), {})

	def test_each_type_reports_its_missing_settings_by_field(self):
		self.assertEqual(source_checks.check_settings("rss_trials", {}), {"link": ["Enter the feed's URL."]})
		self.assertEqual(list(source_checks.check_settings("ctgov", {})), ["ctgov_search_condition"])
		self.assertEqual(list(source_checks.check_settings("pubmed", {"pubmed_query": " "})), ["pubmed_query"])
		self.assertEqual(list(source_checks.check_settings("ctis", {})), ["ctis_search_criteria"])
		self.assertEqual(list(source_checks.check_settings("epo_ops", {})), ["ops_cql_query"])

	def test_ctis_and_epo_rules(self):
		self.assertIn("Unknown key(s): foo", source_checks.check_settings("ctis", {"ctis_search_criteria": {"foo": 1}})["ctis_search_criteria"][0])
		self.assertIn("JSON object", source_checks.check_settings("ctis", {"ctis_search_criteria": ["x"]})["ctis_search_criteria"][0])
		self.assertIn("Remove the publication-date clause", source_checks.check_settings("epo_ops", {"ops_cql_query": 'ta="ms" and pd>2020'})["ops_cql_query"][0])

	def test_an_unknown_type_has_no_problems_of_its_own(self):
		self.assertEqual(source_checks.check_settings("nonsense", {}), {})

	def test_a_missing_setting_counts_as_empty(self):
		self.assertEqual(list(source_checks.check_settings("pubmed", {})), ["pubmed_query"])


class AdminUsesTheSharedChecksTests(TestCase):
	"""The admin form calls source_checks and leaves the fetch anchor alone."""

	def setUp(self):
		org = Organization.objects.create(name="Org", slug="org-source-checks")
		self.team = Team.objects.create(organization=org, name="Team", slug="team-source-checks")
		self.subject = Subject.objects.create(subject_name="MS", subject_slug="ms-sc", team=self.team)
		user = get_user_model().objects.create_superuser("admin-source-checks", "a@example.org", "x")
		self.request = RequestFactory().get("/admin/gregory/sources/add/")
		self.request.user = user
		patcher = patch("gregory.admin.NcbiClient")
		patcher.start().for_site.return_value.esearch.return_value = ESearchResult(count=3, querytranslation="t")
		self.addCleanup(patcher.stop)

	def form(self, **data):
		payload = {"team": self.team.pk, "subject": self.subject.pk, "name": "Test", "active": "on", **data}
		return SourceAdminForm(data=payload, request=self.request)

	def test_the_form_asks_source_checks_for_the_settings_problems(self):
		with patch("gregory.admin.source_checks.check_settings", wraps=source_checks.check_settings) as check:
			form = self.form(source_type="rss_papers", link="")
			self.assertFalse(form.is_valid())
		check.assert_called_once()
		self.assertEqual(check.call_args.args[0], "rss_papers")
		self.assertEqual(form.errors["link"], ["Enter the feed's URL."])

	def test_the_form_asks_source_checks_to_check_the_pubmed_search(self):
		with patch("gregory.admin.source_checks.check_pubmed_search", wraps=source_checks.check_pubmed_search) as search:
			form = self.form(source_type="pubmed", pubmed_query="remyelination")
			self.assertTrue(form.is_valid(), form.errors)
		search.assert_called_once()
		self.assertEqual(form.pubmed_check.count, 3)

	def test_a_source_added_in_the_admin_has_no_anchor_so_it_backfills(self):
		for key, settings_ in (
			("pubmed", {"pubmed_query": "remyelination"}),
			("ctgov", {"ctgov_search_condition": "multiple sclerosis"}),
			("rss_papers", {"link": "https://x.org/feed"}),
		):
			with self.subTest(key):
				form = self.form(source_type=key, **settings_)
				self.assertTrue(form.is_valid(), form.errors)
				self.assertIsNone(form.save().last_successful_fetch_at)


class CtgovSearchParamsTests(TestCase):
	"""The importer builds the same parameters it did before the builder was shared."""

	def source(self, **fields):
		return Sources.objects.create(name="CTGov", method="ctgov_api", source_for="trials", active=True, **fields)

	def test_without_an_anchor_the_search_has_no_date_filter(self):
		source = self.source(
			ctgov_search_condition="multiple sclerosis", ctgov_search_intervention=" ocrelizumab ", ctgov_search_term="  "
		)
		self.assertEqual(
			CtgovCommand()._build_search_params(source),
			{
				"page_size": 100,
				"sort": ["LastUpdatePostDate:desc"],
				"query_cond": "multiple sclerosis",
				"query_intr": "ocrelizumab",
			},
		)

	def test_with_an_anchor_the_window_starts_two_days_before_it(self):
		source = self.source(ctgov_search_term="cell reprogramming")
		source.last_successful_fetch_at = datetime(2026, 10, 19, 15, 30, tzinfo=dt_timezone.utc)
		self.assertEqual(
			CtgovCommand()._build_search_params(source),
			{
				"page_size": 100,
				"sort": ["LastUpdatePostDate:desc"],
				"query_term": "cell reprogramming",
				"filter_advanced": "AREA[LastUpdatePostDate]RANGE[2026-10-17,MAX]",
			},
		)

	def test_the_importer_still_skips_a_source_with_no_search(self):
		self.assertEqual(CtgovCommand()._search_query_params(self.source(description="TERM:x")), {})

	def test_the_shared_builder_takes_a_date_or_a_datetime(self):
		values = {"ctgov_search_condition": "ms"}
		from datetime import date

		for start in (date(2026, 9, 1), datetime(2026, 9, 1, 12, tzinfo=dt_timezone.utc)):
			self.assertEqual(
				ctgov_search.build_search_params(values, start)["filter_advanced"],
				"AREA[LastUpdatePostDate]RANGE[2026-09-01,MAX]",
			)
		self.assertNotIn("filter_advanced", ctgov_search.build_search_params(values))
