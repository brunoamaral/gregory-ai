"""
Unit tests for the hardened CKEditor 5 image-upload endpoint.

Covers the behaviours listed in PR #665 Copilot review comment #3299685317:

    1. Spoofed MIME type vs Pillow-detected format — PNG uploaded with
       content_type='image/jpeg'; confirm the file stored by the backend
       carries content_type='image/png' (Pillow-detected canonical value).
    2. EXIF orientation baking — JPEG with Orientation tag 6 (90° rotated);
       confirm the stored image is transposed and the original bytes are NOT
       passed through (needs_orient_fix path was taken).
    3. Pass-through preserves bytes — image ≤ 1 200 px wide, no EXIF rotation;
       confirm the bytes sent to the storage backend are identical to the
       uploaded bytes.
    4. Post-encode size warning — re-encoded buffer exceeds _UPLOAD_MAX_SIZE;
       confirm logger.warning is emitted and the upload still returns HTTP 200.
    5. Per-format quality kwargs — JPEG: quality=88, progressive=True,
       optimize=True; WebP: quality=90; PNG: optimize=True, compress_level=9.
    6. File too large (> 2 MB) → HTTP 400.
    7. Disallowed format (TIFF) with spoofed JPEG MIME → HTTP 400.

Run inside the Docker container:
    python manage.py test subscriptions.tests.test_ckeditor_upload
"""

import io
import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, RequestFactory

from PIL import Image

from subscriptions.views import (
	ckeditor_upload,
	_UPLOAD_MAX_SIZE,
	_UPLOAD_MAX_WIDTH,
	_JPEG_QUALITY,
	_WEBP_QUALITY,
	_PNG_COMPRESS_LEVEL,
)

User = get_user_model()


# ---------------------------------------------------------------------------
# Image-building helpers
# ---------------------------------------------------------------------------


def _make_jpeg(width=100, height=100, color="red") -> bytes:
	"""Return JPEG bytes for a solid-colour image with NO EXIF data."""
	buf = io.BytesIO()
	Image.new("RGB", (width, height), color).save(buf, "JPEG")
	return buf.getvalue()


def _make_jpeg_with_exif_orientation(
	orientation: int = 6,
	width: int = 100,
	height: int = 200,
	color: str = "red",
) -> bytes:
	"""
	Return JPEG bytes for a solid-colour image carrying the given EXIF
	Orientation tag value (default 6 = 90° rotation stored).
	"""
	img = Image.new("RGB", (width, height), color)
	exif = img.getexif()
	exif[0x0112] = orientation  # tag 274 = Orientation
	buf = io.BytesIO()
	img.save(buf, "JPEG", exif=exif.tobytes())
	return buf.getvalue()


def _make_png(width=100, height=100, color="blue") -> bytes:
	buf = io.BytesIO()
	Image.new("RGB", (width, height), color).save(buf, "PNG")
	return buf.getvalue()


def _make_webp(width=100, height=100, color="green") -> bytes:
	buf = io.BytesIO()
	Image.new("RGB", (width, height), color).save(buf, "WEBP")
	return buf.getvalue()


def _make_tiff(width=100, height=100, color="yellow") -> bytes:
	buf = io.BytesIO()
	Image.new("RGB", (width, height), color).save(buf, "TIFF")
	return buf.getvalue()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class CKEditorUploadViewTests(TestCase):
	"""
	Unit tests for ``subscriptions.views.ckeditor_upload``.

	Uses ``RequestFactory`` so requests are constructed in-process without URL
	routing overhead.  ``django_ckeditor_5.views.handle_uploaded_file`` is
	patched in every test to avoid writing files to ``MEDIA_ROOT`` and to let
	us inspect exactly what would have been stored.
	"""

	def setUp(self) -> None:
		self.factory = RequestFactory()
		# No usable password — tests inject the user via RequestFactory
		# (request.user = self.staff_user) and never call authenticate().
		self.staff_user = User.objects.create_user(
			username="ck_upload_staff",
			is_staff=True,
			is_active=True,
		)
		self.staff_user.set_unusable_password()
		self.staff_user.save(update_fields=["password"])

	# ------------------------------------------------------------------
	# Internal helper
	# ------------------------------------------------------------------

	def _post(self, upload_file, *, patch_handle: bool = True):
		"""
		POST *upload_file* to the view as the staff user.

		When *patch_handle* is ``True`` (default), replaces
		``handle_uploaded_file`` with a capturing stub so no real files are
		written.

		Returns ``(response, captured_file)`` where *captured_file* is the
		``UploadedFile``-like object that was (or would have been) handed to
		``handle_uploaded_file``.  *captured_file* is ``None`` if the view
		returned early before calling ``handle_uploaded_file``.
		"""
		captured: dict = {}

		def capturing_handle(file_obj):
			captured["file"] = file_obj
			return "/media/uploads/test.jpg"

		request = self.factory.post("/", data={"upload": upload_file})
		request.user = self.staff_user

		if patch_handle:
			with mock.patch(
				"django_ckeditor_5.views.handle_uploaded_file",
				side_effect=capturing_handle,
			):
				response = ckeditor_upload(request)
		else:
			response = ckeditor_upload(request)

		return response, captured.get("file")

	# ------------------------------------------------------------------
	# 1. Spoofed MIME type — Pillow-detected format wins
	# ------------------------------------------------------------------

	def test_spoofed_mime_png_as_jpeg_stores_canonical_content_type(self):
		"""
		A PNG file uploaded with ``content_type='image/jpeg'`` must be stored
		with ``content_type='image/png'`` (the Pillow-detected canonical value),
		not the client-supplied ``'image/jpeg'``.
		"""
		png_data = _make_png(100, 100)
		upload = SimpleUploadedFile("image.jpg", png_data, content_type="image/jpeg")

		response, stored_file = self._post(upload)

		self.assertEqual(response.status_code, 200)
		self.assertIsNotNone(stored_file, "handle_uploaded_file was not called")
		self.assertEqual(
			stored_file.content_type,
			"image/png",
			"Stored file must carry the Pillow-detected canonical content-type, "
			"not the client-supplied spoofed value.",
		)

	# ------------------------------------------------------------------
	# 2. EXIF orientation — needs_orient_fix path; original bytes not used
	# ------------------------------------------------------------------

	def test_exif_orientation_6_stored_bytes_differ_from_upload(self):
		"""
		A JPEG with EXIF Orientation tag 6 (90° rotation) triggers the
		re-encode path; the bytes delivered to the storage backend must differ
		from the original uploaded bytes.
		"""
		jpeg_data = _make_jpeg_with_exif_orientation(
			orientation=6, width=100, height=200
		)
		upload = SimpleUploadedFile("rotated.jpg", jpeg_data, content_type="image/jpeg")

		response, stored_file = self._post(upload)

		self.assertEqual(response.status_code, 200)
		self.assertIsNotNone(stored_file)
		stored_file.seek(0)
		stored_bytes = stored_file.read()
		self.assertNotEqual(
			stored_bytes,
			jpeg_data,
			"Re-encode path must produce different bytes than the original upload.",
		)

	def test_exif_orientation_6_stored_image_has_orientation_1(self):
		"""
		After EXIF orientation baking the stored JPEG must carry
		Orientation = 1 (or no tag), confirming the rotation was baked in and
		mail clients will display the image upright without further rotation.
		"""
		# 100 px wide × 200 px tall portrait stored with orientation 6.
		jpeg_data = _make_jpeg_with_exif_orientation(
			orientation=6, width=100, height=200
		)
		upload = SimpleUploadedFile("rotated.jpg", jpeg_data, content_type="image/jpeg")

		response, stored_file = self._post(upload)

		self.assertEqual(response.status_code, 200)
		stored_file.seek(0)
		stored_img = Image.open(stored_file)
		stored_img.load()

		# Tag 0x0112 = 274 = Orientation; default 1 means "no rotation needed".
		orientation = stored_img.getexif().get(0x0112, 1)
		self.assertEqual(
			orientation,
			1,
			f"Expected stored Orientation=1 (baked in), got {orientation}.",
		)

		# Bonus: dimensions must be swapped because the 90° rotation was applied
		# to the pixel data.  Original 100×200 → stored 200×100.
		self.assertEqual(stored_img.width, 200)
		self.assertEqual(stored_img.height, 100)

	# ------------------------------------------------------------------
	# 3. Pass-through — small image with no EXIF rotation
	# ------------------------------------------------------------------

	def test_passthrough_small_jpeg_bytes_are_identical_to_upload(self):
		"""
		A JPEG already ≤ _UPLOAD_MAX_WIDTH px wide and carrying no non-default
		EXIF orientation must be forwarded to the storage backend byte-for-byte
		unchanged (zero quality loss from re-encoding).
		"""
		# Use a width well within the 1 200 px ceiling; solid colour has no EXIF.
		jpeg_data = _make_jpeg(width=200, height=200, color="blue")
		upload = SimpleUploadedFile("small.jpg", jpeg_data, content_type="image/jpeg")

		response, stored_file = self._post(upload)

		self.assertEqual(response.status_code, 200)
		self.assertIsNotNone(stored_file)
		stored_file.seek(0)
		stored_bytes = stored_file.read()
		self.assertEqual(
			stored_bytes,
			jpeg_data,
			"Pass-through path must forward the exact original bytes unchanged.",
		)

	# ------------------------------------------------------------------
	# 4. Post-encode size warning
	# ------------------------------------------------------------------

	def test_post_encode_size_warning_logged_upload_still_succeeds(self):
		"""
		When the re-encoded output exceeds ``_UPLOAD_MAX_SIZE``,
		``logger.warning`` must be emitted (so operators can monitor and
		adjust quality settings), but the response must still be HTTP 200
		— the upload is NOT rejected.

		Strategy: patch ``_UPLOAD_MAX_SIZE`` to a value just above the raw
		upload bytes so the initial size gate passes, then replace
		``Image.Image.save`` to write more bytes than the patched ceiling,
		ensuring the post-encode warning branch is taken deterministically.
		"""
		jpeg_data = _make_jpeg_with_exif_orientation(
			orientation=6, width=100, height=100
		)
		upload_size = len(jpeg_data)

		# patched_max must be:
		#   > upload_size   → initial size gate passes
		#   < encoded_size  → post-encode warning fires
		# The fake save writes patched_max+1 bytes, so encoded_size = patched_max+1.
		patched_max = upload_size + 100

		def oversized_save(self_img, fp, **kwargs):
			"""Write more bytes than patched_max to trigger the warning branch."""
			fp.write(b"\xff" * (patched_max + 1))

		upload = SimpleUploadedFile("orient.jpg", jpeg_data, content_type="image/jpeg")

		with (
			mock.patch("subscriptions.views._UPLOAD_MAX_SIZE", patched_max),
			mock.patch.object(Image.Image, "save", oversized_save),
			mock.patch(
				"django_ckeditor_5.views.handle_uploaded_file",
				return_value="/media/uploads/t.jpg",
			),
			self.assertLogs("subscriptions.views", level="WARNING") as log_ctx,
		):
			request = self.factory.post("/", data={"upload": upload})
			request.user = self.staff_user
			response = ckeditor_upload(request)

		self.assertEqual(
			response.status_code,
			200,
			"Upload must succeed even when re-encoded size warning fires.",
		)
		warning_lines = [m for m in log_ctx.output if "WARNING" in m]
		self.assertTrue(
			any("re-encoded" in m for m in warning_lines),
			f'Expected a "re-encoded" warning log entry; got: {log_ctx.output}',
		)

	# ------------------------------------------------------------------
	# 5. Per-format quality kwargs
	# ------------------------------------------------------------------

	def _assert_save_kwargs(self, upload_file, expected_kwargs: dict) -> None:
		"""
		Call the view with *upload_file*, intercept the ``PIL.Image.Image.save``
		call that happens during the re-encode path, and assert that every
		key/value pair in *expected_kwargs* is present in the captured kwargs.
		"""
		saved_kwargs: dict = {}

		def capturing_save(self_img, fp, **kwargs):
			saved_kwargs.update(kwargs)
			# Write one sentinel byte so encoded_size > 0 and the view can
			# construct the InMemoryUploadedFile without errors.
			fp.write(b"\x00")

		request = self.factory.post("/", data={"upload": upload_file})
		request.user = self.staff_user

		with (
			mock.patch.object(Image.Image, "save", capturing_save),
			mock.patch(
				"django_ckeditor_5.views.handle_uploaded_file",
				return_value="/media/uploads/t.jpg",
			),
		):
			response = ckeditor_upload(request)

		self.assertEqual(response.status_code, 200)
		for kwarg_name, expected_value in expected_kwargs.items():
			self.assertEqual(
				saved_kwargs.get(kwarg_name),
				expected_value,
				f'PIL Image.save kwarg "{kwarg_name}": '
				f"expected {expected_value!r}, got {saved_kwargs.get(kwarg_name)!r}. "
				f"Full captured kwargs: {saved_kwargs}",
			)

	def test_jpeg_saved_with_correct_quality_kwargs(self):
		"""
		JPEG re-encode must use quality={q}, progressive=True, optimize=True.
		""".format(q=_JPEG_QUALITY)
		# EXIF orientation triggers the re-encode path without needing
		# an excessively wide image.
		jpeg_data = _make_jpeg_with_exif_orientation(
			orientation=6, width=100, height=100
		)
		upload = SimpleUploadedFile("test.jpg", jpeg_data, content_type="image/jpeg")
		self._assert_save_kwargs(
			upload,
			{
				"quality": _JPEG_QUALITY,  # 88
				"progressive": True,
				"optimize": True,
			},
		)

	def test_webp_saved_with_correct_quality_kwargs(self):
		"""WebP re-encode must use quality={q}.""".format(q=_WEBP_QUALITY)
		# Use a width > _UPLOAD_MAX_WIDTH so the resize path is taken.
		webp_data = _make_webp(width=_UPLOAD_MAX_WIDTH + 100, height=100)
		upload = SimpleUploadedFile("test.webp", webp_data, content_type="image/webp")
		self._assert_save_kwargs(
			upload,
			{
				"quality": _WEBP_QUALITY,  # 90
			},
		)

	def test_png_saved_with_correct_compress_kwargs(self):
		"""PNG re-encode must use optimize=True, compress_level={c}.""".format(
			c=_PNG_COMPRESS_LEVEL
		)
		# Use a width > _UPLOAD_MAX_WIDTH so the resize path is taken.
		png_data = _make_png(width=_UPLOAD_MAX_WIDTH + 100, height=100)
		upload = SimpleUploadedFile("test.png", png_data, content_type="image/png")
		self._assert_save_kwargs(
			upload,
			{
				"optimize": True,
				"compress_level": _PNG_COMPRESS_LEVEL,  # 9
			},
		)

	# ------------------------------------------------------------------
	# 6. File too large (> 2 MB)
	# ------------------------------------------------------------------

	def test_oversize_upload_rejected_with_400(self):
		"""
		An upload whose size exceeds ``_UPLOAD_MAX_SIZE`` (2 MB) must be
		rejected immediately with HTTP 400 before any PIL processing occurs.
		"""
		oversize_data = b"x" * (_UPLOAD_MAX_SIZE + 1)
		upload = SimpleUploadedFile("big.jpg", oversize_data, content_type="image/jpeg")

		response, stored_file = self._post(upload)

		self.assertEqual(response.status_code, 400)
		self.assertIsNone(
			stored_file,
			"handle_uploaded_file must not be called for oversized uploads.",
		)
		# RequestFactory returns raw Django response objects; parse JSON manually.
		payload = json.loads(response.content)
		self.assertIn("error", payload)
		self.assertIn("message", payload["error"])

	# ------------------------------------------------------------------
	# 7. Disallowed format (TIFF) with spoofed MIME
	# ------------------------------------------------------------------

	def test_tiff_with_spoofed_jpeg_mime_rejected_with_400(self):
		"""
		A valid TIFF image uploaded with ``content_type='image/jpeg'`` must be
		rejected with HTTP 400 after Pillow detects the real format ('TIFF'),
		which is not in ``_UPLOAD_ALLOWED_FORMATS``.
		"""
		tiff_data = _make_tiff(100, 100)
		upload = SimpleUploadedFile("sneaky.jpg", tiff_data, content_type="image/jpeg")

		response, stored_file = self._post(upload)

		self.assertEqual(response.status_code, 400)
		self.assertIsNone(
			stored_file,
			"handle_uploaded_file must not be called for disallowed formats.",
		)
		# RequestFactory returns raw Django response objects; parse JSON manually.
		payload = json.loads(response.content)
		self.assertIn("error", payload)
		self.assertIn("message", payload["error"])
