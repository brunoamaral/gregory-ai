import requests
from django.conf import settings


def send_email(
	to,
	subject,
	html,
	text,
	site,
	sender_name="Gregory AI",
	api_token=None,
	api_url=None,
	sender_prefix=None,
	tag=None,
):
	"""
	Sends an email using the Postmark API.

	:param to: Recipient email address.
	:param subject: Email subject.
	:param html: HTML body of the email.
	:param text: Plain text body of the email.
	:param site: Site object for generating the sender email.
	:param sender_name: Name of the sender (default is "GregoryAI").
	:param api_token: Custom Postmark API token (if provided).
	:param api_url: Custom Postmark API URL (if provided).
	:param sender_prefix: Local part of the sender address (default: 'gregory').
	:param tag: Postmark message tag (e.g. "weekly_summary"). Postmark allows
		exactly one tag per message; used for Postmark-side stats/debugging
		only, not for suppression, which keys off the recipient address.
	:return: Response object from the Postmark API.
	"""
	prefix = sender_prefix or "gregory"
	sender = f"{sender_name} <{prefix}@{site.domain}>"
	email_postmark_api_url = api_url or settings.EMAIL_POSTMARK_API_URL

	# Use the provided API token or fall back to the default from settings
	postmark_api_token = api_token or settings.EMAIL_POSTMARK_API_KEY

	payload = {
		"MessageStream": "broadcast",
		"From": sender,
		"To": to,
		"Subject": subject,
		"TextBody": text,
		"HtmlBody": html,
	}
	if tag:
		payload["Tag"] = tag

	response = requests.post(
		email_postmark_api_url,
		headers={
			"Accept": "application/json",
			"Content-Type": "application/json",
			"X-Postmark-Server-Token": postmark_api_token,
		},
		json=payload,
		timeout=30,
	)

	return response
