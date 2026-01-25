from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta

from api.models import APIAccessSchemeLog
from gregory.models import PredictionRunLog


class Command(BaseCommand):
	help = 'Clear log entries older than a specified number of days (default: 30 days)'

	def add_arguments(self, parser):
		parser.add_argument(
			'--days',
			type=int,
			default=30,
			help='Delete logs older than this many days (default: 30)',
		)
		parser.add_argument(
			'--dry-run',
			action='store_true',
			help='Show what would be deleted without actually deleting',
		)
		parser.add_argument(
			'--api-logs-only',
			action='store_true',
			help='Only clear API access logs',
		)
		parser.add_argument(
			'--prediction-logs-only',
			action='store_true',
			help='Only clear prediction run logs',
		)

	def handle(self, *args, **options):
		days = options['days']
		dry_run = options['dry_run']
		api_logs_only = options['api_logs_only']
		prediction_logs_only = options['prediction_logs_only']

		cutoff_date = timezone.now() - timedelta(days=days)

		self.stdout.write(f'Clearing logs older than {days} days (before {cutoff_date.strftime("%Y-%m-%d %H:%M:%S")})')
		self.stdout.write('')

		total_deleted = 0

		# Clear API Access Logs
		if not prediction_logs_only:
			api_logs = APIAccessSchemeLog.objects.filter(access_date__lt=cutoff_date)
			api_log_count = api_logs.count()

			if api_log_count > 0:
				if dry_run:
					self.stdout.write(f'API Access Logs: Would delete {api_log_count} log(s)')
				else:
					deleted_count, _ = api_logs.delete()
					self.stdout.write(self.style.SUCCESS(f'API Access Logs: Deleted {deleted_count} log(s)'))
					total_deleted += deleted_count
			else:
				self.stdout.write('API Access Logs: No logs to delete')

		# Clear Prediction Run Logs
		if not api_logs_only:
			prediction_logs = PredictionRunLog.objects.filter(run_started__lt=cutoff_date)
			prediction_log_count = prediction_logs.count()

			if prediction_log_count > 0:
				if dry_run:
					self.stdout.write(f'Prediction Run Logs: Would delete {prediction_log_count} log(s)')
				else:
					deleted_count, _ = prediction_logs.delete()
					self.stdout.write(self.style.SUCCESS(f'Prediction Run Logs: Deleted {deleted_count} log(s)'))
					total_deleted += deleted_count
			else:
				self.stdout.write('Prediction Run Logs: No logs to delete')

		self.stdout.write('')
		if dry_run:
			self.stdout.write(self.style.WARNING('DRY RUN: No changes made'))
		else:
			self.stdout.write(self.style.SUCCESS(f'Total deleted: {total_deleted} log(s)'))
