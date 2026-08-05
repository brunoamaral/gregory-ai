from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError
from django.db.models import F
from rest_framework.filters import OrderingFilter as _BaseOrderingFilter


class NullsLastOrderingFilter(_BaseOrderingFilter):
	"""OrderingFilter that forces NULLS LAST for fields listed in nulls_last_fields."""

	nulls_last_fields = frozenset({"ml_score"})

	def filter_queryset(self, request, queryset, view):
		ordering = self.get_ordering(request, queryset, view)
		if not ordering:
			return queryset
		processed = []
		for term in ordering:
			field = term.lstrip("-")
			if field in self.nulls_last_fields:
				expr = (
					F(field).desc(nulls_last=True)
					if term.startswith("-")
					else F(field).asc(nulls_last=True)
				)
				processed.append(expr)
			else:
				processed.append(term)
		return queryset.order_by(*processed)
from api.serializers import (
	ArticleSerializer,
	TrialSerializer,
	TrialDetailSerializer,
	SourceSerializer,
	AuthorSerializer,
	CoauthorSerializer,
	CategorySerializer,
	CategoryTopAuthorSerializer,
	TeamSerializer,
	SubjectsSerializer,
	OrganizationSerializer,
	SponsorSerializer,
)
from api.pagination import (
	FlexiblePagination,
	TrialSitePagination,
	request_bypasses_pagination,
)
from api.direct_streaming import (
	DirectStreamingCSVRenderer,
	csv_header_fields,
	stream_csv,
)
from datetime import datetime, timedelta
from django.db.models import (
	Case,
	Count,
	Exists,
	Max,
	Q,
	Prefetch,
	OuterRef,
	Subquery,
	Value,
	When,
	IntegerField,
)
from django.db.models.functions import Coalesce, ExtractYear
from gregory.classes import SciencePaper, ClinicalTrial
from gregory.utils.trial_field_normalizers import (
	SponsorType,
	TrialPhase,
	TrialRecruitmentStatus,
	TrialRegion,
	TrialSexEligibility,
	TrialStudyType,
)
from gregory.models import (
	Articles,
	ArticleCategoryAssignment,
	ArticleOrgContent,
	ArticleSubjectRelevance,
	ArticleTrialReference,
	Trials,
	TrialCategoryAssignment,
	TrialOrgContent,
	TrialSite,
	Sources,
	Authors,
	Team,
	Subject,
	TeamCategory,
	CategoryModality,
	MLPredictions,
	Sponsor,
)
from organizations.models import Organization
from rest_framework import permissions, viewsets, generics, filters, status
from rest_framework.decorators import api_view, action
from rest_framework.exceptions import ValidationError
from rest_framework.throttling import ScopedRateThrottle
from django_filters import rest_framework as django_filters
from api.filters import (
	ArticleFilter,
	TrialFilter,
	AuthorFilter,
	SourceFilter,
	CategoryFilter,
	SubjectFilter,
	SponsorFilter,
)
from rest_framework.response import Response
from django.http import Http404, StreamingHttpResponse
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView
from drf_spectacular.utils import (
	extend_schema,
	extend_schema_view,
	OpenApiParameter,
	OpenApiTypes,
)
from api.schema_serializers import (
	ArticlesStatsSerializer,
	ErrorResponseSerializer,
	GlobalStatsSerializer,
	TrialSiteRowSerializer,
	TrialsStatsSerializer,
	filterset_request_schema,
)
import hashlib
import json
import logging
import traceback
from django.utils.dateparse import parse_date
from django.utils.timezone import now as tz_now

from api.serializers.mixins import _resolve_per_org_fields_org
from api.utils.utils import (
	checkValidAccess,
	getAPIKey,
	getIPAddress,
	find_trial_by_identifier,
)
from gregory.utils.registry_utils import merge_links
from api.models import APIAccessSchemeLog
from api.utils.exceptions import (
	APIAccessDeniedError,
	APIInvalidAPIKeyError,
	APIInvalidIPAddressError,
	APINoAPIKeyError,
	ArticleExistsError,
	ArticleNotFoundError,
	ArticleNotSavedError,
	CrossOrgPayloadError,
	DuplicateArticleError,
	DuplicateTrialError,
	FieldNotFoundError,
	SourceNotFoundError,
	TrialNotFoundError,
)
from api.utils.responses import (
	ACCESS_DENIED,
	ARTICLE_NOT_FOUND,
	ARTICLE_NOT_SAVED,
	ARTICLE_EXISTS,
	CROSS_ORG_PAYLOAD,
	DUPLICATE_ARTICLE,
	DUPLICATE_TRIAL,
	FIELD_NOT_FOUND,
	INVALID_API_KEY,
	INVALID_IP_ADDRESS,
	INVALID_JSON,
	NO_API_KEY,
	SOURCE_NOT_FOUND,
	TRIAL_NOT_FOUND,
	UNEXPECTED,
	returnData,
	returnError,
)


class CSVStreamingMixin:
	"""
	Viewset mixin that streams CSV list responses in bounded batches.

	Intercepts list() for ?format=csv before DRF serializes the full
	queryset: rows are serialized chunk_size at a time inside a generator
	(prefetch_related batches per chunk via .iterator()), so memory stays
	flat regardless of corpus size and the first byte leaves immediately.
	"""

	csv_stream_chunk_size = 2000

	def list(self, request, *args, **kwargs):
		if request.query_params.get("format", "").lower() != "csv":
			return super().list(request, *args, **kwargs)

		queryset = self.filter_queryset(self.get_queryset())
		page = self.paginate_queryset(queryset)
		source = page if page is not None else queryset

		header_serializer = self.get_serializer()
		header_fields = csv_header_fields(header_serializer, request)

		def serialize_batch(batch):
			return self.get_serializer(batch, many=True).data

		filename = DirectStreamingCSVRenderer().get_filename({"request": request})
		response = StreamingHttpResponse(
			stream_csv(source, serialize_batch, header_fields, self.csv_stream_chunk_size),
			content_type="text/csv; charset=utf-8",
		)
		response["Content-Disposition"] = f'attachment; filename="{filename}"'
		# Without this nginx buffers the whole stream and the TTFB win
		# evaporates in production.
		response["X-Accel-Buffering"] = "no"
		return response

	def finalize_response(self, request, response, *args, **kwargs):
		response = super().finalize_response(request, response, *args, **kwargs)
		if getattr(response, "streaming", False):
			return response
		# Fallback wrap for non-list CSV responses (e.g. detail routes),
		# which are single-object and small.
		if request.query_params.get("format", "").lower() == "csv":
			response.render()
			csv_bytes = (
				response.content
				if isinstance(response.content, bytes)
				else response.content.encode("utf-8")
			)
			filename = DirectStreamingCSVRenderer().get_filename({"request": request})
			streaming_response = StreamingHttpResponse(
				streaming_content=iter([csv_bytes]),
				content_type="text/csv; charset=utf-8",
			)
			streaming_response["Content-Disposition"] = (
				f'attachment; filename="{filename}"'
			)
			return streaming_response
		return response


class BulkExportThrottleMixin:
	"""
	Applies a scoped throttle only when the request bypasses pagination
	(``all_results=true``); normal paginated list requests are unaffected.
	"""

	throttle_scope = "bulk_export"

	def get_throttles(self):
		if request_bypasses_pagination(self.request):
			return [ScopedRateThrottle()]
		return super().get_throttles()


class BodyParamsAsQueryParamsMixin:
	"""Make POST-body params visible to DRF's filter backends.

	DjangoFilterBackend and OrderingFilter read request.query_params only, so on
	a POST every filterset param used to be dropped silently and the response
	came back unfiltered — a wrong 200, not an error. Merging the body into
	query_params gives GET and POST a single code path.

	An explicit query-string value wins over the same key in the body, so the
	documented `POST /search/?filters...` workaround keeps working unchanged.
	This precedence applies to filter/ordering keys only — NOT to pagination
	(`page`/`page_size` are read straight from the body by FlexiblePagination
	when present, ignoring the query string, so those are body-wins on POST).

	`identity_params` (set per view below) names required-parameter keys —
	team_id/subject_id, plus full_name on AuthorSearchView — that the view
	reads directly from request.data on POST for validation. Those same names
	are also ArticleFilter/TrialFilter/AuthorFilter fields, so without this
	carve-out a mismatched query-string value would leak into the filterset
	(via the normal query-string-wins merge) while validation used the body's
	value, silently intersecting the two and emptying the response. Body wins
	unconditionally for these keys instead, so the filterset always agrees with
	what the view validated.

	List-valued body params (`{"subjects": [1, 3]}`, or repeated keys in a
	form-encoded body) are joined into a single comma-separated string rather
	than set as a repeated query key. This codebase's list filters are all
	``BaseInFilter``/``ChoiceInFilter`` subclasses, whose form field reads a
	single CSV-formatted value — confirmed empirically that a repeated query
	key (`?subjects=1&subjects=3`) silently collapses to just the last value,
	while `?subjects=1,3` parses correctly. Comma-joining is what actually
	reaches the filterset intact.
	"""

	identity_params = frozenset()

	def initial(self, request, *args, **kwargs):
		super().initial(request, *args, **kwargs)
		if request.method != "POST":
			return
		data = request.data
		if not hasattr(data, "items"):  # not a JSON object / form payload
			return
		merged = request.query_params.copy()  # a mutable QueryDict
		items = data.lists() if hasattr(data, "lists") else data.items()
		for key, value in items:
			if key in merged and key not in self.identity_params:
				continue  # query string wins, except for identity_params
			if isinstance(value, (list, tuple)):
				merged[key] = ",".join(str(v) for v in value if v is not None)
			elif value is not None:
				merged[key] = str(value)
		merged._mutable = False
		request._request.GET = merged


def _latest_ml_predictions_queryset():
	"""``MLPredictions`` queryset restricted to the latest row per
	(article, subject, algorithm), for use as a serializer prefetch.

	Since PR #748, "current" ML relevance is latest-per-(subject, algorithm)
	only — a retired model_version's stale score must not keep showing up
	in the API forever. Same tie-inclusive Max(created_date) correlated
	subquery as ``Articles.is_ml_relevant_for_subject``, generalised to
	correlate on article_id too (that method fixes article/subject and
	correlates only on algorithm). Index-backed by ``mlpred_art_subj_date_idx``.
	Full prediction history is never deleted; it stays reachable via the
	admin/DB for anyone who needs it.
	"""
	latest_date_per_group = (
		MLPredictions.objects.filter(
			article_id=OuterRef("article_id"),
			subject_id=OuterRef("subject_id"),
			algorithm=OuterRef("algorithm"),
		)
		.values("algorithm")
		.annotate(latest=Max("created_date"))
		.values("latest")[:1]
	)
	return MLPredictions.objects.filter(
		created_date=Subquery(latest_date_per_group)
	).select_related("subject")


class OrgVisibilityMixin:
	"""
	Viewset mixin that scopes the queryset to organisations the caller can see.

	Uses ``request.visible_org_ids`` (set by ``VisibleOrgMiddleware``).  Falls
	back to the full queryset when the attribute is absent so tests and
	management commands that bypass middleware are not broken.

	Override ``_org_filter_path`` in the subclass to set the ORM lookup path
	from the model to the Organisation PK.  Defaults to
	``teams__organization_id`` (Articles and Trials via M2M teams relation).

	Examples:
	  - Team:          _org_filter_path = 'organization_id'
	  - Subject/Source/Category:  _org_filter_path = 'team__organization_id'
	"""

	_org_filter_path = "teams__organization_id"
	# Set to False for viewsets that reach orgs via a simple FK (not M2M),
	# where a plain filter() can't duplicate rows and Exists() would be
	# unnecessary overhead. True means the path crosses a multi-valued (M2M)
	# relation, so we use a correlated Exists() subquery instead of a
	# join + distinct() to avoid duplicating rows without paying the cost of
	# DISTINCT-ing every column in the paginator's COUNT(*) query.
	_org_filter_distinct = True

	def get_queryset(self):
		qs = super().get_queryset()
		if not hasattr(self.request, "visible_org_ids"):
			return qs
		if self._org_filter_distinct:
			subq = qs.model.objects.filter(
				pk=OuterRef("pk"),
				**{f"{self._org_filter_path}__in": self.request.visible_org_ids},
			)
			return qs.filter(Exists(subq))
		return qs.filter(**{f"{self._org_filter_path}__in": self.request.visible_org_ids})


class CachedStatsActionMixin:
	"""
	Shared machinery for filter-scoped, cached ``GET /<resource>/stats/`` actions.

	Subclasses set ``stats_cache_prefix`` (a per-endpoint string such as
	``"trials_stats"``) and implement ``build_stats_payload(filtered_qs)``
	returning a JSON-serialisable dict. The action itself then only calls
	``self._stats_response(request)``, which:

	  1. builds a tenant-safe cache key and returns the cached payload when
	     present;
	  2. otherwise runs ``self.filter_queryset(self.get_queryset())`` — so
	     every filterset parameter AND OrgVisibilityMixin's tenant scoping
	     apply to the stats exactly as they do to the list — builds the
	     payload, and caches it for ``settings.STATS_CACHE_TTL`` seconds in
	     the shared database cache.

	SECURITY: the cache key incorporates (a) the caller's sorted visible
	org ids, (b) the sorted normalised query string, and (c) the per-endpoint
	prefix, hashed with sha256 to bound key length for the DB cache. All
	three are load-bearing — dropping the org ids or the query string would
	serve one tenant's cached numbers to another caller.
	"""

	#: Per-endpoint cache-key prefix; must be unique across stats actions.
	stats_cache_prefix = None

	#: Query params that never change a stats payload — excluded from the
	#: cache key so paginated list navigation can't fragment the cache.
	_stats_key_ignored_params = frozenset({"page", "page_size", "all_results"})

	def _stats_cache_key(self, request):
		visible_org_ids = getattr(request, "visible_org_ids", None)
		orgs = (
			None if visible_org_ids is None else sorted(visible_org_ids)
		)
		# JSON of sorted (key, value) pairs is an unambiguous encoding: naive
		# "k=v&k=v" concatenation lets a param value containing '&' or '=' (easy
		# via ?search=) collide two different filter sets into one cache key.
		params = sorted(
			(key, value)
			for key in request.query_params.keys()
			if key not in self._stats_key_ignored_params
			for value in request.query_params.getlist(key)
		)
		digest = hashlib.sha256(
			json.dumps({"orgs": orgs, "params": params}).encode()
		).hexdigest()
		return f"{self.stats_cache_prefix}:{digest}"

	def _stats_response(self, request):
		cache_key = self._stats_cache_key(request)
		cached = cache.get(cache_key)
		if cached is not None:
			return Response(cached)

		filtered_qs = self.filter_queryset(self.get_queryset())
		payload = self.build_stats_payload(filtered_qs)
		cache.set(cache_key, payload, settings.STATS_CACHE_TTL)
		return Response(payload)

	def _by_subject_counts(self, filtered_qs):
		"""``[{"subject_id", "subject_name", "count"}]`` over *filtered_qs*.

		Aggregates off the ``<model>.subjects`` M2M through-table (plain FK
		joins — immune to the row-duplication ambiguity of stacking filters
		on a multi-valued relation) with a distinct count per parent row.

		SECURITY: an article/trial visible to the caller can be tagged with
		a subject belonging to a NON-visible org. The list serializers strip
		such subjects (OrgScopedSerializerMixin); the stats must not leak
		them either, so when ``request.visible_org_ids`` exists only subjects
		whose team's organisation is visible are included.
		"""
		model = filtered_qs.model
		through = model.subjects.through
		source_field = model._meta.model_name  # e.g. "articles" / "trials"
		qs = through.objects.filter(
			**{f"{source_field}__in": filtered_qs.order_by().values("pk")}
		)
		visible_org_ids = getattr(self.request, "visible_org_ids", None)
		if visible_org_ids is not None:
			qs = qs.filter(subject__team__organization_id__in=visible_org_ids)
		rows = (
			qs.values("subject_id", "subject__subject_name")
			.annotate(count=Count(f"{source_field}_id", distinct=True))
			.order_by("-count", "subject_id")
		)
		return [
			{
				"subject_id": row["subject_id"],
				"subject_name": row["subject__subject_name"],
				"count": row["count"],
			}
			for row in rows
		]


def getDateRangeFromWeek(p_year, p_week):
	firstdayofweek = datetime.strptime(f"{p_year}-W{int(p_week) - 1}-1", "%Y-W%W-%w")
	lastdayofweek = firstdayofweek + timedelta(days=6.9)
	return (firstdayofweek, lastdayofweek)


# Util function that creates an instance of the access log model.
#
# Defensive by design: this is called from inside exception handlers, so a
# failure here (e.g. an oversized field hitting a DataError) must never
# propagate and replace the API's real response. Anything that goes wrong
# writing the audit row is logged via the `logging` module instead.
#
# Three phases:
#   1. Sanitize/truncate every value BEFORE the try block, so all of them are
#      guaranteed bound if the full-row write fails and the except handler
#      needs to reference them (payload_str in particular used to be assigned
#      inside the try, which would raise UnboundLocalError from the handler
#      if the failure happened before that line was reached).
#   2. Attempt the full row.
#   3. On failure: log full request context, then retry a minimal row (no
#      payload) so a schema-drift or encoding surprise degrades the audit
#      trail instead of silently dropping the request from it. If even the
#      minimal row fails, log and give up — this function must never raise.
#
# Note on ip_addr: it is also passed to checkValidAccess, which exact-matches
# it against the comma-separated APIAccessScheme.ip_addresses allowlist. The
# truncation below is local to this function and must stay that way — trim
# it before the allowlist check and a long spoofed X-Forwarded-For could be
# cut down into matching an allowlisted prefix.
def generateAccessSchemeLog(
	call_type, ip_addr, access_scheme, http_code, error_message, post_data
):
	if call_type is not None and len(call_type) > 200:
		call_type = call_type[:200]

	if ip_addr is not None and len(ip_addr) > 45:
		ip_addr = ip_addr[:45]

	if error_message is not None and len(error_message) > 499:
		error_message = error_message[:499]

	payload_str = post_data if isinstance(post_data, str) else str(post_data)
	if len(payload_str) > 1700:
		payload_str = payload_str[:1700]

	try:
		log = APIAccessSchemeLog()
		log.call_type = call_type
		log.ip_addr = ip_addr
		if access_scheme is not None:
			log.api_access_scheme = access_scheme
		log.http_code = http_code
		log.error_message = error_message
		log.payload_received = payload_str

		log.save()
	except Exception:
		logger = logging.getLogger(__name__)
		# Deliberately omit the payload here: it is free-form client-submitted
		# content (post_article bodies can carry author names, emails, etc.)
		# and application logs have looser access control / retention than
		# the database. This line carries exactly the fields the minimal
		# fallback row below persists, and nothing more — payload_received
		# is still written to the DB on the normal (non-failure) path.
		logger.exception(
			"Failed to write APIAccessSchemeLog row; API response is unaffected. "
			"call_type=%s ip_addr=%s http_code=%s error_message=%s",
			call_type,
			ip_addr,
			http_code,
			error_message,
		)
		try:
			APIAccessSchemeLog.objects.create(
				call_type=call_type,
				ip_addr=ip_addr,
				api_access_scheme=access_scheme,
				http_code=http_code,
				error_message="[payload dropped after write failure]",
				payload_received=None,
			)
		except Exception:
			logger.exception("Fallback APIAccessSchemeLog row also failed.")


###
# API Post
###


@extend_schema(
	request=OpenApiTypes.OBJECT,
	responses={
		200: OpenApiTypes.OBJECT,
		201: OpenApiTypes.OBJECT,
		400: ErrorResponseSerializer,
		401: ErrorResponseSerializer,
		403: ErrorResponseSerializer,
		404: ErrorResponseSerializer,
		409: ErrorResponseSerializer,
		500: ErrorResponseSerializer,
	},
	auth=[{"apiKeyAuth": []}],
	description=(
		"Allows authenticated clients to add new articles or trials to the "
		"database. The `kind` field in the payload must match the `source_for` "
		"value of the indicated source. Routing per kind: `science paper` → "
		"CrossRef enrichment, saved to Articles; `trials` → saved to Trials "
		"(dedup by identifier then title); `news article` → saved to Articles, "
		"no CrossRef lookup. Requires an API key (`Authorization` header) "
		"belonging to an organisation — see docs/03-api-and-rss-feeds.md."
	),
)
@api_view(["POST"])
def post_article(request):
	"""
	Allows authenticated clients to add new articles or trials to the database.

	The ``kind`` field in the payload must match the ``source_for`` value of the
	indicated source.  Routing per kind:
	  - ``science paper``  → CrossRef enrichment, saved to Articles
	  - ``trials``         → saved to Trials (dedup by identifier then title)
	  - ``news article``   → saved to Articles, no CrossRef lookup
	"""
	access_scheme = None
	call_type = request.method + " " + request.path
	ip_addr = getIPAddress(request)
	try:
		post_data = json.loads(request.body)
	except json.JSONDecodeError as exception:
		raw_body = request.body.decode("utf-8", errors="replace")
		generateAccessSchemeLog(
			call_type, ip_addr, None, 400, str(exception), raw_body
		)
		return returnError(INVALID_JSON, str(exception), 400)
	try:
		api_key = getAPIKey(request)
		access_scheme = checkValidAccess(api_key, ip_addr)

		# PR 7: keys without an org cannot post
		if access_scheme.organization is None:
			raise APIAccessDeniedError(
				"API keys without an associated organisation cannot post articles."
			)

		# --- Field presence checks -------------------------------------------
		if "kind" not in post_data or post_data["kind"] is None:
			raise FieldNotFoundError("field `kind` was not found in the payload")
		if "source_id" not in post_data or post_data["source_id"] is None:
			raise FieldNotFoundError("source_id field not found in payload")
		if "title" not in post_data and "doi" not in post_data:
			raise FieldNotFoundError(
				"field `doi` and `title` not in the payload. You need at least one."
			)

		# --- Source validation (existence, org, kind match) ------------------
		try:
			source = Sources.objects.get(pk=post_data["source_id"])
		except Sources.DoesNotExist:
			raise SourceNotFoundError(
				f"source_id {post_data['source_id']} was not found in the database"
			)
		if source.team is None:
			raise SourceNotFoundError(f"source_id {source.pk} has no team assigned")
		if source.team.organization_id != access_scheme.organization_id:
			raise CrossOrgPayloadError(
				f"source_id {source.pk} belongs to a different organisation than your API key."
			)
		if post_data["kind"] != source.source_for:
			raise FieldNotFoundError(
				f"payload kind '{post_data['kind']}' does not match source kind '{source.source_for}'"
			)

		# Helper: coerce empty strings to None
		def _val(key):
			v = post_data.get(key)
			return None if v == "" else v

		kind = post_data["kind"]

		# =================================================================
		# Branch: science paper
		# =================================================================
		if kind == "science paper":
			new_article = {
				"title": _val("title"),
				"link": _val("link"),
				"doi": _val("doi"),
				"access": _val("access"),
				"summary": _val("summary"),
				"published_date": _val("published_date"),
				"kind": kind,
				"publisher": _val("publisher"),
				"container_title": _val("container_title"),
				"pdf_link": _val("pdf_link"),
			}
			science_paper = SciencePaper(
				doi=new_article["doi"], title=new_article["title"]
			)
			if science_paper.doi is None:
				science_paper.doi = science_paper.find_doi(title=science_paper.title)
			if science_paper.doi is not None:
				science_paper.refresh()
			if new_article["doi"] is None:
				new_article["doi"] = science_paper.doi
			if new_article["title"] is None:
				new_article["title"] = science_paper.title
			if new_article["link"] is None:
				new_article["link"] = science_paper.link
			if new_article["summary"] is None:
				new_article["summary"] = science_paper.clean_abstract()
			if new_article["published_date"] is None:
				new_article["published_date"] = science_paper.published_date
			if new_article["access"] is None:
				new_article["access"] = science_paper.access
			if new_article["publisher"] is None:
				new_article["publisher"] = science_paper.publisher
			if new_article["container_title"] is None:
				new_article["container_title"] = science_paper.journal
			if new_article["pdf_link"] is None:
				new_article["pdf_link"] = science_paper.pdf_link

			# Dedup by DOI. The DB constraint (unique_article_doi) enforces
			# uniqueness on Lower(doi), so the pre-check must match the same
			# way — an exact filter() would let a case-variant DOI slip
			# through here and fail later with an unhandled IntegrityError.
			if new_article["doi"] is not None:
				existing = Articles.objects.filter(doi__iexact=new_article["doi"])
				if existing.exists():
					for article in existing:
						article.sources.add(source)
						article.teams.add(source.team)
						if source.subject:
							article.subjects.add(source.subject)
					raise ArticleExistsError(
						"There is already an article with the specified DOI. "
						"If the source, team, or subject were different, the article was updated."
					)
			# Dedup by title
			if new_article["title"] is not None:
				if Articles.objects.filter(title=new_article["title"]).exists():
					raise ArticleExistsError(
						"There is already an article with the specified Title"
					)

			try:
				save_article = Articles.objects.create(
					title=new_article["title"],
					summary=new_article["summary"],
					link=new_article["link"],
					links=merge_links(None, new_article["link"]),
					published_date=new_article["published_date"],
					doi=new_article["doi"],
					kind=kind,
					# NULL and "unknown" are the same semantic state; normalise
					# here so nothing writes NULL and drifts back out of sync
					# with the "unknown" spelling other code already folds
					# NULL into at read time.
					access=new_article["access"] or "unknown",
					publisher=new_article["publisher"],
					container_title=new_article["container_title"],
					pdf_link=new_article["pdf_link"],
				)
			except IntegrityError as exc:
				# Backstop for the race window between the pre-checks above and
				# this create(): another request inserted a matching row in
				# between. Fold it into the same ArticleExistsError flow instead
				# of surfacing a raw 500. Branch on the violated constraint:
				# a title+link race (unique_article_title_link) can fire even
				# when the payload carries a DOI, and must not be handled — or
				# reported — as a DOI conflict. The DOI path also must never
				# run with a NULL DOI: doi__iexact=None matches every DOI-less
				# article and would mass-attach the source to all of them.
				if new_article["doi"] is None or "unique_article_doi" not in str(exc):
					raise ArticleExistsError(
						"There is already an article with the specified Title"
					)
				existing = Articles.objects.filter(doi__iexact=new_article["doi"])
				for article in existing:
					article.sources.add(source)
					article.teams.add(source.team)
					if source.subject:
						article.subjects.add(source.subject)
				raise ArticleExistsError(
					"There is already an article with the specified DOI. "
					"If the source, team, or subject were different, the article was updated."
				)
			save_article.sources.add(source)
			save_article.teams.add(source.team)
			if source.subject:
				save_article.subjects.add(source.subject)
			if save_article.pk is None:
				raise ArticleNotSavedError("Could not create the article")

			log_data = {"article_id": save_article.pk}
			generateAccessSchemeLog(
				call_type, ip_addr, access_scheme, 201, "Article created", log_data
			)
			return returnData(
				{
					"name": "Gregory | API",
					"version": "0.1b",
					"data_received": post_data,
					"data_processed_from_doi": new_article,
					"article_id": save_article.article_id,
				}
			)

		# =================================================================
		# Branch: trials
		# =================================================================
		elif kind == "trials":
			trial_data = ClinicalTrial(
				title=_val("title"),
				summary=_val("summary"),
				link=_val("link"),
				published_date=_val("published_date"),
				identifiers=post_data.get("identifiers") or {},
			)

			# Dedup: identifiers first (via helper), then title (mirrors feedreader_trials logic)
			existing_trial = find_trial_by_identifier(
				trial_data.identifiers or {}
			).first()
			if existing_trial is None and trial_data.title:
				existing_trial = Trials.objects.filter(
					title__iexact=trial_data.title
				).first()

			if existing_trial:
				existing_trial.sources.add(source)
				existing_trial.teams.add(source.team)
				if source.subject:
					existing_trial.subjects.add(source.subject)
				raise ArticleExistsError(
					"There is already a trial matching the provided identifiers or title. "
					"If the source, team, or subject were different, the trial was updated."
				)

			save_trial = Trials.objects.create(
				discovery_date=tz_now(),
				title=trial_data.title,
				summary=trial_data.summary,
				link=trial_data.link,
				links=merge_links(None, trial_data.link),
				published_date=trial_data.published_date,
				identifiers=trial_data.identifiers or {},
			)
			save_trial.sources.add(source)
			save_trial.teams.add(source.team)
			if source.subject:
				save_trial.subjects.add(source.subject)
			if save_trial.pk is None:
				raise ArticleNotSavedError("Could not create the trial")

			log_data = {"trial_id": save_trial.pk}
			generateAccessSchemeLog(
				call_type, ip_addr, access_scheme, 201, "Trial created", log_data
			)
			return returnData(
				{
					"name": "Gregory | API",
					"version": "0.1b",
					"data_received": post_data,
					"trial_id": save_trial.trial_id,
				}
			)

		# =================================================================
		# Branch: news article
		# =================================================================
		elif kind == "news article":
			new_article = {
				"title": _val("title"),
				"link": _val("link"),
				"summary": _val("summary"),
				"published_date": _val("published_date"),
				"kind": kind,
			}
			# Dedup by title
			if new_article["title"] is not None:
				if Articles.objects.filter(title=new_article["title"]).exists():
					raise ArticleExistsError(
						"There is already an article with the specified Title"
					)
			# Dedup by link
			if new_article["link"] is not None:
				if Articles.objects.filter(link=new_article["link"]).exists():
					raise ArticleExistsError(
						"There is already an article with the specified link"
					)

			# discovery_date is auto_now_add on Articles, so it is always set
			# server-side and any value passed here would be ignored anyway.
			# News articles have no CrossRef lookup, so access is always
			# "unknown" rather than NULL -- see the science-paper branch above.
			save_article = Articles.objects.create(
				title=new_article["title"],
				summary=new_article["summary"],
				link=new_article["link"],
				links=merge_links(None, new_article["link"]),
				published_date=new_article["published_date"],
				kind=kind,
				access="unknown",
			)
			save_article.sources.add(source)
			save_article.teams.add(source.team)
			if source.subject:
				save_article.subjects.add(source.subject)
			if save_article.pk is None:
				raise ArticleNotSavedError("Could not create the news article")

			log_data = {"article_id": save_article.pk}
			generateAccessSchemeLog(
				call_type, ip_addr, access_scheme, 201, "News article created", log_data
			)
			return returnData(
				{
					"name": "Gregory | API",
					"version": "0.1b",
					"data_received": post_data,
					"article_id": save_article.article_id,
				}
			)

		else:
			raise FieldNotFoundError(f"Unsupported kind '{kind}'")

	except APINoAPIKeyError as exception:
		generateAccessSchemeLog(
			call_type, ip_addr, access_scheme, 401, str(exception), str(post_data)
		)
		return returnError(NO_API_KEY, str(exception), 401)
	except APIInvalidAPIKeyError as exception:
		generateAccessSchemeLog(
			call_type, ip_addr, access_scheme, 401, str(exception), str(post_data)
		)
		return returnError(INVALID_API_KEY, str(exception), 401)
	except APIInvalidIPAddressError as exception:
		generateAccessSchemeLog(
			call_type, ip_addr, access_scheme, 401, str(exception), str(post_data)
		)
		return returnError(INVALID_IP_ADDRESS, str(exception), 401)
	except APIAccessDeniedError as exception:
		if access_scheme is not None:
			generateAccessSchemeLog(
				call_type, ip_addr, access_scheme, 403, str(exception), str(post_data)
			)
		else:
			generateAccessSchemeLog(
				call_type, ip_addr, None, 403, str(exception), str(post_data)
			)
		return returnError(ACCESS_DENIED, str(exception), 403)
	except SourceNotFoundError as exception:
		generateAccessSchemeLog(
			call_type, ip_addr, access_scheme, 404, str(exception), str(post_data)
		)
		return returnError(SOURCE_NOT_FOUND, str(exception), 404)
	except FieldNotFoundError as exception:
		generateAccessSchemeLog(
			call_type, ip_addr, access_scheme, 400, str(exception), str(post_data)
		)
		return returnError(FIELD_NOT_FOUND, str(exception), 400)
	except CrossOrgPayloadError as exception:
		generateAccessSchemeLog(
			call_type, ip_addr, access_scheme, 400, str(exception), str(post_data)
		)
		return returnError(CROSS_ORG_PAYLOAD, str(exception), 400)
	except ArticleExistsError as exception:
		generateAccessSchemeLog(
			call_type, ip_addr, access_scheme, 200, str(exception), str(post_data)
		)
		return returnError(ARTICLE_EXISTS, str(exception), 200)
	except ArticleNotSavedError as exception:
		generateAccessSchemeLog(
			call_type, ip_addr, access_scheme, 500, str(exception), str(post_data)
		)
		return returnError(ARTICLE_NOT_SAVED, str(exception), 500)
	except Exception as exception:
		logging.error(traceback.format_exc())
		generateAccessSchemeLog(
			call_type, ip_addr, access_scheme, 500, str(exception), str(post_data)
		)
		return returnError(UNEXPECTED, str(exception), 500)


###
# API Edit Article
###


@extend_schema(
	request=OpenApiTypes.OBJECT,
	responses={
		200: OpenApiTypes.OBJECT,
		400: ErrorResponseSerializer,
		401: ErrorResponseSerializer,
		403: ErrorResponseSerializer,
		404: ErrorResponseSerializer,
		409: ErrorResponseSerializer,
		500: ErrorResponseSerializer,
	},
	auth=[{"apiKeyAuth": []}],
	description=(
		"Edit editorial and metadata fields on an existing article. Lookup is "
		"by `doi` (required). Raises 404 if not found, 409 if the DOI matches "
		"multiple articles (data quality issue), and 403 if the article is not "
		"associated with the API key's organisation. Per-org fields "
		"(`takeaways`, `summary_plain_english`) are upserted into "
		"ArticleOrgContent for the key's organisation — empty string clears the "
		"field (stored as NULL). Per-article fields (`access`, `retracted`, "
		"`kind`) are written back to the Articles row directly."
	),
)
@api_view(["POST"])
def edit_article(request):
	"""
	Edit editorial and metadata fields on an existing article.

	Lookup is by ``doi`` (required).  Raises 404 if not found, 409 if the DOI
	matches multiple articles (data quality issue), and 403 if the article is
	not associated with the API key's organisation.

	Per-org fields (``takeaways``, ``summary_plain_english``) are upserted into
	``ArticleOrgContent`` for the key's organisation.  Empty string clears the
	field (stored as NULL).

	Per-article fields (``access``, ``retracted``, ``kind``) are written back to
	the ``Articles`` row directly.
	"""
	access_scheme = None
	call_type = request.method + " " + request.path
	ip_addr = getIPAddress(request)
	try:
		post_data = json.loads(request.body)
	except json.JSONDecodeError as exception:
		raw_body = request.body.decode("utf-8", errors="replace")
		generateAccessSchemeLog(
			call_type, ip_addr, None, 400, str(exception), raw_body
		)
		return returnError(INVALID_JSON, str(exception), 400)
	try:
		api_key = getAPIKey(request)
		access_scheme = checkValidAccess(api_key, ip_addr)

		if access_scheme.organization is None:
			raise APIAccessDeniedError(
				"API keys without an associated organisation cannot edit articles."
			)

		doi = post_data.get("doi")
		if not doi:
			raise FieldNotFoundError("field `doi` is required")

		# Case-insensitive: the DB constraint (unique_article_doi) enforces
		# uniqueness on Lower(doi), so an exact match here could 404 on an
		# edit whose DOI casing differs from what was stored.
		matching = Articles.objects.filter(doi__iexact=doi)
		count = matching.count()
		if count == 0:
			raise ArticleNotFoundError(f"No article found with DOI {doi}")
		if count > 1:
			raise DuplicateArticleError(
				ids=list(matching.values_list("article_id", flat=True)),
				message=f"{count} articles match DOI {doi}. Resolve duplicates before editing.",
			)
		article = matching.first()

		# Cross-org check: at least one of the article's teams must belong to the key's org
		if not article.teams.filter(organization=access_scheme.organization).exists():
			raise CrossOrgPayloadError(
				"this article is not visible to your organisation."
			)

		updated_fields = []

		# --- Per-article fields -------------------------------------------
		VALID_ACCESS = [v for v, _ in Articles.ACCESS_OPTIONS]
		VALID_KINDS = [v for v, _ in Articles.KINDS]

		if "access" in post_data:
			val = post_data["access"]
			if val not in VALID_ACCESS:
				raise FieldNotFoundError(f"`access` must be one of {VALID_ACCESS}")
			article.access = val
			updated_fields.append("access")

		if "retracted" in post_data:
			val = post_data["retracted"]
			if not isinstance(val, bool):
				raise FieldNotFoundError("`retracted` must be a boolean")
			article.retracted = val
			updated_fields.append("retracted")

		if "kind" in post_data:
			val = post_data["kind"]
			if val not in VALID_KINDS:
				raise FieldNotFoundError(f"`kind` must be one of {VALID_KINDS}")
			article.kind = val
			updated_fields.append("kind")

		article_fields_changed = [
			f for f in updated_fields if f in ("access", "retracted", "kind")
		]
		if article_fields_changed:
			article.save(update_fields=article_fields_changed)

		# --- Per-org fields -----------------------------------------------
		org_fields = {}
		for field in ("takeaways", "summary_plain_english"):
			if field in post_data:
				val = post_data[field]
				org_fields[field] = None if val == "" else val

		if org_fields:
			org_content, _ = ArticleOrgContent.objects.get_or_create(
				article=article,
				organization=access_scheme.organization,
			)
			for field, val in org_fields.items():
				setattr(org_content, field, val)
				updated_fields.append(field)
			org_content.save()

		generateAccessSchemeLog(
			call_type,
			ip_addr,
			access_scheme,
			200,
			"Article edited",
			{"article_id": article.article_id},
		)
		return returnData(
			{
				"article_id": article.article_id,
				"doi": article.doi,
				"organization_id": access_scheme.organization_id,
				"updated_fields": updated_fields,
			}
		)

	except APINoAPIKeyError as exception:
		generateAccessSchemeLog(
			call_type, ip_addr, access_scheme, 401, str(exception), str(post_data)
		)
		return returnError(NO_API_KEY, str(exception), 401)
	except APIInvalidAPIKeyError as exception:
		generateAccessSchemeLog(
			call_type, ip_addr, access_scheme, 401, str(exception), str(post_data)
		)
		return returnError(INVALID_API_KEY, str(exception), 401)
	except APIInvalidIPAddressError as exception:
		generateAccessSchemeLog(
			call_type, ip_addr, access_scheme, 401, str(exception), str(post_data)
		)
		return returnError(INVALID_IP_ADDRESS, str(exception), 401)
	except APIAccessDeniedError as exception:
		if access_scheme is not None:
			generateAccessSchemeLog(
				call_type, ip_addr, access_scheme, 403, str(exception), str(post_data)
			)
		else:
			generateAccessSchemeLog(
				call_type, ip_addr, None, 403, str(exception), str(post_data)
			)
		return returnError(ACCESS_DENIED, str(exception), 403)
	except ArticleNotFoundError as exception:
		generateAccessSchemeLog(
			call_type, ip_addr, access_scheme, 404, str(exception), str(post_data)
		)
		return returnError(ARTICLE_NOT_FOUND, str(exception), 404)
	except DuplicateArticleError as exception:
		generateAccessSchemeLog(
			call_type, ip_addr, access_scheme, 409, str(exception), str(post_data)
		)
		return returnError(
			DUPLICATE_ARTICLE,
			{"message": str(exception), "article_ids": exception.ids},
			409,
		)
	except FieldNotFoundError as exception:
		generateAccessSchemeLog(
			call_type, ip_addr, access_scheme, 400, str(exception), str(post_data)
		)
		return returnError(FIELD_NOT_FOUND, str(exception), 400)
	except CrossOrgPayloadError as exception:
		generateAccessSchemeLog(
			call_type, ip_addr, access_scheme, 403, str(exception), str(post_data)
		)
		return returnError(CROSS_ORG_PAYLOAD, str(exception), 403)
	except Exception as exception:
		logging.error(traceback.format_exc())
		generateAccessSchemeLog(
			call_type, ip_addr, access_scheme, 500, str(exception), str(post_data)
		)
		return returnError(UNEXPECTED, str(exception), 500)


###
# API Edit Trial
###


@extend_schema(
	request=OpenApiTypes.OBJECT,
	responses={
		200: OpenApiTypes.OBJECT,
		400: ErrorResponseSerializer,
		401: ErrorResponseSerializer,
		403: ErrorResponseSerializer,
		404: ErrorResponseSerializer,
		409: ErrorResponseSerializer,
		500: ErrorResponseSerializer,
	},
	auth=[{"apiKeyAuth": []}],
	description=(
		"Edit per-organisation editorial fields on an existing trial. Lookup "
		"is by `identifiers` dict (required, same format as post_article's "
		"trial branch: keys `nct`, `euct`, `eudract`). Raises 404 if not "
		"found, 409 if multiple trials match (dedup issue). Only per-org "
		"fields (`takeaways`, `summary_plain_english`) are editable — trial "
		"metadata comes from registries and is not client-editable."
	),
)
@api_view(["POST"])
def edit_trial(request):
	"""
	Edit per-organisation editorial fields on an existing trial.

	Lookup is by ``identifiers`` dict (required, same format as ``post_article``
	trial branch: keys ``nct``, ``euct``, ``eudract``).  Raises 404 if not
	found, 409 if multiple trials match (dedup issue).

	Only per-org fields (``takeaways``, ``summary_plain_english``) are
	editable — trial metadata comes from registries and is not client-editable.
	"""
	access_scheme = None
	call_type = request.method + " " + request.path
	ip_addr = getIPAddress(request)
	try:
		post_data = json.loads(request.body)
	except json.JSONDecodeError as exception:
		raw_body = request.body.decode("utf-8", errors="replace")
		generateAccessSchemeLog(
			call_type, ip_addr, None, 400, str(exception), raw_body
		)
		return returnError(INVALID_JSON, str(exception), 400)
	try:
		api_key = getAPIKey(request)
		access_scheme = checkValidAccess(api_key, ip_addr)

		if access_scheme.organization is None:
			raise APIAccessDeniedError(
				"API keys without an associated organisation cannot edit trials."
			)

		identifiers = post_data.get("identifiers")
		if not identifiers:
			raise FieldNotFoundError(
				'field `identifiers` is required (e.g. {"nct": "NCT..."} or {"euct": "..."})'
			)

		matching = find_trial_by_identifier(identifiers)
		count = matching.count()
		if count == 0:
			raise TrialNotFoundError(
				"No trial found matching the provided identifiers."
			)
		if count > 1:
			raise DuplicateTrialError(
				ids=list(matching.values_list("trial_id", flat=True)),
				message=f"{count} trials match the provided identifiers. Resolve duplicates before editing.",
			)
		trial = matching.first()

		# Cross-org check
		if not trial.teams.filter(organization=access_scheme.organization).exists():
			raise CrossOrgPayloadError(
				"this trial is not visible to your organisation."
			)

		updated_fields = []

		# --- Per-org fields -----------------------------------------------
		org_fields = {}
		for field in ("takeaways", "summary_plain_english"):
			if field in post_data:
				val = post_data[field]
				org_fields[field] = None if val == "" else val

		if org_fields:
			org_content, _ = TrialOrgContent.objects.get_or_create(
				trial=trial,
				organization=access_scheme.organization,
			)
			for field, val in org_fields.items():
				setattr(org_content, field, val)
				updated_fields.append(field)
			org_content.save()

		generateAccessSchemeLog(
			call_type,
			ip_addr,
			access_scheme,
			200,
			"Trial edited",
			{"trial_id": trial.trial_id},
		)
		return returnData(
			{
				"trial_id": trial.trial_id,
				"organization_id": access_scheme.organization_id,
				"updated_fields": updated_fields,
			}
		)

	except APINoAPIKeyError as exception:
		generateAccessSchemeLog(
			call_type, ip_addr, access_scheme, 401, str(exception), str(post_data)
		)
		return returnError(NO_API_KEY, str(exception), 401)
	except APIInvalidAPIKeyError as exception:
		generateAccessSchemeLog(
			call_type, ip_addr, access_scheme, 401, str(exception), str(post_data)
		)
		return returnError(INVALID_API_KEY, str(exception), 401)
	except APIInvalidIPAddressError as exception:
		generateAccessSchemeLog(
			call_type, ip_addr, access_scheme, 401, str(exception), str(post_data)
		)
		return returnError(INVALID_IP_ADDRESS, str(exception), 401)
	except APIAccessDeniedError as exception:
		if access_scheme is not None:
			generateAccessSchemeLog(
				call_type, ip_addr, access_scheme, 403, str(exception), str(post_data)
			)
		else:
			generateAccessSchemeLog(
				call_type, ip_addr, None, 403, str(exception), str(post_data)
			)
		return returnError(ACCESS_DENIED, str(exception), 403)
	except TrialNotFoundError as exception:
		generateAccessSchemeLog(
			call_type, ip_addr, access_scheme, 404, str(exception), str(post_data)
		)
		return returnError(TRIAL_NOT_FOUND, str(exception), 404)
	except DuplicateTrialError as exception:
		generateAccessSchemeLog(
			call_type, ip_addr, access_scheme, 409, str(exception), str(post_data)
		)
		return returnError(
			DUPLICATE_TRIAL,
			{"message": str(exception), "trial_ids": exception.ids},
			409,
		)
	except FieldNotFoundError as exception:
		generateAccessSchemeLog(
			call_type, ip_addr, access_scheme, 400, str(exception), str(post_data)
		)
		return returnError(FIELD_NOT_FOUND, str(exception), 400)
	except CrossOrgPayloadError as exception:
		generateAccessSchemeLog(
			call_type, ip_addr, access_scheme, 403, str(exception), str(post_data)
		)
		return returnError(CROSS_ORG_PAYLOAD, str(exception), 403)
	except Exception as exception:
		logging.error(traceback.format_exc())
		generateAccessSchemeLog(
			call_type, ip_addr, access_scheme, 500, str(exception), str(post_data)
		)
		return returnError(UNEXPECTED, str(exception), 500)


###
# ARTICLES
###
_OPTIONAL_API_KEY_SECURITY = [
	{},
	{"basicAuth": []},
	{"jwtAuth": []},
	{"cookieAuth": []},
	{"apiKeyAuth": []},
]


def _ordering_param(fields, description):
	"""Build an explicit ``ordering`` OpenApiParameter with a real enum.

	drf-spectacular auto-detects ``ordering`` from a view's DRF
	``OrderingFilter``/``ordering_fields``, but only ever emits it as a bare
	``string`` — never an enum. That leaves every accepted value (and, more
	importantly, every *rejected* one) undocumented: DRF's ``OrderingFilter``
	silently ignores an unrecognised value rather than rejecting it, so a
	typo produces a plausible-looking, wrongly-sorted response with no error.

	Modelled as a comma-separated array of an enum (matching how this file
	already models ``subjects``/``subjects_any`` — ``style: form,
	explode: false``), not a plain string enum: ``OrderingFilter`` genuinely
	accepts multiple comma-separated fields as a tiebreaker
	(``?ordering=recruiting_first,-discovery_date``), and a string enum would
	incorrectly mark that valid combined value as not one of the allowed
	single values. Passed as a manual parameter, this overrides the
	auto-detected one — drf-spectacular matches by name+location and prefers
	the manual entry.
	"""
	values = []
	for field in fields:
		values.append(field)
		values.append(f"-{field}")
	# Two distinct fields so the example actually demonstrates a tiebreaker
	# (sorting by the same field twice doesn't); fall back to one if that's
	# all this endpoint has.
	example = (
		f"?ordering={fields[0]},-{fields[1]}" if len(fields) > 1 else f"?ordering={values[0]}"
	)
	return OpenApiParameter(
		"ordering",
		{"type": "array", "items": {"type": "string", "enum": values}},
		OpenApiParameter.QUERY,
		style="form",
		explode=False,
		description=description
		+ " Accepts a comma-separated combination of the values above to break "
		f"ties on a second field, e.g. {example}.",
	)


_ARTICLES_ORDERING_PARAM = _ordering_param(
	["discovery_date", "published_date", "title", "article_id", "ml_score"],
	"Sort field, prefix with `-` for descending. Articles without an ml_score "
	"always sort last when ordering by ml_score, in both directions.",
)


@extend_schema_view(
	list=extend_schema(auth=_OPTIONAL_API_KEY_SECURITY, parameters=[_ARTICLES_ORDERING_PARAM]),
	retrieve=extend_schema(auth=_OPTIONAL_API_KEY_SECURITY),
)
class ArticleViewSet(
	BulkExportThrottleMixin,
	CSVStreamingMixin,
	OrgVisibilityMixin,
	CachedStatsActionMixin,
	viewsets.ReadOnlyModelViewSet,
):
	"""
	List all articles in the database with comprehensive filtering options.
	CSV responses are automatically streamed for better performance with large datasets.

	# Query Parameters:
	- **team_id** - filter by team ID
	- **site_id** - filter by Django Site ID; returns articles belonging to any team attached to that site (see Team.site)
	- **doi** - filter by DOI, case-insensitive; accepts a single value or a comma-separated list (e.g. `?doi=10.1/a,10.2/b`)
	- **subject_id** - filter by subject ID (used with team_id)
	- **subjects** - comma-separated list of subject IDs with AND semantics — returns only articles tagged with *all* listed subjects (e.g., `?subjects=1,2`)
	- **subjects_any** - comma-separated list of subject IDs with OR semantics — returns articles tagged with *any* of the listed subjects (e.g., `?subjects_any=1,2`)
	- **author_id** - filter by author ID
	- **category_slug** - filter by category slug
	- **category_id** - filter by category ID
	- **journal_slug** - filter by journal (convert spaces to dashes)
	- **source_id** - filter by source ID
	- **category_modality** - filter by the intervention modality of the article's categories; one of the `CategoryModality` values
	- **has_clinical_trials** - filter for articles linked to one or more clinical trials (true/false)
	- **search** - search in title and summary (supports boolean operators, e.g. `a OR b`)
	- **title** - search only in the title field (case-insensitive substring)
	- **summary** - search only in the summary/abstract field (case-insensitive substring)
	- **ordering** - sort field, prefix with `-` for descending. Allowed values: `discovery_date`, `published_date`, `title`, `article_id`, `ml_score`. Articles without a score always appear last when ordering by `ml_score`.
	- **page** - page number for pagination
	- **page_size** - items per page (max 100)
	- **all_results** - set to 'true' to bypass pagination and get all results (useful for CSV export)

	# Special Article Types:
	- **relevant** - filter for relevant articles (true/false). When combined with **subject_id**, relevance is scoped to that specific subject — only articles that are relevant *for that subject* (via ML predictions or manual marking) are returned. Without subject_id, relevance is checked across all subjects.
	- **ml_threshold** - minimum ML prediction confidence (float 0.0-1.0, e.g., 0.75). Also scoped to subject_id when provided.
	- **open_access** - filter for open access articles (true/false)
	- **last_days** - filter for articles from last N days (number)
	- **week** - filter for specific week number (requires year parameter)
	- **year** - year for week filtering (used with week parameter)

	# Date Range Parameters:
	Filter by publication date. Both parameters are optional and can be used independently or together.
	Invalid or non-ISO-8601 dates return **400 Bad Request**.
	- **published_date_after** - articles published on or after this date (YYYY-MM-DD, inclusive)
	- **published_date_before** - articles published on or before this date (YYYY-MM-DD, inclusive — the full day is included)

	# Stats Endpoint:
	`GET /articles/stats/` returns aggregate counts over the filtered queryset: `total`
	(distinct articles), `by_access` (`{open, restricted, unknown}` — articles without an
	access value are folded into `unknown`), `relevant`, `retracted`, `missing_doi`, and a
	`by_subject` breakdown (`[{subject_id, subject_name, count}]`, distinct per article,
	restricted to subjects visible to the caller). It accepts the same query parameters as
	the list endpoint, so e.g. `/articles/stats/?team_id=1&relevant=true` scopes the counts
	exactly like the equivalent list request. The `relevant` count uses the same semantics
	as the list's `?relevant=true` filter: when `subject_id` is present it counts articles
	relevant *for that subject* (manual review or ML consensus, honoring `ml_threshold`),
	not articles merely relevant for some other subject. Results are cached server-side for
	STATS_CACHE_TTL seconds (default 600).

	# Response Fields:
	Each article includes a **ml_score** field: the average ML probability score across the most recent
	prediction per (algorithm, subject) pair. `null` when no predictions exist yet.

	# Examples:
	- By DOI (single): `/articles/?doi=10.1016/j.procs.2023.01.401`
	- By DOI (multiple): `/articles/?doi=10.1016/j.procs.2023.01.401,10.1016/j.other.2024.02.001`
	- Team articles: `/articles/?team_id=1`
	- Team + subject: `/articles/?team_id=1&subject_id=4`
	- Multi-subject AND: `/articles/?subjects=1,2`
	- Multi-subject OR: `/articles/?subjects_any=1,2`
	- With search: `/articles/?team_id=1&search=stem+cells`
	- Category by slug: `/articles/?team_id=1&category_slug=natalizumab`
	- Category by ID: `/articles/?team_id=1&category_id=5`
	- Relevant articles: `/articles/?relevant=true`
	- Relevant with ML threshold: `/articles/?relevant=true&ml_threshold=0.75`
	- Relevant from last 15 days: `/articles/?relevant=true&last_days=15`
	- Relevant from specific week: `/articles/?relevant=true&week=52&year=2024`
	- Open access articles: `/articles/?open_access=true`
	- Published in 2023: `/articles/?published_date_after=2023-01-01&published_date_before=2023-12-31`
	- Published since a date: `/articles/?published_date_after=2024-01-01`
	- Date range + subject + CSV: `/articles/?team_id=1&subjects=1,3&published_date_after=2022-06-01&published_date_before=2023-12-31&format=csv&all_results=true`
	- CSV export all results: `/articles/?format=csv&all_results=true`
	- Sort by AI relevance: `/articles/?ordering=-ml_score`
	- Relevant + AI relevance sort: `/articles/?relevant=true&ordering=-ml_score`
	- Complex filter: `/articles/?team_id=1&subject_id=4&author_id=123&search=regeneration&relevant=true&ml_threshold=0.8&ordering=-ml_score`
	"""

	queryset = Articles.objects.all().prefetch_related(
		Prefetch(
			"ml_predictions_detail",
			queryset=_latest_ml_predictions_queryset(),
		),
		"authors",
		"teams",
		Prefetch("subjects", queryset=Subject.objects.select_related("team")),
		"sources",
		"team_categories",
		Prefetch(
			"article_subject_relevances",
			queryset=ArticleSubjectRelevance.objects.select_related("subject__team"),
		),
		Prefetch(
			"trial_references",
			queryset=ArticleTrialReference.objects.select_related("trial"),
		),
	).order_by("-discovery_date")
	serializer_class = ArticleSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]
	pagination_class = FlexiblePagination
	# NOTE: `search` is handled solely by ArticleFilter.filter_search (boolean
	# parser). DRF's SearchFilter is intentionally absent — it also binds to
	# ?search= and would AND its naive token match on top of the parsed Q,
	# turning a query like `a OR b` into a literal "must contain OR" and
	# wiping the results. See api/utils/search.build_search_q.
	filter_backends = [
		django_filters.DjangoFilterBackend,
		NullsLastOrderingFilter,
	]
	filterset_class = ArticleFilter
	ordering_fields = ["discovery_date", "published_date", "title", "article_id", "ml_score"]
	ordering = ["-discovery_date"]

	def get_queryset(self):
		"""Prefetch the caller-org's ArticleOrgContent to avoid N+1 on list responses.

		When a request resolves to an organisation (API key or public-org
		filter), attach the matching ``ArticleOrgContent`` rows as
		``_prefetched_org_contents`` so the serializer can resolve per-org
		fields without issuing one query per article.
		"""
		qs = super().get_queryset()
		org = _resolve_per_org_fields_org(self.request)
		if org is not None:
			qs = qs.prefetch_related(
				Prefetch(
					"org_contents",
					queryset=ArticleOrgContent.objects.filter(organization=org),
					to_attr="_prefetched_org_contents",
				)
			)
		return qs

	stats_cache_prefix = "articles_stats"

	@extend_schema(responses=ArticlesStatsSerializer, auth=_OPTIONAL_API_KEY_SECURITY)
	@action(detail=False, methods=["get"], url_path="stats")
	def stats(self, request):
		"""Aggregate counts over the filtered queryset.

		Accepts the same query parameters as the list endpoint (team_id,
		subject_id, relevant, search, …) and the same org-visibility
		scoping, so the counts always match what the equivalent list
		request would return. Cached server-side; see
		``CachedStatsActionMixin``.
		"""
		return self._stats_response(request)

	def build_stats_payload(self, filtered_qs):
		# The list queryset carries heavy prefetches for the serializer;
		# aggregate queries (.values()/.aggregate()) never execute
		# prefetch_related, so none of that cost is paid here. Clear
		# ordering to prevent GROUP BY pollution; distinct counts guard
		# against join row-duplication (e.g. an article in two teams).
		qs = filtered_qs.order_by()

		access_counts = {
			row["access"]: row["count"]
			for row in qs.values("access").annotate(
				count=Count("article_id", distinct=True)
			)
		}
		# Fold NULL (never checked) and any non-canonical value into
		# "unknown" — consumers shouldn't see the internal NULL/'unknown'
		# split.
		by_access = {
			"open": access_counts.get("open", 0),
			"restricted": access_counts.get("restricted", 0),
			"unknown": sum(
				count
				for key, count in access_counts.items()
				if key not in ("open", "restricted")
			),
		}

		flags = qs.aggregate(
			retracted=Count(
				"article_id", distinct=True, filter=Q(retracted=True)
			),
			missing_doi=Count(
				"article_id",
				distinct=True,
				filter=Q(doi__isnull=True) | Q(doi=""),
			),
		)

		# ``relevant`` must mean exactly what ``?relevant=true`` means on
		# the list endpoint — scoped to subject_id and honoring ml_threshold
		# when those params are present. The denormalized Articles.relevant
		# flag is "relevant for ANY subject", which over-counts
		# subject-scoped requests: an article in subject N that is only
		# relevant for subject M would still land in N's bucket. Reuse the
		# filter's live logic instead of duplicating it here.
		article_filter = ArticleFilter(
			self.request.GET, queryset=qs, request=self.request
		)
		relevant_count = (
			article_filter.filter_relevant(qs, "relevant", True)
			.order_by()
			.values("article_id")
			.distinct()
			.count()
		)

		return {
			# access groups are disjoint per article, so their distinct
			# counts sum to the distinct total without a separate query.
			"total": sum(access_counts.values()),
			"by_access": by_access,
			"relevant": relevant_count,
			"retracted": flags["retracted"],
			"missing_doi": flags["missing_doi"],
			"by_subject": self._by_subject_counts(filtered_qs),
		}


###
# CATEGORIES
###


def _category_through_count_subquery(through_model):
	"""Correlated scalar count of a category's rows in a single M2M through
	table (article or trial category assignments).

	Annotating a category with Count("articles", distinct=True) AND
	Count("trials", distinct=True) in the same query joins both M2M
	relations at once, producing an articles x trials cross product per
	category before DISTINCT dedups it — for a category with thousands of
	rows on each side that intermediate result is large enough to spill
	Postgres's hash aggregate to disk. A correlated subquery per relation
	counts each through table independently (no cross join), and because
	each through table already has a unique_together on
	(article/trial, teamcategory), a plain COUNT(*) here already equals the
	distinct count.
	"""
	return Coalesce(
		Subquery(
			through_model.objects.filter(teamcategory=OuterRef("pk"))
			.order_by()
			.values("teamcategory")
			.annotate(c=Count("*"))
			.values("c"),
			output_field=IntegerField(),
		),
		0,
	)


def _category_authors_count_subquery():
	"""Correlated scalar count of the distinct authors behind a category's
	articles.

	Unlike the two through-table counts above, this one walks
	articles_team_categories -> articles_authors, so the DISTINCT is real
	work rather than a formality (a large category's articles can carry tens
	of thousands of author rows). It is therefore NOT part of the default
	/categories/ queryset — see CategoryViewSet.get_queryset, which adds it
	only when the client explicitly sorts by it.
	"""
	return Coalesce(
		Subquery(
			ArticleCategoryAssignment.objects.filter(teamcategory=OuterRef("pk"))
			.order_by()
			.values("teamcategory")
			.annotate(c=Count("articles__authors", distinct=True))
			.values("c"),
			output_field=IntegerField(),
		),
		0,
	)


_CATEGORIES_LIST_PARAMS = [
	OpenApiParameter(
		"get_categories",
		OpenApiTypes.STR,
		OpenApiParameter.QUERY,
		description="Comma-separated list of category IDs, e.g. 1,2,3. Fetches exactly "
		"those categories, combined with any other filters given.",
	),
	OpenApiParameter(
		"include_authors",
		OpenApiTypes.BOOL,
		OpenApiParameter.QUERY,
		description="Include each category's top_authors list (default: true).",
	),
	OpenApiParameter(
		"max_authors",
		OpenApiTypes.INT,
		OpenApiParameter.QUERY,
		description="Maximum top authors to return per category when include_authors=true "
		"(default: 10, max: 50).",
	),
	OpenApiParameter(
		"monthly_counts",
		OpenApiTypes.BOOL,
		OpenApiParameter.QUERY,
		description="Include monthly article/trial counts, including ML-relevant article "
		"counts, per category (default: false).",
	),
	OpenApiParameter(
		"ml_threshold",
		OpenApiTypes.NUMBER,
		OpenApiParameter.QUERY,
		description="ML prediction probability threshold for monthly_counts's relevant-article "
		"count (0.0-1.0, default: 0.5). No effect unless monthly_counts=true.",
	),
	OpenApiParameter(
		"date_from",
		OpenApiTypes.DATE,
		OpenApiParameter.QUERY,
		description="Restrict monthly_counts / top-authors article counts to articles "
		"published on or after this date (YYYY-MM-DD).",
	),
	OpenApiParameter(
		"date_to",
		OpenApiTypes.DATE,
		OpenApiParameter.QUERY,
		description="Restrict monthly_counts / top-authors article counts to articles "
		"published on or before this date (YYYY-MM-DD).",
	),
	OpenApiParameter(
		"timeframe",
		OpenApiTypes.STR,
		OpenApiParameter.QUERY,
		enum=["year", "month", "week"],
		description="Shortcut for date_from relative to now (overridden by an explicit "
		"date_from); has no effect on date_to.",
	),
]

_CATEGORIES_ORDERING_PARAM = _ordering_param(
	["category_name", "id", "article_count_annotated", "trials_count_annotated", "authors_count_annotated"],
	"Sort field, prefix with `-` for descending. authors_count_annotated is the expensive "
	"one — ordering happens before pagination, so it counts distinct authors for every "
	"category matching the filters, not just the page returned.",
)

_CATEGORY_AUTHORS_ACTION_PARAMS = [
	OpenApiParameter(
		"min_articles",
		OpenApiTypes.INT,
		OpenApiParameter.QUERY,
		description="Minimum articles this author must have in the category to be included "
		"(default: 1).",
	),
	OpenApiParameter(
		"sort_by",
		OpenApiTypes.STR,
		OpenApiParameter.QUERY,
		enum=["articles_count", "author_name"],
		description="Sort field (default: articles_count).",
	),
	OpenApiParameter(
		"order",
		OpenApiTypes.STR,
		OpenApiParameter.QUERY,
		enum=["asc", "desc"],
		description="Sort direction (default: desc).",
	),
	OpenApiParameter(
		"date_from",
		OpenApiTypes.DATE,
		OpenApiParameter.QUERY,
		description="Restrict each author's article count to articles published on or "
		"after this date (YYYY-MM-DD).",
	),
	OpenApiParameter(
		"date_to",
		OpenApiTypes.DATE,
		OpenApiParameter.QUERY,
		description="Restrict each author's article count to articles published on or "
		"before this date (YYYY-MM-DD).",
	),
	OpenApiParameter(
		"timeframe",
		OpenApiTypes.STR,
		OpenApiParameter.QUERY,
		enum=["year", "month", "week"],
		description="Shortcut for date_from relative to now (overridden by an explicit "
		"date_from); has no effect on date_to.",
	),
]


@extend_schema_view(
	list=extend_schema(parameters=_CATEGORIES_LIST_PARAMS + [_CATEGORIES_ORDERING_PARAM]),
	authors=extend_schema(parameters=_CATEGORY_AUTHORS_ACTION_PARAMS),
)
class CategoryViewSet(viewsets.ReadOnlyModelViewSet):
	"""
	List all categories in the database with optional filters for team and subject.
	Now includes author statistics for each category.

	# Query Parameters:
	- **team_id** - filter by team ID
	- **subject_id** - filter by subject ID
	- **category_id** - filter by specific category ID
	- **get_categories** - comma-separated list of category IDs (e.g., 1,2,3)
	- **include_authors** - Include top authors data (default: true)
	- **max_authors** - Maximum number of top authors to return per category (default: 10, max: 50)
	- **date_from** - Filter articles from this date (YYYY-MM-DD)
	- **date_to** - Filter articles to this date (YYYY-MM-DD)
	- **timeframe** - 'year', 'month', 'week' (relative to current date)
	- **monthly_counts** - Include monthly article/trial counts with ML predictions (default: false)
	- **ml_threshold** - ML prediction probability threshold when monthly_counts=true (0.0-1.0, default: 0.5)
	- **search** - search in category name and description
	- **ordering** - sort field, prefix with `-` for descending. Allowed values: `category_name`, `id`, `article_count_annotated`, `trials_count_annotated`, `authors_count_annotated`.
	  The first four sort on values the queryset already computes, so they add
	  nothing. `authors_count_annotated` is the expensive one: ordering has to
	  happen before pagination, so it counts distinct authors for every category
	  matching the filters rather than just the page being returned. It is
	  therefore annotated only for requests that sort by it — note this changes
	  *where* the count comes from, not whether it is computed, since the
	  serializer otherwise derives `authors_count` per row on its own.

	# Response includes:
	- Category basic information
	- Total article and trial counts
	- Authors count (unique authors in category)
	- Top authors with their article counts in this category
	- Monthly counts (when monthly_counts=true), including monthly_relevant_article_counts:
	  articles whose latest prediction from at least one ML model meets ml_threshold,
	  counted once per month regardless of how many models flagged them

	# Additional Actions:
	- `/categories/{id}/authors/` - Get detailed author statistics for a specific category

	# Examples:
	- Basic: `GET /categories/?team_id=1`
	- With subject: `GET /categories/?team_id=1&subject_id=2`
	- Date filtered: `GET /categories/?team_id=1&timeframe=year`
	- More authors: `GET /categories/?team_id=1&max_authors=20`
	- Without authors: `GET /categories/?team_id=1&include_authors=false`
	- Monthly counts: `GET /categories/?category_id=6&monthly_counts=true&ml_threshold=0.8`
	- Single category with monthly counts: `GET /categories/?category_id=6&monthly_counts=true`
	- Multiple categories by ID: `GET /categories/?get_categories=1,2,3`
	"""

	serializer_class = CategorySerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]
	filter_backends = [
		django_filters.DjangoFilterBackend,
		filters.SearchFilter,
		filters.OrderingFilter,
	]
	filterset_class = CategoryFilter
	search_fields = ["category_name", "category_description"]
	ordering_fields = [
		"category_name",
		"id",
		"article_count_annotated",
		"trials_count_annotated",
		"authors_count_annotated",
	]
	ordering = ["category_name"]

	def get_queryset(self):
		"""
		Article/trial counts are computed with correlated subquery
		annotations (see below) rather than prefetching the full related
		querysets, so a page of categories doesn't materialise every
		article/trial row just to produce two integers.
		"""
		queryset = TeamCategory.objects.all()

		# --- Org visibility: only categories whose team's org is visible ---
		if hasattr(self.request, "visible_org_ids"):
			queryset = queryset.filter(
				team__organization_id__in=self.request.visible_org_ids
			)

		# Apply filters without expensive annotations
		team_id = self.request.query_params.get("team_id")
		subject_id = self.request.query_params.get("subject_id")
		category_id = self.request.query_params.get("category_id")
		get_categories = self.request.query_params.get("get_categories")

		if team_id:
			queryset = queryset.filter(team_id=team_id)

		if subject_id:
			queryset = queryset.filter(subjects__id=subject_id)

		if category_id:
			queryset = queryset.filter(id=category_id)

		# Support fetching multiple categories by ID: get_categories=1,2,3
		if get_categories:
			ids = []
			for part in get_categories.split(","):
				try:
					ids.append(int(part.strip()))
				except (ValueError, TypeError):
					continue
			if len(ids) > 0:
				queryset = queryset.filter(id__in=ids)

		# Counts are computed via correlated subqueries rather than
		# prefetching every article/trial row for the category: a large
		# category (e.g. ~7,300 articles) would otherwise materialise
		# thousands of rows just to produce two integers. See the identical
		# pattern in CategoriesByTeamAndSubject.get_queryset. Do NOT switch
		# this back to annotating both Count("articles", distinct=True) and
		# Count("trials", distinct=True) in one query — joining both M2M
		# relations at once fans out to an articles x trials cross product
		# per category, which spilled Postgres's hash aggregate to disk in
		# production (see _category_through_count_subquery docstring).
		queryset = queryset.select_related("team").prefetch_related("subjects").annotate(
			article_count_annotated=_category_through_count_subquery(ArticleCategoryAssignment),
			trials_count_annotated=_category_through_count_subquery(TrialCategoryAssignment),
		)

		# `authors_count_annotated` is an allowed ordering value but is NOT
		# annotated by default. Leaving it out unconditionally would be a bug
		# on its own — DRF's OrderingFilter hands the unresolvable name to
		# order_by(), which is a 500 (FieldError) rather than the graceful
		# fallback an unknown name gets, precisely because this one passes the
		# ordering_fields whitelist.
		#
		# The count itself is not avoided by skipping the annotation: the
		# serializer derives authors_count per row when it is absent (see
		# CategorySerializer.get_authors_count), so a default list response
		# still runs one such query per category on the page. What the
		# annotation changes is the scope — ordering is applied before
		# pagination, so ranking by this value forces the distinct-author
		# count for every category matching the filters, not just the page.
		# Annotating on demand keeps that whole-result-set cost on the
		# requests that asked to be sorted by it. (Those requests get the
		# serializer's per-row queries for free in exchange, since the
		# annotation is then present.)
		#
		# Deliberately not date-filtered: because the serializer prefers this
		# annotation over its own fallback, filtering it here would make a
		# category's reported authors_count change depending on whether the
		# client happened to sort by it.
		if self._orders_by_authors_count():
			queryset = queryset.annotate(
				authors_count_annotated=_category_authors_count_subquery()
			)

		# No .distinct() needed: the count annotations are now correlated
		# subqueries (no JOIN), team is select_related via FK, subjects is
		# prefetch_related (a separate query), and subjects__id=<single id>
		# filters the default M2M through table, which has a unique
		# constraint on (teamcategory, subject) — none of these can produce
		# duplicate category rows. Adding .distinct() back would force the
		# paginator's count() down a DISTINCT-over-all-columns path,
		# undermining the cheap-count fix above.
		return queryset

	def _orders_by_authors_count(self):
		"""True when this request's `ordering` sorts by authors count.

		Reads the param the same way DRF's OrderingFilter does: one
		comma-separated value, each term optionally prefixed with `-`.
		"""
		raw = self.request.query_params.get(
			filters.OrderingFilter.ordering_param, ""
		)
		return any(
			term.strip().lstrip("-") == "authors_count_annotated"
			for term in raw.split(",")
		)

	def get_serializer_context(self):
		"""Add author parameters to serializer context"""
		context = super().get_serializer_context()

		# Get query parameters for author data
		include_authors = (
			self.request.query_params.get("include_authors", "true").lower() == "true"
		)
		try:
			max_authors = min(int(self.request.query_params.get("max_authors", 10)), 50)
		except (ValueError, TypeError):
			max_authors = 10

		# Get monthly counts parameter
		monthly_counts = (
			self.request.query_params.get("monthly_counts", "false").lower() == "true"
		)
		ml_threshold = self.request.query_params.get("ml_threshold", 0.5)
		try:
			ml_threshold = float(ml_threshold)
		except (ValueError, TypeError):
			ml_threshold = 0.5

		date_filters = self._build_date_filters(
			self.request.query_params.get("date_from"),
			self.request.query_params.get("date_to"),
			self.request.query_params.get("timeframe"),
		)

		context["author_params"] = {
			"include_authors": include_authors,
			"max_authors": max_authors,
			"date_filters": date_filters,
		}

		context["monthly_counts_params"] = {
			"include_monthly_counts": monthly_counts,
			"ml_threshold": ml_threshold,
		}

		return context

	def _build_date_filters(self, date_from, date_to, timeframe):
		"""Build date filters for articles"""
		date_filters = {}

		if timeframe:
			now = datetime.now()
			if timeframe == "year":
				date_from = now.replace(
					month=1, day=1, hour=0, minute=0, second=0, microsecond=0
				)
			elif timeframe == "month":
				date_from = now.replace(
					day=1, hour=0, minute=0, second=0, microsecond=0
				)
			elif timeframe == "week":
				# Get Monday of current week
				days_since_monday = now.weekday()
				date_from = now - timedelta(days=days_since_monday)
				date_from = date_from.replace(hour=0, minute=0, second=0, microsecond=0)

		if date_from:
			try:
				if isinstance(date_from, str):
					date_from = (
						parse_date(date_from)
						or datetime.strptime(date_from, "%Y-%m-%d").date()
					)
				date_filters["articles__published_date__gte"] = date_from
			except (ValueError, TypeError):
				pass

		if date_to:
			try:
				if isinstance(date_to, str):
					date_to = (
						parse_date(date_to)
						or datetime.strptime(date_to, "%Y-%m-%d").date()
					)
				date_filters["articles__published_date__lte"] = date_to
			except (ValueError, TypeError):
				pass

		return date_filters

	@action(detail=True, methods=["get"])
	def authors(self, request, pk=None):
		"""
		Get detailed author statistics for a specific category.

		# Query Parameters:
		- **min_articles** - Minimum articles per author (default: 1)
		- **sort_by** - 'articles_count', 'author_name' (default: 'articles_count')
		- **order** - 'asc', 'desc' (default: 'desc')
		- Date filtering parameters (same as main endpoint)

		**URL:** `/categories/{id}/authors/`
		"""
		category = self.get_object()

		# Get query parameters
		try:
			min_articles = int(request.query_params.get("min_articles", 1))
		except (ValueError, TypeError):
			min_articles = 1
		sort_by = request.query_params.get("sort_by", "articles_count")
		order = request.query_params.get("order", "desc")
		date_from = request.query_params.get("date_from")
		date_to = request.query_params.get("date_to")
		timeframe = request.query_params.get("timeframe")

		# Build date filters
		date_filters = self._build_date_filters(date_from, date_to, timeframe)

		# Build filter for articles in this category
		article_filter = Q(team_categories=category)
		if date_filters:
			# Adjust the date filter keys for the Articles model
			articles_date_filters = {}
			for key, value in date_filters.items():
				if key.startswith("articles__"):
					articles_date_filters[key.replace("articles__", "")] = value
			article_filter &= Q(**articles_date_filters)

		# Get authors with article counts in this category
		authors_queryset = (
			Authors.objects.filter(articles__team_categories=category)
			.annotate(
				category_articles_count=Count(
					"articles",
					filter=Q(articles__team_categories=category)
					& Q(
						**{
							f"articles__{k}": v
							for k, v in date_filters.items()
							if k.startswith("articles__")
						}
					),
					distinct=True,
				)
			)
			.filter(category_articles_count__gte=min_articles)
		)

		# Apply sorting
		if sort_by == "articles_count":
			order_prefix = "-" if order == "desc" else ""
			authors_queryset = authors_queryset.order_by(
				f"{order_prefix}category_articles_count", "author_id"
			)
		elif sort_by == "author_name":
			order_prefix = "-" if order == "desc" else ""
			authors_queryset = authors_queryset.order_by(
				f"{order_prefix}full_name", "author_id"
			)
		else:
			authors_queryset = authors_queryset.order_by(
				"-category_articles_count", "author_id"
			)

		# Paginate results
		page = self.paginate_queryset(authors_queryset)
		if page is not None:
			serializer = CategoryTopAuthorSerializer(page, many=True)
			return self.get_paginated_response(serializer.data)

		serializer = CategoryTopAuthorSerializer(authors_queryset, many=True)
		return Response(serializer.data)


###
# TRIALS
###

# Recruitment-availability rank for ?ordering=recruiting_first — "can a patient join
# this today?" order, NOT the alphabetical order of the TrialRecruitmentStatus enum
# values. A null recruitment_status_normalized is assigned the lowest-priority rank
# via the annotation's default= branch below (len(_RECRUITING_RANK), i.e. one past
# WITHDRAWN) — for ascending ?ordering=recruiting_first that sorts last, as intended;
# reversing with ?ordering=-recruiting_first reverses the whole scale, so null-status
# trials come first there, not last.
_RECRUITING_RANK = {
	TrialRecruitmentStatus.RECRUITING: 0,
	TrialRecruitmentStatus.ENROLLING_BY_INVITATION: 1,
	TrialRecruitmentStatus.NOT_YET_RECRUITING: 2,
	TrialRecruitmentStatus.ACTIVE_NOT_RECRUITING: 3,
	TrialRecruitmentStatus.SUSPENDED: 4,
	TrialRecruitmentStatus.NOT_RECRUITING: 5,
	TrialRecruitmentStatus.UNKNOWN: 6,
	TrialRecruitmentStatus.OTHER: 7,
	TrialRecruitmentStatus.COMPLETED: 8,
	TrialRecruitmentStatus.TERMINATED: 9,
	TrialRecruitmentStatus.WITHDRAWN: 10,
}


_TRIALS_ORDERING_PARAM = _ordering_param(
	["discovery_date", "published_date", "title", "trial_id", "last_updated", "recruiting_first"],
	"Sort field, prefix with `-` for descending. `recruiting_first` is a "
	"recruitment-availability rank (recruiting -> ... -> withdrawn), NOT "
	"alphabetical on recruitment_status_normalized. Null status sorts last for "
	"plain `recruiting_first` (ascending) but FIRST for `-recruiting_first` — "
	"the `-` reverses the whole scale, null included, it does not just reverse "
	"the named statuses. Ties break on -discovery_date automatically, in both "
	"directions.",
)


@extend_schema_view(
	list=extend_schema(auth=_OPTIONAL_API_KEY_SECURITY, parameters=[_TRIALS_ORDERING_PARAM]),
	retrieve=extend_schema(auth=_OPTIONAL_API_KEY_SECURITY),
)
class TrialViewSet(
	BulkExportThrottleMixin,
	CSVStreamingMixin,
	OrgVisibilityMixin,
	CachedStatsActionMixin,
	viewsets.ReadOnlyModelViewSet,
):
	"""
	List all clinical trials by discovery date with comprehensive filtering options.
	CSV responses are automatically streamed for better performance with large datasets.

	# Core Query Parameters:
	- **trial_id** - filter by specific trial ID
	- **team_id** - filter by team ID
	- **site_id** - filter by Django Site ID; returns trials belonging to any team attached to that site (see Team.site)
	- **subject_id** - filter by subject ID
	- **subjects** - comma-separated list of subject IDs with AND semantics — returns only trials tagged with *all* listed subjects (e.g., `?subjects=1,2`)
	- **subjects_any** - comma-separated list of subject IDs with OR semantics — returns trials tagged with *any* of the listed subjects (e.g., `?subjects_any=1,2`)
	- **category_slug** - filter by category slug
	- **category_id** - filter by category ID
	- **category_modality** - filter by the intervention modality of the trial's categories; one of the `CategoryModality` values
	- **source_id** - filter by source ID
	- **status/recruitment_status** - filter by recruitment status
	- **search** - search in title and summary (supports boolean operators, e.g. `a OR b`)
	- **title** - search only in the title field (case-insensitive substring)
	- **summary** - search only in the summary field (case-insensitive substring)
	- **page** - page number for pagination
	- **page_size** - items per page (max 100)
	- **all_results** - set to 'true' to bypass pagination and get all results (useful for CSV export)

	# Ordering:
	`?ordering=<field>` (prefix with `-` to reverse). Accepted values:
	- **discovery_date** - when Gregory first ingested the trial (default: `-discovery_date`, newest first)
	- **published_date** - registry publication date
	- **title**
	- **trial_id**
	- **last_updated**
	- **recruiting_first** - recruitment-availability order, NOT alphabetical on
	  `recruitment_status_normalized`: `recruiting` → `enrolling_by_invitation` →
	  `not_yet_recruiting` → `active_not_recruiting` → `suspended` →
	  `not_recruiting` → `unknown` → `other` → `completed` → `terminated` →
	  `withdrawn`, with null status last — that ordering is for plain
	  `?ordering=recruiting_first` (ascending); `?ordering=-recruiting_first`
	  reverses the whole scale, so null-status trials come first there instead.
	  Ties (many trials share a rank) are broken by `-discovery_date`
	  automatically, in both directions.

	Unrecognised `ordering` values are silently ignored (not rejected) — a DRF
	`OrderingFilter` default — so a typo or stale field name falls back to the
	default ordering rather than erroring.

	# Sites:
	Per-site rows (`TrialSite` — name/city/state/country/coordinates, sourced from CTIS
	retrieve enrichment and/or ClinicalTrials.gov) are **detail-only**: `trial_sites`
	appears on `GET /trials/{id}/` but never on the list/CSV/search responses (sites fan
	out, mean ~12/trial, so nesting them on every list row would bloat those responses for
	a field most callers don't need there). For a flat, filterable, paginated listing
	across many trials — e.g. to build a site-level pin map — use `GET /trials/sites/`
	instead, which accepts the same filters as this endpoint (team_id, subject_id,
	country, recruitment_status_normalized, …) plus `latitude__isnull`, and rejects
	`all_results=true` (it is not a bulk export). See TRIAL-GEOGRAPHY-PLAN.md PR G2/G3.

	# Stats Endpoint:
	`GET /trials/stats/` returns `total`, `no_status` (recruitment_status_normalized is
	null), one key per `TrialRecruitmentStatus` bucket — `not_yet_recruiting`, `recruiting`,
	`enrolling_by_invitation`, `active_not_recruiting`, `not_recruiting`, `suspended`,
	`completed`, `terminated`, `withdrawn`, `unknown`, `other` (always present, 0 when
	empty) — plus five facet breakdowns computed over the filtered queryset:
	- `by_subject` — `[{subject_id, subject_name, count}]`, distinct per trial,
	  restricted to subjects visible to the caller. Sums to `total`.
	- `by_phase` — `{phase_slug: count, ...}`, one key per `TrialPhase` value
	  (always present, 0 when empty) plus `no_phase` (raw `phase` was never set).
	  Sums to `total`.
	- `by_region` — `{region_slug: count, ...}`, one key per `TrialRegion` value
	  (always present, 0 when empty) plus `no_region` (`regions_normalized` is
	  null or empty). Does **not** sum to `total` — a trial spanning several
	  regions counts once per region.
	- `by_country` — `[{country: alpha2_or_null, count}]`, sorted by `-count`
	  then country code, zero-count countries omitted, a trailing
	  `{"country": null, ...}` entry for trials with no `TrialCountry` rows
	  (omitted when 0). Does **not** sum to `total` — multi-country trials count
	  once per country.
	- `by_year` — `[{year: int_or_null, count}]`, ascending by year, zero-count
	  years omitted, a trailing `{"year": null, ...}` entry for trials with no
	  `date_registration` (omitted when 0). Derived from `date_registration`
	  only (no `published_date` fallback). Sums to `total`.
	- `by_sponsor` — `[{sponsor_id, slug, name, sponsor_type, count}]`, top 25 by
	  count then sponsor name, over the canonical `Sponsor` entity a trial's raw
	  `primary_sponsor` resolves to (see docs/trials-field-normalization.md) —
	  excludes trials with no resolved sponsor yet, reported separately as the
	  sibling key `no_sponsor`. Does not sum to `total` once truncated to 25.
	- `by_sponsor_type` — `{sponsor_type_slug: count, ...}`, one key per
	  `SponsorType` value (always present, 0 when empty) plus `no_type`
	  (resolved sponsor, no sponsor_type derived yet) — excludes the same
	  null-FK group as `no_sponsor`.
	- `by_modality` — `{modality_slug: count, ...}`, one key per
	  `CategoryModality` value (always present, 0 when empty) plus
	  `no_modality`, over the trial's `team_categories.modality`. **Not** a
	  partition of `total` and **not** safe to sum: this joins an M2M, so a
	  trial carrying two categories of different modalities is counted once
	  *per modality*. `no_modality` also conflates two different things —
	  "trial has no category at all" (most trials; categories are a curated
	  lens over a small subset) and "trial has a category that isn't
	  curated with a modality yet" — deliberately, to avoid the extra query
	  cost of splitting them out as separate keys; use `?category_slug=` when
	  the distinction matters.
	- `by_study_type` — `{study_type_slug: count, ...}`, one key per
	  `TrialStudyType` value (always present, 0 when empty) plus
	  `no_study_type`. `no_study_type` is large (~13.2k globally) because most
	  of it is legacy rows with no `source_register` at all, not a
	  normalization gap — see docs/trials-field-normalization.md.
	- `by_sex` — `{sex_slug: count, ...}`, one key per `TrialSexEligibility`
	  value (always present, 0 when empty) plus `no_sex_data`. `no_sex_data`
	  is large (~46% of trials globally) because it includes the legacy
	  `source_register IS NULL` population that also lacks sponsors and
	  countries — same caveat as `no_modality`/`no_sponsor`. See
	  docs/trials-field-normalization.md and
	  INCLUSION-GENDER-NORMALIZATION-PLAN.md.

	Recruitment-status buckets and `by_phase`/`by_region`/`by_country`/`by_study_type`/`by_sex` are counted on the
	`*_normalized` canonical vocabularies (same as their respective filters), not on the
	raw registry strings — see docs/trials-field-normalization.md. It accepts the
	same query parameters as the list endpoint, so e.g.
	`/trials/stats/?team_id=1&recruitment_status_normalized=recruiting` scopes the totals
	exactly like the equivalent list request — including composition across facets, e.g.
	`/trials/stats/?phase_normalized=phase_3` makes `by_country` the phase-3 × country
	slice of the matrix. Results are cached server-side for
	STATS_CACHE_TTL seconds (default 600). **Breaking change:** the `stats` block that used
	to be embedded in every paginated list response has moved here — list responses no
	longer include it, and clients that relied on `response.data["stats"]` must call
	`/trials/stats/` instead.

	# Registry Identifier Parameters:
	Each accepts one or more comma-separated values and returns trials matching *any* of them
	(case-insensitive). Combine with other filters (team, subject, status) as usual.
	- **identifiers** - mixed list matched across all registry keys at once (NCT/EudraCT/EUCT/EUCTR/CTIS),
	  e.g. `?identifiers=NCT02521311,2020-001234-12`. Acronyms are excluded (not unique) — use `acronym` for those.
	- **nct** - ClinicalTrials.gov NCT id(s), e.g. `?nct=NCT02521311,NCT06065670`
	- **eudract** - EudraCT number(s)
	- **euct** - EU CT / EUCTR number(s) (matches either `euct` or `euctr` keys)
	- **ctis** - CTIS number(s)
	- **acronym** - trial acronym(s), e.g. `?acronym=ReCOVER,MODIF-MS`

	# Trial-Specific Parameters:
	- **internal_number** - filter by WHO internal number
	- **phase** - raw-text contains filter on the registry's own phase string (Phase I, II, III, etc.) — free text, so it silently misses spelling/format variants (e.g. "Phase III" vs "Phase 3")
	- **phase_normalized** - exact match against the canonical phase; one of: early_phase_1, phase_1, phase_1_2, phase_2, phase_2_3, phase_3, phase_3_4, phase_4, post_market, not_applicable, other
	- **status** / **recruitment_status** - raw-text `iexact` filters against the registry's own recruitment-status string (e.g. "Recruiting", "RECRUITING", "recruiting" all match each other, but "Not Recruiting" does not match "not_yet_recruiting") — free text, so it silently misses spelling/format variants across registries
	- **recruitment_status_normalized** - exact match against the canonical recruitment status; one of: not_yet_recruiting, recruiting, enrolling_by_invitation, active_not_recruiting, not_recruiting, suspended, completed, terminated, withdrawn, unknown, other
	- **study_type** - legacy free-text case-insensitive substring (`icontains`) filter on the raw registry study-type string — it can both overmatch and undermatch across registries (e.g. matches "Interventional clinical trial of medicinal product" but misses "Intervention"); prefer **study_type_normalized** below
	- **study_type_normalized** - exact match against the canonical study type; one of: interventional, observational, expanded_access, basic_science, other
	- **primary_sponsor** - legacy free-text `icontains` filter on the raw registry sponsor string — silently misses spelling/duplicate variants across registries; prefer **sponsor_id**/**sponsor_slug** below (same legacy treatment as `phase` vs `phase_normalized`)
	- **sponsor_id** - exact match against a canonical sponsor's id (see `/sponsors/`)
	- **sponsor_slug** - exact match against a canonical sponsor's slug (see `/sponsors/`)
	- **source_register** - filter by source registry
	- **countries** - raw-text `icontains` filter on the registry's own countries string — free text, so it silently misses spelling/format variants across registries (see **country** below for the normalized equivalent)
	- **country** - match against one or more normalized country codes (ISO 3166-1 alpha-2), comma-separated, OR semantics (e.g. `?country=DE` or `?country=DE,FR`), derived from every source's raw country data — see docs/trials-field-normalization.md
	- **region** - exact match against a normalized region derived from the trial's countries; one of: africa, asia, europe, north_america, south_america, oceania (e.g. `?region=europe`)

	# Medical/Research Parameters:
	- **condition** - filter by medical condition
	- **intervention** - filter by intervention type
	- **therapeutic_areas** - filter by therapeutic areas
	- **inclusion_agemin/agemax** - filter by age inclusion criteria
	- **inclusion_gender_normalized** - exact match against canonical sex eligibility; one of: all, female, male. Replaces the removed legacy `inclusion_gender` substring filter, which returned confidently wrong results (`?inclusion_gender=Female` matched "Female, Male", a both-sexes trial) — see docs/trials-field-normalization.md. **Breaking change (2026-07-20):** `?inclusion_gender=...` is no longer a recognised parameter and is silently ignored (unfiltered results), not rejected.

	# Results Parameters:
	- **has_results** - `true`/`false`; a trial counts as having results when any of `results_posted`, results completion date, results link, or results-available = "Yes" is set

	# Date Range Parameters:
	Filter by trial registration date (the date the trial was first registered with its registry).
	Both parameters are optional and can be used independently or together.
	Invalid or non-ISO-8601 dates return **400 Bad Request**.
	- **date_registration_after** - trials registered on or after this date (YYYY-MM-DD, inclusive)
	- **date_registration_before** - trials registered on or before this date (YYYY-MM-DD, inclusive)

	# Examples:
	- All trials as CSV: `/trials/?format=csv&all_results=true`
	- Multi-subject OR: `/trials/?subjects_any=1,2`
	- Filtered trials: `/trials/?team_id=1&status=Recruiting&format=csv&all_results=true`
	- Trials with results posted: `/trials/?has_results=true`
	- Trials registered in 2019–2022: `/trials/?date_registration_after=2019-01-01&date_registration_before=2022-12-31`
	- Phase III trials registered since 2020: `/trials/?phase_normalized=phase_3&date_registration_after=2020-01-01`
	- Date range + subject + CSV: `/trials/?team_id=1&subjects=1&date_registration_after=2019-01-01&date_registration_before=2022-12-31&format=csv&all_results=true`
	"""

	queryset = Trials.objects.all().order_by("-discovery_date")
	serializer_class = TrialSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]
	pagination_class = FlexiblePagination
	# `search` is handled solely by TrialFilter.filter_search (boolean parser);
	# DRF's SearchFilter is omitted to avoid double-filtering ?search=. See ArticleViewSet.
	filter_backends = [
		django_filters.DjangoFilterBackend,
		filters.OrderingFilter,
	]
	filterset_class = TrialFilter

	def _ordering_requests_recruiting_first(self):
		"""True when the ``ordering`` query param's terms include ``recruiting_first``
		(either direction). Used to annotate it on the stats action too — see
		get_queryset() below."""
		ordering_param = self.request.query_params.get("ordering", "")
		return any(
			term.strip().lstrip("-") == "recruiting_first"
			for term in ordering_param.split(",")
		)

	def get_queryset(self):
		"""Prefetch the caller-org's TrialOrgContent to avoid N+1 on list responses.

		When a request resolves to an organisation (API key or public-org
		filter), attach the matching ``TrialOrgContent`` rows as
		``_prefetched_org_contents`` so the serializer can resolve per-org
		fields without issuing one query per trial.
		"""
		# Prefetch m2m/reverse-FK relations the serializer reads (sources, team_categories,
		# article_references, trial_countries) so list/CSV-export responses don't issue one
		# query per trial. trial_countries backs the "trial_countries" and
		# "countries_normalized" serializer fields — see
		# docs/trials-field-normalization.md.
		qs = (
			super()
			.get_queryset()
			.select_related("primary_sponsor_normalized")
			.prefetch_related(
				"sources",
				"team_categories",
				"article_references__article",
				"trial_countries",
			)
		)
		# trial_sites backs TrialDetailSerializer's "trial_sites" field, used only on
		# the retrieve action (see get_serializer_class) — prefetching it on every
		# list row would cost a query per page for a field list responses never
		# render. See TRIAL-GEOGRAPHY-PLAN.md PR G3.
		if self.action == "retrieve":
			qs = qs.prefetch_related("trial_sites")
		org = _resolve_per_org_fields_org(self.request)
		if org is not None:
			qs = qs.prefetch_related(
				Prefetch(
					"org_contents",
					queryset=TrialOrgContent.objects.filter(organization=org),
					to_attr="_prefetched_org_contents",
				)
			)
		# Backs ?ordering=recruiting_first (see _RECRUITING_RANK above). Skipped for
		# the stats action unless explicitly requested there too: build_stats_payload's
		# facet aggregations call .order_by() to clear ordering, so annotating
		# unconditionally would be pure overhead on that path — but /trials/stats/
		# also runs filter_queryset(get_queryset()) before build_stats_payload (see
		# CachedStatsActionMixin._stats_response), so a stats request that does pass
		# ?ordering=recruiting_first must still see the annotation, or OrderingFilter
		# 500s trying to order_by a field the queryset doesn't have.
		if self.action != "stats" or self._ordering_requests_recruiting_first():
			qs = qs.annotate(
				recruiting_first=Case(
					*[
						When(recruitment_status_normalized=status, then=Value(rank))
						for status, rank in _RECRUITING_RANK.items()
					],
					default=Value(len(_RECRUITING_RANK)),
					output_field=IntegerField(),
				)
			)
		return qs

	def get_serializer_class(self):
		"""TrialDetailSerializer (adds ``trial_sites``) only for the single-trial
		retrieve response; the list/CSV/search paths keep the lighter
		TrialSerializer. See TRIAL-GEOGRAPHY-PLAN.md PR G3."""
		if self.action == "retrieve":
			return TrialDetailSerializer
		return TrialSerializer

	ordering_fields = [
		"discovery_date",
		"published_date",
		"title",
		"trial_id",
		"last_updated",
		"recruiting_first",
	]
	ordering = ["-discovery_date"]

	def filter_queryset(self, queryset):
		"""Append ``-discovery_date`` as a tiebreaker whenever ``recruiting_first``
		is the requested ordering. Many trials share a rank (see _RECRUITING_RANK
		above), so ``ordering=recruiting_first`` alone gives an unstable page order
		across requests — a pagination-correctness bug, not cosmetics.
		"""
		queryset = super().filter_queryset(queryset)
		ordering_param = self.request.query_params.get("ordering", "")
		primary_term = ordering_param.split(",")[0].strip()
		if primary_term.lstrip("-") == "recruiting_first":
			queryset = queryset.order_by(primary_term, "-discovery_date")
		return queryset

	stats_cache_prefix = "trials_stats"

	@extend_schema(responses=TrialsStatsSerializer, auth=_OPTIONAL_API_KEY_SECURITY)
	@action(detail=False, methods=["get"], url_path="stats")
	def stats(self, request):
		"""Recruitment-status totals (by ``recruitment_status_normalized``) over the
		filtered queryset.

		Accepts the same query parameters as the list endpoint (team_id,
		subject_id, recruitment_status_normalized, search, registry identifiers, …)
		and the same org-visibility scoping, so the totals always match what the
		equivalent list request would return. Cached server-side; see
		``CachedStatsActionMixin``.
		"""
		return self._stats_response(request)

	@extend_schema(
		parameters=[
			OpenApiParameter(
				"latitude__isnull",
				OpenApiTypes.BOOL,
				OpenApiParameter.QUERY,
				description=(
					"Narrow to sites without coordinates (true/1/yes) or with a pin "
					"(false/0/no). Any other value is rejected (400)."
				),
			),
		],
		responses={200: TrialSiteRowSerializer(many=True), 400: ErrorResponseSerializer},
		auth=_OPTIONAL_API_KEY_SECURITY,
	)
	@action(detail=False, methods=["get"], url_path="sites")
	def sites(self, request):
		"""Flat, paginated listing of TrialSite rows across the filtered trial set —
		accepts the same query parameters as the list endpoint (team_id,
		subject_id, country, recruitment_status_normalized, …), so the map reuses
		the same filter rail. Rows are ``{trial_id, name, city, country, latitude,
		longitude}`` — no per-trial nesting; see TrialDetailSerializer for that.

		``?all_results=true`` is rejected (400): unlike ``/trials/``, this endpoint
		is never a bulk export — at mean ~12 sites/trial an unfiltered request over
		a several-thousand-trial subject would be tens of thousands of rows.
		``?latitude__isnull=true`` (or ``1``/``yes``) narrows to sites without
		coordinates (all CTIS sites, currently — see TRIAL-GEOGRAPHY-PLAN.md PR
		G2); ``=false`` (or ``0``/``no``) narrows to sites with a pin. Any other
		value is rejected (400).
		"""
		if request_bypasses_pagination(request):
			raise ValidationError(
				"all_results is not supported on /trials/sites/ — this endpoint is "
				"not a bulk export. Use page/page_size instead."
			)

		trials_qs = self.filter_queryset(self.get_queryset())
		sites_qs = TrialSite.objects.filter(trial__in=trials_qs)

		latitude_isnull = request.query_params.get("latitude__isnull")
		if latitude_isnull is not None:
			normalized = latitude_isnull.strip().lower()
			if normalized in ("true", "1", "yes"):
				sites_qs = sites_qs.filter(latitude__isnull=True)
			elif normalized in ("false", "0", "no"):
				sites_qs = sites_qs.filter(latitude__isnull=False)
			else:
				raise ValidationError(
					f"latitude__isnull must be true/false (or 1/0, yes/no); "
					f"got {latitude_isnull!r}."
				)

		sites_qs = sites_qs.order_by("trial_id", "pk").values(
			"trial_id", "name", "city", "country", "latitude", "longitude"
		)

		paginator = TrialSitePagination()
		page = paginator.paginate_queryset(sites_qs, request, view=self)
		if page is not None:
			return paginator.get_paginated_response(list(page))
		return Response(list(sites_qs))

	def build_stats_payload(self, filtered_qs):
		# Single aggregation query — clear ordering to prevent GROUP BY pollution.
		# distinct=True guards against double-counting a trial visible under two teams.
		# Aggregates on recruitment_status_normalized (not the raw recruitment_status
		# column): the raw column has dozens of per-registry spellings, so grouping on
		# it directly would require re-deriving the same mapping trial_field_normalizers
		# already owns. See docs/trials-field-normalization.md.
		status_counts = {
			item["recruitment_status_normalized"]: item["count"]
			for item in filtered_qs.order_by()
			.values("recruitment_status_normalized")
			.annotate(count=Count("trial_id", distinct=True))
		}

		# Every canonical key is emitted even when its count is 0, so the frontend gets
		# a stable shape. Iterate the enum rather than hardcoding the key list here —
		# a bucket added to TrialRecruitmentStatus is picked up automatically.
		payload = {
			"total": sum(status_counts.values()),
			"no_status": status_counts.get(None, 0),
		}
		for value in TrialRecruitmentStatus.values:
			payload[value] = status_counts.get(value, 0)
		payload["by_subject"] = self._by_subject_counts(filtered_qs)
		payload["by_phase"] = self._by_phase_counts(filtered_qs)
		payload["by_region"] = self._by_region_counts(filtered_qs)
		payload["by_country"] = self._by_country_counts(filtered_qs)
		payload["by_year"] = self._by_year_counts(filtered_qs)
		payload["by_sponsor"] = self._by_sponsor_counts(filtered_qs)
		payload["no_sponsor"] = self._no_sponsor_count(filtered_qs)
		payload["by_sponsor_type"] = self._by_sponsor_type_counts(filtered_qs)
		payload["by_modality"] = self._by_modality_counts(filtered_qs)
		payload["by_study_type"] = self._by_study_type_counts(filtered_qs)
		payload["by_sex"] = self._by_sex_counts(filtered_qs)
		return payload

	# Phase, region, country, and year are trial-intrinsic (not org-owned data like
	# subjects), so unlike _by_subject_counts these facets need no visible_org_ids
	# filtering — every trial in filtered_qs is already scoped to what the caller
	# may see.

	def _by_phase_counts(self, filtered_qs):
		"""``{phase_slug: count, ..., "no_phase": count}`` over *filtered_qs*."""
		counts = {
			row["phase_normalized"]: row["count"]
			for row in filtered_qs.order_by()
			.values("phase_normalized")
			.annotate(count=Count("trial_id", distinct=True))
		}
		payload = {value: counts.get(value, 0) for value in TrialPhase.values}
		payload["no_phase"] = counts.get(None, 0)
		return payload

	def _by_study_type_counts(self, filtered_qs):
		"""``{study_type_slug: count, ..., "no_study_type": count}`` over *filtered_qs*.

		``no_study_type`` is large (~13.2k globally, 2026-07-20) because most of it is
		legacy rows with no ``source_register`` at all, not a normalization gap — see
		docs/trials-field-normalization.md.
		"""
		counts = {
			row["study_type_normalized"]: row["count"]
			for row in filtered_qs.order_by()
			.values("study_type_normalized")
			.annotate(count=Count("trial_id", distinct=True))
		}
		payload = {value: counts.get(value, 0) for value in TrialStudyType.values}
		payload["no_study_type"] = counts.get(None, 0)
		return payload

	def _by_sex_counts(self, filtered_qs):
		"""``{sex_slug: count, ..., "no_sex_data": count}`` over *filtered_qs*.

		``no_sex_data`` is large (~46% of trials globally, 2026-07-20) because it includes
		the legacy ``source_register IS NULL`` population that also lacks sponsors and
		countries, not a normalization gap — see docs/trials-field-normalization.md.
		"""
		counts = {
			row["inclusion_gender_normalized"]: row["count"]
			for row in filtered_qs.order_by()
			.values("inclusion_gender_normalized")
			.annotate(count=Count("trial_id", distinct=True))
		}
		payload = {value: counts.get(value, 0) for value in TrialSexEligibility.values}
		payload["no_sex_data"] = counts.get(None, 0)
		return payload

	def _by_region_counts(self, filtered_qs):
		"""``{region_slug: count, ..., "no_region": count}`` over *filtered_qs*.

		``regions_normalized`` is a JSONField array with no clean ORM GROUP BY
		over array elements, so this runs one containment-filtered count per
		region (7 indexed queries) rather than a single aggregation — acceptable
		since the response sits behind the stats cache.
		"""
		base = filtered_qs.order_by()
		payload = {
			value: base.filter(regions_normalized__contains=[value]).aggregate(
				count=Count("trial_id", distinct=True)
			)["count"]
			for value in TrialRegion.values
		}
		payload["no_region"] = base.filter(
			Q(regions_normalized__isnull=True) | Q(regions_normalized=[])
		).aggregate(count=Count("trial_id", distinct=True))["count"]
		return payload

	def _by_country_counts(self, filtered_qs):
		"""``[{"country": alpha2_or_None, "count": n}, ...]`` over *filtered_qs*,
		sorted by ``-count`` then country code, zero-count countries omitted, the
		null (no ``TrialCountry`` rows) entry last when present.
		"""
		rows = (
			filtered_qs.order_by()
			.values("trial_countries__country")
			.annotate(count=Count("trial_id", distinct=True))
			.order_by("-count", "trial_countries__country")
		)
		result = []
		null_entry = None
		for row in rows:
			code = row["trial_countries__country"]
			if code:
				result.append({"country": code, "count": row["count"]})
			else:
				null_entry = {"country": None, "count": row["count"]}
		if null_entry:
			result.append(null_entry)
		return result

	def _by_year_counts(self, filtered_qs):
		"""``[{"year": int_or_None, "count": n}, ...]`` over *filtered_qs*, from
		``date_registration`` only (no ``published_date`` fallback), ascending,
		zero-count years omitted, the null-registration entry last when present.
		"""
		rows = (
			filtered_qs.order_by()
			.annotate(registration_year=ExtractYear("date_registration"))
			.values("registration_year")
			.annotate(count=Count("trial_id", distinct=True))
			.order_by("registration_year")
		)
		result = []
		null_entry = None
		for row in rows:
			year = row["registration_year"]
			if year is None:
				null_entry = {"year": None, "count": row["count"]}
			else:
				result.append({"year": year, "count": row["count"]})
		if null_entry:
			result.append(null_entry)
		return result

	def _by_sponsor_counts(self, filtered_qs):
		"""Top 25 ``[{"sponsor_id", "slug", "name", "sponsor_type", "count"}]`` over
		*filtered_qs*, sorted ``-count`` then sponsor name, excluding the null-FK
		group (reported separately by ``_no_sponsor_count``). Sponsors are trial-
		intrinsic (not org-owned), so no visible_org_ids filtering is needed here —
		same reasoning as _by_phase_counts/_by_region_counts/_by_country_counts.
		"""
		rows = (
			filtered_qs.order_by()
			.exclude(primary_sponsor_normalized__isnull=True)
			.values(
				"primary_sponsor_normalized_id",
				"primary_sponsor_normalized__slug",
				"primary_sponsor_normalized__name",
				"primary_sponsor_normalized__sponsor_type",
			)
			.annotate(count=Count("trial_id", distinct=True))
			.order_by("-count", "primary_sponsor_normalized__name")[:25]
		)
		return [
			{
				"sponsor_id": row["primary_sponsor_normalized_id"],
				"slug": row["primary_sponsor_normalized__slug"],
				"name": row["primary_sponsor_normalized__name"],
				"sponsor_type": row["primary_sponsor_normalized__sponsor_type"],
				"count": row["count"],
			}
			for row in rows
		]

	def _no_sponsor_count(self, filtered_qs):
		"""Count of trials whose raw ``primary_sponsor`` hasn't resolved to a
		canonical Sponsor yet (null FK) — the sibling of ``by_sponsor``."""
		return (
			filtered_qs.order_by()
			.filter(primary_sponsor_normalized__isnull=True)
			.aggregate(count=Count("trial_id", distinct=True))["count"]
		)

	def _by_sponsor_type_counts(self, filtered_qs):
		"""``{sponsor_type_slug: count, ..., "no_type": count}`` over *filtered_qs*,
		excluding trials with no resolved sponsor (already reported as
		``no_sponsor`` — a trial can't be untyped and unresolved at once here).
		"""
		counts = {
			row["primary_sponsor_normalized__sponsor_type"]: row["count"]
			for row in filtered_qs.order_by()
			.exclude(primary_sponsor_normalized__isnull=True)
			.values("primary_sponsor_normalized__sponsor_type")
			.annotate(count=Count("trial_id", distinct=True))
		}
		payload = {value: counts.get(value, 0) for value in SponsorType.values}
		payload["no_type"] = counts.get(None, 0)
		return payload

	def _by_modality_counts(self, filtered_qs):
		"""``{modality_slug: count, ..., "no_modality": count}`` over *filtered_qs*.

		Joins the ``team_categories`` M2M, so unlike most facets here this one is
		neither a partition of ``total`` nor safe to sum: a trial in two categories
		of different modalities is counted once *per modality* it carries (this is
		intentional — see the facet test asserting it explicitly). And
		``no_modality`` conflates two different situations a caller may want to
		tell apart — a trial with no category at all (the large majority, since
		categories are a curated lens over a small subset of trials), and a trial
		whose category exists but hasn't been curated with a modality yet —
		deliberately, to avoid the extra query cost of splitting them; use
		``?category_slug=`` filtering when the distinction matters.
		"""
		counts = {
			row["team_categories__modality"]: row["count"]
			for row in filtered_qs.order_by()
			.values("team_categories__modality")
			.annotate(count=Count("trial_id", distinct=True))
		}
		payload = {value: counts.get(value, 0) for value in CategoryModality.values}
		payload["no_modality"] = counts.get(None, 0)
		return payload


###
# SPONSORS
###

_SPONSORS_ORDERING_PARAM = _ordering_param(
	["name", "trials_count"],
	"Sort field, prefix with `-` for descending (default: name).",
)


@extend_schema_view(
	list=extend_schema(parameters=[_SPONSORS_ORDERING_PARAM]),
)

class SponsorViewSet(viewsets.ReadOnlyModelViewSet):
	"""
	List canonical sponsor entities (deduplicated trial sponsors — see
	docs/trials-field-normalization.md). Sponsors are global, not org-owned, so
	unlike most other viewsets this one carries no OrgVisibilityMixin — same
	reasoning as the by_sponsor/by_sponsor_type facets on /trials/stats/.

	# Query Parameters:
	- **search** - search by sponsor name
	- **sponsor_type** - filter by sponsor type; one of: industry,
	  academic_medical, government, nonprofit, other
	- **ordering** - 'name' (default) or 'trials_count' (add '-' for reverse)
	- **page_size** - items per page (max 100)

	# Examples:
	- Filter by type: `/sponsors/?sponsor_type=industry`
	- Search by name: `/sponsors/?search=novartis`
	- Most-trials first: `/sponsors/?ordering=-trials_count`
	"""

	queryset = Sponsor.objects.all().order_by("name")
	serializer_class = SponsorSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]
	pagination_class = FlexiblePagination
	filter_backends = [
		django_filters.DjangoFilterBackend,
		filters.SearchFilter,
		filters.OrderingFilter,
	]
	filterset_class = SponsorFilter
	search_fields = ["name"]
	ordering_fields = ["name", "trials_count"]
	ordering = ["name"]

	def get_queryset(self):
		return super().get_queryset().annotate(
			trials_count=Count("trials", distinct=True)
		)


###
# SOURCES
###


class SourceViewSet(OrgVisibilityMixin, viewsets.ReadOnlyModelViewSet):
	"""
	List all sources of data with optional filters for team and subject.

	# Query Parameters:
	- **source_id** - filter by specific source ID
	- **team_id** - filter by team ID
	- **subject_id** - filter by subject ID
	- **active** - filter by active status (true/false)
	- **source_for** - filter by what the source feeds; one of: `science paper`, `trials`, `news article`
	- **link** - filter by source link (case-insensitive substring)
	- **search** - search in source name and description
	- **ordering** - sort field, prefix with `-` for descending. Allowed values: `name`, `source_id`
	"""

	_org_filter_path = "team__organization_id"
	_org_filter_distinct = False
	queryset = Sources.objects.all().order_by("name")
	serializer_class = SourceSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]
	filter_backends = [
		django_filters.DjangoFilterBackend,
		filters.SearchFilter,
		filters.OrderingFilter,
	]
	filterset_class = SourceFilter
	search_fields = ["name", "description"]
	ordering_fields = ["name", "source_id"]
	ordering = ["name"]


###
# AUTHORS
###


def author_articles_count_subquery(visible_org_ids=None, relevant_only=False):
	"""Correlated count of an author's articles, immune to outer-query joins.

	A plain ``Count("articles", ...)`` annotation is corrupted whenever the
	outer queryset already filters across the ``articles__`` join (Django
	computes the aggregate over the same constrained join). A subquery avoids
	that trap entirely.
	"""
	articles = Articles.objects.filter(authors__author_id=OuterRef("author_id"))
	if relevant_only:
		articles = articles.filter(relevant=True)
	if visible_org_ids is not None:
		articles = articles.filter(teams__organization_id__in=visible_org_ids)
	counts = (
		articles.order_by()
		.values("authors__author_id")
		.annotate(n=Count("article_id", distinct=True))
		.values("n")[:1]
	)
	return Coalesce(Subquery(counts, output_field=IntegerField()), Value(0))


_AUTHOR_ID_PATH_PARAM = OpenApiParameter(
	"id",
	OpenApiTypes.INT,
	OpenApiParameter.PATH,
	description="Author ID (Authors.author_id).",
)

_AUTHORS_LIST_PARAMS = [
	OpenApiParameter(
		"team_id",
		OpenApiTypes.INT,
		OpenApiParameter.QUERY,
		description="Restrict to authors with at least one article on this team. "
		"Required if subject_id, category_slug, or category_id is given — without "
		"it, the response is an empty page rather than an error.",
	),
	OpenApiParameter(
		"subject_id",
		OpenApiTypes.INT,
		OpenApiParameter.QUERY,
		description="Restrict to authors with at least one article in this subject. "
		"Requires team_id.",
	),
	OpenApiParameter(
		"category_slug",
		OpenApiTypes.STR,
		OpenApiParameter.QUERY,
		description="Restrict to authors with at least one article in this team "
		"category (by slug, see /categories/). Requires team_id.",
	),
	OpenApiParameter(
		"category_id",
		OpenApiTypes.INT,
		OpenApiParameter.QUERY,
		description="Restrict to authors with at least one article in this team "
		"category (by ID, see /categories/). Requires team_id.",
	),
	OpenApiParameter(
		"date_from",
		OpenApiTypes.DATE,
		OpenApiParameter.QUERY,
		description="Only count articles published on or after this date (YYYY-MM-DD) "
		"toward team_id/subject_id/category filtering and article_count.",
	),
	OpenApiParameter(
		"date_to",
		OpenApiTypes.DATE,
		OpenApiParameter.QUERY,
		description="Only count articles published on or before this date (YYYY-MM-DD) "
		"toward team_id/subject_id/category filtering and article_count.",
	),
	OpenApiParameter(
		"timeframe",
		OpenApiTypes.STR,
		OpenApiParameter.QUERY,
		enum=["year", "month", "week"],
		description="Shortcut for date_from relative to now (overridden by an explicit "
		"date_from); has no effect on date_to.",
	),
	OpenApiParameter(
		"sort_by",
		OpenApiTypes.STR,
		OpenApiParameter.QUERY,
		enum=["author_id", "article_count"],
		description="Sort field (default: author_id). This endpoint does not use the "
		"standard `ordering` param.",
	),
	OpenApiParameter(
		"order",
		OpenApiTypes.STR,
		OpenApiParameter.QUERY,
		enum=["asc", "desc"],
		description="Sort direction (default: desc when sort_by=article_count, asc otherwise).",
	),
]

_AUTHORS_BY_TEAM_SUBJECT_PARAMS = [
	OpenApiParameter(
		"team_id", OpenApiTypes.INT, OpenApiParameter.QUERY, required=True, description="Team ID."
	),
	OpenApiParameter(
		"subject_id", OpenApiTypes.INT, OpenApiParameter.QUERY, required=True, description="Subject ID."
	),
]

_AUTHORS_BY_TEAM_CATEGORY_PARAMS = [
	OpenApiParameter(
		"team_id", OpenApiTypes.INT, OpenApiParameter.QUERY, required=True, description="Team ID."
	),
	OpenApiParameter(
		"category_slug",
		OpenApiTypes.STR,
		OpenApiParameter.QUERY,
		description="Team category slug. One of category_slug/category_id is required.",
	),
	OpenApiParameter(
		"category_id",
		OpenApiTypes.INT,
		OpenApiParameter.QUERY,
		description="Team category ID. One of category_slug/category_id is required.",
	),
]


@extend_schema_view(
	list=extend_schema(parameters=_AUTHORS_LIST_PARAMS),
	retrieve=extend_schema(parameters=[_AUTHOR_ID_PATH_PARAM]),
	coauthors=extend_schema(parameters=[_AUTHOR_ID_PATH_PARAM]),
	by_team_subject=extend_schema(parameters=_AUTHORS_BY_TEAM_SUBJECT_PARAMS),
	by_team_category=extend_schema(parameters=_AUTHORS_BY_TEAM_CATEGORY_PARAMS),
)
class AuthorsViewSet(viewsets.ReadOnlyModelViewSet):
	"""
	Enhanced Authors API with sorting and filtering capabilities.

	# Query Parameters:

	- **author_id** - filter by specific author ID
	- **full_name** - search by author's full name (case-insensitive)
	- **given_name** - search by author's given name (case-insensitive)
	- **family_name** - search by author's family name (case-insensitive)
	- **orcid** - filter by ORCID identifier (case-insensitive contains search)
	- **country** - filter by country code (exact match)
	- **sort_by** - 'article_count' (default: 'author_id')
	- **order** - 'asc' or 'desc' (default: 'desc' for article_count, 'asc' for others)
	- **team_id** - filter by team ID
	- **subject_id** - filter by subject ID
	- **category_slug** - filter by team category slug
	- **category_id** - filter by team category ID
	- **date_from** - filter articles from this date (YYYY-MM-DD)
	- **date_to** - filter articles to this date (YYYY-MM-DD)
	- **timeframe** - 'year', 'month', 'week' (relative to current date)

	# Examples:

	- Get specific author: `?author_id=380002`
	- Search by name: `?full_name=John%20Smith`
	- Search by given name: `?given_name=John`
	- Search by family name: `?family_name=Smith`
	- Filter by ORCID: `?orcid=0000-0000-0000-0001`
	- Filter by country: `?country=US`
	- Sort by article count: `?sort_by=article_count&order=desc`
	- Filter by timeframe: `?sort_by=article_count&timeframe=year`
	- Team and subject filter: `?team_id=1&subject_id=5&sort_by=article_count`
	- Count per category: `?team_id=1&category_slug=natalizumab&sort_by=article_count&order=desc`
	- Category with ID: `?team_id=1&category_id=5&sort_by=article_count&order=desc`
	- Category with timeframe: `?team_id=1&category_slug=natalizumab&timeframe=year&sort_by=article_count`
	- Date range: `?date_from=2024-06-01&date_to=2024-12-31&team_id=1&subject_id=1&sort_by=article_count`
	"""

	serializer_class = AuthorSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]
	filter_backends = [django_filters.DjangoFilterBackend, filters.SearchFilter]
	filterset_class = AuthorFilter
	search_fields = ["full_name", "ORCID"]
	ordering_fields = ["author_id", "full_name", "country", "article_count"]
	ordering = ["author_id"]

	def get_queryset(self):
		queryset = Authors.objects.all()

		# --- Org visibility: only authors with at least one article in a visible org ---
		if hasattr(self.request, "visible_org_ids"):
			queryset = queryset.filter(
				Exists(
					Articles.objects.filter(
						authors=OuterRef("pk"),
						teams__organization_id__in=self.request.visible_org_ids,
					)
				)
			)

		# Get query parameters
		author_id = self.request.query_params.get("author_id")
		full_name = self.request.query_params.get("full_name")
		country = self.request.query_params.get("country")
		sort_by = self.request.query_params.get("sort_by", "author_id")
		order = self.request.query_params.get(
			"order", "desc" if sort_by == "article_count" else "asc"
		)
		team_id = self.request.query_params.get("team_id")
		subject_id = self.request.query_params.get("subject_id")
		category_slug = self.request.query_params.get("category_slug")
		category_id = self.request.query_params.get("category_id")
		date_from = self.request.query_params.get("date_from")
		date_to = self.request.query_params.get("date_to")
		timeframe = self.request.query_params.get("timeframe")

		# Apply simple filters first
		if author_id:
			try:
				author_id = int(author_id)
				queryset = queryset.filter(author_id=author_id)
			except ValueError:
				pass

		if full_name:
			# Use uppercase search for better performance with GIN index
			upper_value = full_name.upper()
			queryset = queryset.filter(ufull_name__contains=upper_value)

		if country:
			# Filter by country (exact match)
			queryset = queryset.filter(country=country)

		# Build date filter for articles
		date_filters = {}

		if timeframe:
			now = datetime.now()
			if timeframe == "year":
				date_from = now.replace(
					month=1, day=1, hour=0, minute=0, second=0, microsecond=0
				)
			elif timeframe == "month":
				date_from = now.replace(
					day=1, hour=0, minute=0, second=0, microsecond=0
				)
			elif timeframe == "week":
				# Get Monday of current week
				days_since_monday = now.weekday()
				date_from = now - timedelta(days=days_since_monday)
				date_from = date_from.replace(hour=0, minute=0, second=0, microsecond=0)

		if date_from:
			try:
				if isinstance(date_from, str):
					date_from = (
						parse_date(date_from)
						or datetime.strptime(date_from, "%Y-%m-%d").date()
					)
				date_filters["articles__published_date__gte"] = date_from
			except (ValueError, TypeError):
				pass

		if date_to:
			try:
				if isinstance(date_to, str):
					date_to = (
						parse_date(date_to)
						or datetime.strptime(date_to, "%Y-%m-%d").date()
					)
				date_filters["articles__published_date__lte"] = date_to
			except (ValueError, TypeError):
				pass

		# Apply team/subject/category filters using single-phase approach
		count_filters = {}  # Used for Count annotation on Authors queryset

		# Validate that team_id is provided when using subject_id or category filters
		if (subject_id or category_slug or category_id) and not team_id:
			# Return empty queryset if team_id is missing for subject/category filtering
			return Authors.objects.none()

		if team_id:
			try:
				team_id = int(team_id)
				count_filters["articles__teams__id"] = team_id
			except ValueError:
				pass

		if subject_id:
			try:
				subject_id = int(subject_id)
				count_filters["articles__subjects__id"] = subject_id
			except ValueError:
				pass

		if category_slug:
			count_filters["articles__team_categories__category_slug"] = category_slug

		if category_id:
			try:
				category_id = int(category_id)
				count_filters["articles__team_categories__id"] = category_id
			except ValueError:
				pass

		# Add date filters to count filters
		count_filters.update(date_filters)

		# Build an org-visibility filter for the Count annotation so that
		# article_count always reflects only visible articles (fixes sorting/
		# filtering by article_count leaking hidden-org data).
		has_org_scope = hasattr(self.request, "visible_org_ids")
		org_q = (
			Q(articles__teams__organization_id__in=self.request.visible_org_ids)
			if has_org_scope
			else Q()
		)

		# Add article count annotation for sorting
		if sort_by == "article_count":
			if count_filters:
				combined_q = (
					Q(**count_filters) & org_q if has_org_scope else Q(**count_filters)
				)
				queryset = queryset.annotate(
					article_count=Count("articles", filter=combined_q, distinct=True)
				).filter(article_count__gt=0)
			elif has_org_scope:
				queryset = queryset.annotate(
					article_count=Count("articles", filter=org_q, distinct=True)
				)
			else:
				queryset = queryset.annotate(
					article_count=Count("articles", distinct=True)
				)
		elif count_filters:
			# Even if not sorting by article_count, we still need to filter authors
			# to only those who have articles matching the criteria
			combined_q = (
				Q(**count_filters) & org_q if has_org_scope else Q(**count_filters)
			)
			queryset = queryset.annotate(
				article_count=Count("articles", filter=combined_q, distinct=True)
			).filter(article_count__gt=0)
		# NOTE: article_count/relevant_articles_count for the default (no
		# sort_by=article_count, no count_filters) case are deliberately NOT
		# annotated here. Annotating them on the full queryset forces
		# Postgres to evaluate a correlated subquery per author just to
		# compute pagination's COUNT(*) over the whole table. They're
		# attached to the paginated page only, in list() below.

		# Apply sorting
		if sort_by == "article_count":
			order_prefix = "-" if order == "desc" else ""
			queryset = queryset.order_by(f"{order_prefix}article_count", "author_id")
		else:
			# Default sorting by author_id or other fields
			order_prefix = "-" if order == "desc" else ""
			queryset = queryset.order_by(f"{order_prefix}{sort_by}")

		return queryset

	def list(self, request, *args, **kwargs):
		queryset = self.filter_queryset(self.get_queryset())
		page = self.paginate_queryset(queryset)
		target = page if page is not None else list(queryset)

		if target:
			visible_org_ids = getattr(request, "visible_org_ids", None)
			author_ids = [obj.author_id for obj in target]

			# article_count is already annotated on the page (via a cheap
			# Count()) whenever sort_by=article_count or count_filters were
			# applied in get_queryset(). Only fetch it here when it's
			# actually missing, so we don't pay for a correlated subquery
			# the serializer would just discard.
			needs_article_count = not hasattr(target[0], "article_count")

			annotations = {
				"_relevant_articles_count": author_articles_count_subquery(
					visible_org_ids, relevant_only=True
				),
			}
			if needs_article_count:
				annotations["_article_count"] = author_articles_count_subquery(
					visible_org_ids, relevant_only=False
				)

			counts_by_id = {
				row["author_id"]: row
				for row in Authors.objects.filter(author_id__in=author_ids)
				.annotate(**annotations)
				.values("author_id", *annotations.keys())
			}
			for obj in target:
				row = counts_by_id.get(obj.author_id, {})
				if needs_article_count:
					obj.article_count = row.get("_article_count", 0)
				obj.relevant_articles_count = row.get("_relevant_articles_count", 0)

		if page is not None:
			serializer = self.get_serializer(page, many=True)
			return self.get_paginated_response(serializer.data)
		serializer = self.get_serializer(target, many=True)
		return Response(serializer.data)

	@action(detail=False, methods=["get"])
	def by_team_subject(self, request):
		"""
		Get authors filtered by team and subject with article counts

		Parameters:
		- team_id (required): Team ID
		- subject_id (required): Subject ID
		- Additional filters from main queryset apply
		"""
		team_id = request.query_params.get("team_id")
		subject_id = request.query_params.get("subject_id")

		if not team_id or not subject_id:
			return Response(
				{"error": "Both team_id and subject_id are required"},
				status=status.HTTP_400_BAD_REQUEST,
			)

		queryset = self.filter_queryset(self.get_queryset())
		page = self.paginate_queryset(queryset)

		if page is not None:
			serializer = self.get_serializer(page, many=True)
			return self.get_paginated_response(serializer.data)

		serializer = self.get_serializer(queryset, many=True)
		return Response(serializer.data)

	@action(detail=False, methods=["get"])
	def by_team_category(self, request):
		"""
		Get authors filtered by team category with article counts

		Parameters:
		- team_id (required): Team ID
		- category_slug OR category_id (required): Team category slug or ID
		- Additional filters from main queryset apply
		"""
		team_id = request.query_params.get("team_id")
		category_slug = request.query_params.get("category_slug")
		category_id = request.query_params.get("category_id")

		if not team_id or (not category_slug and not category_id):
			return Response(
				{
					"error": "team_id and either category_slug or category_id are required"
				},
				status=status.HTTP_400_BAD_REQUEST,
			)

		queryset = self.filter_queryset(self.get_queryset())
		page = self.paginate_queryset(queryset)

		if page is not None:
			serializer = self.get_serializer(page, many=True)
			return self.get_paginated_response(serializer.data)

		serializer = self.get_serializer(queryset, many=True)
		return Response(serializer.data)

	@action(detail=True, methods=["get"])
	def coauthors(self, request, pk=None):
		"""Co-authors ordered by number of shared articles (desc)."""
		author = self.get_object()
		visible_org_ids = getattr(request, "visible_org_ids", None)

		shared = Articles.objects.filter(authors__author_id=author.author_id)
		if visible_org_ids is not None:
			shared = shared.filter(teams__organization_id__in=visible_org_ids)
		shared_ids = shared.values("article_id").distinct()

		coauthors_qs = (
			Authors.objects
			.filter(articles__article_id__in=shared_ids)
			.exclude(author_id=author.author_id)
			.annotate(
				shared_articles=Count(
					"articles", filter=Q(articles__article_id__in=shared_ids), distinct=True,
				),
				articles_count=author_articles_count_subquery(visible_org_ids),
				relevant_articles_count=author_articles_count_subquery(
					visible_org_ids, relevant_only=True,
				),
			)
			.order_by("-shared_articles", "author_id")
		)
		page = self.paginate_queryset(coauthors_qs)
		serializer = CoauthorSerializer(
			page if page is not None else coauthors_qs, many=True,
			context=self.get_serializer_context(),
		)
		if page is not None:
			return self.get_paginated_response(serializer.data)
		return Response(serializer.data)


###
# AUTHORIZATION
###
# The class below generates a new token at every successful call.
# But that token is not saved in the database and associated with the user.
# is that a problem?


class LoginView(TokenObtainPairView):
	permission_classes = (permissions.AllowAny,)


class ProtectedEndpointView(APIView):
	permission_classes = [permissions.IsAuthenticated]

	@extend_schema(responses=OpenApiTypes.OBJECT)
	def get(self, request):
		return Response({"message": "You have accessed the protected endpoint!"})


###
# TEAMS
###


class TeamsViewSet(OrgVisibilityMixin, viewsets.ReadOnlyModelViewSet):
	"""
	List all teams
	"""

	_org_filter_path = "organization_id"
	_org_filter_distinct = False
	queryset = Team.objects.all().order_by("id")
	serializer_class = TeamSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]


###
# ORGANISATIONS
###


class OrganizationsViewSet(viewsets.ReadOnlyModelViewSet):
	"""
	List organisations visible to the caller.

	Anonymous callers see only organisations where ``make_api_public=True``.
	Authenticated users and API-key holders see their own org; add
	``?include_public=true`` to also see public orgs.

	Detail endpoint (``/organizations/<id>/``) returns 404 rather than 403
	when the organisation is not visible (hide-existence rule).
	"""

	serializer_class = OrganizationSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]

	def get_queryset(self):
		qs = Organization.objects.all().order_by("id")
		if not hasattr(self.request, "visible_org_ids"):
			return qs
		return qs.filter(id__in=self.request.visible_org_ids)


###
# SUBJECTS
###

_SUBJECTS_ORDERING_PARAM = _ordering_param(
	["id", "subject_name", "team"],
	"Sort field, prefix with `-` for descending (default: id).",
)


@extend_schema_view(
	list=extend_schema(parameters=[_SUBJECTS_ORDERING_PARAM]),
)

class SubjectsViewSet(OrgVisibilityMixin, viewsets.ReadOnlyModelViewSet):
	"""
	✅ **PREFERRED ENDPOINT**: This is the main subjects endpoint that supports filtering options.

	List all subjects in the database with optional team filtering.

	# Query Parameters:
	- **team_id** - filter by team ID (replaces /teams/{id}/subjects/)
	- **search** - search in subject name and description
	- **ordering** - order by 'id', 'subject_name', 'team' (add '-' for reverse)

	# Examples:
	- Filter by team: `/subjects/?team_id=1`
	- Search subjects: `/subjects/?search=multiple`
	- Team filter with search: `/subjects/?team_id=1&search=sclerosis`
	- Order by name: `/subjects/?ordering=subject_name`
	"""

	_org_filter_path = "team__organization_id"
	_org_filter_distinct = False
	queryset = Subject.objects.all().order_by("id")
	serializer_class = SubjectsSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]
	filter_backends = [
		django_filters.DjangoFilterBackend,
		filters.SearchFilter,
		filters.OrderingFilter,
	]
	filterset_class = SubjectFilter
	search_fields = ["subject_name", "description"]
	ordering_fields = ["id", "subject_name", "team"]
	ordering = ["id"]


class CategoriesByTeamAndSubject(viewsets.ModelViewSet):
	"""
	List all categories for a specific team and subject combination
	"""

	serializer_class = CategorySerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]

	def get_queryset(self):
		team_id = self.kwargs.get("team_id")
		subject_id = self.kwargs.get("subject_id")
		if hasattr(self.request, "visible_org_ids"):
			if not Team.objects.filter(
				id=team_id, organization_id__in=self.request.visible_org_ids
			).exists():
				raise Http404
		# See CategoryViewSet.get_queryset / _category_through_count_subquery:
		# do NOT annotate both relations with Count(..., distinct=True) in
		# one query — that fans out to an articles x trials cross product
		# per category and spilled Postgres's hash aggregate to disk in
		# production.
		return (
			TeamCategory.objects.filter(team__id=team_id, subjects__id=subject_id)
			.annotate(
				article_count_annotated=_category_through_count_subquery(ArticleCategoryAssignment),
				trials_count_annotated=_category_through_count_subquery(TrialCategoryAssignment),
			)
			.order_by("-id")
		)


class ArticleSearchView(
	BodyParamsAsQueryParamsMixin, CSVStreamingMixin, BulkExportThrottleMixin, generics.ListAPIView
):
	"""
	Advanced search for articles by title and abstract (summary).

	This endpoint accepts both GET and POST requests with team_id and subject_id
	parameters, along with optional search parameters. Every filter on
	ArticleFilter works identically on both verbs — for GET, put them on the
	query string; for POST, put them in the JSON body. BodyParamsAsQueryParamsMixin
	merges the body into query_params before filtering, so both paths run
	through the same DjangoFilterBackend/OrderingFilter code; if the same filter
	key is set in both places, the query string wins. team_id and subject_id are
	the exception — they're forced from the body on POST (identity_params),
	both for get_queryset()'s own validation and for ArticleFilter's identically
	named fields, so a mismatched query-string team_id/subject_id has no effect
	at all rather than silently narrowing the filterset against a different
	team/subject than the one validated.

	Parameters (can be sent as query params for GET or in request body for POST):
	- title: Search only in title field
	- summary: Search only in summary/abstract field
	- search: Search in both title and summary fields
	- team_id: Required - Team ID to filter articles by (must be provided)
	- subject_id: Required - Subject ID to filter articles by (must be provided)
	- page: Page number for pagination (default: 1)
	- page_size: Number of results per page (default: 10, max: 100)
	- all_results: Set to 'true' to retrieve all results without pagination (useful for CSV export)
	- ordering: Order results by field (e.g., -discovery_date, -published_date, title, article_id)

	Every other ArticleFilter field also applies here, e.g.
	published_date_after / published_date_before, relevant, subjects,
	open_access:
	- Published in 2023: /articles/search/?team_id=1&subject_id=1&published_date_after=2023-01-01&published_date_before=2023-12-31

	Prefer GET where practical — it's cacheable and linkable. POST exists for
	`search` strings with long boolean expressions that would exceed practical
	URL length limits.

	Results are ordered by discovery date (newest first) by default.

	To download all search results as CSV, add format=csv and all_results=true to the query parameters.
	Example: /articles/search/?team_id=1&subject_id=1&search=covid&format=csv&all_results=true
	"""

	serializer_class = ArticleSerializer
	permission_classes = [
		permissions.AllowAny
	]  # Allow access to anyone since we require team_id and subject_id
	# `search` is parsed by build_search_q inside ArticleFilter.filter_search,
	# for both GET query params and POST body (merged in by
	# BodyParamsAsQueryParamsMixin). DRF's SearchFilter is omitted so it can't
	# AND a naive token match on top and break boolean queries. See
	# ArticleViewSet.
	filter_backends = [
		django_filters.DjangoFilterBackend,
		filters.OrderingFilter,
	]
	filterset_class = ArticleFilter
	ordering_fields = ["discovery_date", "published_date", "title", "article_id"]
	ordering = ["-discovery_date"]  # Default ordering by newest first
	pagination_class = FlexiblePagination
	http_method_names = ["get", "post"]  # Support both GET and POST
	# team_id/subject_id are also ArticleFilter fields; force them from the body
	# on POST so a mismatched query-string value can't leak into the filterset.
	# See BodyParamsAsQueryParamsMixin.
	identity_params = frozenset({"team_id", "subject_id"})

	def get_queryset(self):
		# This method handles both GET and POST requests
		if self.request.method == "GET":
			params = self.request.query_params
		else:
			params = self.request.data

		# Extract required parameters
		team_id = params.get("team_id")
		subject_id = params.get("subject_id")

		# Validate required parameters
		if not team_id or not subject_id:
			return Articles.objects.none()

		# Cast to int early — non-numeric values get a 404 rather than a 500.
		try:
			team_id = int(team_id)
			subject_id = int(subject_id)
		except (TypeError, ValueError):
			raise Http404

		# Visibility check: hidden teams return 404 (before the broad except block)
		if hasattr(self.request, "visible_org_ids"):
			if not Team.objects.filter(
				id=team_id, organization_id__in=self.request.visible_org_ids
			).exists():
				raise Http404

		try:
			# Filter via a correlated Exists() subquery instead of joining the
			# teams/subjects M2M tables and calling .distinct() on the outer
			# queryset — DISTINCT-ing every serialized column defeats DRF's
			# paginator COUNT(*) at scale. See OrgVisibilityMixin.get_queryset.
			match_subq = Articles.objects.filter(
				pk=OuterRef("pk"), teams__id=team_id, subjects__id=subject_id
			)
			queryset = Articles.objects.filter(Exists(match_subq))

			# Prefetch related objects to avoid N+1 queries
			queryset = queryset.prefetch_related(
				Prefetch(
					"ml_predictions_detail",
					queryset=_latest_ml_predictions_queryset(),
				),
				"authors",
				"teams",
				Prefetch("subjects", queryset=Subject.objects.select_related("team")),
				"sources",
				"team_categories",
				Prefetch(
					"article_subject_relevances",
					queryset=ArticleSubjectRelevance.objects.select_related("subject__team"),
				),
				Prefetch(
					"trial_references",
					queryset=ArticleTrialReference.objects.select_related("trial"),
				),
			)

			# Prefetch the caller-org's ArticleOrgContent so the serializer's
			# per-org fields don't issue one query per article. Mirrors
			# ArticleViewSet.get_queryset.
			org = _resolve_per_org_fields_org(self.request)
			if org is not None:
				queryset = queryset.prefetch_related(
					Prefetch(
						"org_contents",
						queryset=ArticleOrgContent.objects.filter(organization=org),
						to_attr="_prefetched_org_contents",
					)
				)

			return queryset
		except (AttributeError, TypeError, ValueError) as e:
			# Malformed search params (e.g. a non-string title/summary/search
			# from a JSON POST body): return no matches, but log it so a genuine
			# bug can't hide as an empty result. Anything unexpected propagates.
			logging.getLogger(__name__).warning(
				"ArticleSearchView: ignoring malformed search params (%s)", e
			)
			return Articles.objects.none()

	@extend_schema(
		request={
			"application/json": filterset_request_schema(
				ArticleFilter,
				required=["team_id", "subject_id"],
				extra_description=(
					"Every ArticleFilter field is accepted here (identical semantics "
					"to the matching GET query parameter — see /articles/). "
					"team_id and subject_id are required."
				),
			),
		},
		responses={200: OpenApiTypes.OBJECT, 400: ErrorResponseSerializer, 404: ErrorResponseSerializer},
	)
	def post(self, request, *args, **kwargs):
		# For POST requests, validate required parameters
		team_id = request.data.get("team_id")
		subject_id = request.data.get("subject_id")

		if not team_id or not subject_id:
			return Response(
				{"error": "Missing required parameters: team_id, subject_id"},
				status=400,
			)

		try:
			team_id = int(team_id)
			subject_id = int(subject_id)
		except (TypeError, ValueError):
			return Response(
				{"error": "team_id and subject_id must be integers"}, status=400
			)

		try:
			# Check if team and subject exist
			Team.objects.get(id=team_id)
			Subject.objects.get(id=subject_id, team_id=team_id)
		except Team.DoesNotExist:
			return Response({"error": f"Team with ID {team_id} not found"}, status=404)
		except Subject.DoesNotExist:
			return Response(
				{
					"error": f"Subject with ID {subject_id} not found or does not belong to team {team_id}"
				},
				status=404,
			)

		# Delegate to the list method which uses get_queryset
		return self.list(request, *args, **kwargs)

	def get(self, request, *args, **kwargs):
		# Validate required parameters for GET requests
		team_id = request.query_params.get("team_id")
		subject_id = request.query_params.get("subject_id")

		if not team_id or not subject_id:
			return Response(
				{"error": "Missing required parameters: team_id, subject_id"},
				status=400,
			)

		try:
			team_id = int(team_id)
			subject_id = int(subject_id)
		except (TypeError, ValueError):
			return Response(
				{"error": "team_id and subject_id must be integers"}, status=400
			)

		try:
			# Check if team and subject exist
			Team.objects.get(id=team_id)
			Subject.objects.get(id=subject_id, team_id=team_id)
		except Team.DoesNotExist:
			return Response({"error": f"Team with ID {team_id} not found"}, status=404)
		except Subject.DoesNotExist:
			return Response(
				{
					"error": f"Subject with ID {subject_id} not found or does not belong to team {team_id}"
				},
				status=404,
			)

		# Delegate to the list method
		return self.list(request, *args, **kwargs)


class TrialSearchView(
	BodyParamsAsQueryParamsMixin, CSVStreamingMixin, BulkExportThrottleMixin, generics.ListAPIView
):
	"""
	Advanced search for clinical trials by title, summary, and recruitment status.

	This endpoint accepts both GET and POST requests with team_id and subject_id
	parameters, along with optional search parameters. Every filter on
	TrialFilter works identically on both verbs — for GET, put them on the query
	string; for POST, put them in the JSON body. BodyParamsAsQueryParamsMixin
	merges the body into query_params before filtering, so both paths run
	through the same DjangoFilterBackend/OrderingFilter code; if the same filter
	key is set in both places, the query string wins. team_id and subject_id are
	the exception — they're forced from the body on POST (identity_params),
	both for get_queryset()'s own validation and for TrialFilter's identically
	named fields, so a mismatched query-string team_id/subject_id has no effect
	at all rather than silently narrowing the filterset against a different
	team/subject than the one validated.

	Parameters (can be sent as query params for GET or in request body for POST):
	- title: Search only in title field
	- summary: Search only in summary/abstract field
	- search: Search in both title and summary fields
	- status: Filter by recruitment status (e.g., 'Recruiting', 'Completed')
	- team_id: Required - Team ID to filter trials by (must be provided)
	- subject_id: Required - Subject ID to filter trials by (must be provided)
	- page: Page number for pagination (default: 1)
	- page_size: Number of results per page (default: 10, max: 100)
	- all_results: Set to 'true' to retrieve all results without pagination (useful for CSV export)
	- ordering: Order results by field (e.g., -discovery_date, -published_date, title, trial_id, -last_updated)

	Every other TrialFilter field also applies here, e.g.
	date_registration_after / date_registration_before, has_results,
	phase_normalized, country, sponsor_id:
	- Registered in 2024: /trials/search/?team_id=1&subject_id=1&date_registration_after=2024-01-01&date_registration_before=2024-12-31

	Prefer GET where practical — it's cacheable and linkable. POST exists for
	`search` strings with long boolean expressions that would exceed practical
	URL length limits.

	Results are ordered by discovery date (newest first) by default.

	To download all search results as CSV, add format=csv and all_results=true to the query parameters.
	Example: /trials/search/?team_id=1&subject_id=1&search=covid&format=csv&all_results=true
	"""

	serializer_class = TrialSerializer
	permission_classes = [
		permissions.AllowAny
	]  # Allow access to anyone since we require team_id and subject_id
	# `search` is parsed by build_search_q inside TrialFilter.filter_search, for
	# both GET query params and POST body (merged in by
	# BodyParamsAsQueryParamsMixin). DRF's SearchFilter is omitted so it can't
	# AND a naive token match on top and break boolean queries. See
	# ArticleViewSet.
	filter_backends = [
		django_filters.DjangoFilterBackend,
		filters.OrderingFilter,
	]
	filterset_class = TrialFilter
	ordering_fields = [
		"discovery_date",
		"published_date",
		"title",
		"trial_id",
		"last_updated",
	]
	ordering = ["-discovery_date"]  # Default ordering by newest first
	pagination_class = FlexiblePagination
	http_method_names = ["get", "post"]  # Support both GET and POST
	# team_id/subject_id are also TrialFilter fields; force them from the body
	# on POST so a mismatched query-string value can't leak into the filterset.
	# See BodyParamsAsQueryParamsMixin.
	identity_params = frozenset({"team_id", "subject_id"})

	def get_queryset(self):
		# This method handles both GET and POST requests
		if self.request.method == "GET":
			params = self.request.query_params
		else:
			params = self.request.data

		# Extract required parameters
		team_id = params.get("team_id")
		subject_id = params.get("subject_id")

		# Validate required parameters
		if not team_id or not subject_id:
			return Trials.objects.none()

		# Cast to int early — non-numeric values get a 404 rather than a 500.
		try:
			team_id = int(team_id)
			subject_id = int(subject_id)
		except (TypeError, ValueError):
			raise Http404

		# Visibility check: hidden teams return 404 (before the broad except block)
		if hasattr(self.request, "visible_org_ids"):
			if not Team.objects.filter(
				id=team_id, organization_id__in=self.request.visible_org_ids
			).exists():
				raise Http404

		try:
			# Check if team and subject exist
			team = Team.objects.get(id=team_id)
			subject = Subject.objects.get(id=subject_id, team=team)
		except (Team.DoesNotExist, Subject.DoesNotExist):
			return Trials.objects.none()

		# Filter via a correlated Exists() subquery instead of joining the
		# teams/subjects M2M tables and calling .distinct() on the outer
		# queryset — DISTINCT-ing every serialized column defeats DRF's
		# paginator COUNT(*) at scale. See OrgVisibilityMixin.get_queryset.
		match_subq = Trials.objects.filter(pk=OuterRef("pk"), teams=team, subjects=subject)
		queryset = Trials.objects.filter(Exists(match_subq))

		# Prefetch related objects to avoid N+1 queries
		queryset = queryset.prefetch_related(
			"sources", "team_categories", "article_references__article", "trial_countries"
		)

		# Prefetch the caller-org's TrialOrgContent so the serializer's
		# per-org fields don't issue one query per trial. Mirrors
		# TrialViewSet.get_queryset.
		org = _resolve_per_org_fields_org(self.request)
		if org is not None:
			queryset = queryset.prefetch_related(
				Prefetch(
					"org_contents",
					queryset=TrialOrgContent.objects.filter(organization=org),
					to_attr="_prefetched_org_contents",
				)
			)

		return queryset

	@extend_schema(
		request={
			"application/json": filterset_request_schema(
				TrialFilter,
				required=["team_id", "subject_id"],
				extra_description=(
					"Every TrialFilter field is accepted here (identical semantics "
					"to the matching GET query parameter — see /trials/). "
					"team_id and subject_id are required."
				),
			),
		},
		responses={200: OpenApiTypes.OBJECT, 400: ErrorResponseSerializer, 404: ErrorResponseSerializer},
	)
	def post(self, request, *args, **kwargs):
		# For POST requests, validate required parameters
		team_id = request.data.get("team_id")
		subject_id = request.data.get("subject_id")

		if not team_id or not subject_id:
			return Response(
				{"error": "Missing required parameters: team_id, subject_id"},
				status=400,
			)

		try:
			team_id = int(team_id)
			subject_id = int(subject_id)
		except (TypeError, ValueError):
			return Response(
				{"error": "team_id and subject_id must be integers"}, status=400
			)

		try:
			# Check if team and subject exist
			Team.objects.get(id=team_id)
			Subject.objects.get(id=subject_id, team_id=team_id)
		except Team.DoesNotExist:
			return Response({"error": f"Team with ID {team_id} not found"}, status=404)
		except Subject.DoesNotExist:
			return Response(
				{
					"error": f"Subject with ID {subject_id} not found or does not belong to team {team_id}"
				},
				status=404,
			)

		# Delegate to the list method which uses get_queryset
		return self.list(request, *args, **kwargs)

	def get(self, request, *args, **kwargs):
		# Validate required parameters for GET requests
		team_id = request.query_params.get("team_id")
		subject_id = request.query_params.get("subject_id")

		if not team_id or not subject_id:
			return Response(
				{"error": "Missing required parameters: team_id, subject_id"},
				status=400,
			)

		try:
			team_id = int(team_id)
			subject_id = int(subject_id)
		except (TypeError, ValueError):
			return Response(
				{"error": "team_id and subject_id must be integers"}, status=400
			)

		try:
			# Check if team and subject exist
			Team.objects.get(id=team_id)
			Subject.objects.get(id=subject_id, team_id=team_id)
		except Team.DoesNotExist:
			return Response({"error": f"Team with ID {team_id} not found"}, status=404)
		except Subject.DoesNotExist:
			return Response(
				{
					"error": f"Subject with ID {subject_id} not found or does not belong to team {team_id}"
				},
				status=404,
			)

		# Delegate to the list method
		return self.list(request, *args, **kwargs)


class AuthorSearchView(BodyParamsAsQueryParamsMixin, generics.ListAPIView):
	"""
	Advanced search for authors by full name.

	Supports both GET and POST requests with required team_id and subject_id
	parameters. Filtering by full_name is case-insensitive and allows partial
	matches. Pagination and CSV export options mirror the article search
	endpoint.
	"""

	serializer_class = AuthorSerializer
	permission_classes = [permissions.AllowAny]
	filter_backends = [filters.SearchFilter, django_filters.DjangoFilterBackend]
	filterset_class = AuthorFilter
	search_fields = ["full_name"]
	pagination_class = FlexiblePagination
	http_method_names = ["get", "post"]
	# team_id/subject_id/full_name are also AuthorFilter fields (team_id/
	# subject_id are read directly in get_queryset for scoping, not via the
	# filterset — but the filterset has no team_id/subject_id field so those
	# two are moot here; full_name is the one that matters). Force full_name
	# from the body on POST so a mismatched query-string value can't leak into
	# the filterset. See BodyParamsAsQueryParamsMixin.
	identity_params = frozenset({"team_id", "subject_id", "full_name"})

	def _check_team_visibility(self, team_id):
		"""Raise Http404 if team_id is not in the caller's visible orgs."""
		if hasattr(self.request, "visible_org_ids"):
			if not Team.objects.filter(
				id=team_id, organization_id__in=self.request.visible_org_ids
			).exists():
				raise Http404

	def get_queryset(self):
		params = (
			self.request.query_params
			if self.request.method == "GET"
			else self.request.data
		)

		team_id = params.get("team_id")
		subject_id = params.get("subject_id")

		if not team_id or not subject_id:
			return Authors.objects.none()

		try:
			author_ids = (
				Articles.objects.filter(teams__id=team_id, subjects__id=subject_id)
				.values_list("authors", flat=True)
				.distinct()
			)

			queryset = Authors.objects.filter(author_id__in=author_ids).order_by(
				"author_id"
			)

			full_name = params.get("full_name")
			if full_name:
				# URL decode the full_name parameter to handle %20 spaces and other encoded characters
				from urllib.parse import unquote

				full_name = unquote(full_name)
				# Use the new full_name database field for more efficient searching
				queryset = queryset.filter(full_name__icontains=full_name)

			queryset = queryset.annotate(
				article_count=author_articles_count_subquery(
					getattr(self.request, "visible_org_ids", None), relevant_only=False
				),
				relevant_articles_count=author_articles_count_subquery(
					getattr(self.request, "visible_org_ids", None), relevant_only=True,
				)
			)

			return queryset
		except (AttributeError, TypeError, ValueError) as e:
			# Malformed search params (e.g. a non-string full_name from a JSON
			# POST body): return no matches, but log it so a genuine bug can't
			# hide as an empty result. Anything unexpected propagates.
			logging.getLogger(__name__).warning(
				"AuthorSearchView: ignoring malformed search params (%s)", e
			)
			return Authors.objects.none()

	@extend_schema(
		request={
			"application/json": {
				"type": "object",
				"properties": {
					"team_id": {"type": "integer", "description": "Required. Team ID to scope authors by."},
					"subject_id": {"type": "integer", "description": "Required. Subject ID to scope authors by."},
					**filterset_request_schema(AuthorFilter)["properties"],
				},
				"required": ["team_id", "subject_id"],
			},
		},
		responses={200: OpenApiTypes.OBJECT, 400: ErrorResponseSerializer, 404: ErrorResponseSerializer},
	)
	def post(self, request, *args, **kwargs):
		# For POST requests, validate required parameters
		team_id = request.data.get("team_id")
		subject_id = request.data.get("subject_id")

		if not team_id or not subject_id:
			return Response(
				{"error": "Missing required parameters: team_id, subject_id"},
				status=400,
			)

		try:
			# Check if team and subject exist
			Team.objects.get(id=team_id)
			Subject.objects.get(id=subject_id, team_id=team_id)
		except Team.DoesNotExist:
			return Response({"error": f"Team with ID {team_id} not found"}, status=404)
		except Subject.DoesNotExist:
			return Response(
				{
					"error": f"Subject with ID {subject_id} not found or does not belong to team {team_id}"
				},
				status=404,
			)

		self._check_team_visibility(team_id)
		return self.list(request, *args, **kwargs)

	def get(self, request, *args, **kwargs):
		# Validate required parameters for GET requests
		team_id = request.query_params.get("team_id")
		subject_id = request.query_params.get("subject_id")

		if not team_id or not subject_id:
			return Response(
				{"error": "Missing required parameters: team_id, subject_id"},
				status=400,
			)

		try:
			# Check if team and subject exist
			Team.objects.get(id=team_id)
			Subject.objects.get(id=subject_id, team_id=team_id)
		except Team.DoesNotExist:
			return Response({"error": f"Team with ID {team_id} not found"}, status=404)
		except Subject.DoesNotExist:
			return Response(
				{
					"error": f"Subject with ID {subject_id} not found or does not belong to team {team_id}"
				},
				status=404,
			)

		self._check_team_visibility(team_id)
		# Delegate to the list method
		return self.list(request, *args, **kwargs)


###
# STATS
###


class StatsView(APIView):
	"""
	Returns aggregate statistics about the data in the system.

	Filters
	-------
	?team=1,2,3
	    Scope to one or more teams (comma-separated integer IDs).
	?organization=1,2  (alias: ?org=)
	    Scope to one or more organisations (comma-separated integer IDs).
	    When combined with ?team=, the effective scope is the intersection:
	    teams that belong to the requested org(s).
	?subject=1,2
	    Scope to one or more subjects (comma-separated integer IDs, OR/union
	    semantics). Adds a ``by_subject`` breakdown to the payload, listing
	    every in-scope subject (including zero-count ones) with its own
	    articles/trials/authors/sources counts. Per-subject subscriber counts
	    are deliberately omitted — see docs/03-api-and-rss-feeds.md.

	All three params are validated against ``request.visible_org_ids``
	(set by VisibleOrgMiddleware). A team, org, or subject that is not visible
	to the caller returns 404 so that existence is not leaked. When the
	middleware attribute is absent (management commands, tests bypassing
	middleware) the visibility check is skipped and the params still narrow
	the queryset.

	A subject that is visible but outside the requested ``?team=``/``?organization=``
	scope does not 404 — it returns a well-formed, all-zero payload, matching
	the existing ``?team=`` + ``?organization=`` intersection behaviour.

	Results are short-lived cached (STATS_CACHE_TTL seconds, default 600) using
	Django's database cache so all gunicorn workers share the same value.
	"""

	permission_classes = [permissions.AllowAny]

	@extend_schema(
		parameters=[
			OpenApiParameter(
				"team",
				OpenApiTypes.STR,
				OpenApiParameter.QUERY,
				description="Scope to one or more teams (comma-separated integer IDs), e.g. ?team=1,2,3.",
			),
			OpenApiParameter(
				"organization",
				OpenApiTypes.STR,
				OpenApiParameter.QUERY,
				description="Scope to one or more organisations (comma-separated integer IDs). Alias: org.",
			),
			OpenApiParameter(
				"org",
				OpenApiTypes.STR,
				OpenApiParameter.QUERY,
				description="Alias for organization.",
			),
			OpenApiParameter(
				"subject",
				OpenApiTypes.STR,
				OpenApiParameter.QUERY,
				description=(
					"Scope to one or more subjects (comma-separated integer IDs, OR "
					"semantics). Adds a by_subject breakdown to the payload."
				),
			),
		],
		responses={200: GlobalStatsSerializer, 400: ErrorResponseSerializer, 404: ErrorResponseSerializer},
	)
	def get(self, request):
		from urllib.parse import urlparse
		from subscriptions.models import Subscribers

		visible_org_ids = getattr(request, "visible_org_ids", None)

		# --- Parse ?team= -----------------------------------------------
		team_param = request.query_params.get("team", None)
		team_ids = None
		if team_param:
			try:
				team_ids = [int(t.strip()) for t in team_param.split(",") if t.strip()]
			except ValueError:
				return Response(
					{
						"error": "Invalid team parameter. Expected integer or comma-separated integers."
					},
					status=status.HTTP_400_BAD_REQUEST,
				)

		# --- Parse ?organization= (alias ?org=) -------------------------
		org_param = request.query_params.get(
			"organization"
		) or request.query_params.get("org")
		org_ids = None
		if org_param:
			try:
				org_ids = [int(o.strip()) for o in org_param.split(",") if o.strip()]
			except ValueError:
				return Response(
					{
						"error": "Invalid organization parameter. Expected integer or comma-separated integers."
					},
					status=status.HTTP_400_BAD_REQUEST,
				)

		# --- Parse ?subject= ---------------------------------------------
		subject_param = request.query_params.get("subject")
		subject_ids = None
		if subject_param:
			try:
				subject_ids = [
					int(s.strip()) for s in subject_param.split(",") if s.strip()
				]
			except ValueError:
				return Response(
					{
						"error": "Invalid subject parameter. Expected integer or comma-separated integers."
					},
					status=status.HTTP_400_BAD_REQUEST,
				)

		# --- Visibility validation (no extra DB queries for org check) --
		if org_ids and visible_org_ids is not None:
			if not set(org_ids).issubset(visible_org_ids):
				raise Http404

		# Team-visibility check against the requested team_ids.
		#
		# When ?organization= is NOT given, effective_org_ids below resolves
		# to exactly visible_org_ids, so a standalone
		# `Team.objects.filter(id__in=team_ids, organization_id__in=visible_org_ids)`
		# here would be the identical predicate to the team_id_list query run
		# a few lines down — i.e. the same SELECT executed twice. We skip it
		# in that case and instead derive visibility from team_id_list's
		# length once it's resolved (see below): any requested team missing
		# from the result was invisible to the caller.
		#
		# When ?organization= IS given, effective_org_ids narrows to org_ids
		# (a subset of visible_org_ids), which is NOT equivalent: a team that
		# is visible to the caller but simply doesn't belong to the requested
		# org(s) must yield a zero count, not a 404 (see
		# OrgAndTeamIntersectionTest.test_team_not_in_org_returns_zero_not_404).
		# That distinction requires the broader visible_org_ids check to run
		# as its own query, independent of the org-narrowed scoping query.
		if team_ids and visible_org_ids is not None and org_ids is not None:
			visible = Team.objects.filter(
				id__in=team_ids, organization_id__in=visible_org_ids
			)
			if visible.count() != len(set(team_ids)):
				raise Http404

		# --- Resolve effective org scope --------------------------------
		# org_ids already validated as a subset of visible_org_ids above,
		# so the effective set is exactly org_ids when given.
		if org_ids is not None:
			effective_org_ids = set(org_ids)
		else:
			effective_org_ids = visible_org_ids  # may be None (middleware absent)

		# --- Resolve in-scope team IDs (one query, reused for all counts)
		# When there is no scoping at all, fall through to .objects.all()
		# to preserve teamless articles/trials in the unscoped count.
		fully_unscoped = effective_org_ids is None and not team_ids

		if not fully_unscoped:
			teams_qs = Team.objects.all()
			if effective_org_ids is not None:
				teams_qs = teams_qs.filter(organization_id__in=effective_org_ids)
			if team_ids:
				teams_qs = teams_qs.filter(id__in=team_ids)
			team_id_list = list(teams_qs.values_list("id", flat=True))
		else:
			team_id_list = None

		# Deferred team-visibility check for the common (?organization=
		# absent) case: team_id_list above already ran the
		# id__in=team_ids / organization_id__in=visible_org_ids predicate,
		# so reuse its result instead of re-querying.
		if (
			team_ids
			and visible_org_ids is not None
			and org_ids is None
			and len(team_id_list) != len(set(team_ids))
		):
			raise Http404

		# --- Resolve subjects (one query: visibility + scope + roster) ---
		# When ?subject= is absent, this is the in-scope roster used for
		# by_subject; when present, it's looked up WITHOUT the team narrowing
		# (applied in Python below), because the team narrowing is what
		# distinguishes a 404 from an all-zero payload (decision 7 in
		# docs/spec-stats-subject-filter.md).
		subj_qs = Subject.objects.all()
		if visible_org_ids is not None:
			subj_qs = subj_qs.filter(team__organization_id__in=visible_org_ids)
		if subject_ids is not None:
			subj_qs = subj_qs.filter(id__in=subject_ids)
		elif team_id_list is not None:
			subj_qs = subj_qs.filter(team_id__in=team_id_list)
		visible_subjects = list(
			subj_qs.values("id", "subject_name", "team_id").order_by("subject_name")
		)

		if (
			subject_ids is not None
			and visible_org_ids is not None
			and len(visible_subjects) != len(set(subject_ids))
		):
			raise Http404

		if subject_ids is not None:
			effective_subject_ids = [
				s["id"]
				for s in visible_subjects
				if team_id_list is None or s["team_id"] in team_id_list
			]
			by_subject_roster = [
				s
				for s in visible_subjects
				if team_id_list is None or s["team_id"] in team_id_list
			]
		else:
			effective_subject_ids = None
			by_subject_roster = visible_subjects

		apply_subject = effective_subject_ids is not None

		# --- Cache lookup -----------------------------------------------
		# team_part is also the namespace for the per-subject-row cache
		# (layer 2, see the by_subject facet below) — reused as-is so a row
		# computed under one team scope is never served under another.
		team_part = (
			"all"
			if team_id_list is None
			else ",".join(str(i) for i in sorted(team_id_list))
		)
		cache_key = (
			"stats:"
			+ team_part
			+ ":subj:"
			+ (
				"all"
				if subject_ids is None
				else ",".join(str(i) for i in sorted(set(subject_ids)))
			)
		)
		cached = cache.get(cache_key)
		if cached is not None:
			return Response(cached)

		# --- Counts (single join per queryset) --------------------------
		if fully_unscoped and not apply_subject:
			articles_count = Articles.objects.count()
			trials_count = Trials.objects.count()
			authors_count = Authors.objects.count()
			subscribers_count = Subscribers.objects.filter(active=True).count()
			sources_qs = Sources.objects.all()
		else:
			# .order_by().values(pk).distinct().count() instead of .distinct().count():
			# the latter runs DISTINCT over every column (incl. Authors.biography,
			# a free-text field), forcing Postgres to dedupe full rows across the
			# join instead of just primary keys — ~5x slower at prod scale. The
			# .order_by() clears Meta.ordering (Articles orders by -discovery_date
			# by default), which would otherwise sneak a second column into the
			# DISTINCT and defeat the point.
			#
			# The team join is kept even when only ?subject= is given: an
			# article belonging solely to a non-visible team could carry a
			# subject the caller can see, and filtering on subject alone
			# would leak it into the count.
			articles_qs = Articles.objects.all()
			if team_id_list is not None:
				articles_qs = articles_qs.filter(teams__in=team_id_list)
			if apply_subject:
				articles_qs = articles_qs.filter(subjects__in=effective_subject_ids)
			articles_count = (
				articles_qs.order_by().values("article_id").distinct().count()
			)

			trials_qs = Trials.objects.all()
			if team_id_list is not None:
				trials_qs = trials_qs.filter(teams__in=team_id_list)
			if apply_subject:
				trials_qs = trials_qs.filter(subjects__in=effective_subject_ids)
			trials_count = (
				trials_qs.order_by().values("trial_id").distinct().count()
			)

			# Exists/OuterRef, not chained .filter() calls: Authors has no direct
			# team/subject columns, so team and subject can only be pinned to the
			# SAME article via a correlated subquery. Two chained
			# .filter(articles__teams__in=...).filter(articles__subjects__in=...)
			# calls each re-join Authors->Articles independently, so an author
			# with one article in the team and an unrelated article carrying the
			# subject would be counted — wrong. This also drops the DISTINCT
			# entirely (Exists is a boolean per author, not a fan-out join).
			authors_qs = Authors.objects.all()
			if team_id_list is not None or apply_subject:
				article_match = Articles.objects.filter(authors=OuterRef("pk"))
				if team_id_list is not None:
					article_match = article_match.filter(teams__in=team_id_list)
				if apply_subject:
					article_match = article_match.filter(
						subjects__in=effective_subject_ids
					)
				authors_qs = authors_qs.filter(Exists(article_match))
			authors_count = authors_qs.count()

			# A single filter() call, not chained ones: Subscribers reaches both
			# team and subject through the same subscriptions (Lists) relation,
			# so both predicates must land on one filter() call to force a single
			# join — a subscriber on List A (team T, no subject) and List B
			# (other team, this subject) must NOT count for ?team=T&subject=S.
			sub_filters = {"active": True}
			if team_id_list is not None:
				sub_filters["subscriptions__team__in"] = team_id_list
			if apply_subject:
				sub_filters["subscriptions__subjects__in"] = effective_subject_ids
			subscribers_count = (
				Subscribers.objects.filter(**sub_filters)
				.order_by()
				.values("subscriber_id")
				.distinct()
				.count()
			)

			# Sources has a direct FK to Team and to Subject — no distinct needed.
			sources_qs = Sources.objects.all()
			if team_id_list is not None:
				sources_qs = sources_qs.filter(team_id__in=team_id_list)
			if apply_subject:
				sources_qs = sources_qs.filter(subject_id__in=effective_subject_ids)

		# --- Sources domain aggregation (single pass) -------------------
		def extract_domain(url):
			if not url:
				return None
			try:
				return urlparse(url).netloc or None
			except Exception:
				return None

		source_data = list(sources_qs.values("link", "source_for", "subject_id"))

		all_domains = set()
		type_domains = {}
		domain_feed_count = {}
		subject_domains = {}  # subject_id -> set of netlocs, for by_subject sources
		for s in source_data:
			d = extract_domain(s["link"])
			if d:
				all_domains.add(d)
				type_domains.setdefault(s["source_for"], set()).add(d)
				domain_feed_count[d] = domain_feed_count.get(d, 0) + 1
				if s["subject_id"] is not None:
					subject_domains.setdefault(s["subject_id"], set()).add(d)

		sources_by_type = {k: len(v) for k, v in type_domains.items()}
		sources_by_domain = sorted(
			[{"domain": d, "count": c} for d, c in domain_feed_count.items()],
			key=lambda x: x["count"],
			reverse=True,
		)

		# --- by_subject facet --------------------------------------------
		# Per-(team scope, subject) row cache (layer 2), separate from the
		# whole-payload cache (layer 1, keyed by cache_key above): the same
		# row is identical no matter which OTHER subjects were requested
		# alongside it, so a warm row is reused across every ?subject=
		# combination over the same team scope instead of being recomputed
		# per combination. `sources` and `subject_name` are deliberately
		# left out of the cached value — both come for free from data this
		# call already fetched (subject_domains / the roster), so caching
		# them would only add a staleness surface for no gain.
		by_subject = []
		if by_subject_roster:
			row_keys = {
				s["id"]: f"stats:subjrow:{team_part}:{s['id']}"
				for s in by_subject_roster
			}
			cached_rows = cache.get_many(list(row_keys.values()))
			missing_ids = [
				sid for sid, key in row_keys.items() if key not in cached_rows
			]

			if missing_ids:
				subj_articles = self._subject_counts(Articles, missing_ids, team_id_list)
				subj_trials = self._subject_counts(Trials, missing_ids, team_id_list)
				subj_authors = self._subject_counts(
					Articles, missing_ids, team_id_list, count_field="articles__authors"
				)
				fresh_rows = {
					row_keys[sid]: {
						"articles": subj_articles.get(sid, 0),
						"trials": subj_trials.get(sid, 0),
						"authors": subj_authors.get(sid, 0),
					}
					for sid in missing_ids
				}
				cache.set_many(fresh_rows, settings.STATS_CACHE_TTL)
				cached_rows.update(fresh_rows)

			by_subject = [
				{
					"subject_id": s["id"],
					"subject_name": s["subject_name"],
					**cached_rows[row_keys[s["id"]]],
					"sources": len(subject_domains.get(s["id"], ())),
				}
				for s in by_subject_roster
			]

		payload = {
			"articles": articles_count,
			"trials": trials_count,
			"subscribers": subscribers_count,
			"authors": authors_count,
			"sources": {
				"total": len(all_domains),
				"by_type": sources_by_type,
				"by_domain": sources_by_domain,
			},
			"by_subject": by_subject,
		}
		cache.set(cache_key, payload, settings.STATS_CACHE_TTL)
		return Response(payload)

	@staticmethod
	def _subject_counts(model, subject_ids, team_id_list, count_field=None):
		"""Per-subject distinct count of ``count_field`` (default: model pk).

		Aggregates off ``model.subjects.through`` so each of articles/trials/
		authors is one grouped query, regardless of caller-side filtering.
		"""
		through = model.subjects.through
		src = model._meta.model_name  # "articles" / "trials"
		qs = through.objects.filter(subject_id__in=subject_ids)
		if team_id_list is not None:
			qs = qs.filter(**{f"{src}__teams__in": team_id_list})
		rows = qs.values("subject_id").annotate(
			count=Count(count_field or f"{src}_id", distinct=True)
		)
		return {r["subject_id"]: r["count"] for r in rows}
