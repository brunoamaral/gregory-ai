import os
import django
from datetime import timedelta

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from api.models import APIAccessSchemeLog


class PruneAPIAccessSchemeLogsCommandTest(TestCase):
	def setUp(self):
		self.recent_log = self._create_log(days_ago=10)
		self.mid_log = self._create_log(days_ago=45)
		self.old_log = self._create_log(days_ago=120)
		self.very_old_log = self._create_log(days_ago=400)

	def _create_log(self, days_ago):
		return APIAccessSchemeLog.objects.create(
			call_type="GET /api/articles/",
			ip_addr="127.0.0.1",
			http_code=200,
			access_date=timezone.now() - timedelta(days=days_ago),
		)

	def test_window_30d_prunes_older_than_30_days(self):
		call_command("prune_api_access_scheme_logs", window="30d")

		remaining_ids = set(APIAccessSchemeLog.objects.values_list("id", flat=True))
		self.assertEqual(remaining_ids, {self.recent_log.id})

	def test_window_90d_prunes_older_than_90_days(self):
		call_command("prune_api_access_scheme_logs", window="90d")

		remaining_ids = set(APIAccessSchemeLog.objects.values_list("id", flat=True))
		self.assertEqual(remaining_ids, {self.recent_log.id, self.mid_log.id})

	def test_window_1y_prunes_older_than_1_year(self):
		call_command("prune_api_access_scheme_logs", window="1y")

		remaining_ids = set(APIAccessSchemeLog.objects.values_list("id", flat=True))
		self.assertEqual(
			remaining_ids, {self.recent_log.id, self.mid_log.id, self.old_log.id}
		)

	def test_dry_run_does_not_delete_logs(self):
		call_command("prune_api_access_scheme_logs", window="30d", dry_run=True)

		self.assertEqual(APIAccessSchemeLog.objects.count(), 4)
