import io
import logging

from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.sites.models import Site
from django.core.exceptions import DisallowedHost
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.http import HttpResponseRedirect, HttpResponseBadRequest, JsonResponse
from django.shortcuts import render, get_object_or_404
from django.utils.timezone import now as tz_now
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from PIL import Image, ImageOps
from urllib.parse import urlparse

from sitesettings.models import CustomSetting
from subscriptions.forms import SubscribersForm
from subscriptions.models import Subscribers, Lists, ListSubscription, SubscriberSiteProfile

logger = logging.getLogger(__name__)


def _site_allowed_domains(site):
	"""
	Return the effective allowed_domains string for a Site.

	Combines the site's ``CustomSetting.allowed_domains`` with the Site's own
	domain so that requests from the canonical site host are always accepted,
	even when no allowed_domains are configured.
	"""
	configured = ''
	if site is not None:
		custom = CustomSetting.objects.filter(site=site).order_by('pk').first()
		if custom and custom.allowed_domains:
			configured = custom.allowed_domains
	own = (site.domain or '').strip() if site is not None else ''
	if own and configured:
		return f"{own},{configured}"
	return own or configured


def _get_redirect_base(request, site):
	"""
	Return a validated base URL (scheme + netloc) for post-subscription redirects.

	The request Origin (or Referer) must match a domain in ``site``'s
	``CustomSetting.allowed_domains`` (or the site's own domain), using
	subdomain-aware matching. If no match is found the site's own domain is
	used as a safe fallback.
	"""
	origin = request.META.get('HTTP_ORIGIN') or request.META.get('HTTP_REFERER', '')
	if origin:
		parsed = urlparse(origin)
		origin_hostname = parsed.hostname  # IPv6-safe, no port
		if origin_hostname and _origin_matches_allowed(origin_hostname, _site_allowed_domains(site)):
			return f"{parsed.scheme}://{parsed.netloc}"
	scheme = 'https' if request.is_secure() else 'http'
	return f"{scheme}://{site.domain}"


def _get_client_ip(request):
	"""Return the real client IP.

	Priority order:
	1. CF-Connecting-IP — set by Cloudflare, always the true client IP.
	2. X-Forwarded-For  — first entry when behind a trusted reverse proxy.
	3. REMOTE_ADDR      — direct connection fallback.
	"""
	cf_ip = request.META.get('HTTP_CF_CONNECTING_IP')
	if cf_ip:
		return cf_ip.strip()
	x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
	if x_forwarded_for:
		return x_forwarded_for.split(',')[0].strip()
	return request.META.get('REMOTE_ADDR')


def _find_site_by_domain(hostname):
	"""
	Look up a Site by exact domain, then by stripping one subdomain level.

	e.g. 'www.example.com' → tries exact, then tries 'example.com'.
	Accepts host strings that may include a port or IPv6 brackets; these are
	normalised safely via urlparse before the lookup.
	Returns the matching Site or None.
	"""
	host = urlparse(f'//{hostname}').hostname or ''
	host = host.lower()
	if not host:
		return None
	try:
		return Site.objects.get(domain=host)
	except Site.DoesNotExist:
		pass
	parts = host.split('.')
	if len(parts) >= 3:
		parent = '.'.join(parts[1:])
		try:
			return Site.objects.get(domain=parent)
		except Site.DoesNotExist:
			pass
	return None


def _origin_matches_allowed(origin_host, allowed_domains_str):
	"""
	Return True if origin_host (or its parent domain after stripping one
	subdomain level) appears in the comma-separated allowed_domains_str.

	origin_host may be a bare hostname or a netloc containing a port; port
	and IPv6 brackets are normalised via urlparse before comparison.
	"""
	host = urlparse(f'//{origin_host}').hostname or ''
	host = host.lower()
	allowed = {d.strip().lower() for d in (allowed_domains_str or '').split(',') if d.strip()}
	if host in allowed:
		return True
	parts = host.split('.')
	if len(parts) >= 3:
		parent = '.'.join(parts[1:])
		if parent in allowed:
			return True
	return False


def _check_origin_allowed(origin_host, site):
	"""
	Return True if origin_host is permitted to submit subscribers on this site.

	The origin must match either the site's own domain or a domain listed in
	the site's ``CustomSetting.allowed_domains`` (subdomain-aware). When no
	allowed_domains are configured, only the site's own domain is accepted.
	"""
	return _origin_matches_allowed(origin_host, _site_allowed_domains(site))


def _resolve_site_from_request(request):
	"""
	Resolve the current Site from Origin/Referer headers, falling back to
	the request Host header and finally to the default SITE_ID site.

	Subdomain-aware: 'www.example.com' resolves to the 'example.com' Site.
	"""
	origin = request.META.get('HTTP_ORIGIN') or request.META.get('HTTP_REFERER', '')
	if origin:
		parsed = urlparse(origin)
		hostname = parsed.hostname  # IPv6-safe, no port
		if hostname:
			site = _find_site_by_domain(hostname)
			if site:
				return site
	try:
		host = request.get_host()
	except DisallowedHost:
		host = None
	if host:
		site = _find_site_by_domain(host)
		if site:
			return site
	return Site.objects.get_current()


@csrf_exempt
def subscribe_view(request):
	# ``request.POST`` may contain multiple ``list`` values when the user
	# checks more than one subscription option.
	list_ids = request.POST.getlist('list')
	subscription_lists = list(Lists.objects.filter(pk__in=list_ids)) if list_ids else []

	# Derive the target site from the submitted lists so that origin validation
	# and redirect-base calculation use the correct site's allowed_domains in
	# multi-site deployments. Fall back to the global default when lists span
	# multiple sites or no lists have been resolved yet.
	site_ids = {lst.site_id for lst in subscription_lists}
	if len(site_ids) == 1:
		target_site = Site.objects.get(pk=next(iter(site_ids)))
	else:
		if len(site_ids) > 1:
			logger.warning(
				"subscribe_view: lists span multiple sites (%s); "
				"falling back to default site for origin validation.",
				site_ids,
			)
		target_site = Site.objects.get_current()

	# Determine redirect base before processing the form so we can redirect
	# to the correct domain even when the form is invalid.
	redirect_base = _get_redirect_base(request, target_site)

	# Validate that every requested list ID resolved to a real List.
	if list_ids:
		found_ids = {str(lst.pk) for lst in subscription_lists}
		missing_ids = [lid for lid in list_ids if lid not in found_ids]
		if missing_ids:
			logger.error(
				"subscribe_view: requested list IDs %s do not exist in the database. "
				"Check that the form is posting correct list IDs.",
				missing_ids,
			)
			return HttpResponseRedirect(f'{redirect_base}/error/')
	else:
		# No list selected at all — nothing to subscribe to.
		logger.error(
			"subscribe_view: no list IDs submitted. "
			"The form must include at least one 'list' field value.",
		)
		return HttpResponseRedirect(f'{redirect_base}/error/')

	# Origin validation: reject requests unless the origin matches the
	# target site's CustomSetting.allowed_domains (or the site's own domain).
	# If no Origin/Referer header is present (e.g. server-side or API usage)
	# the request is allowed through with a warning.
	origin_header = request.META.get('HTTP_ORIGIN') or request.META.get('HTTP_REFERER', '')
	if origin_header:
		parsed_origin = urlparse(origin_header)
		origin_host = parsed_origin.hostname  # IPv6-safe, no port
		if origin_host and not _check_origin_allowed(origin_host, target_site):
			logger.warning(
				"subscribe_view: request from unauthorized origin '%s' rejected.",
				origin_host,
			)
			accept_header = request.META.get('HTTP_ACCEPT', '')
			is_ajax = request.META.get('HTTP_X_REQUESTED_WITH') == 'XMLHttpRequest'
			if is_ajax or 'application/json' in accept_header:
				return JsonResponse({'error': 'Origin not permitted.'}, status=403)
			return HttpResponseRedirect(f'{redirect_base}/error/')
	else:
		logger.warning(
			"subscribe_view: no Origin or Referer header present; "
			"site attribution may be inaccurate."
		)

	subscriber_form = SubscribersForm(request.POST)

	if subscriber_form.is_valid():
		first_name = subscriber_form.cleaned_data['first_name']
		last_name = subscriber_form.cleaned_data['last_name']
		email = subscriber_form.cleaned_data['email']
		profile = subscriber_form.cleaned_data.get('profile', '')

		try:
			subscriber, created = Subscribers.objects.get_or_create(
				email=email,
				defaults={
					'first_name': first_name,
					'last_name': last_name,
				}
			)
			if not created:
				subscriber.first_name = first_name
				subscriber.last_name = last_name
				subscriber.save()

			# Resolve consent context
			client_ip = _get_client_ip(request)
			source_site = _resolve_site_from_request(request)

			# Add to lists via through-model, storing consent data
			for lst in subscription_lists:
				ls, ls_created = ListSubscription.objects.get_or_create(
					subscriber=subscriber,
					list=lst,
					defaults={
						'consent_ip': client_ip,
						'consent_source_site': source_site,
						'consent_method': 'web_form',
						'is_active': True,
					},
				)
				if not ls_created and not ls.is_active:
					# Re-subscribing: reactivate and refresh consent
					ls.is_active = True
					ls.unsubscribed_at = None
					ls.consent_ip = client_ip
					ls.consent_source_site = source_site
					ls.consent_method = 'web_form'
					ls.save(update_fields=['is_active', 'unsubscribed_at', 'consent_ip', 'consent_source_site', 'consent_method'])

			# Create or update the per-site profile
			if profile and source_site:
				SubscriberSiteProfile.objects.update_or_create(
					subscriber=subscriber,
					site=source_site,
					defaults={'profile': profile},
				)

			return HttpResponseRedirect(f'{redirect_base}/thank-you/')

		except Exception as e:
			logger.error(f"Subscription error: {e}")
			return HttpResponseRedirect(f'{redirect_base}/error/')

	else:
		logger.error("Form is invalid.")
		logger.error(subscriber_form.errors)
		return HttpResponseRedirect(f'{redirect_base}/error/')


# ---------------------------------------------------------------------------
# Unsubscribe views
# ---------------------------------------------------------------------------

def _unsubscribe_confirm(request, token, scope, extra_id=None):
	"""
	Shared helper rendering GET (confirmation form) and handling POST (action).

	scope: 'list' | 'site' | 'all'
	extra_id: list_id for scope='list', site_id for scope='site', None for 'all'
	"""
	subscriber = get_object_or_404(Subscribers, unsubscribe_token=token)

	if request.method == 'POST':
		if scope == 'list':
			ListSubscription.objects.filter(
				subscriber=subscriber,
				list_id=extra_id,
				is_active=True,
			).update(is_active=False, unsubscribed_at=tz_now())
		elif scope == 'site':
			ListSubscription.objects.filter(
				subscriber=subscriber,
				list__team__site_id=extra_id,
				is_active=True,
			).update(is_active=False, unsubscribed_at=tz_now())
		elif scope == 'all':
			subscriber.active = False
			subscriber.save(update_fields=['active'])
			ListSubscription.objects.filter(
				subscriber=subscriber,
				is_active=True,
			).update(is_active=False, unsubscribed_at=tz_now())

		return render(request, 'subscriptions/unsubscribe_done.html', {
			'subscriber': subscriber,
			'scope': scope,
		})

	# GET: show confirmation page
	context = {
		'subscriber': subscriber,
		'scope': scope,
		'extra_id': extra_id,
		'token': token,
	}
	if scope == 'list':
		context['list_obj'] = get_object_or_404(Lists, pk=extra_id)
	elif scope == 'site':
		context['site_obj'] = get_object_or_404(Site, pk=extra_id)
	return render(request, 'subscriptions/unsubscribe_confirm.html', context)


def unsubscribe_list(request, token, list_id):
	"""Unsubscribe from a single list."""
	return _unsubscribe_confirm(request, token, scope='list', extra_id=list_id)


def unsubscribe_site(request, token, site_id):
	"""Unsubscribe from all lists belonging to a specific site's teams."""
	return _unsubscribe_confirm(request, token, scope='site', extra_id=site_id)


def unsubscribe_all(request, token):
	"""Global opt-out — marks subscriber as inactive and deactivates all list subscriptions."""
	return _unsubscribe_confirm(request, token, scope='all')


# ---------------------------------------------------------------------------
# Hardened CKEditor 5 upload endpoint
# ---------------------------------------------------------------------------

_UPLOAD_MAX_SIZE = 2 * 1024 * 1024  # 2 MB
# 1 200 px — 2× the standard 600 px email body width, so images stay sharp
# on retina / HiDPI mail clients when the browser scales them to 600 css px.
_UPLOAD_MAX_WIDTH = 1200
# Per-format save quality settings (used only when a resize or EXIF-orient
# correction is needed; images already ≤ _UPLOAD_MAX_WIDTH pass through
# byte-for-byte unchanged).
_JPEG_QUALITY = 88        # above the ringing threshold on screenshots/text
_WEBP_QUALITY = 90        # WebP scale ≠ JPEG scale; ~90 gives visual parity
_PNG_COMPRESS_LEVEL = 9   # PNG is lossless — max zlib effort is safe

# User-controlled content_type is the first gate; Pillow-detected format is
# the authoritative second gate (see comment in the view).
_UPLOAD_ALLOWED_TYPES = frozenset({'image/jpeg', 'image/png', 'image/gif', 'image/webp'})
_UPLOAD_ALLOWED_FORMATS = frozenset({'JPEG', 'PNG', 'GIF', 'WEBP'})
_FORMAT_TO_CONTENT_TYPE = {
	'JPEG': 'image/jpeg',
	'PNG':  'image/png',
	'GIF':  'image/gif',
	'WEBP': 'image/webp',
}


@require_POST
@staff_member_required
def ckeditor_upload(request):
	"""
	Hardened CKEditor 5 image upload endpoint.

	Validates file size (≤ 2 MB), MIME type (JPEG / PNG / GIF / WebP),
	and Pillow readability.  Images wider than 1 200 px are resized
	(LANCZOS, aspect ratio preserved) before storage.  1 200 px is 2× the
	standard 600 px email body width, which keeps images sharp on retina /
	HiDPI mail clients.

	Images already ≤ 1 200 px wide are passed through byte-for-byte
	unchanged *unless* they carry a non-default EXIF orientation tag, in
	which case the orientation is baked in and the image is re-saved at
	high quality.

	Animated GIFs are flattened to their first frame when resizing is
	required; GIFs already ≤ 1 200 px wide pass through unchanged.

	Delegates storage to ``django_ckeditor_5.views.handle_uploaded_file``
	so the file ends up in the same place as a native CKEditor upload.

	Returns the JSON shape that the CKEditor 5 upload adapter expects:
	``{"url": "..."}`` on success, ``{"error": {"message": "..."}}`` on
	failure (HTTP 400).
	"""
	upload = request.FILES.get('upload')
	if not upload:
		return JsonResponse({'error': {'message': 'No file provided.'}}, status=400)

	# ---- size check -------------------------------------------------------
	if upload.size > _UPLOAD_MAX_SIZE:
		return JsonResponse(
			{'error': {'message': 'File too large. Maximum allowed size is 2 MB.'}},
			status=400,
		)

	# ---- MIME type check --------------------------------------------------
	if upload.content_type not in _UPLOAD_ALLOWED_TYPES:
		return JsonResponse(
			{'error': {'message': 'File type not allowed. Please upload a JPEG, PNG, GIF, or WebP image.'}},
			status=400,
		)

	# ---- Pillow integrity check -------------------------------------------
	try:
		upload.seek(0)
		Image.open(upload).verify()
	except Exception:
		return JsonResponse(
			{'error': {'message': 'The file does not appear to be a valid image.'}},
			status=400,
		)

	# Re-open after verify() (which exhausts the stream)
	upload.seek(0)
	try:
		image = Image.open(upload)
		image.load()
	except Exception:
		return JsonResponse(
			{'error': {'message': 'Could not decode image data.'}},
			status=400,
		)

	# ---- Pillow-reported format check ------------------------------------
	# content_type is user-controlled and unreliable; validate the format
	# Pillow detected from the actual file bytes.  This prevents a TIFF (or
	# other disallowed format) from slipping through with a spoofed MIME type.
	canonical_fmt = image.format  # e.g. 'JPEG', 'PNG', 'GIF', 'WEBP'
	if canonical_fmt not in _UPLOAD_ALLOWED_FORMATS:
		return JsonResponse(
			{'error': {'message': 'File type not allowed. Please upload a JPEG, PNG, GIF, or WebP image.'}},
			status=400,
		)
	canonical_content_type = _FORMAT_TO_CONTENT_TYPE[canonical_fmt]

	# ---- EXIF orientation --------------------------------------------------
	# Bake in the EXIF orientation tag (e.g. portrait phone photos stored
	# rotated).  This is a no-op for images without an orientation tag.
	# We detect whether a correction was actually needed so we can decide
	# whether to pass small images through unchanged.
	exif_orientation = image.getexif().get(0x0112, 1)  # tag 274 = Orientation
	needs_orient_fix = exif_orientation != 1
	image = ImageOps.exif_transpose(image)

	# ---- resize / re-encode if necessary ----------------------------------
	# If the image is already ≤ max width AND no EXIF fix was needed, send
	# the original bytes straight to storage without re-encoding, so there
	# is zero quality loss.
	needs_resize = image.width > _UPLOAD_MAX_WIDTH

	if not needs_resize and not needs_orient_fix:
		# Pass through original bytes unchanged.
		upload.seek(0)
	else:
		if needs_resize:
			image.thumbnail((_UPLOAD_MAX_WIDTH, 10_000), Image.LANCZOS)

		# Build per-format save kwargs with quality-preserving settings.
		save_kwargs = {'format': canonical_fmt}

		# Preserve ICC colour profile so colours don't shift.
		icc = image.info.get('icc_profile')
		if icc:
			save_kwargs['icc_profile'] = icc

		if canonical_fmt == 'JPEG':
			save_kwargs.update(
				quality=_JPEG_QUALITY,
				optimize=True,
				progressive=True,
				subsampling=2,  # 4:2:0 — Pillow default; keeps files small
			)
		elif canonical_fmt == 'WEBP':
			save_kwargs.update(
				quality=_WEBP_QUALITY,
				method=6,  # slower encoder pass, better compression ratio
			)
		elif canonical_fmt == 'PNG':
			save_kwargs.update(
				optimize=True,
				compress_level=_PNG_COMPRESS_LEVEL,
			)
		elif canonical_fmt == 'GIF':
			# Animated GIFs are flattened to the first frame on resize.
			save_kwargs['save_all'] = False

		buf = io.BytesIO()
		image.save(buf, **save_kwargs)
		buf.seek(0)
		upload = InMemoryUploadedFile(
			file=buf,
			field_name='upload',
			name=upload.name,
			content_type=canonical_content_type,
			size=buf.getbuffer().nbytes,
			charset=None,
		)

	# ---- delegate to django_ckeditor_5 storage ----------------------------
	from django_ckeditor_5.views import handle_uploaded_file
	from urllib.parse import urlparse as _urlparse
	url = handle_uploaded_file(upload)
	# Defensive: if the storage backend ever returns an absolute URL (e.g. S3),
	# collapse it to the relative /media/... path so CKEditor always inserts
	# relative srcs (decision D1 in specs/announcement-image-host-per-site.md).
	_parsed = _urlparse(url)
	if _parsed.scheme or _parsed.netloc:
		url = _parsed.path
	return JsonResponse({'url': url})
