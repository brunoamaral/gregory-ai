# utils/email.py
import requests
from django.conf import settings

def send_email(to, subject, html, text, site, sender_name="GregoryAI"):
    sender = f"{sender_name} <gregory@{site.domain}>"
    email_postmark_api_url = settings.EMAIL_POSTMARK_API_URL
    email_postmark_api = settings.EMAIL_POSTMARK_API

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
            "X-Postmark-Server-Token": email_postmark_api,
        },
        json=payload
    )

    return response