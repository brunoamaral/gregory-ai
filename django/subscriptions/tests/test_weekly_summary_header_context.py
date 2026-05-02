"""
Tests verifying that list-level email header customization fields
(header_title, header_tagline, show_header_tagline) are injected into
the template context by the send_weekly_summary management command.
"""
import os
from io import StringIO
from unittest.mock import patch, MagicMock

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gregory.tests.test_settings')

import django
django.setup()

from django.contrib.sites.models import Site
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from datetime import timedelta

from gregory.models import Articles, Subject, Team
from organizations.models import Organization
from sitesettings.models import CustomSetting
from subscriptions.models import Lists, Subscribers


class TestWeeklySummaryHeaderContextInjection(TestCase):
	"""
	Verify that header_title, header_tagline, and show_header_tagline from the
	Lists model are correctly injected into the weekly summary email context.
	"""

	def setUp(self):
		self.organization = Organization.objects.create(
			name="Header Test Org", slug="header-test-org"
		)
		self.team = Team.objects.create(
			name="Header Test Team",
			organization=self.organization,
			slug="header-test-team",
		)
		self.subject = Subject.objects.create(
			subject_name="Neurology",
			team=self.team,
			subject_slug="neurology",
		)
		self.digest_list = Lists.objects.create(
			list_name="Neurology Weekly",
			weekly_digest=True,
			team=self.team,
			ml_threshold=0.0,
			list_email_subject="Your Neurology Digest",
		)
		self.digest_list.subjects.add(self.subject)

		self.subscriber = Subscribers.objects.create(
			first_name="Alice",
			last_name="Test",
			email="alice@example.com",
			active=True,
		)
		self.subscriber.subscriptions.add(self.digest_list)

		# Recent article so the command finds it
		self.article = Articles.objects.create(
			title="Test Neurology Article",
			discovery_date=timezone.now() - timedelta(days=1),
			doi="10.9999/test-header",
		)
		self.article.subjects.add(self.subject)

		self.site = Site.objects.get_or_create(
			id=1,
			defaults={"domain": "testserver", "name": "Test Site"},
		)[0]
		self.custom_settings = CustomSetting.objects.get_or_create(
			site=self.site,
			defaults={
				"title": "Test Site",
				"postmark_api_token": "test-token",
				"postmark_api_url": "https://api.postmarkapp.com/email",
			},
		)[0]

	def _run_and_capture_context(self):
		"""
		Run send_weekly_summary, intercept the context dict passed to
		template.render(), and return it.  send_email is mocked to prevent
		real HTTP requests.
		"""
		captured = {}

		real_get_template = __import__(
			'django.template.loader', fromlist=['get_template']
		).get_template

		def fake_get_template(template_name, using=None):
			tmpl = real_get_template(template_name, using=using)
			original_render = tmpl.render

			def capturing_render(context=None, request=None):
				if isinstance(context, dict):
					captured.update(context)
				return original_render(context, request)

			tmpl.render = capturing_render
			return tmpl

		with patch(
			'subscriptions.management.commands.send_weekly_summary.send_email',
			return_value={'status': 'ok'},
		), patch(
			'subscriptions.management.commands.send_weekly_summary.get_template',
			side_effect=fake_get_template,
		):
			out = StringIO()
			call_command('send_weekly_summary', stdout=out, all_articles=True)

		return captured

	# ── Tests ────────────────────────────────────────────────────────────────

	def test_header_title_injected(self):
		"""header_title set on the list appears in the template context."""
		self.digest_list.header_title = "MS Research Weekly"
		self.digest_list.save()

		ctx = self._run_and_capture_context()

		self.assertEqual(ctx.get('header_title'), "MS Research Weekly")

	def test_header_tagline_injected(self):
		"""header_tagline set on the list appears in the template context."""
		self.digest_list.header_tagline = "Your weekly briefing"
		self.digest_list.save()

		ctx = self._run_and_capture_context()

		self.assertEqual(ctx.get('header_tagline'), "Your weekly briefing")

	def test_show_header_tagline_true(self):
		"""show_header_tagline=True is passed through to the context."""
		self.digest_list.show_header_tagline = True
		self.digest_list.save()

		ctx = self._run_and_capture_context()

		self.assertTrue(ctx.get('show_header_tagline'))

	def test_show_header_tagline_false(self):
		"""show_header_tagline=False is passed through to the context."""
		self.digest_list.show_header_tagline = False
		self.digest_list.save()

		ctx = self._run_and_capture_context()

		self.assertFalse(ctx.get('show_header_tagline'))

	def test_empty_header_title_becomes_empty_string(self):
		"""When header_title is None, the context receives an empty string (not None)."""
		self.digest_list.header_title = None
		self.digest_list.save()

		ctx = self._run_and_capture_context()

		self.assertEqual(ctx.get('header_title'), '')

	def test_empty_header_tagline_becomes_empty_string(self):
		"""When header_tagline is None, the context receives an empty string (not None)."""
		self.digest_list.header_tagline = None
		self.digest_list.save()

		ctx = self._run_and_capture_context()

		self.assertEqual(ctx.get('header_tagline'), '')
