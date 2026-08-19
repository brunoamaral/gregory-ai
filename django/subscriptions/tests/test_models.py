import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from django.test import TestCase
from organizations.models import Organization
from gregory.models import Team
from subscriptions.models import Lists, Subscribers


class SubscribersModelTest(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name="Test Org")
		self.team = Team.objects.create(
			organization=self.org, name="Alpha", slug="alpha"
		)

	def test_email_saved_lowercase(self):
		subscriber = Subscribers.objects.create(
			first_name="John", last_name="Doe", email="TEST@EXAMPLE.COM"
		)
		self.assertEqual(subscriber.email, "test@example.com")

	def test_str_representation(self):
		subscriber = Subscribers.objects.create(
			first_name="John", last_name="Doe", email="john@example.com"
		)
		self.assertEqual(str(subscriber), "John Doe (john@example.com)")


class ListsModelTest(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name="Test Org")
		self.team = Team.objects.create(
			organization=self.org, name="Alpha", slug="alpha"
		)

	def test_str_representation(self):
		lst = Lists.objects.create(list_name="Daily", team=self.team)
		self.assertEqual(str(lst), "Daily (Team: Alpha)")


class ListsUtmCampaignSlugTest(TestCase):
	"""
	utm_campaign_slug is set once from list_name at creation and must
	stay stable through renames, so renaming a list never forks its
	analytics history in Umami. Django's slugify() folds accents to
	ASCII and strips everything else that isn't [a-z0-9-], which removes
	the percent-encoding hazard the old `utm_campaign_<list_name>`
	scheme had.
	"""

	def setUp(self):
		self.org = Organization.objects.create(name="Slug Org")
		self.team = Team.objects.create(
			organization=self.org, name="Slug Team", slug="slug-team"
		)

	def _slug_for(self, list_name):
		lst = Lists.objects.create(list_name=list_name, team=self.team)
		return lst.utm_campaign_slug

	def test_accents_are_folded_to_ascii(self):
		slug = self._slug_for("Neuroinflamação Café")
		self.assertRegex(slug, r"^[a-z0-9_-]+$")
		self.assertNotIn("ç", slug)
		self.assertNotIn("ã", slug)

	def test_apostrophe_produces_valid_slug(self):
		slug = self._slug_for("Parkinson's Digest")
		self.assertRegex(slug, r"^[a-z0-9_-]+$")

	def test_ampersand_produces_valid_slug(self):
		slug = self._slug_for("MS & ALS Weekly")
		self.assertRegex(slug, r"^[a-z0-9_-]+$")

	def test_parentheses_produce_valid_slug(self):
		slug = self._slug_for("Weekly Digest (EU)")
		self.assertRegex(slug, r"^[a-z0-9_-]+$")

	def test_slash_produces_valid_slug(self):
		slug = self._slug_for("MS/ALS Research")
		self.assertRegex(slug, r"^[a-z0-9_-]+$")

	def test_renaming_list_leaves_slug_unchanged(self):
		lst = Lists.objects.create(list_name="Original Name", team=self.team)
		original_slug = lst.utm_campaign_slug
		self.assertTrue(original_slug)

		lst.list_name = "Completely Different Name"
		lst.save()

		lst.refresh_from_db()
		self.assertEqual(lst.utm_campaign_slug, original_slug)

	def test_explicit_slug_is_not_overwritten(self):
		lst = Lists.objects.create(
			list_name="Custom Slug List",
			team=self.team,
			utm_campaign_slug="hand-picked-slug",
		)
		self.assertEqual(lst.utm_campaign_slug, "hand-picked-slug")
