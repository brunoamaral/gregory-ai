"""
rss/views.py

Site-scoped RSS feeds: /feed/sites/<site_id>/author/<orcid>/ and
/feed/sites/<site_id>/trials/subject/<subject_slug>/.

A feed serves the REQUESTED SITE's CustomSetting.scope_subjects, not the
calling caller's own visibility -- these are crawler-and-reader-facing
surfaces like rss/sitemaps.py, not request-scoped ones like the rest of the
API. A feed reader has no identity and its response is cached, so the body
must not vary by who (or what) is asking. See
PHASE-5-RSS-SITE-SCOPE-PLAN.md and rss/sitemaps.py's module docstring for
the same reasoning applied to sitemaps.

404 when the site doesn't exist, has no CustomSetting, or has
CustomSetting.rss_enabled=False -- matching how sitemaps 404 on
generate_sitemap=False. 404 (not an empty feed) when the author/subject
carries no subject in that site's scope.

The old caller-scoped, unprefixed URLs (feed/author/<orcid>/,
feed/trials/subject/<slug>/) still resolve, via redirect_articles_by_author_feed
/redirect_trials_by_subject_feed below, to a permanent (301) redirect onto
the equivalent site-scoped URL for brain-regeneration.com (site id 3), the
project's one api_public site. Feed readers cache a 301 and stop
re-requesting the old path, which is the entire reason a redirect was
chosen over reimplementing the old caller-scoped behaviour here: a feed
reader never sees an error, so a silent 404 loses a subscriber permanently
rather than visibly. See the spec's "Old URLs redirect permanently" section.
"""

from django.contrib.syndication.views import Feed
from django.contrib.sites.models import Site
from django.db.models import F
from django.http import Http404, HttpResponsePermanentRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from gregory.models import Articles, Authors, Trials, Subject
from gregory.functions import normalize_orcid
from sitesettings.models import CustomSetting
from sitesettings.utils import author_page_base

# brain-regeneration.com -- the project's only api_public site today, and
# the fixed redirect target for the old unprefixed feed URLs. Revisit only
# if a second public site ever makes that ambiguous (spec: "Old URLs
# redirect permanently").
_REDIRECT_TARGET_SITE_ID = 3


def _site_feed_scope(site_id):
	"""Resolve the Site, its CustomSetting and its RSS-visible subject ids.

	404s exactly like rss/sitemaps.py's _site_sitemaps: unknown site, no
	CustomSetting row, or the feature switch off. Unlike sitemaps there is
	no "narrow to the public scope" step -- a feed's scope IS its site's
	scope_subjects regardless of whether that site is api_public, mirroring
	how a site-bound API key reads its own scope in
	gregory.visibility.visible_subject_ids.
	"""
	site = get_object_or_404(Site, pk=site_id)
	# CustomSetting.site is a plain FK (not unique) -- order explicitly so
	# the chosen row is deterministic if more than one ever exists for a site.
	settings_row = CustomSetting.objects.filter(site=site).order_by("setting_id").first()
	if settings_row is None or not settings_row.rss_enabled:
		raise Http404("RSS feed not enabled for this site.")
	subject_ids = set(settings_row.scope_subjects.values_list("id", flat=True))
	return site, settings_row, subject_ids


def _redirect_preserving_query(request, target_url):
	query_string = request.META.get("QUERY_STRING", "")
	if query_string:
		target_url = f"{target_url}?{query_string}"
	return HttpResponsePermanentRedirect(target_url)


def redirect_articles_by_author_feed(request, orcid):
	"""301 from the old feed/author/<orcid>/ onto its site-scoped equivalent.

	Always redirects, whatever the orcid -- an invalid one 404s at the new
	URL exactly as it would have here, and a redirect costs nothing extra
	to compute. See the module docstring for why this is a redirect and not
	a reimplementation of the old caller-scoped view.
	"""
	target = reverse(
		"site_articles_by_author_feed",
		kwargs={"site_id": _REDIRECT_TARGET_SITE_ID, "orcid": orcid},
	)
	return _redirect_preserving_query(request, target)


def redirect_trials_by_subject_feed(request, subject_slug):
	"""301 from the old feed/trials/subject/<slug>/ onto its site-scoped equivalent."""
	target = reverse(
		"site_trials_by_subject_feed",
		kwargs={"site_id": _REDIRECT_TARGET_SITE_ID, "subject_slug": subject_slug},
	)
	return _redirect_preserving_query(request, target)


class _ArticleFeedItemMixin:
	"""item_* methods shared by author-scoped article feeds.

	item_link is deliberately not here: it needs the requested site's
	domain, which differs per feed class (see SiteArticlesByAuthorFeed).
	"""

	def item_title(self, item):
		return item.title

	def item_description(self, item):
		return item.summary

	def item_guid(self, item):
		if item.doi:
			return f"doi:{item.doi}"
		return f"urn:gregory:article:{item.pk}"

	item_guid_is_permalink = False

	def item_pubdate(self, item):
		return item.published_date

	def item_updateddate(self, item):
		return item.discovery_date


class _TrialFeedItemMixin:
	"""item_* methods shared by subject-scoped trial feeds."""

	def item_title(self, item):
		return item.title

	def item_description(self, item):
		"""Build a rich description from available trial metadata."""
		parts = []

		# Primary summary
		if item.summary:
			parts.append(f"<p>{item.summary}</p>")

		# Trial metadata section
		metadata = []

		if item.recruitment_status_normalized or item.recruitment_status:
			# Prefer the canonical label; for "other" the raw registry string is more
			# informative than the label.
			status_display = (
				item.get_recruitment_status_normalized_display()
				if item.recruitment_status_normalized
				and item.recruitment_status_normalized != "other"
				else item.recruitment_status
				or item.get_recruitment_status_normalized_display()
			)
			metadata.append(f"<strong>Status:</strong> {status_display}")

		if item.phase_normalized or item.phase:
			# Prefer the canonical label; for "other" the raw registry string
			# (e.g. "Retrospective study") is more informative than the label.
			phase_display = (
				item.get_phase_normalized_display()
				if item.phase_normalized and item.phase_normalized != "other"
				else item.phase or item.get_phase_normalized_display()
			)
			metadata.append(f"<strong>Phase:</strong> {phase_display}")

		if item.study_type:
			metadata.append(f"<strong>Study Type:</strong> {item.study_type}")

		if item.primary_sponsor:
			metadata.append(f"<strong>Sponsor:</strong> {item.primary_sponsor}")

		if item.countries:
			metadata.append(f"<strong>Countries:</strong> {item.countries}")

		if item.condition:
			metadata.append(f"<strong>Condition:</strong> {item.condition}")

		if item.intervention:
			metadata.append(f"<strong>Intervention:</strong> {item.intervention}")

		# Eligibility criteria
		eligibility = []
		if item.inclusion_gender:
			eligibility.append(f"Gender: {item.inclusion_gender}")
		if item.inclusion_agemin and item.inclusion_agemax:
			eligibility.append(
				f"Age: {item.inclusion_agemin} - {item.inclusion_agemax}"
			)
		elif item.inclusion_agemin:
			eligibility.append(f"Min Age: {item.inclusion_agemin}")
		elif item.inclusion_agemax:
			eligibility.append(f"Max Age: {item.inclusion_agemax}")

		if eligibility:
			metadata.append(f"<strong>Eligibility:</strong> {', '.join(eligibility)}")

		if item.target_size:
			metadata.append(f"<strong>Target Size:</strong> {item.target_size}")

		if item.date_registration:
			metadata.append(
				f"<strong>Registration Date:</strong> {item.date_registration.strftime('%Y-%m-%d')}"
			)

		if item.source_register:
			metadata.append(f"<strong>Registry:</strong> {item.source_register}")

		if metadata:
			parts.append("<p>" + " | ".join(metadata) + "</p>")

		return "".join(parts) if parts else item.title

	def item_link(self, item):
		return item.link

	def item_guid(self, item):
		return f"urn:gregory:trial:{item.pk}"

	item_guid_is_permalink = False

	def item_pubdate(self, item):
		return item.published_date

	def item_updateddate(self, item):
		return item.last_updated


class SiteArticlesByAuthorFeed(_ArticleFeedItemMixin, Feed):
	"""feed/sites/<site_id>/author/<orcid>/ -- see module docstring."""

	def get_object(self, request, site_id, orcid):
		site, settings_row, subject_ids = _site_feed_scope(site_id)

		normalized = normalize_orcid(orcid)
		if not normalized:
			raise Authors.DoesNotExist
		author = Authors.objects.get(ORCID=normalized)

		# Attach to the per-request obj (not self) so concurrent requests on
		# the shared Feed instance don't interfere.
		author._site = site
		author._settings_row = settings_row
		author._subject_ids = subject_ids

		# 404 if the author has no articles under this site's scope.
		if not author.articles_set.filter(subjects__in=subject_ids).exists():
			raise Http404

		return author

	def title(self, obj):
		return f"Articles by {obj.full_name or 'Author'}"

	def link(self, obj):
		# Link to the site's author page when it publishes one, else orcid.org.
		base = author_page_base(obj._site, obj._settings_row)
		if base:
			return f"{base}/{obj.ORCID}/"
		return f"https://orcid.org/{obj.ORCID}"

	description = "RSS feed for articles by a specific author."

	def items(self, obj):
		articles = list(
			Articles.objects.filter(
				authors=obj,
				subjects__in=obj._subject_ids,
			)
			.distinct()
			# nulls_last: articles ingested without a date (filled later from
			# CrossRef) must not pin to the top of the feed
			.order_by(F("published_date").desc(nulls_last=True))[:50]
		)
		# item_link() below only receives the item, not obj -- stash the
		# requested site's domain on each item rather than on self, for the
		# same concurrency reason obj carries its own state above.
		for article in articles:
			article._feed_site_domain = obj._site.domain
		return articles

	def item_link(self, item):
		return f"https://{item._feed_site_domain}/articles/{str(item.pk)}/"


class SiteTrialsBySubjectFeed(_TrialFeedItemMixin, Feed):
	"""feed/sites/<site_id>/trials/subject/<subject_slug>/ -- see module docstring."""

	def get_object(self, request, site_id, subject_slug):
		site, _settings_row, subject_ids = _site_feed_scope(site_id)
		subject = Subject.objects.get(subject_slug=subject_slug)

		if subject.id not in subject_ids:
			raise Http404

		subject._site = site
		return subject

	def title(self, obj):
		return f"Clinical Trials - {obj.subject_name}"

	def link(self, obj):
		return f"https://{obj._site.domain}/trials/subject/{obj.subject_slug}/"

	def description(self, obj):
		return f"RSS feed for clinical trials related to {obj.subject_name}."

	def items(self, obj):
		return (
			# get_object() has already established that obj's subject is in
			# this site's scope, so matching on it is the whole visibility
			# test -- no second filter to add.
			Trials.objects.filter(subjects=obj)
			.distinct()
			.order_by("-discovery_date")[:50]
		)
