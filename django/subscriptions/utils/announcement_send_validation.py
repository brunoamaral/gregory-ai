"""
Pre-send validation for announcement emails.

Call ``validate_announcement_send_config`` before any send path
(test or live) to ensure the resolved (site, custom_settings) pair
will produce correctly-hosted image URLs.  An empty return list
means the announcement is safe to send; a non-empty list carries
human-readable error messages that should be surfaced to the author
via ``messages.error`` before aborting the send.
"""

import concurrent.futures
import logging

import requests
from bs4 import BeautifulSoup

from subscriptions.utils.render_email_body import strip_scheme

logger = logging.getLogger(__name__)

_MAX_PROBES = 10


def validate_announcement_send_config(
	announcement,           # subscriptions.Announcement
	site,                   # django.contrib.sites.models.Site | None
	custom_settings,        # sitesettings.models.CustomSetting | None
	*,
	probe_media: bool = False,
) -> list[str]:
	"""
	Return a list of human-readable error messages explaining why this
	announcement cannot be safely sent under (site, custom_settings).
	Empty list = OK to send.

	Checks are run in order; all failures are collected (no short-circuit)
	except when custom_settings is None — checks 3–5 would dereference None
	and are skipped.

	Check 1: site is configured and has a non-empty domain.
	Check 2: custom_settings exists.
	Check 3: custom_settings.api_domain is non-empty.
	Check 4: no <img> in the body points at a host other than api_domain.
	Check 5 (probe_media): HEAD each /media/ image; report non-2xx.
	"""
	errors: list[str] = []

	# --- Check 0: every list belongs to the announcement's organization ---
	offending = []
	for lst in announcement.lists.all().select_related('team'):
		if lst.team.organization_id != announcement.organization_id:
			offending.append(lst.list_name)
	if offending:
		errors.append(
			"These lists belong to a different organization than this "
			f"announcement: {', '.join(offending)}. Remove them or "
			"reassign the announcement."
		)

	# --- Check 1: site ---------------------------------------------------
	if site is None or not (site.domain or '').strip():
		errors.append("No Site is configured for this list.")
		# Without a site we cannot check api_domain either.
		return errors

	# --- Check 2: custom_settings exists ---------------------------------
	if custom_settings is None:
		errors.append(
			f"No CustomSetting exists for site {site.domain}. "
			"Create one in admin → Site settings."
		)
		# Checks 3–5 would dereference custom_settings — skip them.
		return errors

	# --- Check 3: api_domain is set --------------------------------------
	api_domain_raw = (getattr(custom_settings, 'api_domain', '') or '').strip()
	if not api_domain_raw:
		errors.append(
			f"CustomSetting for {site.domain} has no api_domain. "
			"Set it in admin → Site settings → CustomSetting."
		)
		# Without api_domain we cannot do host-comparison checks.
		return errors

	expected_host = strip_scheme(api_domain_raw)

	# --- Check 4: no baked-in absolute <img> pointing at a foreign host --
	body = announcement.body or ''
	soup = BeautifulSoup(body, 'html.parser')

	# Collect one error per distinct offending host (avoid spamming if many
	# images share the same wrong host).
	offending_hosts: dict[str, int] = {}  # host → 1-based first index seen
	for idx, img in enumerate(soup.find_all('img'), start=1):
		src = img.get('src', '')
		if src.startswith('https://'):
			from urllib.parse import urlparse as _urlparse
			host = _urlparse(src).netloc
			if host and host != expected_host and host not in offending_hosts:
				offending_hosts[host] = idx

	for host, idx in offending_hosts.items():
		errors.append(
			f"Image #{idx} points at {host}, but this list sends from "
			f"{expected_host}. Re-upload the image from the {expected_host} "
			"admin, or use a relative /media/... src."
		)

	# --- Check 5 (optional): probe /media/ images with HEAD requests ------
	if probe_media:
		media_srcs = [
			img.get('src', '')
			for img in soup.find_all('img')
			if img.get('src', '').startswith('/media/')
		][:_MAX_PROBES]

		if media_srcs:
			def _probe(src: str) -> str | None:
				url = f"https://{expected_host}{src}"
				try:
					resp = requests.head(url, timeout=1.0, allow_redirects=True)
					if not (200 <= resp.status_code < 300):
						return f"Media file not reachable (HTTP {resp.status_code}): {url}"
				except requests.RequestException as exc:
					return f"Media probe failed for {url}: {exc}"
				return None

			with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
				results = list(pool.map(_probe, media_srcs))

			for msg in results:
				if msg:
					errors.append(msg)

	return errors
