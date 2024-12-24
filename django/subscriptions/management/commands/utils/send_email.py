import requests
from django.conf import settings

def send_email(to, subject, html, text, site, sender_name="GregoryAI", api_token=None):
    """
    Sends an email using the Postmark API.

    :param to: Recipient email address.
    :param subject: Email subject.
    :param html: HTML body of the email.
    :param text: Plain text body of the email.
    :param site: Site object for generating the sender email.
    :param sender_name: Name of the sender (default is "GregoryAI").
    :param api_token: Custom Postmark API token (if provided).
    :return: Response object from the Postmark API.
    """
    sender = f"{sender_name} <gregory@{site.domain}>"
    email_postmark_api_url = settings.EMAIL_POSTMARK_API_URL

    # Use the provided API token or fall back to the default from settings
    postmark_api_token = api_token or settings.EMAIL_POSTMARK_API

    payload = {
        "MessageStream": "broadcast",
        "From": sender,
        "To": to,
        "Subject": subject,
        "TextBody": text,
        "HtmlBody": html
    }

    response = requests.post(
        email_postmark_api_url,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Postmark-Server-Token": postmark_api_token,
        },
        json=payload
    )

    return response