"""
Shared announcement-sending logic, used by both the admin "Send" action and
the ``send_announcement`` management command, so the two call sites cannot
drift the way the three digest commands once did (see
subscriptions.utils.postmark).

``send_announcement`` is idempotent and resumable: any subscriber who already
has a successful AnnouncementRecipient row for the announcement is skipped,
so calling it again after a partial run (crash, transient Postmark outage,
retrying a "failed" announcement) never re-mails anyone who already received
it. recipients_count/failures_count are always recomputed from
AnnouncementRecipient rows rather than incremented, so counts stay correct
across multiple partial runs.
"""

import logging

import requests
from django.contrib.sites.models import Site
from django.template.loader import render_to_string
from django.utils import timezone

from sitesettings.models import CustomSetting
from subscriptions.management.commands.utils.get_credentials import (
	build_unsubscribe_base_url,
	get_postmark_credentials,
	get_site_and_settings,
)
from subscriptions.management.commands.utils.send_email import (
	send_email,
	record_sent_message,
)
from subscriptions.models import AnnouncementRecipient, Subscribers
from subscriptions.utils.announcement_send_validation import (
	validate_announcement_send_config,
)
from subscriptions.utils.postmark import (
	POSTMARK_INACTIVE_RECIPIENT,
	classify_postmark_response,
)
from subscriptions.utils.render_email_body import (
	apply_utm_params_to_html,
	render_announcement_html,
	render_announcement_text as _render_text_body,
	sanitize_announcement_html,
)
from subscriptions.utils.suppression import deactivate_subscribers
from subscriptions.utils.utm import build_utm_params
from gregory.templatetags.gregory_tags import with_utm_content as _with_utm_content

logger = logging.getLogger(__name__)


def render_announcement_email(
	announcement,
	subscriber=None,
	site=None,
	list_id=None,
	custom_settings=None,
	unsubscribe_lists=None,
	utm_params=None,
):
	"""Render announcement as HTML email using the base template."""
	api_domain = (
		(getattr(custom_settings, "api_domain", "") or "") if custom_settings else ""
	)
	site_domain = (getattr(site, "domain", "") or "") if site else ""
	sanitized = sanitize_announcement_html(announcement.body)
	body_utm_params = (
		_with_utm_content(utm_params, "announcement_body") if utm_params else None
	)
	sanitized = apply_utm_params_to_html(sanitized, body_utm_params, site_domain)
	rendered_body = render_announcement_html(sanitized, api_domain, site_domain)
	context = {
		"announcement_subject": announcement.subject,
		"announcement_body": rendered_body,
		"utm_params": utm_params or {},
		"site_domain": site_domain,
		"email_type": "announcement",
		"show_date": True,
		"current_date": timezone.now(),
		"header_title": announcement.header_title,
		"header_tagline": announcement.header_tagline,
		"show_header_tagline": announcement.show_header_tagline,
		"preheader_text": announcement.preheader_text,
		"title": getattr(custom_settings, "title", "Gregory AI")
		if custom_settings
		else "Gregory AI",
		"website_url": getattr(custom_settings, "website_url", "")
		if custom_settings
		else "",
		"support_url": getattr(custom_settings, "support_url", "")
		if custom_settings
		else "",
		"about_url": getattr(custom_settings, "about_url", "")
		if custom_settings
		else "",
		"contact_url": getattr(custom_settings, "contact_url", "")
		if custom_settings
		else "",
		"bluesky_url": getattr(custom_settings, "bluesky_url", "")
		if custom_settings
		else "",
		"github_url": getattr(custom_settings, "github_url", "")
		if custom_settings
		else "",
		"mastodon_url": getattr(custom_settings, "mastodon_url", "")
		if custom_settings
		else "",
		"privacy_policy_url": getattr(custom_settings, "privacy_policy_url", "")
		if custom_settings
		else "",
		"terms_url": getattr(custom_settings, "terms_url", "")
		if custom_settings
		else "",
	}
	if subscriber:
		context["subscriber"] = subscriber
	if site:
		context["unsubscribe_base_url"] = build_unsubscribe_base_url(
			site, custom_settings
		)
		context["site"] = site
	if list_id:
		context["list_id"] = list_id
	if unsubscribe_lists:
		context["unsubscribe_lists"] = unsubscribe_lists
	return render_to_string("emails/announcement.html", context)


def render_announcement_text(announcement, subscriber=None, utm_params=None, site_domain=None):
	"""Render plain-text version of the announcement."""
	lines = []
	if subscriber and subscriber.first_name:
		lines.append(f"Hello {subscriber.first_name},\n")
	sanitized = sanitize_announcement_html(announcement.body)
	body_utm_params = (
		_with_utm_content(utm_params, "announcement_body") if utm_params else None
	)
	sanitized = apply_utm_params_to_html(sanitized, body_utm_params, site_domain)
	lines.append(_render_text_body(sanitized))
	return "\n".join(lines)


def _resolve_site_and_settings(lst):
	try:
		return get_site_and_settings(lst.team, list_obj=lst)
	except CustomSetting.DoesNotExist:
		return lst.site or Site.objects.get_current(), None


def send_announcement(announcement):
	"""
	Perform the Postmark send loop for `announcement`, sending to every
	active subscriber of every list attached to it (deduplicated by email,
	attributed to the first list encountered) who does not already have a
	successful AnnouncementRecipient row.

	Sets announcement.status to "sending" immediately, and to "sent" or
	"failed" (based on non-suppressed failures only) once the loop
	completes. Safe to call again on a "failed" announcement, or on one
	left stuck in "sending" by a crash — already-successful recipients are
	skipped, not re-mailed.

	Returns a summary dict: {"sent", "suppressed", "failed", "skipped"}.
	"""
	announcement.status = "sending"
	announcement.save(update_fields=["status"])

	target_lists = list(
		announcement.lists.select_related("team__organization", "site").all()
	)

	# email -> (subscriber, attributed_list, [every matched list]) — first
	# list wins attribution (AnnouncementRecipient.list), but every list a
	# subscriber matched is kept so the footer can render one unsubscribe
	# link per list instead of an ambiguous single one.
	all_subscribers = {}
	for lst in target_lists:
		active_subs = Subscribers.objects.filter(
			list_subscriptions__list=lst,
			list_subscriptions__is_active=True,
			active=True,
		).distinct()
		for sub in active_subs:
			if sub.email not in all_subscribers:
				all_subscribers[sub.email] = (sub, lst, [lst])
			else:
				all_subscribers[sub.email][2].append(lst)

	already_sent_subscriber_ids = set(
		AnnouncementRecipient.objects.filter(
			announcement=announcement, success=True
		).values_list("subscriber_id", flat=True)
	)

	list_credentials = {}  # list_id -> None (invalid) or (api_token, api_url, site, custom_settings)
	skipped = 0

	for subscriber, lst, matched_lists in all_subscribers.values():
		if subscriber.subscriber_id in already_sent_subscriber_ids:
			skipped += 1
			continue

		pk = lst.list_id
		if pk not in list_credentials:
			site, custom_settings = _resolve_site_and_settings(lst)
			errors = validate_announcement_send_config(
				announcement, site, custom_settings
			)
			if errors:
				logger.error(
					"Skipping list '%s' for announcement %s: %s",
					lst.list_name,
					announcement.pk,
					"; ".join(errors),
				)
				list_credentials[pk] = None
			else:
				api_token, api_url = get_postmark_credentials(
					custom_settings=custom_settings,
					organization=lst.team.organization,
				)
				list_credentials[pk] = (api_token, api_url, site, custom_settings)

		creds = list_credentials[pk]
		if creds is None:
			# List config broke between queueing and sending — record so it
			# surfaces in failures_count rather than silently vanishing.
			AnnouncementRecipient.objects.update_or_create(
				announcement=announcement,
				subscriber=subscriber,
				defaults={
					"list": lst,
					"success": False,
					"suppressed": False,
					"error_message": (
						f"List '{lst.list_name}' failed send validation at send time."
					),
				},
			)
			continue

		api_token, api_url, site, custom_settings = creds

		# Attributed to the same list as AnnouncementRecipient.list — the
		# first list a subscriber matched — so utm_campaign lines up with
		# whichever list's unsubscribe link and analytics history this
		# send is already recorded against.
		utm_params = build_utm_params("announcement", lst, "announcement_body")
		site_domain = (getattr(site, "domain", "") or "") if site else ""

		html = render_announcement_email(
			announcement,
			subscriber=subscriber,
			site=site,
			list_id=lst.list_id,
			custom_settings=custom_settings,
			unsubscribe_lists=[(l.list_id, l.list_name) for l in matched_lists],
			utm_params=utm_params,
		)
		text = render_announcement_text(
			announcement,
			subscriber=subscriber,
			utm_params=utm_params,
			site_domain=site_domain,
		)

		live_subject = announcement.subject
		if live_subject.startswith("[TEST] "):
			live_subject = live_subject[7:]
		sender_name = (
			(custom_settings.sender_name or custom_settings.title)
			if custom_settings
			else None
		) or "Gregory AI"

		success = False
		suppressed = False
		error_msg = ""
		response = None
		try:
			response = send_email(
				to=subscriber.email,
				subject=live_subject,
				html=html,
				text=text,
				site=site,
				sender_name=sender_name,
				api_token=api_token,
				api_url=api_url,
				tag="announcement",
			)
		except requests.RequestException as e:
			error_msg = f"Connection error: {e}"
		else:
			delivered, error_code, detail = classify_postmark_response(response)
			if delivered:
				success = True
			elif error_code == POSTMARK_INACTIVE_RECIPIENT:
				suppressed = True
				error_msg = detail
				logger.error(
					"Subscriber %s is suppressed at Postmark (announcement %s); "
					"deactivating globally — no further emails will be sent. %s",
					subscriber.email,
					announcement.pk,
					detail,
				)
				deactivate_subscribers([subscriber.subscriber_id], reason=detail)
			else:
				error_msg = detail

		record_sent_message(
			response,
			recipient=subscriber.email,
			subject=live_subject,
			tag="announcement",
			site=site,
			subscriber=subscriber,
		)

		AnnouncementRecipient.objects.update_or_create(
			announcement=announcement,
			subscriber=subscriber,
			defaults={
				"list": lst,
				"success": success,
				"suppressed": suppressed,
				"error_message": error_msg,
			},
		)

	recipients = AnnouncementRecipient.objects.filter(announcement=announcement)
	sent_count = recipients.filter(success=True).count()
	suppressed_count = recipients.filter(success=False, suppressed=True).count()
	failure_count = recipients.filter(success=False, suppressed=False).count()

	announcement.status = "sent" if failure_count == 0 else "failed"
	announcement.sent_at = timezone.now()
	announcement.recipients_count = sent_count
	announcement.failures_count = failure_count
	announcement.save(
		update_fields=["status", "sent_at", "recipients_count", "failures_count"]
	)

	return {
		"sent": sent_count,
		"suppressed": suppressed_count,
		"failed": failure_count,
		"skipped": skipped,
	}
