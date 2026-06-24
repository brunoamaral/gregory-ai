import os
from datetime import timedelta
from unittest.mock import patch

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from django.contrib.sites.models import Site
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from gregory.management.commands.backfill_unpaywall import Command
from gregory.models import Articles
from sitesettings.models import CustomSetting


UNPAYWALL_OPEN = {
	"is_oa": True,
	"best_oa_location": {
		"url_for_pdf": "https://example.com/open.pdf",
		"url": "https://example.com/open",
	},
}

UNPAYWALL_CLOSED = {
	"is_oa": False,
	"best_oa_location": None,
}

PATCH_GET_DATA = "gregory.management.commands.backfill_unpaywall.unpaywall_utils.getDataByDOI"


class BackfillUnpaywallCommandTest(TestCase):
	def setUp(self):
		site = Site.objects.create(domain="test.example.com", name="Test Site")
		CustomSetting.objects.create(
			site=site, title="Test Gregory", admin_email="admin@test.example.com"
		)

		# Needs access filled, has DOI, recent
		self.needs_access = Articles.objects.create(
			title="Needs access",
			link="https://example.com/needs-access",
			doi="10.1234/needs-access",
			kind="science paper",
		)
		# Already has access — should be skipped in --access mode
		self.has_access = Articles.objects.create(
			title="Has access",
			link="https://example.com/has-access",
			doi="10.1234/has-access",
			kind="science paper",
			access="open",
			pdf_link="https://example.com/existing.pdf",
		)
		# No DOI — must be skipped entirely
		self.no_doi = Articles.objects.create(
			title="No DOI",
			link="https://example.com/no-doi",
			kind="science paper",
		)
		# Needs pdf_link, has access, recent
		self.needs_pdf = Articles.objects.create(
			title="Needs pdf link",
			link="https://example.com/needs-pdf",
			doi="10.1234/needs-pdf",
			kind="science paper",
			access="open",
		)
		# Old article outside any typical --days window
		self.old_article = Articles.objects.create(
			title="Old article",
			link="https://example.com/old",
			doi="10.1234/old",
			kind="science paper",
			access="open",
		)
		Articles.objects.filter(pk=self.old_article.pk).update(
			discovery_date=timezone.now() - timedelta(days=90)
		)

	# ------------------------------------------------------------------
	# Queryset selection
	# ------------------------------------------------------------------

	@patch.dict(os.environ, {"DOMAIN_NAME": "test.example.com"})
	def test_access_queryset_returns_null_access_with_doi(self):
		cmd = Command()
		qs = cmd._build_queryset(run_access=True, run_pdf=False, days=30)
		pks = set(qs.values_list("pk", flat=True))
		self.assertIn(self.needs_access.pk, pks)
		self.assertNotIn(self.has_access.pk, pks)
		self.assertNotIn(self.no_doi.pk, pks)

	@patch.dict(os.environ, {"DOMAIN_NAME": "test.example.com"})
	def test_pdf_links_queryset_respects_days_window(self):
		cmd = Command()
		qs = cmd._build_queryset(run_access=False, run_pdf=True, days=30)
		pks = set(qs.values_list("pk", flat=True))
		self.assertIn(self.needs_pdf.pk, pks)
		self.assertNotIn(self.old_article.pk, pks)
		self.assertNotIn(self.no_doi.pk, pks)
		self.assertNotIn(self.has_access.pk, pks)  # pdf_link already set

	@patch.dict(os.environ, {"DOMAIN_NAME": "test.example.com"})
	def test_all_queryset_is_union_of_both_conditions(self):
		cmd = Command()
		qs = cmd._build_queryset(run_access=True, run_pdf=True, days=30)
		pks = set(qs.values_list("pk", flat=True))
		# needs_access: access=NULL → included by access condition
		self.assertIn(self.needs_access.pk, pks)
		# needs_pdf: recent + pdf_link=NULL → included by pdf condition
		self.assertIn(self.needs_pdf.pk, pks)
		# old_article: access already set AND outside window → excluded
		self.assertNotIn(self.old_article.pk, pks)
		self.assertNotIn(self.no_doi.pk, pks)

	# ------------------------------------------------------------------
	# --access mode
	# ------------------------------------------------------------------

	@patch.dict(os.environ, {"DOMAIN_NAME": "test.example.com"})
	@patch(PATCH_GET_DATA)
	def test_access_mode_sets_open(self, mock_get):
		mock_get.return_value = UNPAYWALL_OPEN
		call_command("backfill_unpaywall", access=True, sleep=0, verbosity=0)
		self.needs_access.refresh_from_db()
		self.assertEqual(self.needs_access.access, "open")

	@patch.dict(os.environ, {"DOMAIN_NAME": "test.example.com"})
	@patch(PATCH_GET_DATA)
	def test_access_mode_sets_restricted(self, mock_get):
		mock_get.return_value = UNPAYWALL_CLOSED
		call_command("backfill_unpaywall", access=True, sleep=0, verbosity=0)
		self.needs_access.refresh_from_db()
		self.assertEqual(self.needs_access.access, "restricted")

	@patch.dict(os.environ, {"DOMAIN_NAME": "test.example.com"})
	@patch(PATCH_GET_DATA)
	def test_no_data_sets_access_unknown(self, mock_get):
		mock_get.return_value = {}  # Unpaywall 404 / no entry
		call_command("backfill_unpaywall", access=True, sleep=0, verbosity=0)
		self.needs_access.refresh_from_db()
		self.assertEqual(self.needs_access.access, "unknown")

	@patch.dict(os.environ, {"DOMAIN_NAME": "test.example.com"})
	@patch(PATCH_GET_DATA)
	def test_access_mode_skips_already_set(self, mock_get):
		mock_get.return_value = UNPAYWALL_OPEN
		call_command("backfill_unpaywall", access=True, sleep=0, verbosity=0)
		self.has_access.refresh_from_db()
		# Existing value must not be overwritten
		self.assertEqual(self.has_access.access, "open")
		self.assertEqual(self.has_access.pdf_link, "https://example.com/existing.pdf")

	# ------------------------------------------------------------------
	# --pdf-links mode
	# ------------------------------------------------------------------

	@patch.dict(os.environ, {"DOMAIN_NAME": "test.example.com"})
	@patch(PATCH_GET_DATA)
	def test_pdf_links_mode_sets_pdf_link(self, mock_get):
		mock_get.return_value = UNPAYWALL_OPEN
		call_command("backfill_unpaywall", pdf_links=True, days=30, sleep=0, verbosity=0)
		self.needs_pdf.refresh_from_db()
		self.assertEqual(self.needs_pdf.pdf_link, "https://example.com/open.pdf")

	@patch.dict(os.environ, {"DOMAIN_NAME": "test.example.com"})
	@patch(PATCH_GET_DATA)
	def test_pdf_links_mode_ignores_old_articles(self, mock_get):
		mock_get.return_value = UNPAYWALL_OPEN
		call_command("backfill_unpaywall", pdf_links=True, days=30, sleep=0, verbosity=0)
		self.old_article.refresh_from_db()
		self.assertIsNone(self.old_article.pdf_link)
		mock_get.assert_called()  # called for recent articles, but not for old_article DOI
		called_dois = [call.args[0] for call in mock_get.call_args_list]
		self.assertNotIn(self.old_article.doi, called_dois)

	# ------------------------------------------------------------------
	# --all mode
	# ------------------------------------------------------------------

	@patch.dict(os.environ, {"DOMAIN_NAME": "test.example.com"})
	@patch(PATCH_GET_DATA)
	def test_all_mode_sets_both_fields_in_one_pass(self, mock_get):
		mock_get.return_value = UNPAYWALL_OPEN
		call_command("backfill_unpaywall", **{"all": True, "days": 30, "sleep": 0, "verbosity": 0})
		self.needs_access.refresh_from_db()
		self.assertEqual(self.needs_access.access, "open")
		self.assertEqual(self.needs_access.pdf_link, "https://example.com/open.pdf")
		self.needs_pdf.refresh_from_db()
		self.assertEqual(self.needs_pdf.pdf_link, "https://example.com/open.pdf")

	# ------------------------------------------------------------------
	# --dry-run
	# ------------------------------------------------------------------

	@patch.dict(os.environ, {"DOMAIN_NAME": "test.example.com"})
	@patch(PATCH_GET_DATA)
	def test_dry_run_does_not_write_access(self, mock_get):
		mock_get.return_value = UNPAYWALL_OPEN
		call_command("backfill_unpaywall", access=True, dry_run=True, sleep=0, verbosity=0)
		self.needs_access.refresh_from_db()
		self.assertIsNone(self.needs_access.access)

	@patch.dict(os.environ, {"DOMAIN_NAME": "test.example.com"})
	@patch(PATCH_GET_DATA)
	def test_dry_run_no_data_does_not_write_unknown(self, mock_get):
		mock_get.return_value = {}
		call_command("backfill_unpaywall", access=True, dry_run=True, sleep=0, verbosity=0)
		self.needs_access.refresh_from_db()
		self.assertIsNone(self.needs_access.access)

	@patch.dict(os.environ, {"DOMAIN_NAME": "test.example.com"})
	@patch(PATCH_GET_DATA)
	def test_dry_run_does_not_write_pdf_link(self, mock_get):
		mock_get.return_value = UNPAYWALL_OPEN
		call_command("backfill_unpaywall", pdf_links=True, days=30, dry_run=True, sleep=0, verbosity=0)
		self.needs_pdf.refresh_from_db()
		self.assertIsNone(self.needs_pdf.pdf_link)
