"""
rss/sitemaps.py

Site-scoped XML sitemaps for frontend article and clinical trial pages.

Serves /sitemap/sites/<site_id>/index.xml (a sitemap index),
/sitemap/sites/<site_id>/articles.xml and, when the site opts in,
/sitemap/sites/<site_id>/trials.xml (?p=N pages, up to 10k URLs each).
URLs point at the requested Site's *frontend* domain.

Membership is per-site configuration on sitesettings.CustomSetting:
generate_sitemap (master switch), sitemap_subjects (which subjects this
site publishes), sitemap_relevant_only (restrict to manually/ML-relevant
articles for those subjects), sitemap_include_trials (whether this site
publishes /trials/<trial_id>/ pages at all — not every frontend does),
sitemap_trial_statuses (narrow the trials section to given recruitment
statuses). Subject curation is what lets two sites backed by one
database expose non-competing content sets to Google.

Visibility is pinned to PUBLIC organisations regardless of caller
identity: sitemaps exist for crawlers, and request-dependent visibility
would let an authenticated caller warm the response cache with private
article IDs.
"""

from django.contrib.sitemaps import Sitemap
from django.contrib.sitemaps.views import sitemap as django_sitemap_view
from django.contrib.sites.models import Site
from django.db.models import Exists, OuterRef, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.cache import cache_page

from api.filters import ml_relevant_articles_q
from gregory.models import Articles, Trials
from gregory.visibility import _public_org_ids
from sitesettings.models import CustomSetting

SITEMAP_CACHE_SECONDS = 3600


class _SiteContentSitemap(Sitemap):
	"""Shared machinery for the per-site sections.

	Subclasses set ``model`` (which must carry ``subjects`` and ``teams``
	M2Ms plus a ``last_updated`` column), ``pk_field`` and ``path_prefix``.
	"""

	# Frontend URLs are always https; don't infer from the API request.
	protocol = "https"
	# Google caps a sitemap file at 50k URLs; 10k keeps each page's query
	# and payload small.
	limit = 10000

	model = None
	pk_field = None
	path_prefix = None

	def __init__(self, site, subject_ids, public_org_ids):
		self._site = site
		self._subject_ids = subject_ids
		self._public_org_ids = public_org_ids

	def get_domain(self, site=None):
		# The framework passes the *request's* Site (the API host).
		# Sitemap URLs must use the frontend domain of the requested site.
		return self._site.domain

	def get_queryset(self):
		# Exists() so a row tagged with several qualifying subjects appears
		# once without DISTINCT-ing the outer query.
		tagged = self.model.objects.filter(
			pk=OuterRef("pk"), subjects__in=self._subject_ids
		)
		# subject_ids are already restricted to public-org subjects, but a
		# row can be tagged with a subject from one team while its own
		# teams M2M points elsewhere — re-check the row's own team
		# ownership too, matching the visibility pattern RSS feeds use
		# (teams__organization_id__in), so private-org content can never
		# surface just because it shares a subject tag with a public one.
		publicly_owned = self.model.objects.filter(
			pk=OuterRef("pk"), teams__organization_id__in=self._public_org_ids
		)
		return self.model.objects.filter(Exists(tagged), Exists(publicly_owned))

	def items(self):
		# Primary-key ordering keeps pagination stable between crawls:
		# new rows only ever append to the last page.
		return (
			self.get_queryset()
			.order_by(self.pk_field)
			.values_list(self.pk_field, "last_updated")
		)

	def location(self, item):
		return f"/{self.path_prefix}/{item[0]}/"

	def lastmod(self, item):
		# May be None for rows predating the last_updated column; the
		# framework simply omits <lastmod> for those URLs.
		return item[1]


class SiteArticlesSitemap(_SiteContentSitemap):
	model = Articles
	pk_field = "article_id"
	path_prefix = "articles"

	def __init__(self, site, subject_ids, relevant_only, public_org_ids):
		super().__init__(site, subject_ids, public_org_ids)
		self._relevant_only = relevant_only

	def get_queryset(self):
		qs = super().get_queryset()
		if self._relevant_only:
			manually_relevant = Q(
				article_subject_relevances__is_relevant=True,
				article_subject_relevances__subject_id__in=self._subject_ids,
			)
			qs = qs.filter(
				manually_relevant
				| ml_relevant_articles_q(subject_ids=self._subject_ids)
			).distinct()
		return qs


class SiteTrialsSitemap(_SiteContentSitemap):
	# Trials carry no relevance judgement (no manual review flag, no ML
	# predictions), so sitemap_relevant_only deliberately does not apply
	# here. Recruitment status is the closest thing to a quality signal:
	# a completed or withdrawn trial is a historical record that competes
	# with the site's own articles for crawl budget, while a recruiting
	# one is what patients actually search for.
	model = Trials
	pk_field = "trial_id"
	path_prefix = "trials"

	def __init__(self, site, subject_ids, public_org_ids, statuses=()):
		super().__init__(site, subject_ids, public_org_ids)
		self._statuses = list(statuses)

	def get_queryset(self):
		qs = super().get_queryset()
		if self._statuses:
			# Empty selection means "every status"; a non-empty one also
			# drops trials whose registry status didn't normalise to
			# anything (recruitment_status_normalized IS NULL), since the
			# operator asked for specific statuses and NULL is not one.
			qs = qs.filter(recruitment_status_normalized__in=self._statuses)
		return qs


def _site_sitemaps(site_id):
	"""Resolve the Site, its sitemap config, and sections — or 404.

	404 (rather than an empty sitemap) when the site has no CustomSetting,
	the switch is off, or no configured subject survives the public-org
	check. Serving an empty sitemap for a misconfigured site would tell
	Google "this site has no content".
	"""
	site = get_object_or_404(Site, pk=site_id)
	# CustomSetting.site is a plain FK (not unique) — order explicitly so
	# the chosen row is deterministic if more than one ever exists for a site.
	settings_row = CustomSetting.objects.filter(site=site).order_by("setting_id").first()
	if settings_row is None or not settings_row.generate_sitemap:
		raise Http404("Sitemap not enabled for this site.")
	public_org_ids = _public_org_ids()
	subject_ids = list(
		settings_row.sitemap_subjects.filter(
			team__organization_id__in=public_org_ids
		).values_list("id", flat=True)
	)
	if not subject_ids:
		raise Http404("No publicly visible sitemap subjects configured.")
	sitemaps = {
		"articles": SiteArticlesSitemap(
			site, subject_ids, settings_row.sitemap_relevant_only, public_org_ids
		)
	}
	# Opt-in: a frontend that has no /trials/<id>/ pages (gregory-ms.com
	# lists trials but does not give each one a page) must not advertise
	# trial URLs that would 404 for a crawler.
	if settings_row.sitemap_include_trials:
		sitemaps["trials"] = SiteTrialsSitemap(
			site,
			subject_ids,
			public_org_ids,
			statuses=settings_row.sitemap_trial_statuses or (),
		)
	return site, sitemaps


@cache_page(SITEMAP_CACHE_SECONDS)
def sitemap_index(request, site_id):
	"""Sitemap index: one <sitemap> entry per page of each section.

	Custom view because django.contrib.sitemaps.views.index reverses the
	section URL with only a {section} kwarg and cannot carry site_id.
	Locations are built from reverse() + an int + fixed section names, so
	no XML escaping is needed.
	"""
	site, sitemaps = _site_sitemaps(site_id)
	locations = []
	for section, section_sitemap in sitemaps.items():
		base = request.build_absolute_uri(
			reverse(
				"site-sitemap-section",
				kwargs={"site_id": site.pk, "section": section},
			)
		)
		locations.append(base)
		locations.extend(
			f"{base}?p={page}"
			for page in range(2, section_sitemap.paginator.num_pages + 1)
		)
	body = "\n".join(
		['<?xml version="1.0" encoding="UTF-8"?>']
		+ ['<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
		+ [f"\t<sitemap><loc>{loc}</loc></sitemap>" for loc in locations]
		+ ["</sitemapindex>"]
	)
	return HttpResponse(body, content_type="application/xml")


@cache_page(SITEMAP_CACHE_SECONDS)
def sitemap_section(request, site_id, section):
	"""One section page. Delegates to the stock sitemap view, which
	handles ?p pagination (404 on bad/out-of-range pages), <lastmod>
	rendering, and the Last-Modified response header."""
	_site, sitemaps = _site_sitemaps(site_id)
	if section not in sitemaps:
		raise Http404("Unknown sitemap section.")
	return django_sitemap_view(
		request, sitemaps={section: sitemaps[section]}, section=section
	)
