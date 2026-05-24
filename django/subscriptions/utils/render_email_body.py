"""
Utilities for rendering announcement email bodies.

Three public functions are exposed:

- ``sanitize_announcement_html(html)`` – bleach-clean the body and apply
  post-bleach attribute-value checks that bleach 4.x cannot express.
- ``render_announcement_html(html, api_domain, site_domain)`` – wrap
  ``<a class="btn-cta">`` in a bulletproof email table and rewrite
  ``/media/…`` image sources to absolute URLs.
- ``render_announcement_text(html)`` – produce a plain-text fallback from
  the sanitized HTML; buttons become ``Label: URL``, images become
  ``[Image: alt]``.

Call these in order: sanitize first, then render (HTML or text).
"""

import html as _html_module
import logging
import re
from urllib.parse import urlparse

import bleach
from bs4 import BeautifulSoup, NavigableString

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Brand constant
# ---------------------------------------------------------------------------

# Hard-coded brand accent colour.  The value ``#1e3a8a`` is the
# ``background-color`` of the header table in
# ``templates/emails/components/header.html``.  If the header colour ever
# changes, update this constant to match.
BUTTON_BRAND_HEX = '#1e3a8a'

# ---------------------------------------------------------------------------
# Bleach allowlists
# ---------------------------------------------------------------------------

ALLOWED_TAGS = [
	'p', 'strong', 'em', 'u', 's', 'ul', 'ol', 'li',
	'a', 'h2', 'h3', 'h4', 'blockquote', 'br', 'hr',
	# added for images and server-rendered button tables
	'img', 'table', 'tbody', 'tr', 'td', 'span',
]

ALLOWED_ATTRS = {
	'a':     ['href', 'target', 'rel', 'class', 'style'],
	'img':   ['src', 'alt', 'width', 'height', 'style'],
	'table': ['role', 'cellpadding', 'cellspacing', 'border', 'style', 'align'],
	'td':    ['align', 'valign', 'style'],
	'tr':    ['style'],
	'tbody': [],
	'span':  ['style'],
}

ALLOWED_STYLES = [
	# img + layout
	'max-width', 'width', 'height', 'display', 'margin',
	'border-radius',
	# button
	'background-color', 'color', 'padding', 'text-align',
	'font-family', 'font-size', 'font-weight', 'line-height',
	'text-decoration',
	'border', 'border-collapse',
]

ALLOWED_PROTOCOLS = ['http', 'https', 'mailto']

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def sanitize_announcement_html(html: str) -> str:
	"""
	Sanitize announcement body HTML.

	1. Runs ``bleach.clean`` with the expanded tag / attribute / style /
	   protocol allowlists defined above.
	2. Post-bleach pass (BeautifulSoup) to enforce rules bleach 4.x cannot
	   express natively:
	   - ``class`` on ``<a>`` is stripped unless its exact value is
	     ``btn-cta``.
	   - ``<img>`` tags whose ``src`` does not start with ``https://`` or
	     ``/media/`` are removed entirely.

	Returns sanitized HTML as a string.
	"""
	clean = bleach.clean(
		html,
		tags=ALLOWED_TAGS,
		attributes=ALLOWED_ATTRS,
		styles=ALLOWED_STYLES,
		protocols=ALLOWED_PROTOCOLS,
		strip=True,
	)

	soup = BeautifulSoup(clean, 'html.parser')

	# Strip class on <a> unless it is exactly 'btn-cta'
	for tag in soup.find_all('a'):
		cls = tag.get('class')
		if cls is not None:
			cls_str = ' '.join(cls) if isinstance(cls, list) else str(cls)
			if cls_str.strip() != 'btn-cta':
				del tag['class']

	# Remove <img> whose src is not https:// or /media/
	for img in soup.find_all('img'):
		src = img.get('src', '')
		if not (src.startswith('https://') or src.startswith('/media/')):
			img.decompose()

	return str(soup)


def render_announcement_html(
	html: str,
	api_domain: str | None,
	site_domain: str | None,
) -> str:
	"""
	Post-process sanitized announcement HTML for HTML email delivery.

	1. Replaces every ``<a class="btn-cta" href="X">L</a>`` with a
	   bulletproof email ``<table>`` (background ``BUTTON_BRAND_HEX``).
	2. Rewrites every ``<img src="/media/...">`` to an absolute URL using
	   ``api_domain`` (preferred) or ``site_domain`` as the host.  Images
	   with ``src`` already starting with ``https://`` are left unchanged.

	``api_domain`` / ``site_domain`` may contain a scheme prefix (e.g.
	``https://api.example.com``); any such prefix is stripped before the
	``https://`` we prepend, preventing double-scheme URLs.

	*html* must be the output of ``sanitize_announcement_html``; this
	function does not re-sanitize.
	"""
	soup = BeautifulSoup(html, 'html.parser')

	# 1. Wrap btn-cta anchors in bulletproof tables
	for a_tag in list(soup.find_all('a', class_='btn-cta')):
		href = a_tag.get('href', '')
		label = a_tag.get_text()
		table_soup = BeautifulSoup(_build_button_table(href, label), 'html.parser')
		table_tag = table_soup.find('table')
		a_tag.replace_with(table_tag)

	# 2. Rewrite /media/... src attributes to absolute URLs
	# Normalise: strip any existing scheme so we never produce
	# double-scheme URLs like https://https://api.example.com/...
	clean_api = _strip_scheme(api_domain)
	clean_site = _strip_scheme(site_domain)

	base: str | None = None
	if clean_api:
		base = f'https://{clean_api}'
	elif clean_site:
		base = f'https://{clean_site}'
	else:
		logger.warning(
			'render_announcement_html: neither api_domain nor site_domain is '
			'set; /media/ image src attributes will remain relative and will '
			'appear broken in email clients.'
		)

	if base:
		for img in soup.find_all('img'):
			src = img.get('src', '')
			if src.startswith('/media/'):
				img['src'] = base + src

	# Warn when an absolute https:// <img> points at a different host than expected.
	# The send path will have already blocked this case via validate_announcement_send_config;
	# the log line here creates a trail for anything that slips through a future code path.
	if base:
		api_host = _strip_scheme(api_domain) or _strip_scheme(site_domain)
		for img in soup.find_all('img'):
			src = img.get('src', '')
			if src.startswith('https://'):
				host = urlparse(src).netloc
				if api_host and host and host != api_host:
					logger.warning(
						'render_announcement_html: <img src=%r> points at %s, '
						'expected %s; leaving as-is.',
						src, host, api_host,
					)

	return str(soup)


def render_announcement_text(html: str) -> str:
	"""
	Generate a plain-text fallback from sanitized announcement HTML.

	- CTA buttons → ``Label: https://…``
	- Images       → ``[Image: alt text]``
	- Runs of 3+ blank lines are collapsed to 2.

	*html* must be the output of ``sanitize_announcement_html``.
	"""
	soup = BeautifulSoup(html, 'html.parser')

	# Replace btn-cta links with "Label: URL" text
	for a_tag in list(soup.find_all('a', class_='btn-cta')):
		href = a_tag.get('href', '')
		label = a_tag.get_text()
		a_tag.replace_with(NavigableString(f'\n\n{label}: {href}\n\n'))

	# Replace images with [Image: alt]
	for img in list(soup.find_all('img')):
		alt = img.get('alt', '').strip() or 'image'
		img.replace_with(NavigableString(f'[Image: {alt}]'))

	text = soup.get_text('\n')
	# Collapse runs of 3 or more newlines to 2
	text = re.sub(r'\n{3,}', '\n\n', text)
	return text.strip()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def strip_scheme(domain: str | None) -> str:
	"""
	Return *domain* with any leading ``http://`` or ``https://`` scheme
	stripped, keeping only the authority (host + optional port).

	Handles three input shapes:
	- ``'api.example.com'``          → ``'api.example.com'``
	- ``'https://api.example.com'``  → ``'api.example.com'``
	- ``'api.example.com/path'``     → ``'api.example.com'``
	  (path component is discarded so it never contaminates image URLs)

	Without the ``//`` prefix, ``urlparse`` treats a bare hostname or
	``host/path`` string as a *path*, meaning the path component would
	be returned verbatim and produce invalid bases such as
	``https://api.example.com/path/media/...``.
	"""
	if not domain:
		return ''
	raw = domain.strip()
	if not raw:
		return ''
	# Prepend '//' only when no scheme is already present so that urlparse
	# places the authority in netloc rather than path.
	if not raw.startswith(('http://', 'https://')):
		raw = '//' + raw
	parsed = urlparse(raw)
	# netloc may include a port (e.g. 'host:8080'); return it as-is so
	# callers that need to keep the port (for non-standard deployments) can.
	return parsed.netloc.rstrip('/')


# Private alias kept for internal callers — use strip_scheme in new code.
_strip_scheme = strip_scheme


def _build_button_table(href: str, label: str) -> str:
	"""Return bulletproof email button HTML for the given href and label."""
	safe_href = _html_module.escape(href, quote=True)
	safe_label = _html_module.escape(label)
	return (
		f'<table role="presentation" cellpadding="0" cellspacing="0" border="0"'
		f' align="center" style="margin: 24px auto;">'
		f'<tbody><tr>'
		f'<td align="center" style="border-radius: 6px;'
		f' background-color: {BUTTON_BRAND_HEX};">'
		f'<a href="{safe_href}" target="_blank" rel="noopener"'
		f' style="display: inline-block; padding: 12px 24px;'
		f' font-family: -apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, sans-serif;'
		f' font-size: 16px; font-weight: 600; line-height: 1;'
		f' color: #ffffff; text-decoration: none;'
		f' border-radius: 6px;">{safe_label}</a>'
		f'</td>'
		f'</tr></tbody>'
		f'</table>'
	)
