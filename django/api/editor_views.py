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
"""

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.db import IntegrityError, transaction
from django.db.models import Case, When
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

from api.serializers import McpTenantSerializer
from api.views import (
	ArticleViewSet,
	AuthorsViewSet,
	CategoryViewSet,
	McpTenantsView,
	SponsorViewSet,
	StatsView,
	SubjectsViewSet,
	TrialViewSet,
)
from api.schema_serializers import (
	EditorCategoryAssignmentResponseSerializer,
	EditorCategoryCreateSerializer,
	EditorCategoryResponseSerializer,
	EditorCategoryUnassignResponseSerializer,
	EditorCategoryUpdateSerializer,
	EditorErrorSerializer,
	EditorialUpdateRequestSerializer,
	EditorialUpdateResponseSerializer,
	EditorHistoryResponseSerializer,
	EditorResolveResponseSerializer,
	EditorTrialLinkRequestSerializer,
	EditorTrialLinkResponseSerializer,
	EditorTrialUnlinkResponseSerializer,
	RelevanceRequestSerializer,
	RelevanceResponseSerializer,
)
from gregory.editor_history import editing_as
from gregory.models import (
	ArticleCategoryAssignment,
	Articles,
	ArticleSiteContent,
	ArticleSubjectRelevance,
	ArticleTrialReference,
	CategoryAssignmentSource,
	CategoryType,
	Subject,
	TeamCategory,
	Trials,
)
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
		body = EditorialUpdateRequestSerializer(data=request.data)
		body.is_valid(raise_exception=True)
		fields = {key: (value or None) for key, value in body.validated_data.items()}
		if not fields:
			raise ValidationError({"non_field_errors": ["Send `takeaways` and/or `summary_plain_english`."]})

		site_id = request.auth.site_id
		with transaction.atomic(), editing_as(request.user, "mcp"):
			# Locked so two overlapping PATCHes queue rather than each read the
			# old row, and only the sent columns are written, so an omitted
			# field keeps whatever another request just stored.
			content, created = ArticleSiteContent.objects.select_for_update().get_or_create(
				article=article, site_id=site_id, defaults=fields
			)
			if not created:
				changed = [k for k, v in fields.items() if getattr(content, k) != v]
				for key in changed:
					setattr(content, key, fields[key])
				if changed:
					content.save(update_fields=[*changed, "updated_at"])
		content.refresh_from_db()
		return Response(self.serialize(content))

	@staticmethod
	def serialize(content):
		updated_by, _ = _last_change(ArticleSiteContent.history, id=content.id)
		return {
			"article_id": content.article_id,
			"site_id": content.site_id,
			"takeaways": content.takeaways,
			"summary_plain_english": content.summary_plain_english,
			"updated_by": updated_by,
			"updated_at": content.updated_at,
		}


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
			"(case-insensitive). `subject_ids` replaces the subjects, which must be "
			"in scope and in the category's team. The slug and team never change. "
			"Every subject of the category must be in the editor's site scope, "
			"since the change shows wherever the category does: 404 when none is, "
			"403 when only some are. A change to terms, subjects or `match_scope` "
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
		with transaction.atomic(), editing_as(request.user, "mcp"):
			assignment, created = ArticleCategoryAssignment.objects.select_for_update().get_or_create(
				articles=article, teamcategory=category, defaults={"source": CategoryAssignmentSource.MANUAL}
			)
			if not created and assignment.source != CategoryAssignmentSource.MANUAL:
				assignment.source = CategoryAssignmentSource.MANUAL
				assignment.save()
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
		with transaction.atomic(), editing_as(request.user, "mcp"):
			assignment = ArticleCategoryAssignment.objects.filter(articles=article, teamcategory=category).first()
			if assignment is None:
				raise NotFound("The article is not in this category.")
			if assignment.source != CategoryAssignmentSource.MANUAL:
				raise ConflictError(
					"The pipeline assigned this article from the category's terms and would add it back. "
					"Change the category's terms instead."
				)
			assignment.delete()
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
		try:
			limit = int(request.query_params.get("limit", HISTORY_DEFAULT_LIMIT))
		except ValueError:
			raise ValidationError({"limit": "Must be an integer."})
		if not 1 <= limit <= HISTORY_MAX_LIMIT:
			raise ValidationError({"limit": f"Must be between 1 and {HISTORY_MAX_LIMIT}."})
		scope = self.site_scope()

		entries = []
		editorial = ArticleSiteContent.history.filter(article_id=article.pk, site_id=request.auth.site_id)
		for row in editorial.order_by("-history_date")[:limit]:
			entries.append(
				_history_entry(
					"editorial",
					row,
					{
						"site_id": row.site_id,
						"takeaways": row.takeaways,
						"summary_plain_english": row.summary_plain_english,
					},
				)
			)
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

		category_history = ArticleCategoryAssignment.history.filter(articles_id=article.pk)
		visible_categories = TeamCategory.objects.filter(
			pk__in=category_history.values("teamcategory_id"), subjects__id__in=scope
		).values("pk")
		for row in category_history.filter(teamcategory_id__in=visible_categories).order_by("-history_date")[:limit]:
			entries.append(
				_history_entry("category", row, {"category_id": row.teamcategory_id, "source": row.source})
			)

		entries.sort(key=lambda e: e["changed_at"], reverse=True)
		return Response({"article_id": article.article_id, "entries": entries[:limit]})


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
]
