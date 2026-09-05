from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.contrib.sites.models import Site
from gregory.models import EncryptedTextField
from gregory.utils.trial_field_normalizers import TrialRecruitmentStatus


class CustomSetting(models.Model):
	setting_id = models.AutoField(primary_key=True)
	site = models.ForeignKey(Site, on_delete=models.PROTECT)
	title = models.CharField(max_length=280, blank=False, null=False, unique=True)
	admin_email = models.EmailField(max_length=254, unique=False, null=True, blank=True)
	sender_name = models.CharField(
		max_length=100,
		blank=True,
		default="",
		help_text="Display name shown in the email From header (e.g. 'Gregory AI'). Leave blank to fall back to the site title.",
	)
	sender_email_prefix = models.CharField(
		max_length=64,
		default="gregory",
		blank=True,
		help_text="Local part of the sender email address (e.g. 'gregory' → gregory@site.domain).",
	)
	api_domain = models.CharField(
		max_length=253,
		blank=True,
		default="",
		help_text="Domain where the Django backend is reachable (e.g. api.example.com). Used for unsubscribe links.",
	)
	website_url = models.URLField(
		blank=True,
		default="",
		help_text="Main website URL shown in the email footer (e.g. https://example.com).",
	)
	support_url = models.URLField(
		blank=True, default="", help_text="Support page URL for the email footer."
	)
	about_url = models.URLField(
		blank=True, default="", help_text="About page URL for the email footer."
	)
	contact_url = models.URLField(
		blank=True, default="", help_text="Contact page URL for the email footer."
	)
	bluesky_url = models.URLField(
		blank=True, default="", help_text="Blue Sky profile URL for the email footer."
	)
	github_url = models.URLField(
		blank=True,
		default="",
		help_text="GitHub profile/repo URL for the email footer.",
	)
	mastodon_url = models.URLField(
		blank=True, default="", help_text="Mastodon profile URL for the email footer."
	)
	postmark_api_token = EncryptedTextField(
		blank=True,
		null=True,
		help_text="Postmark API token for this site. Overrides the organisation-level token.",
	)
	postmark_api_url = models.URLField(
		max_length=200,
		blank=True,
		null=True,
		default="https://api.postmarkapp.com/email",
		help_text="Postmark API URL for this site. Overrides the organisation-level URL.",
	)
	privacy_policy_url = models.URLField(
		blank=True,
		default="",
		help_text="Privacy policy page URL for the email footer.",
	)
	terms_url = models.URLField(
		blank=True,
		default="",
		help_text="Terms of service page URL for the email footer.",
	)
	allowed_domains = models.TextField(
		blank=True,
		default="",
		help_text="Comma-separated list of domains (e.g. example.com, other-site.org) allowed to submit subscribers for any list on this site. The origin domain is used for post-subscription redirects. The site's own domain is always accepted.",
		verbose_name="Allowed Domains",
	)
	generate_sitemap = models.BooleanField(
		default=False,
		help_text=(
			"Serve an XML sitemap for this site at "
			"/sitemap/sites/<site_id>/index.xml. Requires at least one "
			"sitemap subject below."
		),
	)
	sitemap_subjects = models.ManyToManyField(
		"gregory.Subject",
		blank=True,
		related_name="sitemap_sites",
		help_text=(
			"Subjects whose articles (and, when enabled below, clinical "
			"trials) appear in this site's sitemap. Choosing different "
			"subjects per site is how two sites backed by the same "
			"database avoid competing for the same content in search "
			"engines."
		),
	)
	sitemap_relevant_only = models.BooleanField(
		default=False,
		help_text=(
			"Only include articles marked relevant for at least one of the "
			"selected subjects (manual review or ML consensus, same "
			"semantics as the API's relevant=true filter). Does not affect "
			"clinical trials, which carry no relevance judgement."
		),
	)
	sitemap_include_trials = models.BooleanField(
		default=False,
		help_text=(
			"Also list clinical trials for the selected subjects, at "
			"/sitemap/sites/<site_id>/trials.xml. Only enable this if the "
			"site actually publishes trial pages at /trials/<trial_id>/ — "
			"otherwise the sitemap would send crawlers to 404s."
		),
	)
	sitemap_trial_statuses = ArrayField(
		models.CharField(max_length=30, choices=TrialRecruitmentStatus.choices),
		blank=True,
		default=list,
		verbose_name="Sitemap trial statuses",
		help_text=(
			"Restrict the trials section to these recruitment statuses. "
			"Leave all unticked to list every trial for the selected "
			"subjects. Trials with no normalised status are excluded "
			"whenever a selection is made. Narrowing to the open/upcoming "
			"statuses is the usual way to keep the trials section from "
			"dwarfing the curated articles section."
		),
	)
	has_author_pages = models.BooleanField(
		default=False,
		help_text=(
			"This site publishes author profile pages at /authors/<orcid>/. When enabled, "
			"author names in digest emails and the author RSS feed link to this site "
			"instead of orcid.org."
		),
	)
	description = models.TextField(
		blank=True,
		default="",
		help_text="One paragraph describing what this project is. Shown on the 'About this file' sheet of exported workbooks.",
	)
	contact_email = models.EmailField(
		max_length=254,
		blank=True,
		default="",
		help_text="Public contact address for this site, shown on exported files. Falls back to Admin email when blank.",
	)
	data_license = models.CharField(
		max_length=200,
		blank=True,
		default="",
		help_text="Licence the exported data may be reused under (e.g. 'CC BY 4.0'). Distinct from Terms URL, which is the website's terms of service.",
	)
	data_license_url = models.URLField(
		blank=True,
		default="",
		help_text="Link to the licence text referenced by Data licence.",
	)
	citation = models.TextField(
		blank=True,
		default="",
		help_text="How to cite an export from this site. Leave blank to generate '{title}. Clinical trials export, {date}. {website_url}'.",
	)
