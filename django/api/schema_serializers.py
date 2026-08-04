"""Plain (non-ModelSerializer) response shapes used only for OpenAPI schema
generation via drf-spectacular's ``@extend_schema(responses=...)``.

These views build their response dicts by hand (aggregation queries, not a
queryset of model instances), so DRF/drf-spectacular cannot infer a response
shape from a ``serializer_class`` the normal way. Declaring the shape here
keeps /api/schema/ honest about what these endpoints actually return.
"""

from django_filters import rest_framework as filters
from rest_framework import serializers


def filterset_request_schema(filterset_class, required=(), extra_description=""):
	"""Build a raw OpenAPI request-body schema from a django-filter ``FilterSet``.

	Used for the ``POST /*/search/`` endpoints (see
	``api.views.BodyParamsAsQueryParamsMixin``), which accept every filter
	field of the corresponding FilterSet in the JSON body instead of (or in
	addition to) the query string. Deriving the body schema from the same
	``base_filters`` the GET query parameters come from means the two stay in
	sync automatically — no hand-maintained duplicate field list to drift.

	List-valued filters (``BaseCSVFilter``/``BaseInFilter``, e.g. ``subjects``,
	``nct``) accept either a JSON array or a comma-separated string in the
	body — ``BodyParamsAsQueryParamsMixin`` comma-joins a JSON array before
	merging it into query_params. The schema advertises the array form since
	it's the natural JSON shape; the comma-separated string form documented
	on the equivalent GET query parameter also works.
	"""
	properties = {}
	for name, filter_field in filterset_class.base_filters.items():
		description = filter_field.extra.get("help_text") or filter_field.label or ""
		if isinstance(filter_field, filters.BooleanFilter):
			schema = {"type": "boolean"}
		elif isinstance(filter_field, filters.NumberFilter):
			schema = {"type": "number"}
		elif isinstance(filter_field, filters.DateFilter):
			schema = {"type": "string", "format": "date"}
		elif isinstance(filter_field, (filters.BaseCSVFilter, filters.BaseInFilter)):
			schema = {"type": "array", "items": {"type": "string"}}
		else:
			schema = {"type": "string"}
		if description:
			schema["description"] = description
		properties[name] = schema
	schema = {"type": "object", "properties": properties}
	if required:
		schema["required"] = list(required)
	if extra_description:
		schema["description"] = extra_description
	return schema


class ErrorResponseSerializer(serializers.Serializer):
	"""Generic ``{"error": "..."}`` shape used by hand-written error responses."""

	error = serializers.CharField()


class SubjectCountSerializer(serializers.Serializer):
	subject_id = serializers.IntegerField()
	subject_name = serializers.CharField()
	count = serializers.IntegerField()


class ArticlesByAccessSerializer(serializers.Serializer):
	open = serializers.IntegerField()
	restricted = serializers.IntegerField()
	unknown = serializers.IntegerField()


class ArticlesStatsSerializer(serializers.Serializer):
	"""Response of ``GET /articles/stats/`` — see ArticleViewSet.build_stats_payload."""

	total = serializers.IntegerField(help_text="Distinct articles matching the filtered queryset.")
	by_access = ArticlesByAccessSerializer()
	relevant = serializers.IntegerField(
		help_text="Count matching the same semantics as ?relevant=true on the list endpoint."
	)
	retracted = serializers.IntegerField()
	missing_doi = serializers.IntegerField()
	by_subject = SubjectCountSerializer(many=True)


class TrialsByCountrySerializer(serializers.Serializer):
	country = serializers.CharField(
		allow_null=True, help_text="ISO 3166-1 alpha-2 code, or null for trials with no TrialCountry rows."
	)
	count = serializers.IntegerField()


class TrialsByYearSerializer(serializers.Serializer):
	year = serializers.IntegerField(allow_null=True, help_text="Registration year, or null for trials with no date_registration.")
	count = serializers.IntegerField()


class TrialsBySponsorSerializer(serializers.Serializer):
	sponsor_id = serializers.IntegerField()
	slug = serializers.CharField()
	name = serializers.CharField()
	sponsor_type = serializers.CharField(allow_null=True)
	count = serializers.IntegerField()


class TrialsStatsSerializer(serializers.Serializer):
	"""Response of ``GET /trials/stats/`` — see TrialViewSet.build_stats_payload.

	The recruitment-status bucket keys (``recruiting``, ``completed``, ...) are
	one key per ``TrialRecruitmentStatus`` value plus ``no_status`` — declared
	here as a representative subset via ``extra_fields``-style documentation in
	the field help text rather than one IntegerField per enum member, since the
	bucket set is derived from the enum at runtime (see
	docs/03-api-and-rss-feeds.md for the full key list).
	"""

	total = serializers.IntegerField()
	no_status = serializers.IntegerField()
	by_subject = SubjectCountSerializer(many=True)
	by_phase = serializers.DictField(
		child=serializers.IntegerField(),
		help_text="{phase_slug: count, ..., 'no_phase': count} — one key per TrialPhase value.",
	)
	by_region = serializers.DictField(
		child=serializers.IntegerField(),
		help_text=(
			"{region_slug: count, ..., 'no_region': count} — one key per TrialRegion value. "
			"Does not sum to total (a trial can span multiple regions)."
		),
	)
	by_country = TrialsByCountrySerializer(many=True)
	by_year = TrialsByYearSerializer(many=True)
	by_sponsor = TrialsBySponsorSerializer(
		many=True, help_text="Top 25 canonical sponsors by count, excluding unresolved sponsors."
	)
	no_sponsor = serializers.IntegerField()
	by_sponsor_type = serializers.DictField(
		child=serializers.IntegerField(),
		help_text="{sponsor_type_slug: count, ..., 'no_type': count} — one key per SponsorType value.",
	)
	by_modality = serializers.DictField(
		child=serializers.IntegerField(),
		help_text=(
			"{modality_slug: count, ..., 'no_modality': count} — one key per CategoryModality "
			"value. Not a partition of total (a trial can carry categories of several modalities)."
		),
	)
	by_study_type = serializers.DictField(
		child=serializers.IntegerField(),
		help_text="{study_type_slug: count, ..., 'no_study_type': count} — one key per TrialStudyType value.",
	)
	by_sex = serializers.DictField(
		child=serializers.IntegerField(),
		help_text="{sex_slug: count, ..., 'no_sex_data': count} — one key per TrialSexEligibility value.",
	)


class TrialSiteRowSerializer(serializers.Serializer):
	"""Row shape of ``GET /trials/sites/`` — see TrialViewSet.sites."""

	trial_id = serializers.IntegerField()
	name = serializers.CharField(allow_null=True)
	city = serializers.CharField(allow_null=True)
	country = serializers.CharField(allow_null=True)
	latitude = serializers.FloatField(allow_null=True)
	longitude = serializers.FloatField(allow_null=True)


class GlobalStatsByDomainSerializer(serializers.Serializer):
	domain = serializers.CharField()
	count = serializers.IntegerField()


class GlobalStatsSourcesSerializer(serializers.Serializer):
	total = serializers.IntegerField(help_text="Distinct source domains.")
	by_type = serializers.DictField(
		child=serializers.IntegerField(), help_text="{source_for: distinct domain count}"
	)
	by_domain = GlobalStatsByDomainSerializer(many=True)


class GlobalStatsBySubjectSerializer(serializers.Serializer):
	subject_id = serializers.IntegerField()
	subject_name = serializers.CharField()
	articles = serializers.IntegerField()
	trials = serializers.IntegerField()
	authors = serializers.IntegerField()
	sources = serializers.IntegerField()


class GlobalStatsSerializer(serializers.Serializer):
	"""Response of ``GET /stats/`` — see StatsView.get."""

	articles = serializers.IntegerField()
	trials = serializers.IntegerField()
	subscribers = serializers.IntegerField()
	authors = serializers.IntegerField()
	sources = GlobalStatsSourcesSerializer()
	by_subject = GlobalStatsBySubjectSerializer(
		many=True,
		help_text="Present only when ?subject= is given — every in-scope subject, including zero-count ones.",
	)
