import hashlib
import json

from django.conf import settings
from django.core.cache import cache
from django.core.paginator import InvalidPage
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from django.utils.functional import cached_property


def request_bypasses_pagination(request):
	"""
	True when the request asks to bypass pagination via all_results=true/1/yes,
	checked in both query params (GET) and request data (POST).
	"""
	all_results = request.query_params.get("all_results", "").lower()

	if all_results == "" and request.method == "POST":
		all_results = str(request.data.get("all_results", "")).lower()

	return all_results in ("true", "1", "yes")


class MaxOffsetMixin:
	"""
	Rejects requests whose (page * page_size) offset exceeds ``max_offset``.

	HOUSE-LOAD-SPIKE-PLAN.md P1 item 4: deep offsets on an unfiltered,
	unindexed-order query turn into a full table sort. Crawler traffic was
	observed at page=25778 (offset ~250k) driving House into parallel-worker
	exhaustion. This caps the worst case regardless of caller; callers who
	need the full set should use all_results=true or a CSV export instead.

	Split out of FlexiblePagination (HOUSE-LOAD-SPIKE-P2-QUERY-COST.md item 1)
	so the cap can be attached to a plain PageNumberPagination class without
	also pulling in all_results/page_size-from-POST-body support — those
	extras are why AuthorsViewSet must NOT get the full FlexiblePagination:
	its `list()` has a dead `all_results` branch (`target = page if page is
	not None else list(queryset)`) that would materialise and annotate the
	entire visible authors table. See HOUSE-LOAD-SPIKE-P2-QUERY-COST.md.
	"""

	max_offset = 10000

	def _enforce_max_offset(self, request, queryset):
		page_size = self.get_page_size(request)
		if not page_size:
			return

		try:
			page_number = int(self.get_page_number(request, None))
		except (TypeError, ValueError):
			page_number = None
		except AttributeError:
			# get_page_number(request, None) raises here for page=last on a
			# pagination class that doesn't override get_page_number (e.g.
			# CappedPageNumberPagination): DRF's base implementation
			# resolves "last" via paginator.num_pages, and the real
			# paginator doesn't exist at this point in the request, hence
			# None. Resolve "last" against an actual count instead of
			# treating it like any other unparseable value and silently
			# skipping the check — get_page_number(request, <real
			# paginator>) downstream WILL resolve "last" to the true last
			# page once the real paginator exists, unprotected, if we don't
			# cap it here first.
			raw_page = request.query_params.get(self.page_query_param, 1)
			if raw_page not in self.last_page_strings:
				return
			total = queryset.count()
			page_number = -(-total // page_size) if total else 1

		if page_number is not None and page_number * page_size > self.max_offset:
			raise ValidationError(
				f"Requested offset ({page_number * page_size}) exceeds the maximum "
				f"of {self.max_offset}. Use all_results=true to retrieve the full "
				"result set instead of paging this deep (optionally combined with "
				"format=csv) — format=csv alone is still paginated and subject to "
				"this same limit."
			)


class CachedCountMixin:
	"""
	Caches the paginator's ``COUNT(*)`` in the shared DB cache.

	HOUSE-LOAD-SPIKE-P2-QUERY-COST.md item 3: on a shallow, cheap list page
	the paginator's COUNT(*) is the dominant cost (~90% of a `/authors/`
	request, ~67% of a `/articles/` request). DatabaseCache measured 0.16ms
	get / 0.81ms set / 0.21ms miss against it — a 33-120ms count collapses
	to noise.

	``django.core.paginator.Paginator.count`` is a ``cached_property``:
	setting it directly on the instance (``paginator.count = value``)
	short-circuits the descriptor and skips the COUNT(*) query entirely,
	since a non-data descriptor loses to an instance ``__dict__`` entry on
	attribute lookup. There is no seam to do this via
	``super().paginate_queryset(...)`` — DRF's ``PageNumberPagination``
	constructs the ``Paginator`` and calls ``.page()`` inside one method
	body with nothing to override in between — so this replicates that
	method rather than delegating to it. Mix in BEFORE PageNumberPagination
	so this ``paginate_queryset`` — not DRF's — is what ``super()`` resolves
	to from FlexiblePagination/CappedPageNumberPagination.

	Requires ``CACHES['default']['MAX_ENTRIES']`` raised above Django's
	default of 300 (see admin/settings.py) — left at 300 the count cache
	competes with every other DatabaseCache consumer for a tiny table and
	thrashes under cull.
	"""

	# A count doesn't depend on response shape the way a stats payload does
	# — none of these change it, so give it its own ignored-params set
	# rather than widening CachedStatsActionMixin._stats_key_ignored_params.
	# `ordering`/`format` cover FlexiblePagination's users (Articles,
	# Trials, ...); `sort_by`/`order` cover AuthorsViewSet, which doesn't
	# use DRF's `ordering` param at all and would otherwise fragment the
	# count cache across every sort_by/order combination for no reason —
	# neither one is a filterset field anywhere in api/filters.py, so
	# excluding them can't accidentally hide a real filter's effect on the
	# count. Without this, a crawler working through N orderings × M
	# formats would fragment into N*M cache entries for what is always the
	# same number.
	_count_key_ignored_params = frozenset(
		{"page", "page_size", "all_results", "ordering", "format", "sort_by", "order"}
	)

	def _count_cache_key(self, request):
		"""Tenant-safe cache key: path + visible org ids + normalised params.

		Mirrors CachedStatsActionMixin._stats_cache_key's security shape
		(org ids and query params are both load-bearing — dropping either
		would serve one tenant's count to another caller) but keys on
		``request.path`` instead of a per-view prefix string, since this
		pagination class is shared across several viewsets (Articles,
		Trials, Sponsors, ...) and the path is what actually distinguishes
		their counts from one another.
		"""
		visible_org_ids = getattr(request, "visible_org_ids", None)
		orgs = None if visible_org_ids is None else sorted(visible_org_ids)
		params = sorted(
			(key, value)
			for key in request.query_params.keys()
			if key not in self._count_key_ignored_params
			for value in request.query_params.getlist(key)
		)
		digest = hashlib.sha256(
			json.dumps({"path": request.path, "orgs": orgs, "params": params}).encode()
		).hexdigest()
		return f"paginator_count:{digest}"

	def paginate_queryset(self, queryset, request, view=None):
		"""Reimplementation of PageNumberPagination.paginate_queryset that
		seeds ``paginator.count`` from cache before ``.page()`` can trigger
		a COUNT(*) query. Keep in sync with DRF's implementation if it
		changes.
		"""
		self.request = request
		page_size = self.get_page_size(request)
		if not page_size:
			return None

		paginator = self.django_paginator_class(queryset, page_size)

		cache_key = self._count_cache_key(request)
		cached_count = cache.get(cache_key)
		if cached_count is not None:
			paginator.count = cached_count
		else:
			cache.set(cache_key, paginator.count, settings.COUNT_CACHE_TTL)

		page_number = self.get_page_number(request, paginator)

		try:
			self.page = paginator.page(page_number)
		except InvalidPage as exc:
			msg = self.invalid_page_message.format(
				page_number=page_number, message=str(exc)
			)
			raise NotFound(msg)

		if paginator.num_pages > 1 and self.template is not None:
			self.display_page_controls = True

		return list(self.page)


class FlexiblePagination(MaxOffsetMixin, CachedCountMixin, PageNumberPagination):
	"""
	A flexible pagination class that can handle pagination parameters
	from both query strings (GET requests) and request data (POST requests).
	Also supports bypassing pagination entirely when 'all_results=true' is specified.
	"""

	page_size = 10
	page_size_query_param = "page_size"
	max_page_size = 100

	# HOUSE-LOAD-SPIKE-PLAN.md P1 item 4: deep offsets on an unfiltered,
	# unindexed-order query turn into a full table sort. Crawler traffic was
	# observed at page=25778 (offset ~250k) driving House into parallel-worker
	# exhaustion. This caps the worst case regardless of caller; callers who
	# need the full set should use all_results=true or a CSV export instead.
	max_offset = 10000

	@cached_property
	def should_bypass_pagination(self):
		"""
		Check if pagination should be bypassed based on request parameters.
		"""
		return request_bypasses_pagination(self.request)

	def paginate_queryset(self, queryset, request, view=None):
		"""
		Either paginate the queryset or return None to indicate no pagination.
		"""
		self.request = request

		if self.should_bypass_pagination:
			return None

		self._enforce_max_offset(request, queryset)

		return super().paginate_queryset(queryset, request, view)

	def get_page_size(self, request):
		"""
		Get the page size from either query parameters or request data.
		"""
		if self.should_bypass_pagination:
			return None

		if request.method == "POST" and "page_size" in request.data:
			try:
				return min(int(request.data["page_size"]), self.max_page_size)
			except (KeyError, ValueError, TypeError):
				pass

		return super().get_page_size(request)

	def get_page_number(self, request, paginator):
		"""
		Get the page number from either query parameters or request data.
		"""
		if self.should_bypass_pagination:
			return 1

		page_number = request.query_params.get(self.page_query_param, 1)

		if request.method == "POST" and "page" in request.data:
			try:
				page_number = request.data["page"]
			except (KeyError, ValueError, TypeError):
				pass

		return page_number

	def get_paginated_response(self, data):
		"""
		Enhance the paginated response with additional metadata.
		"""
		return Response(
			{
				"count": self.page.paginator.count,
				"next": self.get_next_link(),
				"previous": self.get_previous_link(),
				"current_page": self.page.number,
				"total_pages": self.page.paginator.num_pages,
				"page_size": self.get_page_size(self.request),
				"results": data,
			}
		)


class CappedPageNumberPagination(MaxOffsetMixin, CachedCountMixin, PageNumberPagination):
	"""Plain PageNumberPagination with an offset ceiling and a cached count.

	HOUSE-LOAD-SPIKE-P2-QUERY-COST.md item 1: caps GET /authors/, the largest
	table in the API with the most expensive visibility filter, previously
	unbounded (AuthorsViewSet set no pagination_class and fell back to DRF's
	plain PageNumberPagination). Deliberately NOT FlexiblePagination — see
	MaxOffsetMixin's docstring for why reusing it here would open a worse
	hole than it closes. No all_results support, no page_size-from-POST-body,
	no response-envelope change otherwise: behaviour is identical to plain
	PageNumberPagination except for the added cap and (item 3) the cached
	COUNT(*) — see CachedCountMixin.
	"""

	def paginate_queryset(self, queryset, request, view=None):
		self._enforce_max_offset(request, queryset)
		return super().paginate_queryset(queryset, request, view)


class TrialSitePagination(PageNumberPagination):
	"""Pagination for ``GET /trials/sites/`` (TRIAL-GEOGRAPHY-PLAN.md PR G3).

	Deliberately does NOT support ``all_results=true`` — the payload is flat and
	small per row, but at mean ~12 sites/trial an unfiltered request over a
	several-thousand-trial subject could still be tens of thousands of rows;
	pagination is mandatory so this endpoint is never a bulk export. See
	TrialViewSet.sites, which raises a 400 when ``all_results`` resolves to a
	pagination-bypass value (true/1/yes — see request_bypasses_pagination);
	other values (e.g. ``all_results=false``) are not bypasses and paginate
	normally, same as everywhere else ``all_results`` is accepted.
	"""

	page_size = 100
	page_size_query_param = "page_size"
	max_page_size = 500
