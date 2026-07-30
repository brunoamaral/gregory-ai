"""
Tests for passing organization= through trial notifications.

send_trials_notification.py used to call get_optimized_email_context without
organization=, unlike send_weekly_summary and send_admin_summary, so
org_content_map was always empty for trial notifications and the
missing-organization warning in content_organizer.py didn't cover this
email type either.
"""

from unittest.mock import MagicMock, patch

from django.contrib.sites.models import Site
from django.core.management import call_command
from django.test import TestCase
from django.utils.timezone import now

from gregory.models import Articles, ArticleOrgContent, Team, Trials
from organizations.models import Organization
from sitesettings.models import CustomSetting
from subscriptions.models import Lists, ListSubscription, Subscribers
from templates.emails.components.content_organizer import get_optimized_email_context


class TrialNotificationOrgContentMapTest(TestCase):
	"""Direct unit tests of get_optimized_email_context for trial_notification."""

	@classmethod
	def setUpTestData(cls):
		cls.org = Organization.objects.create(name="TrialOrg")
		cls.other_org = Organization.objects.create(name="OtherOrg")
		cls.site = Site.objects.get_or_create(
			id=41, defaults={"domain": "trialorg.example.com", "name": "TrialOrg"}
		)[0]
		cls.article = Articles.objects.create(
			title="Trial-linked article",
			link="https://example.com/articles/trial-linked",
			doi="10.9999/trial-linked",
		)
		ArticleOrgContent.objects.create(
			article=cls.article,
			organization=cls.org,
			takeaways="Org-specific takeaways",
		)

	def test_organization_passed_builds_org_content_map(self):
		context = get_optimized_email_context(
			email_type="trial_notification",
			articles=Articles.objects.filter(pk=self.article.pk),
			trials=None,
			site=self.site,
			organization=self.org,
		)
		self.assertIn(self.article.article_id, context["org_content_map"])
		self.assertEqual(
			context["org_content_map"][self.article.article_id].takeaways,
			"Org-specific takeaways",
		)

	def test_wrong_organization_yields_empty_org_content_map(self):
		context = get_optimized_email_context(
			email_type="trial_notification",
			articles=Articles.objects.filter(pk=self.article.pk),
			trials=None,
			site=self.site,
			organization=self.other_org,
		)
		self.assertEqual(context["org_content_map"], {})

	def test_missing_organization_logs_warning_for_trial_notification(self):
		with self.assertLogs(
			"templates.emails.components.content_organizer", level="WARNING"
		) as cm:
			context = get_optimized_email_context(
				email_type="trial_notification",
				trials=None,
				site=self.site,
			)
		self.assertEqual(context["org_content_map"], {})
		warning_text = "\n".join(cm.output)
		self.assertIn("trial_notification", warning_text)


def _mock_ok_result():
	result = MagicMock(status_code=200)
	result.json.return_value = {"ErrorCode": 0, "Message": "OK"}
	return result


class SendTrialsNotificationPassesOrganizationTest(TestCase):
	"""Integration check that the command itself threads organization=
	through to get_optimized_email_context, the way send_weekly_summary and
	send_admin_summary already do."""

	def setUp(self):
		self.org = Organization.objects.create(name="Command Org")
		self.team = Team.objects.create(
			organization=self.org, name="Command Team", slug="command-team"
		)
		self.site = Site.objects.get_or_create(
			id=42, defaults={"domain": "cmdorg.example.com", "name": "CmdOrg"}
		)[0]
		self.custom_settings = CustomSetting.objects.get_or_create(
			site=self.site,
			defaults={
				"title": "Cmd Site",
				"postmark_api_token": "test-token",
				"postmark_api_url": "https://api.postmarkapp.com/email",
			},
		)[0]
		self.subject = self.team.subjects.create(
			subject_name="Cmd Subject", subject_slug="cmd-subject"
		)
		self.lst = Lists.objects.create(
			list_name="Cmd List",
			team=self.team,
			site=self.site,
			clinical_trials_notifications=True,
		)
		self.lst.subjects.add(self.subject)
		self.subscriber = Subscribers.objects.create(
			first_name="Cmd", last_name="Sub", email="cmd@example.com", active=True
		)
		ListSubscription.objects.create(
			subscriber=self.subscriber, list=self.lst, is_active=True
		)
		self.trial = Trials.objects.create(
			title="Cmd Trial",
			link="https://example.com/trials/cmd-trial",
			discovery_date=now(),
		)
		self.trial.subjects.add(self.subject)

	def test_organization_kwarg_passed_through(self):
		with (
			patch(
				"subscriptions.management.commands.send_trials_notification.get_optimized_email_context",
				wraps=get_optimized_email_context,
			) as mock_context,
			patch(
				"subscriptions.management.commands.send_trials_notification.send_email",
				return_value=_mock_ok_result(),
			),
		):
			call_command("send_trials_notification")

		self.assertTrue(mock_context.called)
		_, kwargs = mock_context.call_args
		self.assertEqual(kwargs.get("organization"), self.org)
