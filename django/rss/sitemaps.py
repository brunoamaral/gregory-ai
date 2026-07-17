"""
rss/sitemaps.py

Site-scoped XML sitemaps for frontend article pages.

Serves /sitemap/sites/<site_id>/index.xml (a sitemap index) and
/sitemap/sites/<site_id>/articles.xml (?p=N pages, up to 10k URLs each).
URLs point at the requested Site's *frontend* domain.

Membership is per-site configuration on sitesettings.CustomSetting:
generate_sitemap (master switch), sitemap_subjects (which subjects this
site publishes), sitemap_relevant_only (restrict to manually/ML-relevant
articles for those subjects). Subject curation is what lets two sites
backed by one database expose non-competing article sets to Google.

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
from gregory.models import Articles
from gregory.visibility import _public_org_ids
from sitesettings.models import CustomSetting

SITEMAP_CACHE_SECONDS = 3600


class SiteArticlesSitemap(Sitemap):
	# Frontend URLs are always https; don't infer from the API request.
	protocol = "https"
	# Google caps a sitemap file at 50k URLs; 10k keeps each page's query
	# and payload small.
	limit = 10000

	def __init__(self, site, subject_ids, relevant_only):
		self._site = site
		self._subject_ids = subject_ids
		self._relevant_only = relevant_only

	def get_domain(self, site=None):
		# The framework passes the *request's* Site (the API host).
		# Sitemap URLs must use the frontend domain of the requested site.
		return self._site.domain

	def items(self):
		# Exists() so an article tagged with several qualifying subjects
		# appears once without DISTINCT-ing the outer query.
		tagged = Articles.objects.filter(
			pk=OuterRef("pk"), subjects__in=self._subject_ids
		)
		qs = Articles.objects.filter(Exists(tagged))
		if self._relevant_only:
			manually_relevant = Q(
				article_subject_relevances__is_relevant=True,
				article_subject_relevances__subject_id__in=self._subject_ids,
			)
			qs = qs.filter(
				manually_relevant
				| ml_relevant_articles_q(subject_ids=self._subject_ids)
			).distinct()
		# article_id ordering keeps pagination stable between crawls:
		# new articles only ever append to the last page.
		return qs.order_by("article_id").values_list("article_id", "last_updated")

	def location(self, item):
		return f"/articles/{item[0]}/"

	def lastmod(self, item):
		# May be None for rows predating the last_updated column; the
		# framework simply omits <lastmod> for those URLs.
		return item[1]


def _site_sitemaps(site_id):
	"""Resolve the Site, its sitemap config, and sections — or 404.

	404 (rather than an empty sitemap) when the site has no CustomSetting,
	the switch is off, or no configured subject survives the public-org
	check. Serving an empty sitemap for a misconfigured site would tell
	Google "this site has no content".
	"""
	site = get_object_or_404(Site, pk=site_id)
	settings_row = CustomSetting.objects.filter(site=site).first()
	if settings_row is None or not settings_row.generate_sitemap:
		raise Http404("Sitemap not enabled for this site.")
	subject_ids = list(
		settings_row.sitemap_subjects.filter(
			team__organization_id__in=_public_org_ids()
		).values_list("id", flat=True)
	)
	if not subject_ids:
		raise Http404("No publicly visible sitemap subjects configured.")
	return site, {
		"articles": SiteArticlesSitemap(
			site, subject_ids, settings_row.sitemap_relevant_only
		)
	}


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
