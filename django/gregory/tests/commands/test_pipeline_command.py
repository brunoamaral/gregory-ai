import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from io import StringIO
from unittest.mock import patch, MagicMock
from django.core.management import call_command
from django.test import TestCase


def _make_org_mock(slug, has_creds=True):
	org = MagicMock()
	org.slug = slug
	if has_creds:
		org.credentials.orcid_client_id = "test_id"
		org.credentials.orcid_client_secret = "test_secret"
	else:
		org.credentials  # access raises DoesNotExist below — configure via side_effect
	return org


class PipelineCommandTest(TestCase):
	@patch("gregory.management.commands.pipeline.call_command")
	@patch("django.apps.apps.get_model")
	def test_pipeline_runs_standard_commands_and_per_org_orcid(
		self, mock_get_model, mock_call
	):
		mock_org = MagicMock()
		mock_org.slug = "test-org"
		mock_org.credentials.orcid_client_id = "id"
		mock_org.credentials.orcid_client_secret = "secret"
		mock_org_class = MagicMock()
		mock_org_class.objects.all.return_value.order_by.return_value = [mock_org]
		mock_get_model.return_value = mock_org_class

		out = StringIO()
		call_command("pipeline", stdout=out)

		called_commands = [c[0][0] if c[0] else None for c in mock_call.call_args_list]
		standard = [
			"feedreader_articles",
			"feedreader_articles_pubmed",
			"feedreader_trials",
			"feedreader_trials_ctgov",
			"find_doi",
			"update_articles_info",
			"get_authors",
			"find_pmids",
			"refresh_pubmed_records",
			"rebuild_categories",
			"get_takeaways",
		]
		for cmd in standard:
			self.assertIn(cmd, called_commands)
		self.assertIn("update_orcid", called_commands)
		self.assertIn("predict_articles", called_commands)

	@patch("gregory.management.commands.pipeline.call_command")
	@patch("django.apps.apps.get_model")
	def test_patent_steps_run_in_order(self, mock_get_model, mock_call):
		mock_get_model.return_value.objects.all.return_value.order_by.return_value = []
		call_command("pipeline", stdout=StringIO())

		calls = [(c[0][0], c[1]) for c in mock_call.call_args_list if c[0]]
		names = [name for name, _ in calls]
		self.assertLess(names.index("feedreader_trials_ctis"), names.index("feedreader_patents_ops"))
		self.assertLess(names.index("rebuild_categories"), names.index("detect_patent_trial_links"))
		# 3d: EMA medicines, after the patent importer and before the DOI pass.
		self.assertLess(names.index("feedreader_patents_ops"), names.index("sync_ema_medicines"))
		self.assertLess(names.index("sync_ema_medicines"), names.index("find_doi"))
		self.assertEqual(dict(calls)["sync_ema_medicines"], {})
		self.assertEqual(dict(calls)["feedreader_patents_ops"], {"max_families": 500})
		# A full detection pass, not --recent: links must follow trials that change.
		self.assertEqual(dict(calls)["detect_patent_trial_links"], {})

	@patch("gregory.management.commands.pipeline.call_command")
	@patch("django.apps.apps.get_model")
	def test_pipeline_skips_org_without_orcid_credentials(
		self, mock_get_model, mock_call
	):
		from gregory.models import OrganizationCredentials

		mock_org = MagicMock()
		mock_org.slug = "no-creds-org"
		type(mock_org).credentials = property(
			lambda self: (_ for _ in ()).throw(OrganizationCredentials.DoesNotExist())
		)
		mock_org_class = MagicMock()
		mock_org_class.objects.all.return_value.order_by.return_value = [mock_org]
		mock_get_model.return_value = mock_org_class

		out = StringIO()
		call_command("pipeline", stdout=out)

		called_commands = [c[0][0] if c[0] else None for c in mock_call.call_args_list]
		self.assertNotIn("update_orcid", called_commands)
		self.assertIn("Skipping update_orcid", out.getvalue())

	@patch("gregory.management.commands.pipeline.call_command")
	@patch("django.apps.apps.get_model")
	def test_rebuild_categories_is_incremental_by_default(
		self, mock_get_model, mock_call
	):
		mock_org_class = MagicMock()
		mock_org_class.objects.all.return_value.order_by.return_value = []
		mock_get_model.return_value = mock_org_class

		out = StringIO()
		call_command("pipeline", stdout=out)

		rebuild_calls = [
			c
			for c in mock_call.call_args_list
			if c.args and c.args[0] == "rebuild_categories"
		]
		self.assertEqual(len(rebuild_calls), 1)
		self.assertEqual(rebuild_calls[0].kwargs.get("days"), 30)

	@patch("gregory.management.commands.pipeline.call_command")
	@patch("django.apps.apps.get_model")
	def test_full_category_rebuild_flag_disables_window(
		self, mock_get_model, mock_call
	):
		mock_org_class = MagicMock()
		mock_org_class.objects.all.return_value.order_by.return_value = []
		mock_get_model.return_value = mock_org_class

		out = StringIO()
		call_command("pipeline", "--full-category-rebuild", stdout=out)

		rebuild_calls = [
			c
			for c in mock_call.call_args_list
			if c.args and c.args[0] == "rebuild_categories"
		]
		self.assertEqual(len(rebuild_calls), 1)
		self.assertNotIn("days", rebuild_calls[0].kwargs)

	@patch("gregory.management.commands.pipeline.call_command")
	@patch("django.apps.apps.get_model")
	def test_command_errors_are_logged_and_execution_continues(
		self, mock_get_model, mock_call
	):
		mock_org_class = MagicMock()
		mock_org_class.objects.all.return_value.order_by.return_value = []
		mock_get_model.return_value = mock_org_class

		def side_effect(cmd, *args, **kwargs):
			if cmd == "feedreader_trials":
				raise Exception("boom")

		mock_call.side_effect = side_effect

		out = StringIO()
		err = StringIO()
		call_command("pipeline", "--recent-days", "10", stdout=out, stderr=err)
		self.assertIn("Error running command feedreader_trials", err.getvalue())
		called_commands = [c[0][0] if c[0] else None for c in mock_call.call_args_list]
		self.assertIn("detect_trial_references", called_commands)
