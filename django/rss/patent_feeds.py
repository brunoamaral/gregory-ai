"""
rss/patent_feeds.py

Patent family feeds, under /feed/sites/<site_id>/patents/:

  subject/<slug>/        new patent families for a condition
  category/<slug>/       new patent families for a topic
  search/?...            search feed (same machinery as rss/search_feeds.py)

Patents are a newer content type that comes from the EPO's Open Patent
Services under a licence that shapes these feeds more than the code does
(docs/patents.md, "Licence and the API"). Where this module and the article
and trial feeds disagree, this one wins for patents:

  * BOTH switches: a patent feed needs CustomSetting.rss_enabled AND
    patents_enabled. With patents_enabled off it is a 404 even when the
    subject is in scope, matching /patents/, the MCP server and the editor
    address. The search feed is gated the same way.
  * Ordered by earliest_publication_date, newest first -- NOT discovery date,
    which the patent backfill makes meaningless (see
    rss.feed_scope.patent_base_qs). <pubDate> is the publication date at
    midnight UTC. 50 items: about three to four months of a busy condition,
    far longer than the weekly import lag, so a newly imported family always
    lands inside the window while backfilled history never reaches readers.
    Families with no publication date are left out.
  * Processed records only (rss.feed_items, "Patent families"): never the
    abstract, claims, inventors, CPC/IPC codes or publication numbers, because
    a feed is syndicated verbatim and OPS clause 3.2 forbids making "the data
    as such" public. The search parameters `search`, `title` and `summary` may
    FILTER on patent text server-side -- filtering is processing -- but the
    text itself is never output.
  * No relevance judgement exists for patents, so there are no /relevant/
    variants, as for trials.
"""

from django.contrib.syndication.views import Feed
from django.db.models import Exists, OuterRef

from api.filters import PatentFilter
from gregory.models import PatentCategoryAssignment, Sponsor
from rss.feed_items import PatentFeedItemMixin, hydrate_patents
from rss.feed_scope import (
	patent_base_qs,
	resolve_scoped_category,
	resolve_scoped_subject,
	site_feed_scope,
)
from rss.search_feeds import _SearchFeedMixin
from rss.views import _homepage, _page_ids, _SelfLinkMixin

PATENT_FEED_LIMIT = 50

PATENT_SEARCH_PARAMS = (
	"search",
	"title",
	"summary",
	"subject_id",
	"category_slug",
	"applicant_sponsor_slug",
	"has_grant",
	"has_trial_link",
)

# The sentence every patent channel carries. The URL is the source attribution
# the licence asks for.
_SOURCE_NOTE = (
	"Patent families are listed by first publication and sourced from the "
	"European Patent Office (EPO Open Patent Services, "
	"https://www.epo.org/en/service-support/ordering/open-patent-services)."
)


def _publication_order(queryset):
	"""Newest first publication first; patent_id breaks ties deterministically.

	patent_base_qs already excludes NULL publication dates, so no nulls_last.
	"""
	return queryset.order_by("-earliest_publication_date", "-patent_id")


class SitePatentsBySubjectFeed(_SelfLinkMixin, PatentFeedItemMixin, Feed):
	"""feed/sites/<site_id>/patents/subject/<subject_slug>/ -- see module docstring."""

	def get_object(self, request, site_id, subject_slug):
		site, settings_row, scope = site_feed_scope(site_id, require_patents=True)
		subject = resolve_scoped_subject(scope, subject_slug)

		# Per-request state goes on obj, not the shared Feed instance.
		subject._site = site
		subject._settings_row = settings_row
		subject._scope = scope
		subject._feed_url = self._request_url(request)
		return subject

	def title(self, obj):
		return f"{obj.subject_name} patents — {obj._site.name}"

	def link(self, obj):
		return _homepage(obj._site)

	def description(self, obj):
		return f"New {obj.subject_name} patent families. {_SOURCE_NOTE}"

	def items(self, obj):
		ids = _page_ids(_publication_order(patent_base_qs([obj.pk])), PATENT_FEED_LIMIT)
		return hydrate_patents(ids, obj._site, obj._settings_row, {obj.pk}, scope=obj._scope)


class SitePatentsByCategoryFeed(_SelfLinkMixin, PatentFeedItemMixin, Feed):
	"""feed/sites/<site_id>/patents/category/<category_slug>/.

	Families in the category AND tagged with a subject that is in both the
	category's subjects and the site's scope: a family can sit in a category
	through a subject this site doesn't cover, and must not leak that way.
	"""

	def get_object(self, request, site_id, category_slug):
		site, settings_row, scope = site_feed_scope(site_id, require_patents=True)
		category, cat_subject_ids = resolve_scoped_category(scope, category_slug)

		category._site = site
		category._settings_row = settings_row
		category._scope = scope
		category._cat_subject_ids = cat_subject_ids
		category._feed_url = self._request_url(request)
		return category

	def title(self, obj):
		return f"Patents: {obj.category_name} — {obj._site.name}"

	def link(self, obj):
		return _homepage(obj._site)

	def description(self, obj):
		return f"New patent families on {obj.category_name}. {_SOURCE_NOTE}"

	def items(self, obj):
		queryset = patent_base_qs(obj._cat_subject_ids).filter(
			Exists(
				PatentCategoryAssignment.objects.filter(
					patents=OuterRef("pk"), teamcategory=obj
				)
			)
		)
		ids = _page_ids(_publication_order(queryset), PATENT_FEED_LIMIT)
		return hydrate_patents(
			ids, obj._site, obj._settings_row, obj._cat_subject_ids, scope=obj._scope
		)


class SitePatentsSearchFeed(_SearchFeedMixin, PatentFeedItemMixin, Feed):
	"""feed/sites/<site_id>/patents/search/ -- see rss/search_feeds.py.

	The allowlisted PatentFilter parameters on top of the site's scope, with
	the patent rules above: both switches, publication order, processed
	records only. `search`, `title` and `summary` match patent text but the
	text is never output.
	"""

	params_allowlist = PATENT_SEARCH_PARAMS
	cache_namespace = "patents-search"
	filter_class = PatentFilter
	empty_title = "All patents"
	title_prefix = "Patent search"
	patents = True

	def search_queryset(self, subject_ids):
		return patent_base_qs(subject_ids)

	def order(self, queryset):
		return _publication_order(queryset)

	def labels(self, params, subject, category):
		labels = super().labels(params, subject, category)
		if "applicant_sponsor_slug" in params:
			name = (
				Sponsor.objects.filter(slug=params["applicant_sponsor_slug"])
				.values_list("name", flat=True)
				.first()
			)
			labels.append(name or params["applicant_sponsor_slug"])
		has_grant = params.get("has_grant")
		if has_grant is not None:
			labels.append("grant published" if has_grant == "true" else "no grant published")
		has_trial_link = params.get("has_trial_link")
		if has_trial_link is not None:
			labels.append("linked to a trial" if has_trial_link == "true" else "no linked trial")
		return labels

	def description(self, obj):
		return f"The newest patent families matching this search. {_SOURCE_NOTE}"

	def items(self, obj):
		return hydrate_patents(obj.ids, obj.site, obj.settings_row, obj.scope)
