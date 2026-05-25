from django.contrib.sites.models import Site
from django.test import TestCase

from .models import CustomSetting


class SenderNameFallbackTests(TestCase):
	"""The senders use `customsettings.sender_name or customsettings.title` as
	the From display name. These tests pin that fallback contract so the field
	stays backwards-compatible for sites that never set sender_name."""

	def setUp(self):
		self.site = Site.objects.create(domain='example.test', name='Example')

	def _make(self, **kwargs):
		defaults = {'site': self.site, 'title': 'Fallback Title'}
		defaults.update(kwargs)
		# title is unique=True, ensure each instance has a distinct one
		return CustomSetting.objects.create(**defaults)

	def test_sender_name_defaults_to_blank(self):
		cs = self._make(title='Blank Default Site')
		self.assertEqual(cs.sender_name, '')

	def test_blank_sender_name_falls_back_to_title(self):
		cs = self._make(title='My Project')
		resolved = cs.sender_name or cs.title
		self.assertEqual(resolved, 'My Project')

	def test_set_sender_name_overrides_title(self):
		cs = self._make(title='Internal Project Name', sender_name='Public Brand')
		resolved = cs.sender_name or cs.title
		self.assertEqual(resolved, 'Public Brand')
