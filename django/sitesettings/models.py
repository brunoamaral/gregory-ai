from django.db import models
from django.contrib.sites.models import Site
from gregory.models import EncryptedTextField

class CustomSetting(models.Model):
	setting_id = models.AutoField(primary_key=True)
	site = models.ForeignKey(Site, on_delete=models.PROTECT)
	title = models.CharField(max_length=280,blank=False, null=False, unique=True)
	admin_email = models.EmailField(max_length=254, unique=False, null=True,blank=True)
	sender_name = models.CharField(
		max_length=100,
		blank=True,
		default='',
		help_text="Display name shown in the email From header (e.g. 'Gregory AI'). Leave blank to fall back to the site title."
	)
	sender_email_prefix = models.CharField(
		max_length=64,
		default='gregory',
		blank=True,
		help_text="Local part of the sender email address (e.g. 'gregory' → gregory@site.domain)."
	)
	api_domain = models.CharField(
		max_length=253,
		blank=True,
		default='',
		help_text="Domain where the Django backend is reachable (e.g. api.example.com). Used for unsubscribe links."
	)
	website_url = models.URLField(
		blank=True,
		default='',
		help_text="Main website URL shown in the email footer (e.g. https://example.com)."
	)
	support_url = models.URLField(
		blank=True,
		default='',
		help_text="Support page URL for the email footer."
	)
	about_url = models.URLField(
		blank=True,
		default='',
		help_text="About page URL for the email footer."
	)
	contact_url = models.URLField(
		blank=True,
		default='',
		help_text="Contact page URL for the email footer."
	)
	bluesky_url = models.URLField(
		blank=True,
		default='',
		help_text="Blue Sky profile URL for the email footer."
	)
	github_url = models.URLField(
		blank=True,
		default='',
		help_text="GitHub profile/repo URL for the email footer."
	)
	mastodon_url = models.URLField(
		blank=True,
		default='',
		help_text="Mastodon profile URL for the email footer."
	)
	postmark_api_token = EncryptedTextField(
		blank=True,
		null=True,
		help_text="Postmark API token for this site. Overrides the organisation-level token."
	)
	postmark_api_url = models.URLField(
		max_length=200,
		blank=True,
		null=True,
		default='https://api.postmarkapp.com/email',
		help_text="Postmark API URL for this site. Overrides the organisation-level URL."
	)
	privacy_policy_url = models.URLField(
		blank=True,
		default='',
		help_text="Privacy policy page URL for the email footer."
	)
	terms_url = models.URLField(
		blank=True,
		default='',
		help_text="Terms of service page URL for the email footer."
	)
	allowed_domains = models.TextField(
		blank=True,
		default='',
		help_text="Comma-separated list of domains (e.g. example.com, other-site.org) allowed to submit subscribers for any list on this site. The origin domain is used for post-subscription redirects. The site's own domain is always accepted.",
		verbose_name="Allowed Domains"
	)
