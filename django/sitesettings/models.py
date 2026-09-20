import re
import string

from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.core.validators import MaxLengthValidator
from django.db import models
from django.contrib.sites.models import Site
from gregory.models import EncryptedTextField
from gregory.utils.trial_field_normalizers import TrialRecruitmentStatus

MCP_DESCRIPTION_MAX_CHARS = 2_000
MCP_PROMPT_TEMPLATE_MAX_CHARS = 8_000
MCP_DOCUMENT_BODY_MAX_CHARS = 50_000
RESERVED_MCP_DOCUMENT_SLUGS = frozenset({"subjects", "categories", "about"})

_PROMPT_ARGUMENT_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PROMPT_ARGUMENT_ALLOWED_KEYS = frozenset({"name", "description", "required"})


def validate_prompt_template(template: str, arguments) -> list[dict]:
	"""Return `arguments` in canonical form, or raise ValidationError keyed by field.

	Canonical form is a list of {"name", "description", "required"} dicts, in
	the order given, with "description" defaulting to "" and "required"
	defaulting to True. Errors are raised as
	ValidationError({"template": [...], "arguments": [...]}) so the admin can
	show each one next to its field.
	"""
	errors: dict[str, list[str]] = {}

	if not isinstance(arguments, list):
		raise ValidationError({"arguments": ["arguments must be a list."]})

	canonical = []
	seen_names: set[str] = set()
	argument_errors: list[str] = []
	for item in arguments:
		if not isinstance(item, dict):
			argument_errors.append(f"Each argument must be an object, got {item!r}.")
			continue

		unknown_keys = set(item.keys()) - _PROMPT_ARGUMENT_ALLOWED_KEYS
		if unknown_keys:
			argument_errors.append(
				f"Unknown key(s) {sorted(unknown_keys)} in argument {item!r}. "
				f"Allowed keys are name, description, required."
			)
			continue

		name = item.get("name")
		if not isinstance(name, str) or not _PROMPT_ARGUMENT_NAME_RE.match(name):
			argument_errors.append(
				f"Argument name {name!r} must match ^[A-Za-z_][A-Za-z0-9_]*$."
			)
			continue

		if name in seen_names:
			argument_errors.append(f"Argument name '{name}' is declared more than once.")
			continue
		seen_names.add(name)

		description = item.get("description", "")
		if not isinstance(description, str):
			argument_errors.append(f"Argument '{name}' description must be a string.")
			continue

		required = item.get("required", True)
		if not isinstance(required, bool):
			argument_errors.append(f"Argument '{name}' required must be true or false.")
			continue

		canonical.append({"name": name, "description": description, "required": required})

	if argument_errors:
		errors["arguments"] = argument_errors

	if not isinstance(template, str):
		raise ValidationError({"template": ["template must be a string."]})

	tpl = string.Template(template)
	if not tpl.is_valid():
		errors.setdefault("template", []).append(
			"Invalid $ in the template. Write $$ for a literal dollar sign, "
			"and $name or ${name} for an argument."
		)
	else:
		placeholders = set(tpl.get_identifiers())
		declared = seen_names
		undeclared = placeholders - declared
		unused = declared - placeholders
		if undeclared:
			errors.setdefault("template", []).append(
				f"The template uses placeholder(s) {sorted(undeclared)} with no "
				f"matching declared argument."
			)
		if unused:
			errors.setdefault("arguments", []).append(
				f"Argument(s) {sorted(unused)} are declared but never used in the template."
			)

	if errors:
		raise ValidationError(errors)

	return canonical


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
	scope_subjects = models.ManyToManyField(
		"gregory.Subject",
		blank=True,
		related_name="scope_sites",
		help_text=(
			"Subjects this site owns for anonymous API/RSS visibility. This "
			"defines what the site is allowed to publish -- it is distinct "
			"from sitemap_subjects (SEO curation, which should narrow "
			"within this scope, not define it). Seeded from "
			"sitemap_subjects at migration time as a starting value, not a "
			"rule -- the two are free to diverge from that point on. A "
			"subject left out of every site's scope is how "
			"internal/unpublished research stays private; there is no "
			"separate 'internal' flag."
		),
	)
	api_public = models.BooleanField(
		default=False,
		help_text=(
			"When true, this site's scope_subjects are visible to "
			"anonymous API callers -- the union of every api_public site's "
			"scope is what an unauthenticated request can see. Defaults to "
			"False: a new site is private until someone deliberately "
			"publishes it. Replaces the organisation-level "
			"OrganizationApiSettings.make_api_public flag for content "
			"visibility, which could not express one organisation owning "
			"both a public and a private site. That flag still governs "
			"/organizations/ and per-org serializer fields -- see its own "
			"docstring -- but no longer article/trial/RSS visibility."
		),
	)
	rss_enabled = models.BooleanField(
		default=False,
		help_text=(
			"When true, this site serves RSS feeds scoped to its "
			"scope_subjects, at /feed/sites/<site_id>/... . Matches the "
			"per-surface pattern of generate_sitemap: a site can serve an "
			"API without feeds, or the reverse. Defaults to False."
		),
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
	sitemap_include_authors = models.BooleanField(
		default=False,
		help_text=(
			"Also list author profile pages for the selected subjects, at "
			"/sitemap/sites/<site_id>/authors.xml. Only enable this if the "
			"site actually publishes author pages at /authors/<orcid>/ — "
			"otherwise the sitemap would send crawlers to 404s."
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
	mcp_enabled = models.BooleanField(
		default=False,
		help_text=(
			"Offers this site's research assistant (MCP). Takes effect only "
			"when scope_subjects above is non-empty. The public MCP server "
			"only serves api_public sites, so on a private site this flag "
			"does nothing until authenticated MCP servers exist. Off by "
			"default: a site is not a tenant until someone decides it is."
		),
	)
	mcp_description = models.TextField(
		blank=True,
		default="",
		validators=[MaxLengthValidator(MCP_DESCRIPTION_MAX_CHARS)],
		help_text=(
			"Optional. How the research assistant describes this instance to "
			"the model: its focus and framing. The model reads it, not "
			"people — it becomes the first paragraph of the assistant's "
			"generated instructions. A blank value generates that "
			"introduction from the site's name and subjects instead."
		),
	)


class SiteMcpPrompt(models.Model):
	site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name="mcp_prompts")
	name = models.SlugField(max_length=64, help_text="The MCP prompt name. Unique per site.")
	title = models.CharField(max_length=200)
	description = models.CharField(max_length=500, blank=True, default="")
	template = models.TextField(
		validators=[MaxLengthValidator(MCP_PROMPT_TEMPLATE_MAX_CHARS)],
		help_text=(
			"string.Template syntax: $topic or ${topic} for an argument, and "
			"$$ for a literal dollar sign."
		),
	)
	arguments = models.JSONField(
		default=list,
		blank=True,
		help_text='[{"name": "topic", "description": "…", "required": true}]',
	)
	is_active = models.BooleanField(
		default=True,
		help_text="Inactive rows are kept but never published.",
	)
	ordering = models.PositiveIntegerField(default=0)

	class Meta:
		ordering = ["ordering", "name"]
		constraints = [
			models.UniqueConstraint(fields=["site", "name"], name="sitemcpprompt_site_name_uniq"),
		]

	def __str__(self):
		return f"{self.site.domain}: {self.name}"

	def clean(self):
		super().clean()
		self.arguments = validate_prompt_template(self.template, self.arguments)


class SiteMcpDocument(models.Model):
	MIME_TYPE_CHOICES = [
		("text/markdown", "Markdown"),
		("text/plain", "Plain text"),
	]

	site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name="mcp_documents")
	slug = models.SlugField(
		max_length=64,
		help_text="Served at gregory-ai://doc/{slug}. Unique per site.",
	)
	title = models.CharField(max_length=200)
	description = models.CharField(max_length=500, blank=True, default="")
	mime_type = models.CharField(
		max_length=50, choices=MIME_TYPE_CHOICES, default="text/markdown"
	)
	body = models.TextField(validators=[MaxLengthValidator(MCP_DOCUMENT_BODY_MAX_CHARS)])
	is_active = models.BooleanField(
		default=True,
		help_text="Inactive rows are kept but never published.",
	)
	ordering = models.PositiveIntegerField(default=0)

	class Meta:
		ordering = ["ordering", "slug"]
		constraints = [
			models.UniqueConstraint(fields=["site", "slug"], name="sitemcpdocument_site_slug_uniq"),
		]

	def __str__(self):
		return f"{self.site.domain}: {self.slug}"

	def clean(self):
		super().clean()
		if self.slug in RESERVED_MCP_DOCUMENT_SLUGS:
			raise ValidationError(
				{
					"slug": (
						"subjects, categories and about are reserved for "
						"built-in resources."
					)
				}
			)
