"""
Rendering and circuit-breaker evaluation for author outreach sends — see
docs/author-outreach-spec.md "Copy", "Configuration", "UTM", and "Safety limits",
and docs/author-outreach.md. The
send_author_outreach management command (same PR) is the only caller.

Two responsibilities live here, both read-only with respect to the database
(neither function writes anything):

- build_render_context() / render_author_outreach_email(): turn one
  AuthorOutreach row into (subject, html_body, text_body).
- evaluate_circuit_breakers(): read a campaign's live EmailMessage
  aggregates and decide whether a safety-limit breaker has tripped.

Context safety
--------------
build_render_context() returns *only* strings and a list of {title, url}
dicts — never a model instance, a QuerySet, or anything else that exposes
attribute/relation traversal. This is not only for
campaign.body_template's sake (docs/author-outreach-spec.md "Configuration":
"Rendered against an explicit context of strings and dicts only — never
model instances — so an admin-authored template can't walk ORM
relations") — the packaged default templates (emails/author_outreach.html
/.txt) are rendered against the exact same context, so there is exactly
one context-building code path for both, and the packaged templates are
held to the identical safety bar as an admin's override rather than a
looser one "because we wrote it".

Every link in the context is built and UTM-tagged here, in Python, before
the template ever sees it — the templates only interpolate finished URL
strings. That is what lets the context stay primitives-only: neither the
packaged templates nor an admin override needs (or gets) a utm_params
dict, a site object, or a site_domain to call add_utm_params themselves.
"""

import logging
import re
from datetime import datetime
from datetime import timezone as dt_timezone

from bs4 import BeautifulSoup, NavigableString
from django.template import Context, Template
from django.template.loader import get_template

from gregory.templatetags.gregory_tags import add_utm_params
from sitesettings.utils import author_page_base
from subscriptions.management.commands.utils.get_credentials import (
	build_unsubscribe_base_url,
)
from subscriptions.models import AuthorOutreachCampaign, EmailMessage
from subscriptions.utils.email_events import HARD_BOUNCE_TYPES
from subscriptions.utils.postmark import POSTMARK_INACTIVE_RECIPIENT
from subscriptions.utils.utm import build_utm_params

logger = logging.getLogger(__name__)

# Sort key floor for an author's articles with no published_date, matching
# the sentinel subscriptions.utils.author_outreach uses for the same
# purpose at build time — keeps the "most recent first" ordering
# well-defined even when every article is undated.
_MIN_DATETIME = datetime(1, 1, 1, tzinfo=dt_timezone.utc)

DEFAULT_SUBJECT_TEMPLATE = "Your research has been selected for the {site_name} newsletter"


def _site_base_url(site):
	"""scheme://domain for `site`, or "" when the site has no domain. Mirrors
	sitesettings.utils.author_page_base's localhost handling."""
	domain = (site.domain or "").strip() if site else ""
	if not domain:
		return ""
	scheme = "http" if domain in ("localhost", "127.0.0.1") else "https"
	return f"{scheme}://{domain}"


def build_render_context(row, campaign, site, custom_settings):
	"""
	Build the placeholder context for one AuthorOutreach row, per
	docs/author-outreach-spec.md "Configuration": author_name, articles (list of
	{title, url}), article_title/article_url (the first article),
	author_page_url, author_page_url_display, site_name, site_url,
	sender_name, opt_out_url.

	Every URL is already UTM-tagged (article links with
	build_utm_params("author_outreach", campaign, "article_link"); the
	author-page and site links with utm_content overridden to
	"author_page"/"site", mirroring gregory_tags.with_utm_content) and,
	via gregory_tags.add_utm_params, tagging is a no-op for anything not on
	this site's own host — the DOI/registry links this feature never
	renders would be left untouched even if a future change added one.

	Privacy: the only per-author values placed in a URL's *path* are the
	article id (public) and the author's ORCID (the author page is
	necessarily keyed on it) and, for opt_out_url, the row's own
	opt_out_token — the sole exception docs/author-outreach-spec.md "Non-goals"
	names. No author id, ORCID, or email is ever placed in a query
	parameter.
	"""
	domain = (site.domain or "").strip() if site else ""
	base_url = _site_base_url(site)
	article_utm = build_utm_params("author_outreach", campaign, "article_link")

	articles = sorted(
		row.articles.all(),
		key=lambda a: a.published_date or _MIN_DATETIME,
		reverse=True,
	)
	article_dicts = []
	for article in articles:
		url = ""
		if base_url:
			raw_url = f"{base_url}/articles/{article.article_id}/"
			url = add_utm_params(raw_url, article_utm, domain)
		article_dicts.append({"title": article.title or "", "url": url})

	author_page_url = ""
	author_page_url_display = ""
	page_base = author_page_base(site, custom_settings)
	if page_base and row.author.ORCID:
		author_page_utm = {**article_utm, "utm_content": "author_page"}
		raw_author_page_url = f"{page_base}/{row.author.ORCID}/"
		author_page_url = add_utm_params(raw_author_page_url, author_page_utm, domain)
		# Anchor *text* only — the href must stay author_page_url or the
		# click loses its utm_content=author_page attribution. Untagged and
		# scheme-stripped so a cold-outreach recipient can read where the
		# link goes before clicking it, which is worth something to someone
		# who was not expecting the email, without a query string that
		# makes a first-person message look machine-generated.
		author_page_url_display = re.sub(r"^https?://", "", raw_author_page_url)

	site_url = ""
	if base_url:
		site_utm = {**article_utm, "utm_content": "site"}
		site_url = add_utm_params(f"{base_url}/", site_utm, domain)

	# Not UTM-tagged, unlike the links above: this is the opt-out link, not
	# marketing content, matching how the existing unsubscribe links are
	# built (subscriptions.management.commands.utils.get_credentials.
	# build_unsubscribe_base_url). It resolves on the Django backend
	# (api_domain when set), not necessarily the site's public host.
	optout_base = build_unsubscribe_base_url(site, custom_settings)
	opt_out_path = f"/subscriptions/author-optout/{row.opt_out_token}/"
	opt_out_url = f"{optout_base}{opt_out_path}" if optout_base else opt_out_path

	site_name = (
		(custom_settings.title if custom_settings else "")
		or (site.name if site else "")
		or domain
		or "Gregory AI"
	)
	sender_name = (custom_settings.sender_name if custom_settings else "") or site_name
	author_name = row.author.credit_name or row.author.full_name or "there"

	return {
		"author_name": author_name,
		"articles": article_dicts,
		"article_title": article_dicts[0]["title"] if article_dicts else "",
		"article_url": article_dicts[0]["url"] if article_dicts else "",
		"author_page_url": author_page_url,
		"author_page_url_display": author_page_url_display,
		"site_name": site_name,
		"site_url": site_url,
		"sender_name": sender_name,
		"opt_out_url": opt_out_url,
	}


def render_author_outreach_email(row, campaign, site, custom_settings):
	"""
	Render one AuthorOutreach row's email. Returns (subject, html_body,
	text_body).

	Blank campaign.body_template uses the packaged default
	(emails/author_outreach.html/.txt) — "upcoming" mode only, since its
	copy's "will be featured in the next digest" claim is false, in the
	past tense, for a retrospective (back-catalogue) campaign; see
	docs/author-outreach-spec.md "The first run, and the back catalogue" and
	AuthorOutreachCampaign.body_template's help_text. A retrospective
	campaign with no body_template is refused here rather than silently
	sending a false claim — send_author_outreach also checks this once,
	up front, before touching any row, so a whole campaign's worth of
	sends can't burn slots on a config mistake one row at a time.

	A non-blank body_template is rendered via django.template.Template
	against a plain django.template.Context of build_render_context()'s
	primitives-only dict — never a model instance, per
	docs/author-outreach-spec.md "Configuration" — and used directly as the
	HTML body (paragraphs and <a> tags, the same minimal style as the
	packaged default). The .txt alternative is derived from that same
	rendered HTML by _html_to_text(), so an admin only ever writes one
	template, not a matched HTML/text pair.
	"""
	context = build_render_context(row, campaign, site, custom_settings)
	subject = campaign.subject_line or DEFAULT_SUBJECT_TEMPLATE.format(
		site_name=context["site_name"]
	)

	if campaign.body_template:
		html_body = Template(campaign.body_template).render(Context(context))
		text_body = _html_to_text(html_body)
	else:
		if campaign.mode != AuthorOutreachCampaign.MODE_UPCOMING:
			raise ValueError(
				f"Campaign '{campaign.utm_campaign_slug}' is in "
				f"'{campaign.mode}' mode but has no body_template. The "
				"packaged default template's copy is written for "
				"'upcoming' mode only — see docs/author-outreach-spec.md "
				"'The first run, and the back catalogue'. Set "
				"body_template on the campaign before sending."
			)
		html_body = get_template("emails/author_outreach.html").render(context)
		text_body = get_template("emails/author_outreach.txt").render(context)

	return subject, html_body, text_body


def _html_to_text(html):
	"""
	Convert body_template's rendered HTML (paragraphs and <a> tags only,
	per policy) into a plain-text alternative, preserving every link as
	"Label (url)" so the .txt part is a real alternative rather than
	stripped-and-broken markup — the same failure mode PR #836 fixed for
	the digest templates (see that commit's message). BeautifulSoup
	decodes HTML entities as it parses, so a URL's '&amp;' between UTM
	parameters comes back out as a literal '&', not the escaped form.
	"""
	soup = BeautifulSoup(html, "html.parser")
	for a_tag in list(soup.find_all("a")):
		href = a_tag.get("href", "")
		label = a_tag.get_text()
		text_value = f"{label} ({href})" if href else label
		a_tag.replace_with(NavigableString(text_value))
	text = soup.get_text("\n\n")
	text = re.sub(r"\n{3,}", "\n\n", text)
	return text.strip()


def evaluate_circuit_breakers(campaign):
	"""
	docs/author-outreach-spec.md "Safety limits": read this campaign's live
	EmailMessage aggregates and return a human-readable reason string the
	moment any threshold is met or exceeded, or None when every guard is
	still clear. Thresholds are read from the campaign's own fields (PR 3)
	— never hardcoded — so they can be tuned without a deploy.

	EmailMessage carries no direct FK to AuthorOutreachCampaign; the only
	path back is AuthorOutreach.email_message's reverse relation
	(related_name="author_outreach_rows"), so every count below is scoped
	to *this campaign's* sends specifically, never the whole author
	outreach feature or the other three senders sharing EmailMessage.

	send_author_outreach calls this immediately before every individual
	send (docs/author-outreach-spec.md: "The guards are evaluated before every
	individual send, not once per run"), so a breaker that trips mid-run
	is caught before the very next message goes out, not just at the next
	invocation.
	"""
	messages = EmailMessage.objects.filter(author_outreach_rows__campaign=campaign).distinct()
	sent = messages.filter(accepted=True).count()
	complaints = messages.filter(complained_at__isnull=False).count()
	hard_bounces = messages.filter(bounce_type__in=HARD_BOUNCE_TYPES).count()
	inactive = messages.filter(error_code=POSTMARK_INACTIVE_RECIPIENT).count()

	if complaints >= campaign.complaint_halt_absolute:
		return (
			f"{complaints} spam complaint(s) reached the absolute halt "
			f"threshold ({campaign.complaint_halt_absolute})."
		)
	if sent >= campaign.complaint_halt_rate_min_sent:
		rate = (complaints / sent) * 100 if sent else 0.0
		if rate > campaign.complaint_halt_rate_percent:
			return (
				f"Complaint rate {rate:.3f}% ({complaints}/{sent}) exceeds "
				f"{campaign.complaint_halt_rate_percent}% after "
				f"{campaign.complaint_halt_rate_min_sent}+ sent."
			)
	if hard_bounces >= campaign.bounce_halt_absolute:
		return (
			f"{hard_bounces} hard bounce(s) reached the absolute halt "
			f"threshold ({campaign.bounce_halt_absolute})."
		)
	if sent >= campaign.bounce_halt_rate_min_sent:
		rate = (hard_bounces / sent) * 100 if sent else 0.0
		if rate > campaign.bounce_halt_rate_percent:
			return (
				f"Hard-bounce rate {rate:.3f}% ({hard_bounces}/{sent}) "
				f"exceeds {campaign.bounce_halt_rate_percent}% after "
				f"{campaign.bounce_halt_rate_min_sent}+ sent."
			)
	if inactive >= campaign.inactive_halt_absolute:
		return (
			f"{inactive} Postmark 406 (inactive recipient) response(s) "
			f"reached the absolute halt threshold "
			f"({campaign.inactive_halt_absolute})."
		)
	return None
