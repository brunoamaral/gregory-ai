"""
api/editor_views.py

The ``/editor/`` routes: what the MCP server calls on behalf of a signed-in
editor (MCP-AUTH-PLAN.md). Not for browsers and not for API keys. Every route
here is authenticated by ``EditorServiceAuthentication``: the service credential
(``GREGORY_MCP_SERVICE_KEY``) plus the verified editor and the one site the
request is for. nginx also keeps ``/editor/`` off the public internet; Django
does not rely on that.

Three rules hold for every view:

- The site is the token's, never a parameter. An article, trial, subject or
  category outside that site's ``scope_subjects`` is "not found", the same
  answer as one that doesn't exist, so its existence isn't revealed.
- The grant is re-checked on every request (``mcpauth.editor_auth``), and
  writes need ``can_edit``.
- Writes run inside ``editing_as(user, "mcp")``, so the history row names the
  person, and are throttled per (user, site).

Reads reuse the existing viewsets (``EDITOR_READ_ROUTES`` below) behind the same
authentication, with ``gregory.visibility`` giving them the editor's site scope.

The ``/editor/sources/`` routes are the exception to "any editor reads": they need a
grant with ``can_manage_sources`` (``IsSourceManager``) for every method, and have
their own throttles. Their checks live in ``gregory.source_checks``.
"""

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.db import IntegrityError, transaction
from django.db.models import Case, Exists, OuterRef, When
from django.db.models.functions import Lower
from django.utils import timezone
from django.utils.text import slugify
from drf_spectacular.utils import (
	OpenApiParameter,
	OpenApiResponse,
	OpenApiTypes,
	extend_schema,
	extend_schema_view,
)
from rest_framework import permissions, status
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import (
	APIException,
	AuthenticationFailed,
	NotFound,
	PermissionDenied,
	ValidationError,
)
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from api.medicine_scope import category_has_subject
from api.serializers import McpTenantSerializer
from api.views import (
	ArticleViewSet,
	AuthorsViewSet,
	CategoryViewSet,
	McpTenantsView,
	MedicineViewSet,
	PatentViewSet,
	SponsorViewSet,
	StatsView,
	SubjectsViewSet,
	TrialViewSet,
)
from api.schema_serializers import (
	EditorCategoryAssignmentResponseSerializer,
	EditorCategoryCreateSerializer,
	EditorPatentCategoryAssignmentResponseSerializer,
	EditorPatentCategoryUnassignResponseSerializer,
	EditorPatentEditorialUpdateResponseSerializer,
	EditorPatentHistoryResponseSerializer,
	EditorPatentResolveResponseSerializer,
	EditorPatentTrialLinkResponseSerializer,
	EditorCategoryResponseSerializer,
	EditorCategoryUnassignResponseSerializer,
	EditorCategoryUpdateSerializer,
	EditorErrorSerializer,
	EditorMedicineLinkResponseSerializer,
	EditorialUpdateRequestSerializer,
	EditorialUpdateResponseSerializer,
	EditorHistoryResponseSerializer,
	EditorResolveResponseSerializer,
	EditorSourceConflictSerializer,
	EditorSourceCreateResponseSerializer,
	EditorSourceInputSerializer,
	EditorSourceListResponseSerializer,
	EditorSourcePreviewResponseSerializer,
	EditorTrialLinkRequestSerializer,
	EditorTrialLinkResponseSerializer,
	EditorTrialUnlinkResponseSerializer,
	RelevanceRequestSerializer,
	RelevanceResponseSerializer,
)
from gregory import source_checks, source_types
from gregory.editor_history import editing_as
from gregory.models import (
	ArticleCategoryAssignment,
	Articles,
	ArticleSiteContent,
	ArticleSubjectRelevance,
	ArticleTrialReference,
	CategoryAssignmentSource,
	CategoryType,
	Medicine,
	MedicineCategoryAssignment,
	PatentCategoryAssignment,
	PatentPublication,
	PatentSiteContent,
	PatentTrialLink,
	Patents,
	PublicationType,
	Sources,
	Subject,
	TeamCategory,
	Trials,
)
from gregory.utils.patent_numbers import publication_number_q
from gregory.visibility import site_scope_subject_ids
from mcpauth.editor_auth import (
	EditorAuth,
	EditorAuthError,
	authenticate_editor,
	check_service_credential,
)

#: Preferred order when picking a trial's primary identifier for a manual link.
PRIMARY_IDENTIFIER_KEYS = ("nct", "euct", "eudract", "euctr", "isrctn", "ctis")
HISTORY_DEFAULT_LIMIT = 50
#: DRF validation bodies are keyed by field (or ``non_field_errors``), not ``detail``.
EDITOR_VALIDATION_ERROR = OpenApiResponse(
	response=OpenApiTypes.OBJECT,
	description=(
		"Validation error: each invalid field, or `non_field_errors`, maps to a list of messages."
	),
)
HISTORY_MAX_LIMIT = 200


class EditorServiceAuthentication(BaseAuthentication):
	"""The service credential plus a verified editor and site.

	Succeeds as ``(user, EditorAuth)``; ``request.user`` is the editor, so the
	rest of Django (history, visibility) sees a person, never the MCP server.
	A bad credential or unknown editor is a 401; a valid one for an editor with
	no active grant on the site is a 403.
	"""

	def authenticate(self, request):
		try:
			user, auth = authenticate_editor(request)
		except EditorAuthError as exc:
			if exc.status == 401:
				raise AuthenticationFailed(exc.message)
			raise PermissionDenied(exc.message)
		# Read by simple-history's stamp (gregory.editor_history) for `via`.
		request._request.editor_via = "mcp"
		return user, auth

	def authenticate_header(self, request):
		return "Bearer"


class IsEditorReader(permissions.BasePermission):
	"""Any verified editor with an active grant on the site."""

	def has_permission(self, request, view):
		return isinstance(request.auth, EditorAuth)


class IsEditorWriter(permissions.BasePermission):
	"""Reads: any verified editor. Writes: only a grant with ``can_edit``."""

	message = "This editor has read-only access to the site."

	def has_permission(self, request, view):
		if not isinstance(request.auth, EditorAuth):
			return False
		return request.method in permissions.SAFE_METHODS or request.auth.can_edit


class _EditorWriteThrottle(SimpleRateThrottle):
	"""Writes per (user, site), configurable in ``MCP_EDITOR_RATE_LIMITS`` (D21).

	Two windows run together, one instance each. A rejected write is a 429 with
	``Retry-After``; the MCP tool surfaces it and does not retry.
	"""

	window = ""

	def get_rate(self):
		return f"{settings.MCP_EDITOR_RATE_LIMITS[self.window]}/{self.window}"

	def allow_request(self, request, view):
		if request.method in permissions.SAFE_METHODS:
			return True
		return super().allow_request(request, view)

	def get_cache_key(self, request, view):
		auth = request.auth
		return f"throttle_editor_{self.window}_{auth.user_id}_{auth.site_id}"


class EditorHourThrottle(_EditorWriteThrottle):
	window = "hour"


class EditorDayThrottle(_EditorWriteThrottle):
	window = "day"


class IsSourceManager(permissions.BasePermission):
	"""The ``/editor/sources/`` routes, every method including GET: only a grant with
	``can_edit`` and ``can_manage_sources``. Regular editors get 403 even to list."""

	message = "This editor cannot add sources."

	def has_permission(self, request, view):
		auth = request.auth
		return isinstance(auth, EditorAuth) and auth.can_edit and auth.can_manage_sources


class _SourceThrottle(SimpleRateThrottle):
	"""Source creates and previews per (user, site), apart from the general editor write
	throttles. Limits come from ``MCP_SOURCE_RATE_LIMITS``."""

	name = ""
	rate_key = ""
	period = ""

	def get_rate(self):
		return f"{settings.MCP_SOURCE_RATE_LIMITS[self.rate_key]}/{self.period}"

	def get_cache_key(self, request, view):
		auth = request.auth
		return f"throttle_editor_{self.name}_{auth.user_id}_{auth.site_id}"


class SourceCreateThrottle(_SourceThrottle):
	name = "source_create"
	rate_key = "creates_per_day"
	period = "day"


class SourcePreviewThrottle(_SourceThrottle):
	name = "source_preview"
	rate_key = "previews_per_hour"
	period = "hour"


class _EditorView(APIView):
	authentication_classes = [EditorServiceAuthentication]
	permission_classes = [IsEditorWriter]
	throttle_classes = [EditorHourThrottle, EditorDayThrottle]

	def site_scope(self):
		"""Subject ids the editor's site publishes (private or not)."""
		return site_scope_subject_ids(self.request.auth.site_id, public_only=False)

	def scoped_articles(self):
		return Articles.objects.filter(subjects__id__in=self.site_scope()).distinct()

	def get_article(self, article_id):
		article = self.scoped_articles().filter(pk=article_id).first()
		if article is None:
			raise NotFound("Article not found.")
		return article

	def get_trial(self, trial_id):
		trial = Trials.objects.filter(pk=trial_id, subjects__id__in=self.site_scope()).distinct().first()
		if trial is None:
			raise NotFound("Trial not found.")
		return trial

	def patent_scope(self):
		"""Subject ids the editor's site shows PATENTS for: its scope, but only
		when the site has ``patents_enabled`` on (empty otherwise)."""
		return site_scope_subject_ids(self.request.auth.site_id, public_only=False, patents=True)

	def get_patent(self, patent_id):
		"""A patent family in the editor's patent scope. A site with the switch
		off has none, so every patent route is 404 there, the same answer as
		for a family that doesn't exist."""
		patent = Patents.objects.filter(pk=patent_id, subjects__id__in=self.patent_scope()).distinct().first()
		if patent is None:
			raise NotFound("Patent not found.")
		return patent


class _CategoryScopeMixin:
	"""Which categories an editor may touch, shared by the category routes.

	A category is in scope when one of its subjects is in the site's scope:
	that is when the site shows it. Assigning an article to it needs only that.
	Changing the category itself needs every one of its subjects in scope,
	because the change shows on every site that lists any of them.
	"""

	def category_in_scope(self, category_id):
		category = (
			TeamCategory.objects.filter(pk=category_id, subjects__id__in=self.site_scope())
			.distinct()
			.prefetch_related("subjects")
			.first()
		)
		if category is None:
			raise NotFound("Category not found.")
		return category

	def category_for_edit(self, category_id):
		category = self.category_in_scope(category_id)
		outside = {s.id for s in category.subjects.all()} - set(self.site_scope())
		if outside:
			raise PermissionDenied(
				"This category also covers subjects outside this site, so it can only be changed in the admin."
			)
		return category

	def subjects_for_category(self, subject_ids, team_id=None):
		"""The subjects, all in scope and in one team (``team_id`` when given)."""
		scope = set(self.site_scope())
		wanted = set(subject_ids)
		if wanted - scope:
			raise NotFound("Subject not found.")
		subjects = list(Subject.objects.filter(pk__in=wanted).select_related("team"))
		if len(subjects) != len(wanted):
			raise NotFound("Subject not found.")
		teams = {s.team_id for s in subjects}
		if len(teams) != 1:
			raise ValidationError({"subject_ids": ["All subjects must belong to the same team."]})
		if team_id is not None and teams != {team_id}:
			raise ValidationError({"subject_ids": ["Subjects must belong to the category's team."]})
		return subjects


def _last_change(history_model, **lookup):
	"""``(who, when)`` of the newest history row matching ``lookup``."""
	row = history_model.filter(**lookup).order_by("-history_date").first()
	if row is None:
		return None, None
	return (row.editor_label or row.api_access_scheme_label or None), row.history_date


class ConflictError(APIException):
	status_code = 409
	default_code = "conflict"


# --- Shared by the article and patent routes ------------------------------
#
# The two record types get the same editorial, category and link rules. The
# locking, the column-limited save, the automatic-vs-manual category rules and
# the history scoping live here once, parameterised by the content or
# assignment model and the name of its foreign key to the record.


def _editorial_fields(request):
	"""The validated ``takeaways`` / ``summary_plain_english`` of a PATCH body,
	with an empty string meaning "clear" (stored as NULL)."""
	body = EditorialUpdateRequestSerializer(data=request.data)
	body.is_valid(raise_exception=True)
	fields = {key: (value or None) for key, value in body.validated_data.items()}
	if not fields:
		raise ValidationError({"non_field_errors": ["Send `takeaways` and/or `summary_plain_english`."]})
	return fields


def _upsert_site_content(request, content_model, record_field, record, fields):
	"""Upsert this editor's site's content row for ``record`` and return it."""
	site_id = request.auth.site_id
	with transaction.atomic(), editing_as(request.user, "mcp"):
		# Locked so two overlapping PATCHes queue rather than each read the
		# old row, and only the sent columns are written, so an omitted
		# field keeps whatever another request just stored.
		content, created = content_model.objects.select_for_update().get_or_create(
			**{record_field: record}, site_id=site_id, defaults=fields
		)
		if not created:
			changed = [k for k, v in fields.items() if getattr(content, k) != v]
			for key in changed:
				setattr(content, key, fields[key])
			if changed:
				content.save(update_fields=[*changed, "updated_at"])
	content.refresh_from_db()
	return content


def _site_content_payload(content_model, record_field, content):
	updated_by, _ = _last_change(content_model.history, id=content.id)
	return {
		f"{record_field}_id": getattr(content, f"{record_field}_id"),
		"site_id": content.site_id,
		"takeaways": content.takeaways,
		"summary_plain_english": content.summary_plain_english,
		"updated_by": updated_by,
		"updated_at": content.updated_at,
	}


def _assign_category(request, assignment_model, record_field, record, category):
	"""Assign ``record`` to ``category`` by hand: ``(assignment, created)``. A
	row the pipeline made becomes a hand assignment, which it then never removes."""
	with transaction.atomic(), editing_as(request.user, "mcp"):
		assignment, created = assignment_model.objects.select_for_update().get_or_create(
			**{record_field: record, "teamcategory": category},
			defaults={"source": CategoryAssignmentSource.MANUAL},
		)
		if not created and assignment.source != CategoryAssignmentSource.MANUAL:
			assignment.source = CategoryAssignmentSource.MANUAL
			assignment.save()
	return assignment, created


def _unassign_category(request, assignment_model, record_field, record, category, noun):
	"""Remove a hand assignment. One the pipeline made would come back on the
	next run, so it is refused (409) rather than deleted."""
	with transaction.atomic(), editing_as(request.user, "mcp"):
		assignment = assignment_model.objects.filter(**{record_field: record, "teamcategory": category}).first()
		if assignment is None:
			raise NotFound(f"The {noun} is not in this category.")
		if assignment.source != CategoryAssignmentSource.MANUAL:
			raise ConflictError(
				f"The pipeline assigned this {noun} from the category's terms and would add it back. "
				"Change the category's terms instead."
			)
		assignment.delete()


def _history_limit(request):
	try:
		limit = int(request.query_params.get("limit", HISTORY_DEFAULT_LIMIT))
	except ValueError:
		raise ValidationError({"limit": "Must be an integer."})
	if not 1 <= limit <= HISTORY_MAX_LIMIT:
		raise ValidationError({"limit": f"Must be between 1 and {HISTORY_MAX_LIMIT}."})
	return limit


def _editorial_history_entries(content_model, record_field, record_pk, site_id, limit):
	rows = content_model.history.filter(**{f"{record_field}_id": record_pk, "site_id": site_id})
	return [
		_history_entry(
			"editorial",
			row,
			{
				"site_id": row.site_id,
				"takeaways": row.takeaways,
				"summary_plain_english": row.summary_plain_english,
			},
		)
		for row in rows.order_by("-history_date")[:limit]
	]


def _category_history_entries(assignment_model, record_field, record_pk, scope, limit):
	history = assignment_model.history.filter(**{f"{record_field}_id": record_pk})
	# Scope first, then the limit, as for trial links.
	visible = TeamCategory.objects.filter(
		pk__in=history.values("teamcategory_id"), subjects__id__in=scope
	).values("pk")
	return [
		_history_entry("category", row, {"category_id": row.teamcategory_id, "source": row.source})
		for row in history.filter(teamcategory_id__in=visible).order_by("-history_date")[:limit]
	]


# --- Read: resolve a DOI ------------------------------------------------


@extend_schema(
	summary="Resolve a DOI to an article id (editor)",
	description=(
		"Editor-only. Finds the article with this DOI (case-insensitive) among "
		"the articles inside the editor's site scope. 404 when there is none, "
		"which is also the answer for an article outside that scope. 409 when "
		"more than one in-scope article has the DOI, with their ids."
	),
	parameters=[OpenApiParameter("doi", OpenApiTypes.STR, OpenApiParameter.QUERY, required=True)],
	responses={
		200: EditorResolveResponseSerializer,
		400: EDITOR_VALIDATION_ERROR,
		401: EditorErrorSerializer,
		403: EditorErrorSerializer,
		404: EditorErrorSerializer,
		409: EditorErrorSerializer,
	},
	auth=[{"editorServiceAuth": []}],
)
class EditorArticleResolveView(_EditorView):
	"""``GET /editor/articles/resolve/?doi=``"""

	def get(self, request):
		doi = request.query_params.get("doi", "").strip()
		if not doi:
			raise ValidationError({"doi": "This query parameter is required."})
		matches = list(self.scoped_articles().filter(doi__iexact=doi).values("article_id", "doi", "title"))
		if not matches:
			raise NotFound("Article not found.")
		if len(matches) > 1:
			error = ConflictError(f"{len(matches)} articles match DOI {doi}.")
			error.detail = {
				"detail": f"{len(matches)} articles match DOI {doi}.",
				"article_ids": [m["article_id"] for m in matches],
			}
			raise error
		return Response(matches[0])


# --- Write: editorial text ----------------------------------------------


@extend_schema(
	summary="Set this site's editorial content for an article (editor)",
	description=(
		"Editor-only. Upserts `takeaways` and/or `summary_plain_english` of the "
		"article for the editor's site; fields not sent are left alone and an "
		"empty string clears a field (stored as NULL). Applies immediately and is "
		"recorded under the editor's name. Returns the stored values and who last "
		"changed them. Writes are throttled per editor and site."
	),
	request=EditorialUpdateRequestSerializer,
	responses={
		200: EditorialUpdateResponseSerializer,
		400: EDITOR_VALIDATION_ERROR,
		401: EditorErrorSerializer,
		403: EditorErrorSerializer,
		404: EditorErrorSerializer,
		429: EditorErrorSerializer,
	},
	auth=[{"editorServiceAuth": []}],
)
class EditorArticleEditorialView(_EditorView):
	"""``PATCH /editor/articles/{article_id}/editorial/``"""

	def patch(self, request, article_id):
		article = self.get_article(article_id)
		fields = _editorial_fields(request)
		content = _upsert_site_content(request, ArticleSiteContent, "article", article, fields)
		return Response(_site_content_payload(ArticleSiteContent, "article", content))


# --- Write: relevance ---------------------------------------------------


@extend_schema(
	summary="Set an article's relevance for a subject (editor)",
	description=(
		"Editor-only. Sets `is_relevant` (true, false or null for not reviewed) "
		"for one subject. The subject must be in the editor's site scope, else "
		"404. Relevance is per subject, so the change applies to every site "
		"whose scope lists the subject. Returns the stored value and who last "
		"changed it."
	),
	request=RelevanceRequestSerializer,
	responses={
		200: RelevanceResponseSerializer,
		400: EDITOR_VALIDATION_ERROR,
		401: EditorErrorSerializer,
		403: EditorErrorSerializer,
		404: EditorErrorSerializer,
		429: EditorErrorSerializer,
	},
	auth=[{"editorServiceAuth": []}],
)
class EditorArticleRelevanceView(_EditorView):
	"""``PUT /editor/articles/{article_id}/relevance/{subject_id}/``"""

	def put(self, request, article_id, subject_id):
		article = self.get_article(article_id)
		if subject_id not in self.site_scope():
			raise NotFound("Subject not found.")
		body = RelevanceRequestSerializer(data=request.data)
		body.is_valid(raise_exception=True)
		is_relevant = body.validated_data["is_relevant"]

		with transaction.atomic(), editing_as(request.user, "mcp"):
			relevance, created = ArticleSubjectRelevance.objects.get_or_create(
				article=article, subject_id=subject_id, defaults={"is_relevant": is_relevant}
			)
			if not created and relevance.is_relevant != is_relevant:
				relevance.is_relevant = is_relevant
				relevance.save()
		updated_by, updated_at = _last_change(ArticleSubjectRelevance.history, id=relevance.id)
		return Response(
			{
				"article_id": article.article_id,
				"subject_id": subject_id,
				"is_relevant": relevance.is_relevant,
				"updated_by": updated_by,
				"updated_at": updated_at,
			}
		)


# --- Write: trial links -------------------------------------------------


def primary_identifier(trial) -> str:
	"""The identifier stored on a manual link: the trial's registry id."""
	identifiers = trial.identifiers or {}
	for key in PRIMARY_IDENTIFIER_KEYS:
		if identifiers.get(key):
			return str(identifiers[key])[:100]
	for value in identifiers.values():
		if value:
			return str(value)[:100]
	return f"trial:{trial.trial_id}"


def _link_payload(reference, trial):
	updated_by, updated_at = _last_change(ArticleTrialReference.history, id=reference.id)
	return {
		"article_id": reference.article_id,
		"trial_id": trial.trial_id,
		"source": reference.source,
		"identifier_type": reference.identifier_type,
		"identifier_value": reference.identifier_value,
		"updated_by": updated_by,
		"updated_at": updated_at,
	}


@extend_schema(
	summary="Link a trial to an article (editor)",
	description=(
		"Editor-only. Adds a manual link between the article and a trial. Both "
		"must be inside the editor's site scope, else 404. Idempotent: linking a "
		"pair that is already linked returns the existing link (200) and creates "
		"nothing; a new link is 201. A link an editor added is never changed or "
		"duplicated by `detect_trial_references`."
	),
	request=EditorTrialLinkRequestSerializer,
	responses={
		200: EditorTrialLinkResponseSerializer,
		201: EditorTrialLinkResponseSerializer,
		400: EDITOR_VALIDATION_ERROR,
		401: EditorErrorSerializer,
		403: EditorErrorSerializer,
		404: EditorErrorSerializer,
		429: EditorErrorSerializer,
	},
	auth=[{"editorServiceAuth": []}],
)
class EditorArticleTrialsView(_EditorView):
	"""``POST /editor/articles/{article_id}/trials/``"""

	def post(self, request, article_id):
		article = self.get_article(article_id)
		body = EditorTrialLinkRequestSerializer(data=request.data)
		body.is_valid(raise_exception=True)
		trial = self.get_trial(body.validated_data["trial_id"])

		with transaction.atomic(), editing_as(request.user, "mcp"):
			existing = (
				ArticleTrialReference.objects.filter(article=article, trial=trial, suppressed=False)
				# A manual link first: it carries the editor's attribution.
				.order_by(
					Case(When(source=ArticleTrialReference.SOURCE_MANUAL, then=0), default=1),
					"id",
				)
				.first()
			)
			if existing is not None:
				return Response(_link_payload(existing, trial))
			# get_or_create on the unique (article, trial, identifier_type) key,
			# so two identical POSTs at once both answer instead of one 500ing.
			reference, created = ArticleTrialReference.objects.get_or_create(
				article=article,
				trial=trial,
				identifier_type="manual",
				defaults={
					"identifier_value": primary_identifier(trial),
					"source": ArticleTrialReference.SOURCE_MANUAL,
					"created_by": request.user,
				},
			)
		return Response(
			_link_payload(reference, trial),
			status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
		)


@extend_schema(
	summary="Unlink a trial from an article (editor)",
	description=(
		"Editor-only. Removes the link between the article and a trial. A link "
		"an editor added is deleted. An auto-detected link is hidden instead (it "
		"stays, marked `suppressed`) so the next `detect_trial_references` run "
		"doesn't recreate it. 404 when the pair isn't linked, or either side is "
		"outside the editor's site scope."
	),
	responses={
		200: EditorTrialUnlinkResponseSerializer,
		401: EditorErrorSerializer,
		403: EditorErrorSerializer,
		404: EditorErrorSerializer,
		429: EditorErrorSerializer,
	},
	auth=[{"editorServiceAuth": []}],
)
class EditorArticleTrialDetailView(_EditorView):
	"""``DELETE /editor/articles/{article_id}/trials/{trial_id}/``"""

	def delete(self, request, article_id, trial_id):
		article = self.get_article(article_id)
		trial = self.get_trial(trial_id)

		removed = []
		with transaction.atomic(), editing_as(request.user, "mcp"):
			links = list(ArticleTrialReference.objects.filter(article=article, trial=trial, suppressed=False))
			for link in links:
				if link.source == ArticleTrialReference.SOURCE_MANUAL:
					link.delete()
				else:
					link.suppressed = True
					link.save()
				removed.append(link.source)
		if not removed:
			raise NotFound("The article and trial are not linked.")
		return Response({"article_id": article.article_id, "trial_id": trial.trial_id, "removed": sorted(set(removed))})


# --- Write: categories -------------------------------------------------


def _category_payload(category):
	updated_by, updated_at = _last_change(TeamCategory.history, id=category.pk)
	return {
		"id": category.pk,
		"category_name": category.category_name,
		"category_slug": category.category_slug,
		"category_description": category.category_description,
		"category_terms": list(category.category_terms or []),
		"category_exclude_terms": list(category.category_exclude_terms or []),
		"modality": category.modality,
		"category_type": category.category_type,
		"match_scope": category.match_scope,
		"team_id": category.team_id,
		"subject_ids": sorted(s.id for s in category.subjects.all()),
		"article_count": category.articles.count(),
		"trials_count": category.trials.count(),
		"last_synced_at": category.last_synced_at,
		"updated_by": updated_by,
		"updated_at": updated_at,
	}


def _merge_terms(current, add=(), remove=()):
	"""``current`` plus ``add`` minus ``remove``, comparing case-insensitively
	and keeping the stored spelling and order."""
	removed = {term.lower() for term in remove}
	terms = [term for term in current if term.lower() not in removed]
	have = {term.lower() for term in terms}
	for term in add:
		if term.lower() not in have:
			have.add(term.lower())
			terms.append(term)
	return terms


_CATEGORY_WRITE_ERRORS = {
	400: EDITOR_VALIDATION_ERROR,
	401: EditorErrorSerializer,
	403: EditorErrorSerializer,
	404: EditorErrorSerializer,
	429: EditorErrorSerializer,
}


@extend_schema_view(
	list=extend_schema(exclude=True),
	retrieve=extend_schema(exclude=True),
	authors=extend_schema(exclude=True),
	create=extend_schema(
		summary="Create a category (editor)",
		description=(
			"Editor-only. Creates a category for the subjects given, which must all be "
			"in the editor's site scope (404 otherwise) and belong to one team, which "
			"becomes the category's team. An automatic category needs at least one "
			"term; the next pipeline run matches every article and trial against it, "
			"so it starts empty. The slug defaults to the slugified name and is "
			"unique across every team: a taken slug is 409, with the holder's "
			"`category_id` when that category is in scope. Recorded under the "
			"editor's name."
		),
		request=EditorCategoryCreateSerializer,
		responses={201: EditorCategoryResponseSerializer, 409: EditorErrorSerializer, **_CATEGORY_WRITE_ERRORS},
		auth=[{"editorServiceAuth": []}],
	),
	partial_update=extend_schema(
		summary="Change a category (editor)",
		description=(
			"Editor-only. Changes the fields sent. `category_terms` replaces every "
			"term; `add_terms` and `remove_terms` edit the current list instead "
			"(case-insensitive). `category_exclude_terms` (or `add_exclude_terms` / "
			"`remove_exclude_terms`) does the same for the terms that keep content out "
			"of the category. `subject_ids` replaces the subjects, which must be "
			"in scope and in the category's team. The slug and team never change. "
			"Every subject of the category must be in the editor's site scope, "
			"since the change shows wherever the category does: 404 when none is, "
			"403 when only some are. A change to terms, excluded terms, subjects or `match_scope` "
			"makes the next pipeline run re-match all content for the category. "
			"Recorded under the editor's name."
		),
		request=EditorCategoryUpdateSerializer,
		responses={200: EditorCategoryResponseSerializer, **_CATEGORY_WRITE_ERRORS},
		auth=[{"editorServiceAuth": []}],
	),
)
class EditorCategoryViewSet(_CategoryScopeMixin, CategoryViewSet):
	"""``/editor/categories/``: the categories list and detail as on
	``/categories/``, scoped to the editor's site, plus create (``POST``) and
	change (``PATCH /editor/categories/{id}/``) for editors who can edit."""

	authentication_classes = [EditorServiceAuthentication]
	permission_classes = [IsEditorWriter]
	throttle_classes = [EditorHourThrottle, EditorDayThrottle]

	def site_scope(self):
		return site_scope_subject_ids(self.request.auth.site_id, public_only=False)

	def create(self, request):
		body = EditorCategoryCreateSerializer(data=request.data)
		body.is_valid(raise_exception=True)
		data = body.validated_data
		subjects = self.subjects_for_category(data["subject_ids"])
		slug = data.get("category_slug") or slugify(data["category_name"])[:50]
		self._refuse_taken_slug(slug)
		try:
			with transaction.atomic(), editing_as(request.user, "mcp"):
				category = TeamCategory.objects.create(
					team_id=subjects[0].team_id,
					category_name=data["category_name"],
					category_slug=slug,
					category_description=data["category_description"] or None,
					category_terms=data["category_terms"],
					category_exclude_terms=data["category_exclude_terms"],
					category_type=data["category_type"],
					modality=data["modality"],
					match_scope=data["match_scope"],
				)
				category.subjects.set(subjects)
		except IntegrityError:
			# Two creates with the same slug at once: the loser gets the same 409.
			self._refuse_taken_slug(slug)
			raise
		return Response(_category_payload(category), status=status.HTTP_201_CREATED)

	def _refuse_taken_slug(self, slug):
		holder = TeamCategory.objects.filter(category_slug=slug).first()
		if holder is None:
			return
		error = ConflictError(f"A category with the slug {slug} already exists.")
		error.detail = {"detail": f"A category with the slug {slug} already exists."}
		if holder.subjects.filter(pk__in=self.site_scope()).exists():
			error.detail["category_id"] = holder.pk
		raise error

	def partial_update(self, request, pk=None):
		category = self.category_for_edit(pk)
		body = EditorCategoryUpdateSerializer(data=request.data)
		body.is_valid(raise_exception=True)
		data = body.validated_data
		subjects = None
		if "subject_ids" in data:
			subjects = self.subjects_for_category(data["subject_ids"], team_id=category.team_id)

		with transaction.atomic(), editing_as(request.user, "mcp"):
			category = TeamCategory.objects.select_for_update().get(pk=category.pk)
			terms = list(category.category_terms or [])
			if "category_terms" in data:
				terms = data["category_terms"]
			elif "add_terms" in data or "remove_terms" in data:
				terms = _merge_terms(terms, data.get("add_terms", ()), data.get("remove_terms", ()))
			if category.category_type == CategoryType.AUTOMATIC and not terms:
				raise ValidationError({"category_terms": ["An automatic category needs at least one term."]})

			exclude_terms = list(category.category_exclude_terms or [])
			if "category_exclude_terms" in data:
				exclude_terms = data["category_exclude_terms"]
			elif "add_exclude_terms" in data or "remove_exclude_terms" in data:
				exclude_terms = _merge_terms(
					exclude_terms, data.get("add_exclude_terms", ()), data.get("remove_exclude_terms", ())
				)

			changed = []
			for field in ("category_name", "modality", "match_scope"):
				if field in data and getattr(category, field) != data[field]:
					setattr(category, field, data[field])
					changed.append(field)
			if "category_description" in data:
				description = data["category_description"] or None
				if category.category_description != description:
					category.category_description = description
					changed.append("category_description")
			if terms != list(category.category_terms or []):
				category.category_terms = terms
				changed.append("category_terms")
			if exclude_terms != list(category.category_exclude_terms or []):
				category.category_exclude_terms = exclude_terms
				changed.append("category_exclude_terms")
			if changed:
				category.save(update_fields=changed)
			if subjects is not None and {s.id for s in subjects} != {s.id for s in category.subjects.all()}:
				category.subjects.set(subjects)
		category = TeamCategory.objects.prefetch_related("subjects").get(pk=category.pk)
		return Response(_category_payload(category))


@extend_schema(
	summary="Assign an article to a category (editor)",
	description=(
		"Editor-only. Assigns the article to the category by hand. Both must be "
		"in the editor's site scope (a category is in scope when one of its "
		"subjects is), else 404. A new assignment is 201. An article the pipeline "
		"already matched becomes a hand assignment (200), which the pipeline then "
		"never removes; one already assigned by hand is returned unchanged (200). "
		"Recorded under the editor's name."
	),
	request=None,
	responses={
		200: EditorCategoryAssignmentResponseSerializer,
		201: EditorCategoryAssignmentResponseSerializer,
		**_CATEGORY_WRITE_ERRORS,
	},
	auth=[{"editorServiceAuth": []}],
)
class EditorArticleCategoryView(_CategoryScopeMixin, _EditorView):
	"""``PUT`` and ``DELETE /editor/articles/{article_id}/categories/{category_id}/``"""

	def put(self, request, article_id, category_id):
		article = self.get_article(article_id)
		category = self.category_in_scope(category_id)
		assignment, created = _assign_category(request, ArticleCategoryAssignment, "articles", article, category)
		return Response(
			self.serialize(assignment),
			status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
		)

	@extend_schema(
		summary="Remove an article from a category (editor)",
		description=(
			"Editor-only. Removes a hand assignment. An assignment the pipeline "
			"made from the category's terms is 409: it would come back on the next "
			"run, so change the terms instead. 404 when the article is not in the "
			"category, or either is outside the editor's site scope."
		),
		request=None,
		responses={
			200: EditorCategoryUnassignResponseSerializer,
			409: EditorErrorSerializer,
			**_CATEGORY_WRITE_ERRORS,
		},
		auth=[{"editorServiceAuth": []}],
	)
	def delete(self, request, article_id, category_id):
		article = self.get_article(article_id)
		category = self.category_in_scope(category_id)
		_unassign_category(request, ArticleCategoryAssignment, "articles", article, category, "article")
		return Response(
			{"article_id": article.article_id, "category_id": category.pk, "removed": CategoryAssignmentSource.MANUAL}
		)

	@staticmethod
	def serialize(assignment):
		updated_by, updated_at = _last_change(ArticleCategoryAssignment.history, id=assignment.pk)
		return {
			"article_id": assignment.articles_id,
			"category_id": assignment.teamcategory_id,
			"source": assignment.source,
			"updated_by": updated_by,
			"updated_at": updated_at,
		}


# --- Read: history ------------------------------------------------------

_CHANGE = {"+": "created", "~": "updated", "-": "deleted"}


def _history_entry(kind, row, details):
	return {
		"kind": kind,
		"change": _CHANGE.get(row.history_type, row.history_type),
		"changed_at": row.history_date,
		"changed_by": row.editor_label or row.api_access_scheme_label or None,
		"via": row.via or None,
		"details": details,
	}


@extend_schema(
	summary="Edit history of an article (editor)",
	description=(
		"Editor-only. Who changed this article's editorial content for the "
		"editor's site, its relevance for subjects in that site's scope, its "
		"links to trials in scope, and its hand assignments to categories in "
		"scope, and when. Newest first. Changes made by an API key name the key "
		"instead of a person; changes made by the pipeline have no name, and the "
		"pipeline's own category matching records none."
	),
	parameters=[
		OpenApiParameter(
			"limit",
			OpenApiTypes.INT,
			OpenApiParameter.QUERY,
			description=f"Entries to return (default {HISTORY_DEFAULT_LIMIT}, max {HISTORY_MAX_LIMIT}).",
		)
	],
	responses={
		200: EditorHistoryResponseSerializer,
		400: EDITOR_VALIDATION_ERROR,
		401: EditorErrorSerializer,
		403: EditorErrorSerializer,
		404: EditorErrorSerializer,
	},
	auth=[{"editorServiceAuth": []}],
)
class EditorArticleHistoryView(_EditorView):
	"""``GET /editor/articles/{article_id}/history/``"""

	def get(self, request, article_id):
		article = self.get_article(article_id)
		limit = _history_limit(request)
		scope = self.site_scope()

		entries = _editorial_history_entries(ArticleSiteContent, "article", article.pk, request.auth.site_id, limit)
		relevance = ArticleSubjectRelevance.history.filter(article_id=article.pk, subject_id__in=scope)
		for row in relevance.order_by("-history_date")[:limit]:
			entries.append(
				_history_entry("relevance", row, {"subject_id": row.subject_id, "is_relevant": row.is_relevant})
			)
		link_history = ArticleTrialReference.history.filter(article_id=article.pk)
		# Scope first, then the limit: slicing first could fill the page with
		# out-of-scope trials and drop older in-scope changes.
		visible_trials = Trials.objects.filter(
			pk__in=link_history.values("trial_id"), subjects__id__in=scope
		).values("pk")
		for row in link_history.filter(trial_id__in=visible_trials).order_by("-history_date")[:limit]:
			entries.append(
				_history_entry(
					"trial_link",
					row,
					{
						"trial_id": row.trial_id,
						"source": row.source,
						"suppressed": row.suppressed,
						"identifier_type": row.identifier_type,
					},
				)
			)

		entries += _category_history_entries(ArticleCategoryAssignment, "articles", article.pk, scope, limit)

		entries.sort(key=lambda e: e["changed_at"], reverse=True)
		return Response({"article_id": article.article_id, "entries": entries[:limit]})


# --- Patents --------------------------------------------------------------
#
# The same actions as the article routes, on patent families. Differences:
# a patent is found through the site's PATENT scope (a site with
# ``patents_enabled`` off has none, so every route is 404), editors cannot
# create a patent-trial link (only detection can: it needs a shared sponsor and
# timing), and patents have no relevance verdict.

_PATENT_ERRORS = {
	401: EditorErrorSerializer,
	403: EditorErrorSerializer,
	404: EditorErrorSerializer,
}


@extend_schema(
	summary="Resolve a publication number to a patent id (editor)",
	description=(
		"Editor-only. Takes ONE number (a list is 400: use `GET /patents/"
		"?publication_number=a,b` for lists). Finds the patent family with a "
		"member publication matching this number, in any common format (`EP4123456A1`, `EP 4123456 A1`; "
		"without a kind code it matches every kind) among the families inside the "
		"editor's site patent scope. 404 when there is none, which is also the "
		"answer for a family outside that scope or a site without patents. 409 "
		"when several in-scope families match, with their ids."
	),
	parameters=[OpenApiParameter("publication_number", OpenApiTypes.STR, OpenApiParameter.QUERY, required=True)],
	responses={
		200: EditorPatentResolveResponseSerializer,
		400: EDITOR_VALIDATION_ERROR,
		409: EditorErrorSerializer,
		**_PATENT_ERRORS,
	},
	auth=[{"editorServiceAuth": []}],
)
class EditorPatentResolveView(_EditorView):
	"""``GET /editor/patents/resolve/?publication_number=``"""

	def get(self, request):
		number = request.query_params.get("publication_number", "").strip()
		if not number:
			raise ValidationError({"publication_number": "This query parameter is required."})
		if "," in number or ";" in number:
			# `/patents/?publication_number=` takes a list; this resolves one.
			# Merging a list into one string would match nothing and answer 404,
			# which reads as "no such patent".
			raise ValidationError(
				{"publication_number": "Send one publication number. To look up several, use GET /patents/?publication_number=a,b."}
			)
		condition = publication_number_q([number])
		if not condition:
			raise NotFound("Patent not found.")
		member = PatentPublication.objects.filter(condition, patent=OuterRef("pk"))
		matches = list(
			Patents.objects.filter(subjects__id__in=self.patent_scope())
			.filter(Exists(member))
			.distinct()
			.order_by("patent_id")
			.values("patent_id", "family_id", "title", "representative_publication")
		)
		if not matches:
			raise NotFound("Patent not found.")
		if len(matches) > 1:
			error = ConflictError(f"{len(matches)} patents match {number}.")
			error.detail = {
				"detail": f"{len(matches)} patents match {number}.",
				"patent_ids": [m["patent_id"] for m in matches],
			}
			raise error
		return Response(matches[0])


@extend_schema(
	summary="Set this site's editorial content for a patent (editor)",
	description=(
		"Editor-only. Upserts `takeaways` and/or `summary_plain_english` of the "
		"patent family for the editor's site; fields not sent are left alone and "
		"an empty string clears a field (stored as NULL). Applies immediately and "
		"is recorded under the editor's name. Returns the stored values and who "
		"last changed them. Writes are throttled per editor and site."
	),
	request=EditorialUpdateRequestSerializer,
	responses={
		200: EditorPatentEditorialUpdateResponseSerializer,
		400: EDITOR_VALIDATION_ERROR,
		429: EditorErrorSerializer,
		**_PATENT_ERRORS,
	},
	auth=[{"editorServiceAuth": []}],
)
class EditorPatentEditorialView(_EditorView):
	"""``PATCH /editor/patents/{patent_id}/editorial/``"""

	def patch(self, request, patent_id):
		patent = self.get_patent(patent_id)
		fields = _editorial_fields(request)
		content = _upsert_site_content(request, PatentSiteContent, "patent", patent, fields)
		return Response(_site_content_payload(PatentSiteContent, "patent", content))


@extend_schema(
	summary="Assign a patent to a category (editor)",
	description=(
		"Editor-only. Assigns the patent family to the category by hand. The "
		"patent must be in the editor's site patent scope and the category in its "
		"scope (a category is in scope when one of its subjects is), else 404. A "
		"new assignment is 201. A family the pipeline already matched becomes a "
		"hand assignment (200), which `rebuild_categories` never removes; one "
		"already assigned by hand is returned unchanged (200). Recorded under the "
		"editor's name."
	),
	request=None,
	responses={
		200: EditorPatentCategoryAssignmentResponseSerializer,
		201: EditorPatentCategoryAssignmentResponseSerializer,
		**_CATEGORY_WRITE_ERRORS,
	},
	auth=[{"editorServiceAuth": []}],
)
class EditorPatentCategoryView(_CategoryScopeMixin, _EditorView):
	"""``PUT`` and ``DELETE /editor/patents/{patent_id}/categories/{category_id}/``"""

	def put(self, request, patent_id, category_id):
		patent = self.get_patent(patent_id)
		category = self.category_in_scope(category_id)
		assignment, created = _assign_category(request, PatentCategoryAssignment, "patents", patent, category)
		return Response(
			self.serialize(assignment),
			status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
		)

	@extend_schema(
		summary="Remove a patent from a category (editor)",
		description=(
			"Editor-only. Removes a hand assignment. An assignment the pipeline "
			"made from the category's terms is 409: it would come back on the next "
			"run, so change the terms instead. 404 when the patent is not in the "
			"category, or either is outside the editor's site scope."
		),
		request=None,
		responses={
			200: EditorPatentCategoryUnassignResponseSerializer,
			409: EditorErrorSerializer,
			**_CATEGORY_WRITE_ERRORS,
		},
		auth=[{"editorServiceAuth": []}],
	)
	def delete(self, request, patent_id, category_id):
		patent = self.get_patent(patent_id)
		category = self.category_in_scope(category_id)
		_unassign_category(request, PatentCategoryAssignment, "patents", patent, category, "patent")
		return Response(
			{"patent_id": patent.patent_id, "category_id": category.pk, "removed": CategoryAssignmentSource.MANUAL}
		)

	@staticmethod
	def serialize(assignment):
		updated_by, updated_at = _last_change(PatentCategoryAssignment.history, id=assignment.pk)
		return {
			"patent_id": assignment.patents_id,
			"category_id": assignment.teamcategory_id,
			"source": assignment.source,
			"updated_by": updated_by,
			"updated_at": updated_at,
		}


def _patent_link_payload(link):
	updated_by, updated_at = _last_change(PatentTrialLink.history, id=link.pk)
	return {
		"patent_id": link.patent_id,
		"trial_id": link.trial_id,
		"suppressed": link.suppressed,
		"timing": link.timing,
		"basis": link.basis,
		"updated_by": updated_by,
		"updated_at": updated_at,
	}


@extend_schema(
	summary="Restore a suppressed patent-trial link (editor)",
	description=(
		"Editor-only. Un-hides a link an editor suppressed. Editors cannot create "
		"a patent-trial link: links need a shared sponsor and timing, which only "
		"detection computes, so a pair that is not linked at all is 404 with that "
		"explanation. Idempotent: a link that is not suppressed is returned "
		"unchanged (200). 404 as well when the patent is outside the editor's "
		"site patent scope or the trial outside its scope."
	),
	request=None,
	responses={
		200: EditorPatentTrialLinkResponseSerializer,
		429: EditorErrorSerializer,
		**_PATENT_ERRORS,
	},
	auth=[{"editorServiceAuth": []}],
)
class EditorPatentTrialDetailView(_EditorView):
	"""``PUT`` (restore) and ``DELETE`` (suppress) ``/editor/patents/{patent_id}/trials/{trial_id}/``"""

	def set_suppressed(self, request, patent_id, trial_id, suppressed, missing_message):
		"""Set ``suppressed`` on the link, locked, and return its payload. A link
		already in that state is returned unchanged and records nothing."""
		patent = self.get_patent(patent_id)
		trial = self.get_trial(trial_id)
		with transaction.atomic(), editing_as(request.user, "mcp"):
			link = PatentTrialLink.objects.select_for_update().filter(patent=patent, trial=trial).first()
			if link is None:
				raise NotFound(missing_message)
			if link.suppressed != suppressed:
				link.suppressed = suppressed
				link.save(update_fields=["suppressed"])
		return Response(_patent_link_payload(link))

	def put(self, request, patent_id, trial_id):
		return self.set_suppressed(
			request,
			patent_id,
			trial_id,
			False,
			"The patent and trial are not linked. Editors can't create patent-trial links, "
			"only restore suppressed ones: links need a shared sponsor and timing, which "
			"only the pipeline's detection computes.",
		)

	@extend_schema(
		summary="Suppress a patent-trial link (editor)",
		description=(
			"Editor-only. Hides the link between the patent and a trial everywhere "
			"(it stays, marked `suppressed`, so detection doesn't recreate it). "
			"Idempotent: an already suppressed link is returned unchanged (200). "
			"404 when the pair isn't linked, or either side is outside the editor's "
			"site scope."
		),
		request=None,
		responses={
			200: EditorPatentTrialLinkResponseSerializer,
			429: EditorErrorSerializer,
			**_PATENT_ERRORS,
		},
		auth=[{"editorServiceAuth": []}],
	)
	def delete(self, request, patent_id, trial_id):
		return self.set_suppressed(request, patent_id, trial_id, True, "The patent and trial are not linked.")


@extend_schema(
	summary="Edit history of a patent (editor)",
	description=(
		"Editor-only. Who changed this patent family's editorial content for the "
		"editor's site, its hand assignments to categories in scope, and the "
		"suppressing or restoring of its links to trials in scope, and when. "
		"Newest first. There are no relevance entries: patents have none. Only "
		"people's changes are recorded (the pipeline's category matching and link "
		"detection write no history); a change made by an API key names the key "
		"instead of a person."
	),
	parameters=[
		OpenApiParameter(
			"limit",
			OpenApiTypes.INT,
			OpenApiParameter.QUERY,
			description=f"Entries to return (default {HISTORY_DEFAULT_LIMIT}, max {HISTORY_MAX_LIMIT}).",
		)
	],
	responses={
		200: EditorPatentHistoryResponseSerializer,
		400: EDITOR_VALIDATION_ERROR,
		**_PATENT_ERRORS,
	},
	auth=[{"editorServiceAuth": []}],
)
class EditorPatentHistoryView(_EditorView):
	"""``GET /editor/patents/{patent_id}/history/``"""

	def get(self, request, patent_id):
		patent = self.get_patent(patent_id)
		limit = _history_limit(request)
		scope = self.site_scope()

		entries = _editorial_history_entries(PatentSiteContent, "patent", patent.pk, request.auth.site_id, limit)
		link_history = PatentTrialLink.history.filter(patent_id=patent.pk)
		# Scope first, then the limit, as for articles.
		visible_trials = Trials.objects.filter(
			pk__in=link_history.values("trial_id"), subjects__id__in=scope
		).values("pk")
		for row in link_history.filter(trial_id__in=visible_trials).order_by("-history_date")[:limit]:
			entries.append(
				_history_entry(
					"trial_link",
					row,
					{
						"trial_id": row.trial_id,
						"timing": row.timing,
						"basis": row.basis,
						"suppressed": row.suppressed,
					},
				)
			)
		entries += _category_history_entries(PatentCategoryAssignment, "patents", patent.pk, scope, limit)

		entries.sort(key=lambda e: e["changed_at"], reverse=True)
		return Response({"patent_id": patent.patent_id, "entries": entries[:limit]})


# --- EMA medicines ----------------------------------------------------------
#
# A medicine is global EMA reference data with no subject of its own, so it is
# reached through the categories it is linked to. Editors of a category can hide
# a link the matcher made (it stays, marked ``suppressed``, so the matcher never
# brings it back), restore it, add a hand link the matcher did not find, or delete
# a hand link. Only sites with ``medicines_enabled`` on have any of this: for the
# others every route is a 404.


@extend_schema(
	summary="Restore or add a medicine-category link (editor)",
	description=(
		"Editor-only. Makes the medicine live in the category: a link an editor "
		"suppressed is restored (200), and a pair with no link gets a new hand link "
		"(201) that the matcher never changes or removes. A link that is already live "
		"is returned unchanged (200), automatic or manual, so repeating the call "
		"records nothing new. The change applies to the whole category, on every site "
		"that shows it. The category must be one the editor can change (all of its "
		"subjects in the editor's site scope, else 403) and the site must have "
		"`medicines_enabled` on. 404 when the category is out of scope, the site has "
		"no medicines, or the medicine is not one this site can see: it must be linked "
		"(live or suppressed) to a category with a subject in the site's medicine scope. "
		"Recorded under the editor's name."
	),
	request=None,
	responses={
		200: EditorMedicineLinkResponseSerializer,
		201: EditorMedicineLinkResponseSerializer,
		**_CATEGORY_WRITE_ERRORS,
	},
	auth=[{"editorServiceAuth": []}],
)
class EditorCategoryMedicineView(_CategoryScopeMixin, _EditorView):
	"""``PUT`` (restore or add) and ``DELETE`` (suppress or delete)
	``/editor/categories/{category_id}/medicines/{medicine_id}/``.

	Rows are saved one at a time inside ``editing_as(user, "mcp")`` so
	``MedicineCategoryAssignment.history`` names the editor; a queryset ``update()``
	would record nobody. Locked with ``select_for_update`` so two overlapping calls
	queue instead of both reading the old row.
	"""

	def medicine_scope(self):
		"""Subject ids the editor's site shows MEDICINES for: its scope, but only
		when the site has ``medicines_enabled`` on (empty otherwise)."""
		return site_scope_subject_ids(self.request.auth.site_id, public_only=False, medicines=True)

	def get_category_and_medicine(self, category_id, medicine_id):
		"""The category (one this editor may change, shown with medicines) and the
		medicine (one this site can see), else 404.

		A medicine is not tied to a subject, so "can see" means it has a link, live
		or suppressed, to a category with a subject in the site's medicine scope.
		Suppressed counts: otherwise suppressing a medicine's only link would put
		it out of reach of the restore that undoes it. So does having had a link to
		this very category: a hand link that was deleted can be deleted again
		(idempotent) and added back.
		"""
		scope = self.medicine_scope()
		if not scope:
			# A site with the switch off has no medicines at all, whatever the category.
			raise NotFound("Category not found.")
		category = self.category_for_edit(category_id)
		if not {s.id for s in category.subjects.all()} & scope:
			raise NotFound("Category not found.")
		medicine = Medicine.objects.defer("raw").filter(pk=medicine_id).first()
		known = medicine is not None and (
			MedicineCategoryAssignment.objects.filter(medicine=medicine)
			.filter(category_has_subject(scope))
			.exists()
			or MedicineCategoryAssignment.history.filter(medicine_id=medicine.pk, teamcategory_id=category.pk).exists()
		)
		if not known:
			raise NotFound("Medicine not found.")
		return category, medicine

	@staticmethod
	def payload(medicine, category, link, change):
		updated_by, updated_at = _last_change(
			MedicineCategoryAssignment.history, medicine_id=medicine.pk, teamcategory_id=category.pk
		)
		return {
			"medicine_id": medicine.pk,
			"category_id": category.pk,
			"linked": link is not None and not link.suppressed,
			"source": link.source if link is not None else None,
			"suppressed": link.suppressed if link is not None else False,
			"change": change,
			"updated_by": updated_by,
			"updated_at": updated_at,
		}

	def put(self, request, category_id, medicine_id):
		category, medicine = self.get_category_and_medicine(category_id, medicine_id)
		change = "unchanged"
		with transaction.atomic(), editing_as(request.user, "mcp"):
			link, created = MedicineCategoryAssignment.objects.select_for_update().get_or_create(
				medicine=medicine,
				teamcategory=category,
				defaults={"source": CategoryAssignmentSource.MANUAL},
			)
			if created:
				change = "added"
			elif link.suppressed:
				link.suppressed = False
				link.save(update_fields=["suppressed"])
				change = "restored"
		return Response(
			self.payload(medicine, category, link, change),
			status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
		)

	@extend_schema(
		summary="Suppress or delete a medicine-category link (editor)",
		description=(
			"Editor-only. Takes the medicine out of the category. A link the matcher "
			"made is suppressed (it stays, hidden everywhere, so the matcher never "
			"brings it back); a hand link is deleted. Idempotent: a link that is "
			"already suppressed, or that does not exist, is answered 200 with "
			"`linked: false` and nothing is recorded. The change applies to the whole "
			"category. Same scope and 403/404 rules as the PUT."
		),
		request=None,
		responses={200: EditorMedicineLinkResponseSerializer, **_CATEGORY_WRITE_ERRORS},
		auth=[{"editorServiceAuth": []}],
	)
	def delete(self, request, category_id, medicine_id):
		category, medicine = self.get_category_and_medicine(category_id, medicine_id)
		change = "unchanged"
		with transaction.atomic(), editing_as(request.user, "mcp"):
			link = (
				MedicineCategoryAssignment.objects.select_for_update()
				.filter(medicine=medicine, teamcategory=category)
				.first()
			)
			if link is None:
				pass
			elif link.source == CategoryAssignmentSource.MANUAL:
				link.delete()
				link = None
				change = "deleted"
			elif not link.suppressed:
				link.suppressed = True
				link.save(update_fields=["suppressed"])
				change = "suppressed"
		return Response(self.payload(medicine, category, link, change))


# --- Read: tenants, for the MCP server's editor mount --------------------


class ServiceCredentialAuthentication(BaseAuthentication):
	"""The service credential alone, for routes that name no editor (the MCP
	server asking which sites exist before anyone has signed in)."""

	def authenticate(self, request):
		try:
			check_service_credential(request)
		except EditorAuthError as exc:
			raise AuthenticationFailed(exc.message)
		return AnonymousUser(), "service"

	def authenticate_header(self, request):
		return "Bearer"


class IsServiceCaller(permissions.BasePermission):
	def has_permission(self, request, view):
		return request.auth == "service"


@extend_schema(
	summary="List every MCP tenant, private included (MCP server)",
	description=(
		"Service-credential only. Like `GET /tenants/`, but lists every site with "
		"`mcp_enabled` and a non-empty scope, whether or not it is `api_public`, "
		"each with its full published scope. The MCP server's editor mount uses "
		"this to resolve a host to a site: a private site has an editor address "
		"but no anonymous one (`api_public` tells the two mounts apart)."
	),
	responses={200: McpTenantSerializer(many=True), 401: EditorErrorSerializer},
	auth=[{"editorServiceAuth": []}],
)
class EditorTenantsView(McpTenantsView):
	"""``GET /editor/tenants/``"""

	authentication_classes = [ServiceCredentialAuthentication]
	permission_classes = [IsServiceCaller]
	include_all_mcp_sites = True


# --- Sources: list, preview and add (source managers only) ------------------

SOURCE_FIRST_FETCH = "next pipeline run (every 12 hours), from today on"
SOURCE_NAME_PREFIX = {"rss_papers": "RSS", "rss_trials": "RSS", "pubmed": "PubMed", "ctgov": "CTGov"}
#: Sources that start from the day they are added: the importer's next run is an
#: ordinary incremental one from this anchor, not a backfill. RSS keeps no anchor.
ANCHORED_SOURCE_TYPES = ("pubmed", "ctgov")

_SOURCE_ERRORS = {
	400: EDITOR_VALIDATION_ERROR,
	401: EditorErrorSerializer,
	403: EditorErrorSerializer,
	404: EditorErrorSerializer,
	429: EditorErrorSerializer,
	503: EditorErrorSerializer,
}


class ServiceUnavailableError(APIException):
	status_code = 503
	default_code = "service_unavailable"


def _source_payload(source):
	"""A source for the list and create responses."""
	source_type = source_types.type_for(source.source_for, source.method)
	key = source_type.key if source_type else source_types.LEGACY_KEY
	settings_out = {}
	if key in source_types.EDITOR_TYPES:
		for name in source_types.editor_fields(key):
			if name == "excluded_publication_types":
				settings_out[name] = sorted(t.name for t in source.excluded_publication_types.all())
			else:
				settings_out[name] = getattr(source, name)
	return {
		"id": source.pk,
		"name": source.name,
		"source_type": key,
		"source_for": source.source_for,
		"method": source.method,
		"subject_id": source.subject_id,
		"subject_name": source.subject.subject_name if source.subject_id else None,
		"team_id": source.team_id,
		"team_name": source.team.name if source.team_id else None,
		"active": source.active,
		"settings": settings_out,
		"last_successful_fetch_at": source.last_successful_fetch_at,
	}


def _duplicate_payload(source):
	return {
		"source_id": source.pk,
		"name": source.name,
		"subject_id": source.subject_id,
		"subject_name": source.subject.subject_name if source.subject_id else None,
	}


def _describe_problems(problems):
	return "; ".join(f"{field}: {message}" for field, messages in problems.items() for message in messages)


class _SourceInspection:
	"""What the checks found for a source someone wants to add. `problems` maps a field to
	its messages and is empty when the source passes; `duplicate` is the active source of
	the same team that already does this."""

	def __init__(self, data, subject):
		self.data = data
		self.subject = subject
		self.key = data["source_type"]
		self.problems = {}
		self.duplicate = None
		self.preview = None
		self.excluded_types = []

	@property
	def refused(self):
		if self.duplicate is not None:
			return (
				f"An active source of this team already does this: source {self.duplicate.pk}"
				f" ({self.duplicate.name})."
			)
		return _describe_problems(self.problems) if self.problems else None


class _SourceView(_EditorView):
	"""Shared by the source routes: ``IsSourceManager`` on every method, and one
	``inspect`` that preview and create both run, so a preview shows exactly what
	create would decide."""

	permission_classes = [IsSourceManager]

	def subject_in_scope(self, subject_id):
		if subject_id not in set(self.site_scope()):
			raise NotFound("Subject not found.")
		subject = Subject.objects.select_related("team").filter(pk=subject_id).first()
		if subject is None:
			raise NotFound("Subject not found.")
		if subject.team_id is None:
			raise ValidationError({"subject_id": ["This subject has no team."]})
		return subject

	def read_input(self, request):
		body = EditorSourceInputSerializer(data=request.data)
		body.is_valid(raise_exception=True)
		data = body.validated_data
		return data, self.subject_in_scope(data["subject_id"])

	def inspect(self, data, subject):
		"""Run every check create runs, in the order the cheap ones come first, and say
		what the source would bring in. Saves nothing. One call goes to the outside
		service of the type (the feed, PubMed or ClinicalTrials.gov); when that service
		cannot be reached the answer is 503."""
		found = _SourceInspection(data, subject)
		key = found.key
		values = {name: data.get(name) for name in source_types.editor_fields(key)}
		problems = source_checks.check_settings(key, values)
		excluded_names = data.get("excluded_publication_types") or []
		if excluded_names:
			found.excluded_types, unknown = _publication_types(excluded_names)
			if unknown:
				problems["excluded_publication_types"] = [
					f"Unknown publication type(s): {', '.join(unknown)}. Use the names PubMed "
					"gives, e.g. Review or Clinical Trial."
				]
		found.problems = problems
		if problems:
			return found
		found.duplicate = source_checks.find_duplicate(subject.team, key, values)
		if found.duplicate is not None:
			return found
		try:
			if key in ("rss_papers", "rss_trials"):
				found.preview = source_checks.rss_preview(
					source_types.BY_KEY[key].source_for, values["link"], values.get("keyword_filter")
				)
				# The feed may live at another address than the one given.
				found.duplicate = source_checks.find_duplicate(
					subject.team, key, values, extra_links=[found.preview.final_url]
				)
			elif key == "pubmed":
				found.preview = source_checks.pubmed_preview(
					values["pubmed_query"], [t.name for t in found.excluded_types]
				)
			else:
				found.preview = source_checks.ctgov_preview(values)
		except source_checks.SourceRefused as exc:
			found.problems = {exc.field or "non_field_errors": [exc.message]}
			found.preview = None
		except source_checks.ServiceUnreachable as exc:
			raise ServiceUnavailableError(f"{exc.service} could not be reached. Try again later.")
		return found

	@staticmethod
	def preview_fields(preview):
		"""The counts, samples and PubMed's reading, as the responses carry them."""
		if preview is None:
			return {"counts": [], "samples": [], "first_run": "", "pubmed": None}
		return {
			"counts": preview.counts,
			"samples": preview.samples,
			"first_run": preview.first_run,
			"pubmed": (
				{"querytranslation": preview.querytranslation, "warnings": preview.warnings}
				if preview.querytranslation is not None
				else None
			),
		}


def _publication_types(names):
	"""``(types, unknown_names)`` for publication type names, matched ignoring case."""
	wanted = {}
	for name in names:
		wanted.setdefault(name.strip().lower(), name.strip())
	found = {}
	for publication_type in PublicationType.objects.annotate(lowered=Lower("name")).filter(
		lowered__in=list(wanted)
	):
		found.setdefault(publication_type.name.lower(), publication_type)
	unknown = [wanted[lowered] for lowered in wanted if lowered not in found]
	return list(found.values()), unknown


@extend_schema_view(
	get=extend_schema(
		summary="List the sources of the site's subjects (source managers)",
		description=(
			"Source managers only (a grant with `can_manage_sources`; any other editor gets "
			"403, reads included). Lists the sources whose subject is in the editor's site "
			"scope, of every type, so the full picture is visible. Their settings are "
			"shown for the four types that can be added here. Not paginated."
		),
		parameters=[
			OpenApiParameter("subject_id", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False),
			OpenApiParameter(
				"source_type",
				OpenApiTypes.STR,
				OpenApiParameter.QUERY,
				required=False,
				enum=sorted(source_types.BY_KEY),
			),
			OpenApiParameter(
				"active",
				OpenApiTypes.BOOL,
				OpenApiParameter.QUERY,
				required=False,
				description="`true` for active sources only, `false` for paused ones. Both when omitted.",
			),
		],
		responses={200: EditorSourceListResponseSerializer, **_SOURCE_ERRORS},
		auth=[{"editorServiceAuth": []}],
	),
	post=extend_schema(
		summary="Add a source (source managers)",
		description=(
			"Source managers only. Adds an RSS feed of papers or trials, a PubMed search or a "
			"ClinicalTrials.gov search for a subject in the editor's site scope (else 404). "
			"The source is **live at once**: active, owned by the subject's team, recorded "
			"under the editor's name, and fetched from the next pipeline run for every site "
			"that covers the subject. It brings in content from today on, not older content: "
			"PubMed and ClinicalTrials.gov sources start from the day they are added and RSS "
			"sources read the feed's current entries.\n\n"
			"Runs every check itself and never trusts that a preview was run first: the same "
			"checks as the admin (400, with the admin's messages), for RSS an address check "
			"(public hosts only; 10 s timeout, 5 MB, 3 redirects; must parse as a feed with "
			"entries), PubMed or ClinicalTrials.gov accepting the search, and duplicates (409, "
			"naming the existing source of the same team). 503 when PubMed or "
			"ClinicalTrials.gov cannot be reached; nothing is created. There is no volume "
			"limit: the counts are reported in the response, never refused. Throttled per "
			"editor and site, apart from the other editor writes (default 30 a day)."
		),
		request=EditorSourceInputSerializer,
		responses={201: EditorSourceCreateResponseSerializer, 409: EditorSourceConflictSerializer, **_SOURCE_ERRORS},
		auth=[{"editorServiceAuth": []}],
	),
)
class EditorSourcesView(_SourceView):
	"""``GET /editor/sources/`` lists, ``POST /editor/sources/`` adds a source.

	Create runs every check itself (never trusting that a preview ran), in the order
	settings, duplicate, the one outside call of the type, then saves inside
	``editing_as(user, "mcp")``. 400 for a failed check, 409 for a duplicate, 503
	when PubMed or ClinicalTrials.gov cannot be reached. Throttled per editor and
	site by ``SourceCreateThrottle``; the list is not throttled.
	"""

	def get_throttles(self):
		return [SourceCreateThrottle()] if self.request.method == "POST" else []

	def get(self, request):
		queryset = (
			Sources.objects.filter(subject_id__in=self.site_scope())
			.select_related("subject", "team")
			.prefetch_related("excluded_publication_types")
			.order_by("name", "pk")
		)
		params = request.query_params
		if params.get("subject_id"):
			try:
				queryset = queryset.filter(subject_id=int(params["subject_id"]))
			except ValueError:
				raise ValidationError({"subject_id": ["Must be an integer."]})
		if params.get("source_type"):
			source_type = source_types.BY_KEY.get(params["source_type"])
			if source_type is None:
				raise ValidationError({"source_type": [f"Must be one of: {', '.join(sorted(source_types.BY_KEY))}."]})
			queryset = queryset.filter(source_for=source_type.source_for, method=source_type.method)
		if params.get("active"):
			value = params["active"].lower()
			if value not in ("true", "false", "1", "0"):
				raise ValidationError({"active": ["Must be true or false."]})
			queryset = queryset.filter(active=value in ("true", "1"))
		results = [_source_payload(source) for source in queryset]
		return Response({"count": len(results), "results": results})

	def post(self, request):
		data, subject = self.read_input(request)
		found = self.inspect(data, subject)
		if found.duplicate is not None:
			error = ConflictError(found.refused)
			error.detail = {"detail": found.refused, "duplicate_of": _duplicate_payload(found.duplicate)}
			raise error
		if found.problems:
			raise ValidationError(found.problems)

		source_type = source_types.BY_KEY[found.key]
		fields = {
			"subject": subject,
			"team": subject.team,
			"active": True,
			"ignore_ssl": False,
			"source_for": source_type.source_for,
			"method": source_type.method,
			"name": data.get("name") or f"{SOURCE_NAME_PREFIX[found.key]} | {subject.subject_name}",
			"description": data.get("description") or None,
		}
		for name in source_types.editor_fields(found.key):
			if name != "excluded_publication_types":
				fields[name] = data.get(name) or None
		if found.key in ANCHORED_SOURCE_TYPES:
			# No backfill: the importer's first run is an ordinary incremental one
			# from now (PubMed: the last 2 days; ClinicalTrials.gov: updated in the
			# last 2 days), not the 30-day or unfiltered first run of an admin source.
			fields["last_successful_fetch_at"] = timezone.now()
		with transaction.atomic(), editing_as(request.user, "mcp"):
			source = Sources.objects.create(**fields)
			if found.excluded_types:
				source.excluded_publication_types.set(found.excluded_types)
		source = Sources.objects.select_related("subject", "team").prefetch_related("excluded_publication_types").get(pk=source.pk)
		return Response(
			{
				"source": _source_payload(source),
				**self.preview_fields(found.preview),
				"first_fetch": SOURCE_FIRST_FETCH,
			},
			status=status.HTTP_201_CREATED,
		)


@extend_schema(
	summary="Preview a source before adding it (source managers)",
	description=(
		"Source managers only. Takes the same body as `POST /editor/sources/`, runs the "
		"same checks and saves nothing. Says what the source would bring in: RSS, the "
		"entries in the feed now and how many pass the keyword filter; PubMed, the papers "
		"created in the last 30 days (the monthly volume to expect; the first run brings in "
		"only the last 2 days) with PubMed's reading of the search; ClinicalTrials.gov, all "
		"matching trials and those updated in the last 30 days (the first run brings in only "
		"those updated in the last 2 days). Up to 5 sample titles. `refused` holds the reason "
		"create would refuse (the checks, or a duplicate in `duplicate_of`), else null. A "
		"refusal is still a 200. 503 when PubMed or ClinicalTrials.gov cannot be reached. "
		"Throttled per editor and site (default 30 an hour), apart from creates."
	),
	request=EditorSourceInputSerializer,
	responses={200: EditorSourcePreviewResponseSerializer, **_SOURCE_ERRORS},
	auth=[{"editorServiceAuth": []}],
)
class EditorSourcePreviewView(_SourceView):
	"""``POST /editor/sources/preview/``: the checks and counts of create, saving
	nothing. A refusal is a 200 with ``refused`` set; only an unreachable service is
	an error (503). Throttled per editor and site by ``SourcePreviewThrottle``."""

	throttle_classes = [SourcePreviewThrottle]

	def post(self, request):
		data, subject = self.read_input(request)
		found = self.inspect(data, subject)
		return Response(
			{
				"source_type": found.key,
				"subject_id": subject.pk,
				**self.preview_fields(found.preview),
				"refused": found.refused,
				"duplicate_of": _duplicate_payload(found.duplicate) if found.duplicate is not None else None,
			}
		)


# --- Read: the existing viewsets, behind editor authentication ------------


def _editor_read_view(view_class):
	"""A subclass of an existing read view that accepts only editors.

	The viewset is unchanged: ``gregory.visibility`` gives it the editor's one
	site as its scope, so every filter, serializer and cache key works as it
	does for any other caller. Left out of the OpenAPI schema, which already
	documents the originals.
	"""
	return extend_schema(exclude=True)(
		type(
			f"Editor{view_class.__name__}",
			(view_class,),
			{
				"__module__": __name__,
				"__doc__": view_class.__doc__,
				"authentication_classes": [EditorServiceAuthentication],
				"permission_classes": [IsEditorReader],
			},
		)
	)


EditorArticleViewSet = _editor_read_view(ArticleViewSet)
EditorTrialViewSet = _editor_read_view(TrialViewSet)
EditorAuthorsViewSet = _editor_read_view(AuthorsViewSet)
EditorSubjectsViewSet = _editor_read_view(SubjectsViewSet)
EditorSponsorViewSet = _editor_read_view(SponsorViewSet)
EditorPatentViewSet = _editor_read_view(PatentViewSet)
EditorMedicineViewSet = _editor_read_view(MedicineViewSet)
class EditorStatsView(_editor_read_view(StatsView)):
	"""``/editor/stats/``: counts over the editor's site scope.

	StatsView narrows subjects to the teams of the caller's visible
	organisations, but a site's ``scope_subjects`` may be curated from any team.
	With ``visible_org_ids`` unset it skips that narrowing and scopes every
	count by ``visible_subject_ids`` alone, which for an editor is exactly the
	site's scope, the same rows ``/editor/articles/`` shows.
	"""

	def get(self, request, *args, **kwargs):
		request.visible_org_ids = None
		return super().get(request, *args, **kwargs)

#: The read viewsets mounted under /editor/, same URLs as the originals.
EDITOR_READ_ROUTES = [
	("articles", EditorArticleViewSet, "editor-articles"),
	("trials", EditorTrialViewSet, "editor-trials"),
	("authors", EditorAuthorsViewSet, "editor-authors"),
	("categories", EditorCategoryViewSet, "editor-categories"),
	("subjects", EditorSubjectsViewSet, "editor-subjects"),
	("sponsors", EditorSponsorViewSet, "editor-sponsors"),
	("patents", EditorPatentViewSet, "editor-patents"),
	("medicines", EditorMedicineViewSet, "editor-medicines"),
]
