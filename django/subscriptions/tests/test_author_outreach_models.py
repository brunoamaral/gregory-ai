"""
Model-level tests for AuthorOutreachCampaign and AuthorOutreach — see
docs/author-outreach.md and
docs/author-outreach-spec.md "Configuration" / "Queue and approval" / "One row
per site per author". Admin actions (approve/skip/reset) are covered
separately in test_author_outreach_admin.py.
"""

from django.contrib.sites.models import Site
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from gregory.models import Authors
from sitesettings.models import CustomSetting
from subscriptions.models import AuthorOutreach, AuthorOutreachCampaign
from subscriptions.utils.utm import build_utm_params


class AuthorOutreachCampaignCleanTest(TestCase):
	"""clean() enforces: (1) enabled requires CustomSetting.has_author_pages,
	(2) featured_within_days is meaningless outside retrospective mode."""

	def setUp(self):
		self.site = Site.objects.create(domain="outreach-clean.example.com", name="Clean")

	def _campaign(self, **kwargs):
		defaults = dict(
			site=self.site,
			name="Test Campaign",
			utm_campaign_slug="test-campaign",
			mode=AuthorOutreachCampaign.MODE_UPCOMING,
			enabled=False,
		)
		defaults.update(kwargs)
		return AuthorOutreachCampaign(**defaults)

	def test_refuses_enabled_when_has_author_pages_false(self):
		CustomSetting.objects.create(
			site=self.site, title="CS off", has_author_pages=False
		)
		campaign = self._campaign(enabled=True)
		with self.assertRaises(ValidationError) as ctx:
			campaign.clean()
		self.assertIn("enabled", ctx.exception.message_dict)

	def test_refuses_enabled_when_no_custom_setting_exists_at_all(self):
		# No CustomSetting row for this site at all — must fail closed,
		# not treat "unknown" as "author pages are fine".
		campaign = self._campaign(enabled=True)
		with self.assertRaises(ValidationError) as ctx:
			campaign.clean()
		self.assertIn("enabled", ctx.exception.message_dict)

	def test_allows_enabled_when_has_author_pages_true(self):
		CustomSetting.objects.create(
			site=self.site, title="CS on", has_author_pages=True
		)
		campaign = self._campaign(enabled=True)
		campaign.clean()  # must not raise

	def test_allows_disabled_regardless_of_has_author_pages(self):
		# No CustomSetting at all, but enabled=False — the has_author_pages
		# check must not even run.
		campaign = self._campaign(enabled=False)
		campaign.clean()  # must not raise

	def test_refuses_non_default_featured_within_days_in_upcoming_mode(self):
		campaign = self._campaign(
			mode=AuthorOutreachCampaign.MODE_UPCOMING, featured_within_days=30
		)
		with self.assertRaises(ValidationError) as ctx:
			campaign.clean()
		self.assertIn("featured_within_days", ctx.exception.message_dict)

	def test_allows_default_featured_within_days_in_upcoming_mode(self):
		campaign = self._campaign(
			mode=AuthorOutreachCampaign.MODE_UPCOMING,
			featured_within_days=AuthorOutreachCampaign.DEFAULT_FEATURED_WITHIN_DAYS,
		)
		campaign.clean()  # must not raise

	def test_allows_non_default_featured_within_days_in_retrospective_mode(self):
		campaign = self._campaign(
			mode=AuthorOutreachCampaign.MODE_RETROSPECTIVE, featured_within_days=90
		)
		campaign.clean()  # must not raise


class AuthorOutreachCampaignFieldsTest(TestCase):
	def setUp(self):
		self.site = Site.objects.create(domain="outreach-fields.example.com", name="Fields")

	def test_utm_campaign_slug_must_be_unique(self):
		AuthorOutreachCampaign.objects.create(
			site=self.site, name="First", utm_campaign_slug="shared-slug"
		)
		with self.assertRaises(IntegrityError):
			with transaction.atomic():
				AuthorOutreachCampaign.objects.create(
					site=self.site, name="Second", utm_campaign_slug="shared-slug"
				)

	def test_build_utm_params_accepts_campaign_unchanged(self):
		"""
		utm_campaign_slug is named exactly that so
		subscriptions.utils.utm.build_utm_params(source, list_obj, content)
		accepts an AuthorOutreachCampaign the same way it already accepts a
		Lists row, with no change to that function.
		"""
		campaign = AuthorOutreachCampaign.objects.create(
			site=self.site, name="UTM Campaign", utm_campaign_slug="utm-campaign-slug"
		)
		params = build_utm_params("author_outreach", campaign, "article_link")
		self.assertEqual(params["utm_campaign"], "utm-campaign-slug")
		self.assertEqual(params["utm_source"], "author_outreach")
		self.assertEqual(params["utm_medium"], "email")
		self.assertEqual(params["utm_content"], "article_link")

	def test_safety_limit_defaults_match_spec(self):
		campaign = AuthorOutreachCampaign.objects.create(
			site=self.site, name="Defaults", utm_campaign_slug="defaults-slug"
		)
		self.assertEqual(campaign.complaint_halt_absolute, 2)
		self.assertEqual(campaign.complaint_halt_rate_percent, 0.1)
		self.assertEqual(campaign.complaint_halt_rate_min_sent, 500)
		self.assertEqual(campaign.bounce_halt_absolute, 10)
		self.assertEqual(campaign.bounce_halt_rate_percent, 5.0)
		self.assertEqual(campaign.bounce_halt_rate_min_sent, 40)
		self.assertEqual(campaign.inactive_halt_absolute, 5)
		self.assertEqual(campaign.send_rate_per_minute, 20)
		self.assertEqual(campaign.daily_send_limit, 50)
		self.assertEqual(campaign.mode, AuthorOutreachCampaign.MODE_UPCOMING)
		self.assertFalse(campaign.enabled)
		self.assertFalse(campaign.halted)

	def test_history_records_changes(self):
		campaign = AuthorOutreachCampaign.objects.create(
			site=self.site, name="History", utm_campaign_slug="history-slug"
		)
		self.assertEqual(campaign.history.count(), 1)
		campaign.name = "History renamed"
		campaign.save()
		self.assertEqual(campaign.history.count(), 2)


class AuthorOutreachModelTest(TestCase):
	def setUp(self):
		self.site = Site.objects.create(domain="outreach-queue.example.com", name="Queue")
		self.other_site = Site.objects.create(
			domain="outreach-queue-2.example.com", name="Queue 2"
		)
		self.campaign = AuthorOutreachCampaign.objects.create(
			site=self.site, name="Queue Campaign", utm_campaign_slug="queue-campaign"
		)
		self.other_campaign = AuthorOutreachCampaign.objects.create(
			site=self.site, name="Second Campaign", utm_campaign_slug="second-campaign"
		)
		self.author = Authors.objects.create(
			given_name="Ada",
			family_name="Researcher",
			ORCID="0000-0000-0000-0001",
			emails=["ada@example.com"],
			orcid_claimed=True,
			orcid_verified_email=True,
		)

	def _row(self, **kwargs):
		defaults = dict(
			campaign=self.campaign,
			site=self.site,
			author=self.author,
			email="ada@example.com",
		)
		defaults.update(kwargs)
		return AuthorOutreach.objects.create(**defaults)

	def test_default_status_is_pending(self):
		row = self._row()
		row.refresh_from_db()
		self.assertEqual(row.status, AuthorOutreach.STATUS_PENDING)

	def test_unique_constraint_blocks_second_row_same_site_and_author(self):
		self._row()
		with self.assertRaises(IntegrityError):
			with transaction.atomic():
				# Different campaign, same (site, author) — the constraint
				# is on (site, author), not (campaign, author), which is
				# exactly what lets a back-catalogue campaign permanently
				# exclude an author from the steady-state campaign.
				self._row(campaign=self.other_campaign)

	def test_same_author_different_site_is_allowed(self):
		self._row()
		other_campaign_on_other_site = AuthorOutreachCampaign.objects.create(
			site=self.other_site, name="Other Site Campaign", utm_campaign_slug="other-site-campaign"
		)
		# Must not raise: a different site may still try the same author.
		self._row(campaign=other_campaign_on_other_site, site=self.other_site)

	def test_opt_out_token_is_unique_and_auto_generated(self):
		row1 = self._row()
		author2 = Authors.objects.create(
			given_name="Bob", family_name="Second", ORCID="0000-0000-0000-0002"
		)
		row2 = self._row(author=author2, email="bob@example.com")
		self.assertIsNotNone(row1.opt_out_token)
		self.assertIsNotNone(row2.opt_out_token)
		self.assertNotEqual(row1.opt_out_token, row2.opt_out_token)

	def test_legal_basis_defaults_to_legitimate_interest(self):
		row = self._row()
		self.assertEqual(
			row.legal_basis, AuthorOutreach.LEGAL_BASIS_LEGITIMATE_INTEREST
		)
