from django.contrib.sites.models import Site
from django.test import TestCase

from .admin import CustomSettingAdminForm
from .models import CustomSetting
from .utils import author_page_base


class SenderNameFallbackTests(TestCase):
	"""The senders use `customsettings.sender_name or customsettings.title` as
	the From display name. These tests pin that fallback contract so the field
	stays backwards-compatible for sites that never set sender_name."""

	def setUp(self):
		self.site = Site.objects.create(domain="example.test", name="Example")

	def _make(self, **kwargs):
		defaults = {"site": self.site, "title": "Fallback Title"}
		defaults.update(kwargs)
		# title is unique=True, ensure each instance has a distinct one
		return CustomSetting.objects.create(**defaults)

	def test_sender_name_defaults_to_blank(self):
		cs = self._make(title="Blank Default Site")
		self.assertEqual(cs.sender_name, "")

	def test_blank_sender_name_falls_back_to_title(self):
		cs = self._make(title="My Project")
		resolved = cs.sender_name or cs.title
		self.assertEqual(resolved, "My Project")

	def test_set_sender_name_overrides_title(self):
		cs = self._make(title="Internal Project Name", sender_name="Public Brand")
		resolved = cs.sender_name or cs.title
		self.assertEqual(resolved, "Public Brand")


class AuthorPageBaseTests(TestCase):
	"""author_page_base resolves the base URL for a site's author profile
	pages, or "" when the site doesn't publish them - see
	docs/06-organisations-teams-and-sites.md#author-profile-page-links."""

	def setUp(self):
		self.site = Site.objects.create(domain="brain-regeneration.com", name="BR")

	def _make(self, **kwargs):
		defaults = {"site": self.site, "title": "Some Title"}
		defaults.update(kwargs)
		return CustomSetting.objects.create(**defaults)

	def test_flag_off_returns_empty(self):
		cs = self._make(title="Flag Off", has_author_pages=False)
		self.assertEqual(author_page_base(self.site, cs), "")

	def test_flag_on_returns_base_url(self):
		cs = self._make(title="Flag On", has_author_pages=True)
		self.assertEqual(
			author_page_base(self.site, cs),
			"https://brain-regeneration.com/authors",
		)

	def test_no_custom_setting_returns_empty(self):
		site = Site.objects.create(domain="no-settings.test", name="No Settings")
		self.assertEqual(author_page_base(site), "")

	def test_blank_domain_returns_empty(self):
		site = Site.objects.create(domain="   ", name="Blank Domain")
		cs = CustomSetting.objects.create(
			site=site, title="Blank Domain Site", has_author_pages=True
		)
		self.assertEqual(author_page_base(site, cs), "")

	def test_localhost_uses_http_scheme(self):
		site = Site.objects.create(domain="localhost", name="Localhost")
		cs = CustomSetting.objects.create(
			site=site, title="Localhost Site", has_author_pages=True
		)
		self.assertEqual(author_page_base(site, cs), "http://localhost/authors")

	def test_lowest_setting_id_wins_when_multiple_rows(self):
		first = self._make(title="First Row", has_author_pages=True)
		self._make(title="Second Row", has_author_pages=False)
		self.assertEqual(
			author_page_base(self.site),
			"https://brain-regeneration.com/authors",
		)
		self.assertEqual(
			CustomSetting.objects.filter(site=self.site)
			.order_by("setting_id")
			.first()
			.setting_id,
			first.setting_id,
		)

	def test_missing_site_returns_empty(self):
		self.assertEqual(author_page_base(None), "")


class SitemapTrialStatusesAdminFormTests(TestCase):
	"""The tickbox widget writes into a Postgres ArrayField. A
	MultipleChoiceField hands back a list of strings, which ArrayField
	accepts — pin that round trip so a widget change can't silently start
	storing something the sitemap query won't match."""

	def setUp(self):
		self.site = Site.objects.create(domain="statuses.test", name="Statuses")

	def _form(self, statuses):
		data = {
			"site": self.site.pk,
			"title": "Statuses Site",
			"sender_email_prefix": "gregory",
			"sitemap_trial_statuses": statuses,
		}
		return CustomSettingAdminForm(data=data)

	def test_defaults_to_empty_list(self):
		cs = CustomSetting.objects.create(site=self.site, title="Default Statuses")
		self.assertEqual(cs.sitemap_trial_statuses, [])

	def test_selected_statuses_round_trip_as_a_list(self):
		form = self._form(["recruiting", "not_yet_recruiting"])
		self.assertTrue(form.is_valid(), form.errors)
		saved = form.save()
		saved.refresh_from_db()
		self.assertEqual(
			sorted(saved.sitemap_trial_statuses),
			["not_yet_recruiting", "recruiting"],
		)

	def test_no_selection_saves_an_empty_list_not_a_blank_string(self):
		form = self._form([])
		self.assertTrue(form.is_valid(), form.errors)
		saved = form.save()
		saved.refresh_from_db()
		# [""] would make the sitemap filter match nothing at all.
		self.assertEqual(saved.sitemap_trial_statuses, [])

	def test_status_outside_the_enum_is_rejected(self):
		form = self._form(["definitely_not_a_status"])
		self.assertFalse(form.is_valid())
		self.assertIn("sitemap_trial_statuses", form.errors)
