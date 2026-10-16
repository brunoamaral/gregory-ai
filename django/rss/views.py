"""
rss/views.py

Site-scoped RSS feeds, all under /feed/sites/<site_id>/:

  author/<orcid>/                               articles by an author
  articles/subject/<slug>/[relevant/]           research by condition
  articles/category/<slug>/[relevant/]          research by topic
  trials/subject/<slug>/                        new trials by condition
  trials/subject/<slug>/recruiting/             trials a patient can join

The module is split three ways: rss/feed_scope.py decides WHO may see a feed
(site scope, slug resolution, base querysets), rss/feed_items.py decides what
an ITEM looks like (descriptions, item_* mixins, hydration) and this module
holds the feed classes and the permanent redirects.

A feed serves the REQUESTED SITE's CustomSetting.scope_subjects, not the
caller's own visibility -- these are crawler-and-reader-facing surfaces
like rss/sitemaps.py, not request-scoped ones like the rest of the API. A
feed reader has no identity and its response is cached, so the body must
not vary by who (or what) is asking. See docs/03-api-and-rss-feeds.md#rss-feeds
and rss/sitemaps.py's module docstring for the same reasoning applied to
sitemaps.

404 when the site doesn't exist, has no CustomSetting, or has
CustomSetting.rss_enabled=False -- matching how sitemaps 404 on
generate_sitemap=False -- or when the subject/category/author does not
resolve inside that site's scope. A subject or category that IS in scope but
currently has nothing to show is a valid subscription that will fill up
later, so it returns an empty feed (200), not a 404. The author feed is the
exception: an author with no in-scope article isn't a valid subscription
for that site, so it keeps its 404.

Feeds are EDITORIAL content and follow stricter rules than the API: a
retracted article never appears, the editorial text shown is this site's own,
and "relevant" means an editor's verdict wins over the ML models (see
api.filters.relevant_articles_q). Those rules live in rss/, not in the API
filters, because how the API treats relevance is a frontend decision.

Articles and trials are ordered by DISCOVERY date, newest first, and
<pubDate> is the discovery date too. Ordering by publication date would let a
paper published three months ago but found today land below the cut-off and
never be delivered. The author feed keeps publication-date order: it is low
volume, so the drop-off problem doesn't apply, and readers expect it.

Every site-scoped route is wrapped in cache_page(FEED_CACHE_SECONDS) in
admin/urls.py, like the sitemaps. Editorial edits therefore take up to an
hour to reach feeds.

The old caller-scoped, unprefixed URLs (feed/author/<orcid>/,
feed/trials/subject/<slug>/) still resolve, via redirect_articles_by_author_feed
/redirect_trials_by_subject_feed below, to a permanent (301) redirect onto
the equivalent site-scoped URL for brain-regeneration.com (site id 3), the
project's one api_public site. Feed readers cache a 301 and stop
re-requesting the old path, which is the entire reason a redirect was
chosen over reimplementing the old caller-scoped behaviour here: a feed
reader never sees an error, so a silent 404 loses a subscriber permanently
rather than visibly. See docs/03-api-and-rss-feeds.md#old-feed-urls--permanent-redirect.
"""

from django.contrib.syndication.views import Feed
from django.db.models import Exists, F, OuterRef
from django.http import Http404, HttpResponsePermanentRedirect
from django.urls import reverse

from api.filters import relevant_articles_q
from gregory.models import ArticleCategoryAssignment, Authors
from gregory.functions import normalize_orcid
from sitesettings.utils import author_page_base

from rss.feed_items import (
	ArticleFeedItemMixin,
	TrialFeedItemMixin,
	hydrate_articles,
	hydrate_trials,
)
from rss.feed_scope import (
	article_base_qs,
	resolve_scoped_category,
	resolve_scoped_subject,
	site_feed_scope,
	trial_base_qs,
)

# One hour, like the sitemaps. Responses don't vary by caller, so one cached
# copy per URL is correct.
FEED_CACHE_SECONDS = 3600

# Item limits. 100 for the whole-condition article feeds: a busy condition
# gets ~40 new articles a day, so 50 would be a little over a day and a
# reader that polls less often would silently drop articles. The relevant-only
# variant is the one to recommend for low-volume reading.
ARTICLE_FEED_LIMIT = 100
TRIAL_FEED_LIMIT = 50
AUTHOR_FEED_LIMIT = 50

# What a patient can still sign up for. enrolling_by_invitation is left out:
# a patient can't enrol themselves in one of those.
RECRUITING_STATUSES = ["recruiting", "not_yet_recruiting"]

# brain-regeneration.com -- the project's only api_public site today, and
# the fixed redirect target for the old unprefixed feed URLs. Revisit only
# if a second public site ever makes that ambiguous (spec: "Old URLs
# redirect permanently").
_REDIRECT_TARGET_SITE_ID = 3


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


class _SelfLinkMixin:
	"""Point <atom:link rel="self"> at the URL the feed was requested from.

	Feed.get_feed otherwise builds it from get_current_site(request), which is
	always the default Site (SITE_ID), not the site the feed is for.
	get_object() must set obj._feed_url = self._request_url(request).
	"""

	@staticmethod
	def _request_url(request):
		return request.build_absolute_uri(request.path)

	def feed_url(self, obj):
		return getattr(obj, "_feed_url", None)




def _homepage(site):
	"""Channel <link> for the subject/category feeds: the site's homepage.

	Per-subject and per-category pages aren't something every frontend has,
	and there is no setting yet saying which paths exist. The existing trials
	feed keeps its /trials/subject/<slug>/ link, which is already in use.
	"""
	return f"https://{site.domain}/"


def _page_ids(queryset, limit):
	"""Newest-first ids for a page of items, as a list.

	Feeds pick the ids first and hydrate them afterwards (rss/feed_items.py),
	so the ordered query stays cheap and the full rows are loaded with a
	fixed number of prefetches.
	"""
	return list(queryset.values_list("pk", flat=True)[:limit])


def _discovery_order(queryset):
	"""Newest discovery first. pk breaks ties so the cut-off is deterministic.

	Trials.discovery_date is nullable and Postgres sorts NULLs first on DESC,
	so push those last: an undated trial must not pin to the top of the feed.
	"""
	return queryset.order_by(F("discovery_date").desc(nulls_last=True), "-pk")


class SiteArticlesByAuthorFeed(_SelfLinkMixin, ArticleFeedItemMixin, Feed):
	"""feed/sites/<site_id>/author/<orcid>/ -- see module docstring."""

	def get_object(self, request, site_id, orcid):
		site, settings_row, subject_ids = site_feed_scope(site_id)

		normalized = normalize_orcid(orcid)
		if not normalized:
			raise Authors.DoesNotExist
		author = Authors.objects.get(ORCID=normalized)

		# Attach to the per-request obj (not self) so concurrent requests on
		# the shared Feed instance don't interfere.
		author._site = site
		author._feed_url = self._request_url(request)
		author._settings_row = settings_row
		author._subject_ids = subject_ids

		# 404 if the author has no (non-retracted) articles under this
		# site's scope. Unlike the subject feeds an empty author feed is not
		# a valid subscription, so this one keeps its 404.
		if not article_base_qs(subject_ids).filter(authors=author).exists():
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
		ids = _page_ids(
			article_base_qs(obj._subject_ids)
			.filter(authors=obj)
			# nulls_last: articles ingested without a date (filled later from
			# CrossRef) must not pin to the top of the feed
			.order_by(F("published_date").desc(nulls_last=True), "-pk"),
			AUTHOR_FEED_LIMIT,
		)
		return hydrate_articles(ids, obj._site, obj._subject_ids)

	def item_pubdate(self, item):
		# Publication order and date, not discovery: a person's feed is low
		# volume (so nothing drops off the end) and readers expect the
		# journal's date. See the module docstring.
		return item.published_date


class SiteArticlesBySubjectFeed(_SelfLinkMixin, ArticleFeedItemMixin, Feed):
	"""feed/sites/<site_id>/articles/subject/<subject_slug>/[relevant/].

	relevant_only=True is the same feed narrowed to articles that count as
	relevant for the subject (api.filters.relevant_articles_q: an editor's
	verdict wins, the ML models decide only when there is none). The variant
	is fixed per route, so storing it on the shared instance is safe;
	per-request state still goes on obj.
	"""

	def __init__(self, relevant_only=False):
		self.relevant_only = relevant_only
		super().__init__()

	def get_object(self, request, site_id, subject_slug):
		site, _settings_row, subject_ids = site_feed_scope(site_id)
		subject = resolve_scoped_subject(subject_ids, subject_slug)

		subject._site = site
		subject._feed_url = self._request_url(request)
		return subject

	def title(self, obj):
		prefix = "Relevant " if self.relevant_only else ""
		return f"{prefix}{obj.subject_name} research — {obj._site.name}"

	def link(self, obj):
		return _homepage(obj._site)

	def description(self, obj):
		if self.relevant_only:
			return (
				f"New {obj.subject_name} research marked relevant by an editor, "
				"or that our models agree is relevant and no editor has rejected, "
				"newest first."
			)
		return (
			f"All new {obj.subject_name} research, newest first. "
			"Not filtered by relevance."
		)

	def items(self, obj):
		# obj's subject is in this site's scope (get_object), so tagging
		# with it is the whole visibility test.
		queryset = article_base_qs([obj.pk])
		if self.relevant_only:
			queryset = queryset.filter(relevant_articles_q(subject_ids=[obj.pk]))
		ids = _page_ids(queryset.order_by("-discovery_date", "-pk"), ARTICLE_FEED_LIMIT)
		return hydrate_articles(ids, obj._site, {obj.pk})


class SiteArticlesByCategoryFeed(_SelfLinkMixin, ArticleFeedItemMixin, Feed):
	"""feed/sites/<site_id>/articles/category/<category_slug>/[relevant/].

	Articles in the category AND tagged with a subject that is in both the
	category's subjects and the site's scope: an article can sit in a category
	through a subject this site doesn't cover, and must not leak that way.
	"""

	def __init__(self, relevant_only=False):
		self.relevant_only = relevant_only
		super().__init__()

	def get_object(self, request, site_id, category_slug):
		site, _settings_row, subject_ids = site_feed_scope(site_id)
		category, cat_subject_ids = resolve_scoped_category(subject_ids, category_slug)

		category._site = site
		category._feed_url = self._request_url(request)
		category._cat_subject_ids = cat_subject_ids
		return category

	def title(self, obj):
		if self.relevant_only:
			return f"Relevant research: {obj.category_name} — {obj._site.name}"
		return f"{obj.category_name} — {obj._site.name}"

	def link(self, obj):
		return _homepage(obj._site)

	def description(self, obj):
		if self.relevant_only:
			return (
				f"New research on {obj.category_name} marked relevant by an editor, "
				"or that our models agree is relevant and no editor has rejected, "
				"newest first."
			)
		return (
			f"All new research on {obj.category_name}, newest first. "
			"Not filtered by relevance."
		)

	def items(self, obj):
		queryset = article_base_qs(obj._cat_subject_ids).filter(
			Exists(
				ArticleCategoryAssignment.objects.filter(
					articles=OuterRef("pk"), teamcategory=obj
				)
			)
		)
		if self.relevant_only:
			queryset = queryset.filter(
				relevant_articles_q(subject_ids=obj._cat_subject_ids)
			)
		ids = _page_ids(queryset.order_by("-discovery_date", "-pk"), ARTICLE_FEED_LIMIT)
		return hydrate_articles(ids, obj._site, obj._cat_subject_ids)


class SiteTrialsBySubjectFeed(_SelfLinkMixin, TrialFeedItemMixin, Feed):
	"""feed/sites/<site_id>/trials/subject/<subject_slug>/ -- see module docstring."""

	def get_object(self, request, site_id, subject_slug):
		site, settings_row, subject_ids = site_feed_scope(site_id)
		subject = resolve_scoped_subject(subject_ids, subject_slug)

		subject._site = site
		subject._settings_row = settings_row
		subject._feed_url = self._request_url(request)
		return subject

	def title(self, obj):
		return f"{obj.subject_name} clinical trials — {obj._site.name}"

	def link(self, obj):
		return f"https://{obj._site.domain}/trials/subject/{obj.subject_slug}/"

	def description(self, obj):
		return f"RSS feed for clinical trials related to {obj.subject_name}."

	def trial_queryset(self, obj):
		return trial_base_qs([obj.pk])

	def items(self, obj):
		ids = _page_ids(_discovery_order(self.trial_queryset(obj)), TRIAL_FEED_LIMIT)
		return hydrate_trials(ids, obj._site, obj._settings_row, {obj.pk})


class SiteRecruitingTrialsBySubjectFeed(SiteTrialsBySubjectFeed):
	"""feed/sites/<site_id>/trials/subject/<subject_slug>/recruiting/.

	The trials feed narrowed to trials a patient could still join. A trial is
	picked up when it is discovered; moving from "not yet recruiting" to
	"recruiting" later does not bring it back (status changes are a different
	kind of item). A NULL status is left out.
	"""

	def title(self, obj):
		return f"Recruiting {obj.subject_name} trials — {obj._site.name}"

	def description(self, obj):
		return (
			f"RSS feed for {obj.subject_name} clinical trials that are recruiting "
			"or about to start recruiting."
		)

	def trial_queryset(self, obj):
		return super().trial_queryset(obj).filter(
			recruitment_status_normalized__in=RECRUITING_STATUSES
		)
