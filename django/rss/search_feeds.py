"""
rss/search_feeds.py

Search feeds: "tell me when something new matches what I searched for".

  feed/sites/<site_id>/articles/search/?...     SiteArticlesSearchFeed
  feed/sites/<site_id>/trials/search/?...       SiteTrialsSearchFeed
  feed/sites/<site_id>/patents/search/?...      SitePatentsSearchFeed (rss/patent_feeds.py,
                                                built on _SearchFeedMixin)

These are the one place the feeds accept a query string, because a search is
open-ended and can't be a fixed path segment. They reuse the SAME filter
classes as /articles/ and /trials/ (api.filters.ArticleFilter / TrialFilter)
with the same parameter names, so the frontend can pass its search state
straight through and every filter keeps exactly one implementation. Everything
else follows the fixed feeds: the requested site's scope, discovery-date
order, the same item mixins (rss/feed_items.py).

Because the URL space is unbounded -- the shape the AI crawler exploited in
the August House load spike -- the feed is fenced in:

  * An ALLOWLIST of parameters. Anything else (utm_*, ml_threshold,
    published_date_after, ...) is dropped before it reaches a filter and
    doesn't change the cache key.
  * Invalid values are a 400 (the frontend builds these URLs, so an error
    should be loud there); a subject or category outside the site's scope is
    a 404, as for the fixed feeds.
  * No pagination, 50 items, GET/HEAD only.
  * A one-hour server-side cache keyed on host + site + CANONICAL query string
    (allowlisted params, trimmed, sorted by key, empties dropped), so
    ?b=1&a=2&utm_source=x and ?a=2&b=1 share one entry and each distinct
    search costs at most one query an hour. The canonical string is also the
    key a future saved-search model would use.
  * X-Robots-Tag: noindex, nofollow on every response, plus a robots.txt
    Disallow on the API host (robots_txt below).

The filters read self.request.GET directly rather than the data they are
given, so the FilterSet is handed a shallow copy of the request whose GET is
ONLY the allowlisted params; otherwise a parameter that isn't on the
allowlist would still leak into filtering. That copy also carries the site's
scope as the visible subjects and the site (not the caller) as the editorial
owner, so nothing in the response depends on who is asking.

`relevant` is deliberately NOT left to ArticleFilter: its rule is the API's
human-OR-AI one and, with no subject_id, falls back to every auto_predict
subject in the database. Feeds are editorial content, so the feed applies the
editorial rule (an editor's verdict wins, api.filters.relevant_articles_q)
scoped to the site's own subjects.
"""

import hashlib
import math
from copy import copy
from types import SimpleNamespace
from urllib.parse import urlencode

from django.core.cache import cache
from django.core.exceptions import BadRequest
from django.contrib.syndication.views import Feed
from django.http import (
	Http404,
	HttpResponse,
	HttpResponseBadRequest,
	HttpResponseNotAllowed,
	QueryDict,
)
from django.utils.cache import patch_cache_control

from api.filters import ArticleFilter, TrialFilter, relevant_articles_q
from gregory.models import OrganizationSite, Sponsor, Subject
from gregory.utils.trial_field_normalizers import (
	TrialPhase,
	TrialRecruitmentStatus,
	TrialRegion,
	TrialSexEligibility,
)

from rss.feed_items import ArticleFeedItemMixin, TrialFeedItemMixin, hydrate_articles, hydrate_trials
from rss.feed_scope import (
	article_base_qs,
	resolve_scoped_category,
	site_feed_scope,
	trial_base_qs,
)
from rss.views import (
	FEED_CACHE_SECONDS,
	_discovery_order,
	_homepage,
	_page_ids,
	_SelfLinkMixin,
)

ARTICLE_SEARCH_PARAMS = (
	"search",
	"title",
	"summary",
	"subject_id",
	"category_slug",
	"relevant",
	"open_access",
	"has_takeaways",
)
TRIAL_SEARCH_PARAMS = (
	"search",
	"title",
	"summary",
	"subject_id",
	"category_slug",
	"recruitment_status_normalized",
	"phase_normalized",
	"region",
	"country",
	"sponsor_slug",
	"age_eligible",
	"inclusion_gender_normalized",
)

# Text searches. Capped because feed readers only send GET, so there is no
# equivalent of the API's POST route for very long boolean searches.
TEXT_PARAMS = ("search", "title", "summary")
MAX_TEXT_PARAM_LENGTH = 300
# Every other value is capped too: none of them has a legitimate reason to be
# long, and `country` takes a comma-separated list that would otherwise be an
# unbounded IN (...).
MAX_PARAM_LENGTH = 300

# Every boolean any search feed accepts. canonical_params() only looks at a
# feed's own allowlist, so listing the patent booleans here neither lets them
# through the article/trial feeds nor loosens their validation.
BOOLEAN_PARAMS = ("relevant", "open_access", "has_takeaways", "has_grant", "has_trial_link")

SEARCH_FEED_LIMIT = 50

# Eligible ages beyond this are a typo, not a search.
MAX_AGE_ELIGIBLE = 150


class InvalidSearchFeed(BadRequest):
	"""A search-feed URL with an invalid value. Becomes a plain-text 400.

	A subclass of BadRequest so anything that doesn't catch it still gets
	Django's 400. The message never echoes the offending value.
	"""


def canonical_params(request, allowlist):
	"""(params, canonical) for the allowlisted query parameters of `request`.

	Keys are taken in sorted order, values are stripped, empty ones dropped.
	Boolean values are lower-cased so ?open_access=True and =true share a
	cache entry. `params` is an immutable QueryDict holding only these keys;
	`canonical` is its urlencoded string. Nothing outside the allowlist
	survives this function, so nothing outside it can reach a filter.
	"""
	pairs = []
	for key in sorted(allowlist):
		value = request.GET.get(key, "").strip()
		if not value:
			continue
		if key in BOOLEAN_PARAMS:
			value = value.lower()
		pairs.append((key, value))
	canonical = urlencode(pairs)
	return QueryDict(canonical), canonical


def _validate(params):
	"""Raise InvalidSearchFeed for any value the feed can't make sense of."""
	problems = []
	for key in TEXT_PARAMS:
		if len(params.get(key, "")) > MAX_TEXT_PARAM_LENGTH:
			problems.append(f"{key} is longer than {MAX_TEXT_PARAM_LENGTH} characters")
	for key, value in params.items():
		if key not in TEXT_PARAMS and len(value) > MAX_PARAM_LENGTH:
			problems.append(f"{key} is longer than {MAX_PARAM_LENGTH} characters")
	subject_id = params.get("subject_id")
	if subject_id is not None and not (subject_id.isascii() and subject_id.isdigit()):
		problems.append("subject_id must be an integer")
	for key in BOOLEAN_PARAMS:
		value = params.get(key)
		if value is not None and value not in ("true", "false"):
			problems.append(f"{key} must be true or false")
	age = params.get("age_eligible")
	if age is not None:
		try:
			age_value = float(age)
		except ValueError:
			age_value = None
		if age_value is None or not math.isfinite(age_value) or not 0 <= age_value <= MAX_AGE_ELIGIBLE:
			problems.append(f"age_eligible must be a number between 0 and {MAX_AGE_ELIGIBLE}")
	if problems:
		raise InvalidSearchFeed("Invalid search feed parameters: " + "; ".join(problems) + ".")


def _filter_request(request, params, site, scope, patent_scope=()):
	"""A shallow copy of `request` that is safe to hand to ArticleFilter/TrialFilter.

	  * GET is only the allowlisted params (minus `relevant`, which the feed
	    applies itself), because the filters read request.GET, not the data
	    they are given.
	  * visible_subject_ids is the site's scope and visible_patent_subject_ids
	    is `patent_scope`, empty by default: no patent link is visible through
	    an article or trial search. The patent search feed passes the site's
	    scope so has_trial_link works.
	  * The editorial owner is the feed's site and its organisations, not
	    whoever sent the request, so has_takeaways means "this site has
	    takeaways" for every reader. The filters take these from the cached
	    attributes api.editorial sets on the request.
	"""
	get = params.copy()
	get.pop("relevant", None)
	get._mutable = False
	shim = copy(request)
	shim.GET = get
	shim._editorial_site_ids = [site.pk]
	shim.visible_subject_ids = set(scope)
	shim.visible_patent_subject_ids = set(patent_scope)
	shim._editorial_org_ids = sorted(
		OrganizationSite.objects.filter(site=site).values_list("organization_id", flat=True)
	)
	return shim, get


def _run_filter(filter_class, queryset, get, shim):
	filterset = filter_class(get, queryset=queryset, request=shim)
	if not filterset.is_valid():
		fields = ", ".join(sorted(filterset.errors))
		raise InvalidSearchFeed(f"Invalid value for: {fields}.")
	return filterset.qs


def _choice_label(choices, raw):
	"""Display labels for a (possibly comma-separated) choice value."""
	labels = dict(choices)
	return ", ".join(str(labels.get(v.strip(), v.strip())) for v in raw.split(",") if v.strip())


def _quote(text):
	# Collapse whitespace so a pasted multi-line search can't break the title.
	return " ".join(text.split())


class _SearchFeedMixin(_SelfLinkMixin):
	"""Shared get_object/__call__ for the search feeds.

	Subclasses set `params_allowlist`, `cache_namespace` and implement
	`search_queryset(scope, subject_ids)` -> the base queryset for the
	narrowed subject set and `filter_class`, plus `hydrate(obj, ids)`.
	"""

	params_allowlist = ()
	cache_namespace = ""
	filter_class = None
	content_word = ""
	empty_title = ""
	title_prefix = "Search"
	# True for the patent search feed: it needs CustomSetting.patents_enabled
	# as well as rss_enabled, and patent links are visible to has_trial_link.
	patents = False

	def __call__(self, request, *args, **kwargs):
		if request.method not in ("GET", "HEAD"):
			return HttpResponseNotAllowed(["GET", "HEAD"])

		_params, canonical = canonical_params(request, self.params_allowlist)
		# The host is part of the key: the body contains the feed's own URL.
		raw_key = f"{self.cache_namespace}|{request.get_host()}|{kwargs['site_id']}|{canonical}"
		key = "rssfeed:" + hashlib.sha256(raw_key.encode()).hexdigest()

		cached = cache.get(key)
		if cached is None:
			try:
				response = super().__call__(request, *args, **kwargs)
			except InvalidSearchFeed as exc:
				# Never cached: only a 200 reaches cache.set below.
				response = HttpResponseBadRequest(str(exc), content_type="text/plain; charset=utf-8")
				response["X-Robots-Tag"] = "noindex, nofollow"
				return response
			cached = (response.content, response["Content-Type"])
			cache.set(key, cached, FEED_CACHE_SECONDS)

		response = HttpResponse(cached[0], content_type=cached[1])
		response["X-Robots-Tag"] = "noindex, nofollow"
		patch_cache_control(response, public=True, max_age=FEED_CACHE_SECONDS)
		return response

	def get_object(self, request, site_id):
		site, settings_row, scope = site_feed_scope(site_id, require_patents=self.patents)
		params, canonical = canonical_params(request, self.params_allowlist)
		_validate(params)

		# Narrow the subject set. A subject or category outside the SITE's
		# scope is a 404, as for the fixed feeds.
		subject = None
		subject_ids = set(scope)
		if "subject_id" in params:
			subject_id = int(params["subject_id"])
			if subject_id not in scope:
				raise Http404("Subject is not in this site's scope.")
			subject = Subject.objects.get(pk=subject_id)
			subject_ids = {subject_id}

		category = None
		if "category_slug" in params:
			category, cat_subject_ids = resolve_scoped_category(scope, params["category_slug"])
			# An article sits in a category through a subject; it must not leak
			# through one the site doesn't cover. If the reader also picked a
			# subject that isn't one of the category's, nothing can match: an
			# empty feed, not a 404 (both are in scope).
			subject_ids = subject_ids & cat_subject_ids

		shim, get = _filter_request(
			request, params, site, scope, patent_scope=scope if self.patents else ()
		)
		queryset = self.search_queryset(subject_ids)
		queryset = _run_filter(self.filter_class, queryset, get, shim)
		queryset = self.apply_relevance(queryset, params, subject_ids)

		ids = _page_ids(self.order(queryset), SEARCH_FEED_LIMIT)

		# Attach everything to the per-request obj (not self): the Feed
		# instance is shared between concurrent requests.
		obj = SimpleNamespace(
			ids=ids,
			site=site,
			settings_row=settings_row,
			scope=scope,
			canonical=canonical,
			label_title=self.channel_title(params, site, subject, category),
		)
		url = request.build_absolute_uri(request.path)
		# _SelfLinkMixin._request_url drops the query string; a search feed's
		# identity IS its (canonical) query string.
		obj._feed_url = f"{url}?{canonical}" if canonical else url
		return obj

	def apply_relevance(self, queryset, params, subject_ids):
		return queryset

	def order(self, queryset):
		return _discovery_order(queryset)

	def title(self, obj):
		return obj.label_title

	def link(self, obj):
		return _homepage(obj.site)

	def channel_title(self, params, site, subject, category):
		labels = self.labels(params, subject, category)
		if not labels:
			return f"{self.empty_title} — {site.name}"
		return f"{self.title_prefix}: {' · '.join(labels)} — {site.name}"

	def labels(self, params, subject, category):
		"""Channel-title labels in a fixed order. Subclasses add their own."""
		labels = []
		if "search" in params:
			labels.append(f'"{_quote(params["search"])}"')
		if "title" in params:
			labels.append(f'title: "{_quote(params["title"])}"')
		if "summary" in params:
			labels.append(f'abstract: "{_quote(params["summary"])}"')
		if subject is not None:
			labels.append(subject.subject_name)
		if category is not None:
			labels.append(category.category_name)
		return labels


class SiteArticlesSearchFeed(_SearchFeedMixin, ArticleFeedItemMixin, Feed):
	"""feed/sites/<site_id>/articles/search/ -- see the module docstring."""

	params_allowlist = ARTICLE_SEARCH_PARAMS
	cache_namespace = "articles-search"
	filter_class = ArticleFilter
	empty_title = "All articles"

	def search_queryset(self, subject_ids):
		return article_base_qs(subject_ids)

	def apply_relevance(self, queryset, params, subject_ids):
		relevant = params.get("relevant")
		if relevant is None:
			return queryset
		# Scoped to the subjects this feed covers, under the editorial rule
		# (the default of relevant_articles_q). ArticleFilter's own `relevant`
		# is the API's rule and is deliberately not used here.
		relevant_q = relevant_articles_q(subject_ids=subject_ids)
		return queryset.filter(relevant_q) if relevant == "true" else queryset.exclude(relevant_q)

	def labels(self, params, subject, category):
		labels = super().labels(params, subject, category)
		relevant = params.get("relevant")
		if relevant is not None:
			labels.append("relevant only" if relevant == "true" else "not marked relevant")
		open_access = params.get("open_access")
		if open_access is not None:
			labels.append("open access" if open_access == "true" else "not open access")
		has_takeaways = params.get("has_takeaways")
		if has_takeaways is not None:
			labels.append(
				"with editorial summary" if has_takeaways == "true" else "without editorial summary"
			)
		return labels

	def description(self, obj):
		return "The newest articles matching this search, newest first."

	def items(self, obj):
		return hydrate_articles(obj.ids, obj.site, obj.scope)


class SiteTrialsSearchFeed(_SearchFeedMixin, TrialFeedItemMixin, Feed):
	"""feed/sites/<site_id>/trials/search/ -- see the module docstring."""

	params_allowlist = TRIAL_SEARCH_PARAMS
	cache_namespace = "trials-search"
	filter_class = TrialFilter
	empty_title = "All clinical trials"

	def search_queryset(self, subject_ids):
		return trial_base_qs(subject_ids)

	def labels(self, params, subject, category):
		labels = super().labels(params, subject, category)
		if "recruitment_status_normalized" in params:
			labels.append(
				_choice_label(TrialRecruitmentStatus.choices, params["recruitment_status_normalized"])
			)
		if "phase_normalized" in params:
			labels.append(_choice_label(TrialPhase.choices, params["phase_normalized"]))
		if "region" in params:
			labels.append(_choice_label(TrialRegion.choices, params["region"]))
		if "country" in params:
			labels.append(params["country"])
		if "sponsor_slug" in params:
			name = (
				Sponsor.objects.filter(slug=params["sponsor_slug"])
				.values_list("name", flat=True)
				.first()
			)
			labels.append(name or params["sponsor_slug"])
		if "inclusion_gender_normalized" in params:
			labels.append(
				_choice_label(TrialSexEligibility.choices, params["inclusion_gender_normalized"])
			)
		if "age_eligible" in params:
			labels.append(f"age {params['age_eligible']}")
		return labels

	def description(self, obj):
		return "The newest clinical trials matching this search, newest first."

	def items(self, obj):
		return hydrate_trials(obj.ids, obj.site, obj.settings_row, obj.scope)


# What crawlers must not follow on the API host: every search URL is a new
# query for us to run (the patent search feed included).
ROBOTS_TXT = (
	"User-agent: *\n"
	"Disallow: /feed/sites/*/articles/search/\n"
	"Disallow: /feed/sites/*/trials/search/\n"
	"Disallow: /feed/sites/*/patents/search/\n"
)


def robots_txt(request):
	"""/robots.txt for the API host.

	Django had no robots route; nginx answered /robots.txt itself (a 404
	unless a static file was dropped in). nginx now has to hand it to Django
	-- see nginx-example-configuration/nginx.conf, the api. server block.
	"""
	return HttpResponse(ROBOTS_TXT, content_type="text/plain; charset=utf-8")
