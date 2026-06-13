"""
Tests for subscriptions.utils.announcement_send_validation.

All tests use MagicMock for site/custom_settings/announcement — no DB needed.
"""

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from subscriptions.utils.announcement_send_validation import (
	validate_announcement_send_config,
)


def _make_site(domain="example.com"):
	s = MagicMock()
	s.domain = domain
	return s


def _make_cs(api_domain="api.example.com"):
	cs = MagicMock()
	cs.api_domain = api_domain
	return cs


def _make_announcement(body=""):
	ann = MagicMock()
	ann.body = body
	# Ensure lists.all().select_related() returns an empty iterable so the
	# new org-check in validate_announcement_send_config does not raise.
	ann.lists.all.return_value.select_related.return_value = []
	return ann


class ValidateOkTests(SimpleTestCase):
	"""Configurations that should return an empty error list."""

	def test_returns_empty_list_when_all_checks_pass(self):
		ann = _make_announcement('<img src="/media/foo.png">')
		errors = validate_announcement_send_config(
			ann,
			_make_site("ex.com"),
			_make_cs("api.ex.com"),
		)
		self.assertEqual(errors, [])

	def test_allows_https_img_matching_api_domain(self):
		ann = _make_announcement('<img src="https://api.ex.com/media/foo.png">')
		errors = validate_announcement_send_config(
			ann,
			_make_site("ex.com"),
			_make_cs("api.ex.com"),
		)
		self.assertEqual(errors, [])

	def test_tolerates_api_domain_with_scheme(self):
		"""api_domain stored with https:// prefix should still match."""
		ann = _make_announcement('<img src="https://api.ex.com/media/foo.png">')
		errors = validate_announcement_send_config(
			ann,
			_make_site("ex.com"),
			_make_cs("https://api.ex.com"),
		)
		self.assertEqual(errors, [])

	def test_no_images_in_body(self):
		ann = _make_announcement("<p>Hello world</p>")
		errors = validate_announcement_send_config(
			ann,
			_make_site("ex.com"),
			_make_cs("api.ex.com"),
		)
		self.assertEqual(errors, [])


class ValidateBlockTests(SimpleTestCase):
	"""Configurations that should produce at least one error."""

	def test_blocks_when_site_is_none(self):
		ann = _make_announcement()
		errors = validate_announcement_send_config(ann, None, _make_cs())
		self.assertTrue(errors)
		self.assertIn("No Site", errors[0])

	def test_blocks_when_site_domain_empty(self):
		ann = _make_announcement()
		errors = validate_announcement_send_config(ann, _make_site(""), _make_cs())
		self.assertTrue(errors)
		self.assertIn("No Site", errors[0])

	def test_blocks_when_site_domain_whitespace_only(self):
		ann = _make_announcement()
		errors = validate_announcement_send_config(ann, _make_site("   "), _make_cs())
		self.assertTrue(errors)

	def test_blocks_when_custom_settings_missing(self):
		ann = _make_announcement()
		site = _make_site("ex.com")
		errors = validate_announcement_send_config(ann, site, None)
		self.assertTrue(errors)
		self.assertIn("ex.com", errors[0])
		self.assertIn("CustomSetting", errors[0])

	def test_blocks_when_api_domain_blank(self):
		ann = _make_announcement()
		site = _make_site("ex.com")
		cs = _make_cs("")
		errors = validate_announcement_send_config(ann, site, cs)
		self.assertTrue(errors)
		self.assertIn("api_domain", errors[0])

	def test_blocks_when_api_domain_whitespace_only(self):
		ann = _make_announcement()
		errors = validate_announcement_send_config(
			ann, _make_site("ex.com"), _make_cs("   ")
		)
		self.assertTrue(errors)

	def test_blocks_when_body_has_foreign_absolute_img_host(self):
		ann = _make_announcement('<img src="https://api.other.com/media/foo.png">')
		errors = validate_announcement_send_config(
			ann,
			_make_site("ex.com"),
			_make_cs("api.ex.com"),
		)
		self.assertTrue(errors)
		self.assertIn("api.other.com", errors[0])
		self.assertIn("api.ex.com", errors[0])

	def test_groups_distinct_foreign_hosts(self):
		"""Two images on api.other.com + one on api.third.com → exactly 2 errors."""
		body = (
			'<img src="https://api.other.com/media/a.png">'
			'<img src="https://api.other.com/media/b.png">'
			'<img src="https://api.third.com/media/c.png">'
		)
		ann = _make_announcement(body)
		errors = validate_announcement_send_config(
			ann,
			_make_site("ex.com"),
			_make_cs("api.ex.com"),
		)
		self.assertEqual(len(errors), 2)
		hosts = {e.split("points at ")[1].split(",")[0] for e in errors}
		self.assertEqual(hosts, {"api.other.com", "api.third.com"})


class ProbeMediaTests(SimpleTestCase):
	"""Tests for the optional probe_media=True path."""

	def test_probe_media_off_does_not_hit_network(self):
		ann = _make_announcement('<img src="/media/foo.png">')
		with patch(
			"subscriptions.utils.announcement_send_validation.requests.head",
			side_effect=AssertionError("should not call requests.head"),
		):
			errors = validate_announcement_send_config(
				ann,
				_make_site("ex.com"),
				_make_cs("api.ex.com"),
				probe_media=False,
			)
		self.assertEqual(errors, [])

	def test_probe_media_on_reports_404(self):
		ann = _make_announcement('<img src="/media/missing.png">')
		mock_resp = MagicMock()
		mock_resp.status_code = 404
		with patch(
			"subscriptions.utils.announcement_send_validation.requests.head",
			return_value=mock_resp,
		):
			errors = validate_announcement_send_config(
				ann,
				_make_site("ex.com"),
				_make_cs("api.ex.com"),
				probe_media=True,
			)
		self.assertTrue(errors)
		self.assertIn("404", errors[0])
		self.assertIn("/media/missing.png", errors[0])

	def test_probe_media_caps_at_10(self):
		"""Body with 15 /media/ images must probe at most 10."""
		imgs = "".join(f'<img src="/media/img{i}.png">' for i in range(15))
		ann = _make_announcement(imgs)
		call_count = []

		def _fake_head(url, **kwargs):
			call_count.append(url)
			r = MagicMock()
			r.status_code = 200
			return r

		with patch(
			"subscriptions.utils.announcement_send_validation.requests.head",
			side_effect=_fake_head,
		):
			validate_announcement_send_config(
				ann,
				_make_site("ex.com"),
				_make_cs("api.ex.com"),
				probe_media=True,
			)
		self.assertLessEqual(len(call_count), 10)

	def test_probe_media_request_exception_reported(self):
		ann = _make_announcement('<img src="/media/img.png">')
		import requests as _requests

		with patch(
			"subscriptions.utils.announcement_send_validation.requests.head",
			side_effect=_requests.ConnectionError("connection refused"),
		):
			errors = validate_announcement_send_config(
				ann,
				_make_site("ex.com"),
				_make_cs("api.ex.com"),
				probe_media=True,
			)
		self.assertTrue(errors)
		self.assertIn("connection refused", errors[0])
