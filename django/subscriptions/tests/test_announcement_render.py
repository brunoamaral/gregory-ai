"""
Tests for announcement email rendering helpers.

Covers:
- SanitizeAnnouncementHtmlTests  — bleach + post-pass behaviour
- RenderAnnouncementHtmlTests    — button wrap, image URL rewrite
- RenderAnnouncementTextTests    — plain-text fallback shape
- AnnouncementAdminFormTests     — alt-text form validation
- CKEditorUploadViewTests        — size, type, resize, auth
"""

import io

from bs4 import BeautifulSoup
from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client
from PIL import Image

from subscriptions.utils.render_email_body import (
	sanitize_announcement_html,
	render_announcement_html,
	render_announcement_text,
	BUTTON_BRAND_HEX,
)

User = get_user_model()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _delete_if_exists(storage_path: str) -> None:
	"""Silently delete *storage_path* from default_storage if it exists."""
	try:
		if default_storage.exists(storage_path):
			default_storage.delete(storage_path)
	except Exception:
		pass


def _make_jpeg(width=800, height=400, color="red") -> bytes:
	buf = io.BytesIO()
	Image.new("RGB", (width, height), color).save(buf, "JPEG")
	return buf.getvalue()


def _make_png(width=100, height=100, color="blue") -> bytes:
	buf = io.BytesIO()
	Image.new("RGB", (width, height), color).save(buf, "PNG")
	return buf.getvalue()


def _make_gif(width=50, height=50) -> bytes:
	buf = io.BytesIO()
	Image.new("P", (width, height)).save(buf, "GIF")
	return buf.getvalue()


def _make_webp(width=100, height=100, color="green") -> bytes:
	buf = io.BytesIO()
	Image.new("RGB", (width, height), color).save(buf, "WEBP")
	return buf.getvalue()


# ---------------------------------------------------------------------------
# sanitize_announcement_html
# ---------------------------------------------------------------------------


class SanitizeAnnouncementHtmlTests(TestCase):
	def test_keeps_img_with_allowed_attrs(self):
		html = '<img src="https://example.com/img.png" alt="test" width="300" height="200" style="max-width:100%;">'
		result = sanitize_announcement_html(html)
		soup = BeautifulSoup(result, "html.parser")
		img = soup.find("img")
		self.assertIsNotNone(img)
		self.assertEqual(img["src"], "https://example.com/img.png")
		self.assertEqual(img["alt"], "test")

	def test_strips_script_tag(self):
		# bleach with strip=True removes the <script> element but keeps the
		# text content.  The executable tag is gone so the content cannot run.
		html = '<p>Hello</p><script>alert("xss")</script>'
		result = sanitize_announcement_html(html)
		self.assertNotIn("<script>", result)
		self.assertNotIn("</script>", result)

	def test_strips_iframe(self):
		html = '<iframe src="https://evil.com"></iframe>'
		result = sanitize_announcement_html(html)
		self.assertNotIn("iframe", result)

	def test_strips_style_tag(self):
		html = "<style>body { color: red; }</style><p>text</p>"
		result = sanitize_announcement_html(html)
		self.assertNotIn("<style>", result)

	def test_strips_onerror_attribute(self):
		html = '<img src="https://example.com/img.png" alt="x" onerror="alert(1)">'
		result = sanitize_announcement_html(html)
		self.assertNotIn("onerror", result)

	def test_strips_img_with_javascript_src(self):
		html = '<img src="javascript:alert(1)" alt="bad">'
		result = sanitize_announcement_html(html)
		soup = BeautifulSoup(result, "html.parser")
		self.assertIsNone(soup.find("img"))

	def test_strips_img_with_data_src(self):
		html = '<img src="data:image/png;base64,abc123" alt="bad">'
		result = sanitize_announcement_html(html)
		soup = BeautifulSoup(result, "html.parser")
		self.assertIsNone(soup.find("img"))

	def test_keeps_img_with_media_src(self):
		html = '<img src="/media/uploads/photo.jpg" alt="a photo">'
		result = sanitize_announcement_html(html)
		soup = BeautifulSoup(result, "html.parser")
		img = soup.find("img")
		self.assertIsNotNone(img)
		self.assertEqual(img["src"], "/media/uploads/photo.jpg")

	def test_keeps_btn_cta_class_on_anchor(self):
		html = '<a class="btn-cta" href="https://example.com">Click me</a>'
		result = sanitize_announcement_html(html)
		soup = BeautifulSoup(result, "html.parser")
		a = soup.find("a")
		self.assertIsNotNone(a)
		cls = a.get("class", [])
		self.assertIn("btn-cta", cls if isinstance(cls, list) else [cls])

	def test_strips_arbitrary_class_on_anchor(self):
		html = '<a class="evil-class" href="https://example.com">Click</a>'
		result = sanitize_announcement_html(html)
		soup = BeautifulSoup(result, "html.parser")
		a = soup.find("a")
		self.assertIsNotNone(a)
		self.assertNotIn("class", a.attrs)

	def test_strips_multiple_classes_on_anchor(self):
		"""class='btn-cta extra' is not exactly 'btn-cta' and must be stripped."""
		html = '<a class="btn-cta extra" href="https://example.com">X</a>'
		result = sanitize_announcement_html(html)
		soup = BeautifulSoup(result, "html.parser")
		a = soup.find("a")
		self.assertIsNotNone(a)
		self.assertNotIn("class", a.attrs)

	def test_preserves_allowed_tags(self):
		html = "<p><strong>bold</strong> and <em>italic</em></p>"
		result = sanitize_announcement_html(html)
		self.assertIn("<strong>", result)
		self.assertIn("<em>", result)


# ---------------------------------------------------------------------------
# render_announcement_html
# ---------------------------------------------------------------------------


class RenderAnnouncementHtmlTests(TestCase):
	def test_wraps_btn_cta_in_table(self):
		html = '<p>Intro</p><a class="btn-cta" href="https://example.com">Join Now</a>'
		sanitized = sanitize_announcement_html(html)
		result = render_announcement_html(sanitized, "api.example.com", None)
		soup = BeautifulSoup(result, "html.parser")
		# Original <a class="btn-cta"> should be gone
		self.assertIsNone(soup.find("a", class_="btn-cta"))
		# A <table> must be present
		table = soup.find("table")
		self.assertIsNotNone(table)
		# The <a> inside the table must carry the href and brand colour
		a = table.find("a")
		self.assertIsNotNone(a)
		self.assertEqual(a["href"], "https://example.com")
		self.assertIn(BUTTON_BRAND_HEX, result)
		self.assertIn("Join Now", a.get_text())

	def test_rewrites_media_src_to_absolute_with_api_domain(self):
		html = '<img src="/media/uploads/photo.jpg" alt="photo">'
		sanitized = sanitize_announcement_html(html)
		result = render_announcement_html(sanitized, "api.example.com", None)
		self.assertIn("https://api.example.com/media/uploads/photo.jpg", result)

	def test_falls_back_to_site_domain_when_api_domain_empty(self):
		html = '<img src="/media/uploads/photo.jpg" alt="photo">'
		sanitized = sanitize_announcement_html(html)
		result = render_announcement_html(sanitized, "", "site.example.com")
		self.assertIn("https://site.example.com/media/uploads/photo.jpg", result)

	def test_leaves_https_img_src_unchanged(self):
		html = '<img src="https://cdn.example.com/photo.jpg" alt="photo">'
		sanitized = sanitize_announcement_html(html)
		result = render_announcement_html(sanitized, "api.example.com", None)
		self.assertIn("https://cdn.example.com/photo.jpg", result)
		# Must NOT be double-prefixed
		self.assertNotIn("https://api.example.com/https://", result)

	def test_multiple_buttons_all_wrapped(self):
		html = (
			'<a class="btn-cta" href="https://a.com">A</a>'
			'<a class="btn-cta" href="https://b.com">B</a>'
		)
		sanitized = sanitize_announcement_html(html)
		result = render_announcement_html(sanitized, "api.example.com", None)
		soup = BeautifulSoup(result, "html.parser")
		tables = soup.find_all("table")
		self.assertEqual(len(tables), 2)

	def test_api_domain_with_scheme_does_not_produce_double_scheme(self):
		"""api_domain stored as 'https://api.example.com' must not yield
		https://https://api.example.com/media/…"""
		html = '<img src="/media/uploads/photo.jpg" alt="photo">'
		sanitized = sanitize_announcement_html(html)
		result = render_announcement_html(sanitized, "https://api.example.com", None)
		self.assertIn("https://api.example.com/media/uploads/photo.jpg", result)
		self.assertNotIn("https://https://", result)

	def test_api_domain_with_path_does_not_contaminate_media_url(self):
		"""api_domain stored with a trailing path (e.g. 'api.example.com/foo')
		must not produce https://api.example.com/foo/media/..."""
		html = '<img src="/media/uploads/photo.jpg" alt="photo">'
		sanitized = sanitize_announcement_html(html)
		result = render_announcement_html(sanitized, "api.example.com/foo", None)
		self.assertIn("https://api.example.com/media/uploads/photo.jpg", result)
		self.assertNotIn("/foo/media/", result)

	def test_logs_warning_on_foreign_absolute_img_host(self):
		"""A foreign-host <img src="https://..."> must emit a logger.warning and
		be left unchanged in the rendered output (not silently rewritten)."""
		html = '<img src="https://other.com/x.png" alt="external">'
		sanitized = sanitize_announcement_html(html)
		with self.assertLogs(
			"subscriptions.utils.render_email_body", level="WARNING"
		) as cm:
			result = render_announcement_html(sanitized, "api.ex.com", None)
		# Warning must name both the offending host and the expected host.
		warning_text = "\n".join(cm.output)
		self.assertIn("other.com", warning_text)
		self.assertIn("api.ex.com", warning_text)
		# The src must NOT be silently rewritten.
		self.assertIn("https://other.com/x.png", result)


# ---------------------------------------------------------------------------
# render_announcement_text
# ---------------------------------------------------------------------------


class RenderAnnouncementTextTests(TestCase):
	def test_button_becomes_label_colon_url(self):
		html = '<a class="btn-cta" href="https://example.com/join">Join Now</a>'
		sanitized = sanitize_announcement_html(html)
		text = render_announcement_text(sanitized)
		self.assertIn("Join Now: https://example.com/join", text)

	def test_image_becomes_bracket_alt(self):
		html = '<img src="https://example.com/photo.jpg" alt="A lovely photo">'
		sanitized = sanitize_announcement_html(html)
		text = render_announcement_text(sanitized)
		self.assertIn("[Image: A lovely photo]", text)

	def test_image_without_alt_uses_default(self):
		# No alt — will be stripped by sanitizer (src is https://, so kept by
		# sanitizer, but alt is missing; render_announcement_text should use
		# the 'image' fallback).
		html = '<img src="https://example.com/photo.jpg" alt="">'
		sanitized = sanitize_announcement_html(html)
		text = render_announcement_text(sanitized)
		self.assertIn("[Image: image]", text)

	def test_plain_text_paragraph(self):
		html = "<p>Hello, <strong>world</strong>!</p>"
		sanitized = sanitize_announcement_html(html)
		text = render_announcement_text(sanitized)
		self.assertIn("Hello,", text)
		self.assertIn("world", text)

	def test_collapses_excess_blank_lines(self):
		html = "<p>A</p><p>B</p><p>C</p>"
		sanitized = sanitize_announcement_html(html)
		text = render_announcement_text(sanitized)
		# Should not have more than 2 consecutive newlines
		import re

		self.assertIsNone(re.search(r"\n{3,}", text))


# ---------------------------------------------------------------------------
# AnnouncementAdminForm — alt-text validation
# ---------------------------------------------------------------------------


class AnnouncementAdminFormTests(TestCase):
	def _make_form(self, body):
		from subscriptions.forms import AnnouncementAdminForm

		return AnnouncementAdminForm(
			data={
				"subject": "Test Subject",
				"body": body,
				"lists": [],
			}
		)

	def test_valid_when_no_images(self):
		form = self._make_form("<p>No images here.</p>")
		# Only check the body field validation
		form.full_clean()
		self.assertNotIn("body", form.errors)

	def test_valid_when_all_images_have_alt(self):
		body = '<img src="https://example.com/img.jpg" alt="Descriptive text">'
		form = self._make_form(body)
		form.full_clean()
		self.assertNotIn("body", form.errors)

	def test_invalid_when_image_missing_alt(self):
		body = '<img src="https://example.com/img.jpg">'
		form = self._make_form(body)
		form.full_clean()
		self.assertIn("body", form.errors)
		self.assertIn("Alt text is required", form.errors["body"][0])

	def test_invalid_when_image_has_empty_alt(self):
		body = '<img src="https://example.com/img.jpg" alt="   ">'
		form = self._make_form(body)
		form.full_clean()
		self.assertIn("body", form.errors)

	def test_error_names_offending_image_index(self):
		body = (
			'<img src="https://a.com/1.jpg" alt="ok">'
			'<img src="https://a.com/2.jpg">'  # missing alt
			'<img src="https://a.com/3.jpg" alt="">'  # empty alt
		)
		form = self._make_form(body)
		form.full_clean()
		error_msg = form.errors["body"][0]
		self.assertIn("2", error_msg)
		self.assertIn("3", error_msg)


# ---------------------------------------------------------------------------
# CKEditorUploadViewTests
# ---------------------------------------------------------------------------


class CKEditorUploadViewTests(TestCase):
	"""
	Tests for the hardened CKEditor 5 upload endpoint.

	Each successful upload registers a cleanup callback (via ``addCleanup``)
	that deletes the stored file from ``default_storage``, keeping the
	MEDIA_ROOT clean across test runs and preventing leftover files in CI.
	"""

	UPLOAD_URL = "/ckeditor5/image_upload/"

	def setUp(self):
		self.client = Client()
		self.staff_user = User.objects.create_user(
			username="staff", password="pass", is_staff=True
		)
		self.plain_user = User.objects.create_user(
			username="plain", password="pass", is_staff=False
		)

	def _post(self, files=None, user=None):
		if user:
			self.client.force_login(user)
		resp = self.client.post(self.UPLOAD_URL, data=files or {})
		# Register cleanup for any file the endpoint stored successfully.
		if resp.status_code == 200:
			url = resp.json().get("url", "")
			if url:
				# The URL is /media/<path>; extract the relative storage path.
				storage_path = url.lstrip("/")
				if storage_path.startswith("media/"):
					storage_path = storage_path[len("media/") :]
				self.addCleanup(_delete_if_exists, storage_path)
		return resp

	# ---- auth -----------------------------------------------------------

	def test_anonymous_returns_302_redirect(self):
		resp = self.client.post(self.UPLOAD_URL)
		self.assertIn(resp.status_code, [302, 403])

	def test_non_staff_returns_302_redirect(self):
		resp = self._post(user=self.plain_user)
		self.assertIn(resp.status_code, [302, 403])

	# ---- size check -----------------------------------------------------

	def test_oversize_file_rejected(self):
		data = b"x" * (2 * 1024 * 1024 + 1)
		upload = SimpleUploadedFile("big.jpg", data, content_type="image/jpeg")
		resp = self._post(files={"upload": upload}, user=self.staff_user)
		self.assertEqual(resp.status_code, 400)
		self.assertIn("error", resp.json())

	# ---- type check -----------------------------------------------------

	def test_svg_rejected(self):
		svg_data = b'<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>'
		upload = SimpleUploadedFile("icon.svg", svg_data, content_type="image/svg+xml")
		resp = self._post(files={"upload": upload}, user=self.staff_user)
		self.assertEqual(resp.status_code, 400)
		self.assertIn("error", resp.json())

	def test_wrong_mime_type_rejected(self):
		upload = SimpleUploadedFile(
			"doc.pdf", b"%PDF-1.4", content_type="application/pdf"
		)
		resp = self._post(files={"upload": upload}, user=self.staff_user)
		self.assertEqual(resp.status_code, 400)

	# ---- resize ---------------------------------------------------------

	def test_oversized_jpeg_is_resized(self):
		"""Upload a 1600×400 JPEG; the stored file must be ≤ 1200 px wide."""
		jpeg_data = _make_jpeg(width=1600, height=400)
		upload = SimpleUploadedFile("wide.jpg", jpeg_data, content_type="image/jpeg")
		resp = self._post(files={"upload": upload}, user=self.staff_user)
		self.assertEqual(resp.status_code, 200)
		url = resp.json().get("url", "")
		self.assertTrue(url, "Response should contain a url key")
		# Derive the storage path from the URL and verify pixel width.
		storage_path = url.lstrip("/")
		if storage_path.startswith("media/"):
			storage_path = storage_path[len("media/") :]
		with default_storage.open(storage_path, "rb") as f:
			img = Image.open(f)
			img.load()
			self.assertLessEqual(img.width, 1200)

	def test_small_png_passes_through_unchanged(self):
		"""Upload a 100×100 PNG; it must be accepted and returned as-is."""
		png_data = _make_png(width=100, height=100)
		upload = SimpleUploadedFile("small.png", png_data, content_type="image/png")
		resp = self._post(files={"upload": upload}, user=self.staff_user)
		self.assertEqual(resp.status_code, 200)
		self.assertIn("url", resp.json())

	def test_gif_accepted(self):
		gif_data = _make_gif(width=50, height=50)
		upload = SimpleUploadedFile("anim.gif", gif_data, content_type="image/gif")
		resp = self._post(files={"upload": upload}, user=self.staff_user)
		self.assertEqual(resp.status_code, 200)
		self.assertIn("url", resp.json())

	def test_webp_accepted(self):
		webp_data = _make_webp(width=100, height=100)
		upload = SimpleUploadedFile("photo.webp", webp_data, content_type="image/webp")
		resp = self._post(files={"upload": upload}, user=self.staff_user)
		self.assertEqual(resp.status_code, 200)
		self.assertIn("url", resp.json())

	def test_non_image_binary_rejected(self):
		"""A JPEG content-type but random bytes must be rejected."""
		upload = SimpleUploadedFile(
			"fake.jpg", b"\x00\xff\xfe\xfd" * 100, content_type="image/jpeg"
		)
		resp = self._post(files={"upload": upload}, user=self.staff_user)
		self.assertEqual(resp.status_code, 400)
