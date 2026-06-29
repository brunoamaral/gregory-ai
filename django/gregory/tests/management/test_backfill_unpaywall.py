import csv
import os
import tempfile
from datetime import timedelta
from io import StringIO
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

	# ------------------------------------------------------------------
	# Summary output
	# ------------------------------------------------------------------

	@patch.dict(os.environ, {"DOMAIN_NAME": "test.example.com"})
	@patch(PATCH_GET_DATA)
	def test_dry_run_summary_counts_would_update(self, mock_get):
		mock_get.return_value = UNPAYWALL_OPEN
		out = StringIO()
		call_command(
			"backfill_unpaywall", access=True, dry_run=True, sleep=0, verbosity=0,
			stdout=out,
		)
		output = out.getvalue()
		self.assertIn("[dry run]", output)
		self.assertIn("Updated articles:", output)
		# needs_access is the one article that would be updated
		self.assertIn("1", output)

	@patch.dict(os.environ, {"DOMAIN_NAME": "test.example.com"})
	@patch(PATCH_GET_DATA)
	def test_summary_no_data_counted_separately(self, mock_get):
		mock_get.return_value = {}
		out = StringIO()
		call_command("backfill_unpaywall", access=True, sleep=0, verbosity=0, stdout=out)
		output = out.getvalue()
		self.assertIn("No Unpaywall data:", output)
		self.assertIn("Updated articles:", output)

	# ------------------------------------------------------------------
	# CSV report
	# ------------------------------------------------------------------

	@patch.dict(os.environ, {"DOMAIN_NAME": "test.example.com"})
	@patch(PATCH_GET_DATA)
	def test_csv_report_has_expected_headers_and_rows(self, mock_get):
		mock_get.return_value = UNPAYWALL_OPEN
		with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as tf:
			csv_path = tf.name
		try:
			call_command(
				"backfill_unpaywall", access=True, sleep=0, verbosity=0,
				csv_path=csv_path,
			)
			with open(csv_path, newline="", encoding="utf-8") as f:
				reader = csv.DictReader(f)
				fieldnames = reader.fieldnames
				rows = list(reader)
			expected_fields = {
				"article_id", "doi", "title", "status",
				"fields_updated", "access_before", "access_after",
				"pdf_link_before", "pdf_link_after",
			}
			self.assertEqual(set(fieldnames), expected_fields)
			article_ids = {r["article_id"] for r in rows}
			self.assertIn(str(self.needs_access.article_id), article_ids)
			updated = next(r for r in rows if r["article_id"] == str(self.needs_access.article_id))
			self.assertEqual(updated["status"], "updated")
			self.assertEqual(updated["access_after"], "open")
		finally:
			os.unlink(csv_path)

	@patch.dict(os.environ, {"DOMAIN_NAME": "test.example.com"})
	@patch(PATCH_GET_DATA)
	def test_csv_dry_run_shows_would_update_status(self, mock_get):
		mock_get.return_value = UNPAYWALL_OPEN
		with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as tf:
			csv_path = tf.name
		try:
			call_command(
				"backfill_unpaywall", access=True, dry_run=True, sleep=0, verbosity=0,
				csv_path=csv_path,
			)
			with open(csv_path, newline="", encoding="utf-8") as f:
				rows = list(csv.DictReader(f))
			updated = next(r for r in rows if r["article_id"] == str(self.needs_access.article_id))
			self.assertEqual(updated["status"], "would_update")
			# DB must not have changed
			self.needs_access.refresh_from_db()
			self.assertIsNone(self.needs_access.access)
		finally:
			os.unlink(csv_path)

	# ------------------------------------------------------------------
	# --log-file
	# ------------------------------------------------------------------

	@patch.dict(os.environ, {"DOMAIN_NAME": "test.example.com"})
	@patch(PATCH_GET_DATA)
	def test_log_file_records_processed_ids(self, mock_get):
		mock_get.return_value = UNPAYWALL_OPEN
		with tempfile.NamedTemporaryFile(suffix=".log", delete=False, mode="w") as tf:
			log_path = tf.name
		try:
			call_command(
				"backfill_unpaywall", access=True, sleep=0, verbosity=0,
				log_file=log_path,
			)
			with open(log_path) as f:
				logged_ids = {int(line.strip()) for line in f if line.strip().isdigit()}
			self.assertIn(self.needs_access.article_id, logged_ids)
		finally:
			os.unlink(log_path)

	@patch.dict(os.environ, {"DOMAIN_NAME": "test.example.com"})
	@patch(PATCH_GET_DATA)
	def test_log_file_skips_already_processed(self, mock_get):
		mock_get.return_value = UNPAYWALL_OPEN
		with tempfile.NamedTemporaryFile(suffix=".log", delete=False, mode="w") as tf:
			log_path = tf.name
			tf.write(f"{self.needs_access.article_id}\n")
		try:
			call_command(
				"backfill_unpaywall", access=True, sleep=0, verbosity=0,
				log_file=log_path,
			)
			# Article was pre-logged, so Unpaywall should never be called for its DOI.
			called_dois = [call.args[0] for call in mock_get.call_args_list]
			self.assertNotIn(self.needs_access.doi, called_dois)
			# DB must remain untouched.
			self.needs_access.refresh_from_db()
			self.assertIsNone(self.needs_access.access)
		finally:
			os.unlink(log_path)

	@patch.dict(os.environ, {"DOMAIN_NAME": "test.example.com"})
	@patch(PATCH_GET_DATA)
	def test_dry_run_does_not_write_log(self, mock_get):
		mock_get.return_value = UNPAYWALL_OPEN
		with tempfile.NamedTemporaryFile(suffix=".log", delete=False, mode="w") as tf:
			log_path = tf.name
		try:
			call_command(
				"backfill_unpaywall", access=True, dry_run=True, sleep=0, verbosity=0,
				log_file=log_path,
			)
			with open(log_path) as f:
				content = f.read().strip()
			self.assertEqual(content, "", "dry-run must not write any IDs to the log file")
		finally:
			os.unlink(log_path)
