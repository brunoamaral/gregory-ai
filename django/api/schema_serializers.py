"""Plain (non-ModelSerializer) response shapes used only for OpenAPI schema
generation via drf-spectacular's ``@extend_schema(responses=...)``.

These views build their response dicts by hand (aggregation queries, not a
queryset of model instances), so DRF/drf-spectacular cannot infer a response
shape from a ``serializer_class`` the normal way. Declaring the shape here
keeps /api/schema/ honest about what these endpoints actually return.
"""

from django.utils.text import slugify
from django_filters import rest_framework as filters
from rest_framework import serializers

from gregory import source_types
from gregory.models import (
	CategoryMatchScope,
	CategoryModality,
	CategoryType,
	MedicineEvent,
	PatentTrialLink,
)

# Named for settings.SPECTACULAR_SETTINGS["ENUM_NAME_OVERRIDES"], which can only
# point at module-level names: ``kind`` and ``basis`` are also field names on other
# models, so without a fixed name the generated enums get hash suffixes.
MEDICINE_EVENT_KIND_CHOICES = MedicineEvent.Kind.choices
MEDICINE_EVENT_BASIS_CHOICES = MedicineEvent.Basis.choices
PATENT_TRIAL_LINK_BASIS_CHOICES = PatentTrialLink.BASIS_CHOICES
EDITOR_HISTORY_KIND_CHOICES = ["editorial", "relevance", "trial_link", "category"]


def filterset_request_schema(
	filterset_class, required=(), extra_description="", extra_properties=None
):
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
	properties.update(extra_properties or {})
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


class PatentsByPriorityYearSerializer(serializers.Serializer):
	"""One entry of ``GET /patents/stats/`` ``by_priority_year``."""

	year = serializers.IntegerField(allow_null=True)
	count = serializers.IntegerField()


class PatentsStatsSerializer(serializers.Serializer):
	"""Response of ``GET /patents/stats/`` — see PatentViewSet.build_stats_payload."""

	total = serializers.IntegerField()
	with_grant = serializers.IntegerField(
		help_text="Families with a granted publication (has_grant), a heuristic on kind codes."
	)
	with_trial_link = serializers.IntegerField(
		help_text=(
			"Families with at least one visible, unsuppressed trial link. "
			"Category-basis links only unless include_subject_links=true."
		)
	)
	by_priority_year = PatentsByPriorityYearSerializer(
		many=True,
		help_text=(
			"Ascending by year of the earliest priority date; zero-count years "
			"omitted, a trailing {year: null} entry for families with no "
			"priority date (omitted when 0). Sums to total."
		),
	)


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


# --- /editor/ routes (MCP editor access) -- see api/editor_views.py ---------


class EditorErrorSerializer(serializers.Serializer):
	"""DRF's standard error body on the ``/editor/`` routes."""

	detail = serializers.CharField()
	article_ids = serializers.ListField(
		child=serializers.IntegerField(),
		required=False,
		help_text="On 409 from the DOI lookup: the articles that share the DOI.",
	)
	patent_ids = serializers.ListField(
		child=serializers.IntegerField(),
		required=False,
		help_text="On 409 from the publication-number lookup: the families that match.",
	)
	category_id = serializers.IntegerField(
		required=False,
		help_text=(
			"On 409 from creating a category: the existing category that holds the "
			"slug, when it is inside the editor's site scope."
		),
	)


class EditorResolveResponseSerializer(serializers.Serializer):
	article_id = serializers.IntegerField()
	doi = serializers.CharField()
	title = serializers.CharField()


class EditorialUpdateRequestSerializer(serializers.Serializer):
	takeaways = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False)
	summary_plain_english = serializers.CharField(
		required=False, allow_blank=True, trim_whitespace=False
	)


class EditorialUpdateResponseSerializer(serializers.Serializer):
	article_id = serializers.IntegerField()
	site_id = serializers.IntegerField()
	takeaways = serializers.CharField(allow_null=True)
	summary_plain_english = serializers.CharField(allow_null=True)
	updated_by = serializers.CharField(
		allow_null=True, help_text="Name and email of whoever last changed the row."
	)
	updated_at = serializers.DateTimeField()


class RelevanceRequestSerializer(serializers.Serializer):
	is_relevant = serializers.BooleanField(
		allow_null=True,
		help_text="true, false, or null for not reviewed.",
	)


class RelevanceResponseSerializer(serializers.Serializer):
	article_id = serializers.IntegerField()
	subject_id = serializers.IntegerField()
	is_relevant = serializers.BooleanField(allow_null=True)
	updated_by = serializers.CharField(allow_null=True)
	updated_at = serializers.DateTimeField(allow_null=True)


class EditorTrialLinkRequestSerializer(serializers.Serializer):
	trial_id = serializers.IntegerField(min_value=1)


class EditorTrialLinkResponseSerializer(serializers.Serializer):
	article_id = serializers.IntegerField()
	trial_id = serializers.IntegerField()
	source = serializers.ChoiceField(choices=["auto", "manual"])
	identifier_type = serializers.CharField()
	identifier_value = serializers.CharField()
	updated_by = serializers.CharField(allow_null=True)
	updated_at = serializers.DateTimeField(allow_null=True)


class EditorTrialUnlinkResponseSerializer(serializers.Serializer):
	article_id = serializers.IntegerField()
	trial_id = serializers.IntegerField()
	removed = serializers.ListField(
		child=serializers.CharField(),
		help_text=(
			"Which kinds of link were removed, `manual` and/or `auto`: a manual "
			"one is deleted, an auto-detected one is hidden."
		),
	)


class EditorHistoryEntrySerializer(serializers.Serializer):
	kind = serializers.ChoiceField(choices=EDITOR_HISTORY_KIND_CHOICES)
	change = serializers.ChoiceField(choices=["created", "updated", "deleted"])
	changed_at = serializers.DateTimeField()
	changed_by = serializers.CharField(
		allow_null=True,
		help_text="Name and email, or the API key's name. Null for a change with no recorded author (the pipeline).",
	)
	via = serializers.ChoiceField(choices=["mcp", "api_key", "admin"], allow_null=True)
	details = serializers.DictField(
		help_text=(
			"The values after the change: the editorial text, the relevance, the "
			"trial link's source, or the category and how it was assigned."
		)
	)


class EditorHistoryResponseSerializer(serializers.Serializer):
	article_id = serializers.IntegerField()
	entries = EditorHistoryEntrySerializer(many=True)



# --- /editor/ patent routes ----------------------------------------------------


class EditorPatentResolveResponseSerializer(serializers.Serializer):
	patent_id = serializers.IntegerField()
	family_id = serializers.CharField(allow_null=True)
	title = serializers.CharField()
	representative_publication = serializers.CharField()


class EditorPatentEditorialUpdateResponseSerializer(serializers.Serializer):
	patent_id = serializers.IntegerField()
	site_id = serializers.IntegerField()
	takeaways = serializers.CharField(allow_null=True)
	summary_plain_english = serializers.CharField(allow_null=True)
	updated_by = serializers.CharField(
		allow_null=True, help_text="Name and email of whoever last changed the row."
	)
	updated_at = serializers.DateTimeField()


class EditorPatentCategoryAssignmentResponseSerializer(serializers.Serializer):
	patent_id = serializers.IntegerField()
	category_id = serializers.IntegerField()
	source = serializers.CharField(
		help_text="Always `manual` after an editor assigns it; the pipeline never removes a manual assignment.",
	)
	updated_by = serializers.CharField(allow_null=True)
	updated_at = serializers.DateTimeField(allow_null=True)


class EditorPatentCategoryUnassignResponseSerializer(serializers.Serializer):
	patent_id = serializers.IntegerField()
	category_id = serializers.IntegerField()
	removed = serializers.CharField(help_text="Always `manual`: only a hand assignment can be removed.")


class EditorPatentTrialLinkResponseSerializer(serializers.Serializer):
	patent_id = serializers.IntegerField()
	trial_id = serializers.IntegerField()
	suppressed = serializers.BooleanField(
		help_text="True after a DELETE (the link is hidden everywhere), false after a PUT (restored)."
	)
	timing = serializers.CharField()
	basis = serializers.CharField()
	updated_by = serializers.CharField(allow_null=True)
	updated_at = serializers.DateTimeField(allow_null=True)


class EditorMedicineLinkResponseSerializer(serializers.Serializer):
	medicine_id = serializers.IntegerField()
	category_id = serializers.IntegerField()
	linked = serializers.BooleanField(
		help_text="True when the medicine is live in the category (shown by the API and the feeds)."
	)
	source = serializers.CharField(
		allow_null=True,
		help_text="`manual` or `automatic`; null when the pair has no link (a hand link was deleted).",
	)
	suppressed = serializers.BooleanField(
		help_text="True when an editor hid an automatic link; the matcher never brings it back."
	)
	change = serializers.CharField(
		help_text=(
			"What this call did: `added` (a new hand link), `restored` (a suppressed link "
			"is live again), `suppressed` (an automatic link is now hidden), `deleted` (a "
			"hand link was removed) or `unchanged` (it was already as asked)."
		)
	)
	updated_by = serializers.CharField(allow_null=True)
	updated_at = serializers.DateTimeField(allow_null=True)


class EditorPatentHistoryResponseSerializer(serializers.Serializer):
	patent_id = serializers.IntegerField()
	entries = EditorHistoryEntrySerializer(many=True)


# --- /editor/ category routes -------------------------------------------------

_TERMS_HELP = (
	"Words or phrases matched against titles and summaries, case-insensitively. "
	"Blank entries are dropped and repeats (ignoring case) kept once."
)


_EXCLUDE_TERMS_HELP = (
	"Words or phrases that keep content out of the category: content containing "
	"any of them in a searched field is never assigned automatically, whatever its "
	"score. Matched like `category_terms`. Manual assignments are not affected."
)


def clean_terms(terms):
	"""Strip, drop blanks and keep the first of any case-insensitive repeat."""
	seen = set()
	out = []
	for term in terms or []:
		term = term.strip()
		if term and term.lower() not in seen:
			seen.add(term.lower())
			out.append(term)
	return out


class EditorCategoryCreateSerializer(serializers.Serializer):
	category_name = serializers.CharField(max_length=200)
	subject_ids = serializers.ListField(
		child=serializers.IntegerField(min_value=1),
		min_length=1,
		help_text=(
			"Subjects the category applies to. Each must be in the editor's site "
			"scope and all must belong to one team, which becomes the category's team."
		),
	)
	category_terms = serializers.ListField(
		child=serializers.CharField(max_length=100, allow_blank=True, trim_whitespace=False),
		required=False,
		default=list,
		help_text=_TERMS_HELP + " At least one is required for an automatic category.",
	)
	category_exclude_terms = serializers.ListField(
		child=serializers.CharField(max_length=100, allow_blank=True, trim_whitespace=False),
		required=False,
		default=list,
		help_text=_EXCLUDE_TERMS_HELP + " Optional, also for an automatic category.",
	)
	category_description = serializers.CharField(required=False, allow_blank=True, default="")
	category_slug = serializers.SlugField(
		max_length=50,
		required=False,
		help_text="Defaults to the slugified name. Slugs are unique across every team.",
	)
	modality = serializers.ChoiceField(
		choices=CategoryModality.choices, required=False, allow_null=True, default=None
	)
	category_type = serializers.ChoiceField(
		choices=CategoryType.choices,
		default=CategoryType.AUTOMATIC,
		help_text=(
			"`automatic` (default): the pipeline assigns content by the terms. "
			"`manual`: only hand assignments."
		),
	)
	match_scope = serializers.ChoiceField(
		choices=CategoryMatchScope.choices, default=CategoryMatchScope.TITLE_SUMMARY
	)

	def validate_category_terms(self, value):
		return clean_terms(value)

	def validate_category_exclude_terms(self, value):
		return clean_terms(value)

	def validate_category_name(self, value):
		value = value.strip()
		if not value:
			raise serializers.ValidationError("This field may not be blank.")
		return value

	def validate(self, data):
		if data["category_type"] == CategoryType.AUTOMATIC and not data["category_terms"]:
			raise serializers.ValidationError(
				{"category_terms": ["An automatic category needs at least one term."]}
			)
		if not data.get("category_slug") and not slugify(data["category_name"]):
			raise serializers.ValidationError(
				{"category_slug": ["The name has no letters or digits to build a slug from; send one."]}
			)
		return data


class EditorCategoryUpdateSerializer(serializers.Serializer):
	category_name = serializers.CharField(max_length=200, required=False)
	category_description = serializers.CharField(required=False, allow_blank=True)
	modality = serializers.ChoiceField(choices=CategoryModality.choices, required=False, allow_null=True)
	match_scope = serializers.ChoiceField(choices=CategoryMatchScope.choices, required=False)
	subject_ids = serializers.ListField(
		child=serializers.IntegerField(min_value=1),
		min_length=1,
		required=False,
		help_text="Replaces the category's subjects. Each must be in scope and in the category's team.",
	)
	category_terms = serializers.ListField(
		child=serializers.CharField(max_length=100, allow_blank=True, trim_whitespace=False),
		required=False,
		help_text="Replaces every term. " + _TERMS_HELP + " Not with `add_terms` or `remove_terms`.",
	)
	add_terms = serializers.ListField(
		child=serializers.CharField(max_length=100, allow_blank=True, trim_whitespace=False),
		required=False,
		help_text="Terms to add to the current ones; a term already there (ignoring case) is skipped.",
	)
	remove_terms = serializers.ListField(
		child=serializers.CharField(max_length=100, allow_blank=True, trim_whitespace=False),
		required=False,
		help_text="Terms to remove, matched ignoring case. A term that isn't there is ignored.",
	)
	category_exclude_terms = serializers.ListField(
		child=serializers.CharField(max_length=100, allow_blank=True, trim_whitespace=False),
		required=False,
		help_text=(
			"Replaces every excluded term. " + _EXCLUDE_TERMS_HELP
			+ " Not with `add_exclude_terms` or `remove_exclude_terms`."
		),
	)
	add_exclude_terms = serializers.ListField(
		child=serializers.CharField(max_length=100, allow_blank=True, trim_whitespace=False),
		required=False,
		help_text="Excluded terms to add; one already there (ignoring case) is skipped.",
	)
	remove_exclude_terms = serializers.ListField(
		child=serializers.CharField(max_length=100, allow_blank=True, trim_whitespace=False),
		required=False,
		help_text="Excluded terms to remove, matched ignoring case. One that isn't there is ignored.",
	)

	def validate_category_name(self, value):
		value = value.strip()
		if not value:
			raise serializers.ValidationError("This field may not be blank.")
		return value

	def validate(self, data):
		if not data:
			raise serializers.ValidationError({"non_field_errors": ["Send at least one field to change."]})
		if "category_terms" in data and ("add_terms" in data or "remove_terms" in data):
			raise serializers.ValidationError(
				{"category_terms": ["Send either category_terms, or add_terms and/or remove_terms."]}
			)
		if "category_exclude_terms" in data and (
			"add_exclude_terms" in data or "remove_exclude_terms" in data
		):
			raise serializers.ValidationError(
				{
					"category_exclude_terms": [
						"Send either category_exclude_terms, or add_exclude_terms and/or remove_exclude_terms."
					]
				}
			)
		for key in (
			"category_terms",
			"add_terms",
			"remove_terms",
			"category_exclude_terms",
			"add_exclude_terms",
			"remove_exclude_terms",
		):
			if key in data:
				data[key] = clean_terms(data[key])
		return data


class EditorCategoryResponseSerializer(serializers.Serializer):
	id = serializers.IntegerField()
	category_name = serializers.CharField()
	category_slug = serializers.CharField()
	category_description = serializers.CharField(allow_null=True)
	category_terms = serializers.ListField(child=serializers.CharField())
	category_exclude_terms = serializers.ListField(child=serializers.CharField())
	modality = serializers.CharField(allow_null=True)
	category_type = serializers.ChoiceField(choices=CategoryType.choices)
	match_scope = serializers.ChoiceField(choices=CategoryMatchScope.choices)
	team_id = serializers.IntegerField()
	subject_ids = serializers.ListField(child=serializers.IntegerField())
	article_count = serializers.IntegerField()
	trials_count = serializers.IntegerField()
	last_synced_at = serializers.DateTimeField(
		allow_null=True,
		help_text=(
			"When the pipeline last matched content to this category. Null for a "
			"new category; after a change to terms, subjects or scope the next "
			"pipeline run re-matches all content for it."
		),
	)
	updated_by = serializers.CharField(allow_null=True, help_text="Who last changed the category.")
	updated_at = serializers.DateTimeField(allow_null=True)


class EditorCategoryAssignmentResponseSerializer(serializers.Serializer):
	article_id = serializers.IntegerField()
	category_id = serializers.IntegerField()
	# CharField rather than a ChoiceField: CategoryAssignmentSource has the same
	# values as CategoryType, and two enums over one choice set make
	# drf-spectacular warn (and name them arbitrarily).
	source = serializers.CharField(
		help_text="Always `manual` after an editor assigns it; the pipeline never removes a manual assignment.",
	)
	updated_by = serializers.CharField(allow_null=True)
	updated_at = serializers.DateTimeField(allow_null=True)


class EditorCategoryUnassignResponseSerializer(serializers.Serializer):
	article_id = serializers.IntegerField()
	category_id = serializers.IntegerField()
	removed = serializers.CharField(help_text="Always `manual`: only a hand assignment can be removed.")


# --- Editor sources (/editor/sources/) ---------------------------------------

_SOURCE_COMMON_FIELDS = ("source_type", "subject_id", "name", "description")


class EditorSourceInputSerializer(serializers.Serializer):
	"""The body of ``POST /editor/sources/`` and ``POST /editor/sources/preview/``.

	Which settings apply depends on `source_type`: a setting that belongs to another
	type is refused (400), and so is any field not listed here, so a stray `ignore_ssl`
	or `team_id` is never silently ignored. Empty values of another type's settings
	are tolerated.
	"""

	source_type = serializers.ChoiceField(
		choices=[(key, source_types.BY_KEY[key].label) for key in source_types.EDITOR_TYPES],
		help_text=(
			"`rss_papers`: an RSS feed of science papers. `rss_trials`: an RSS feed of "
			"clinical trials. `pubmed`: a PubMed search. `ctgov`: a ClinicalTrials.gov search."
		),
	)
	subject_id = serializers.IntegerField(
		min_value=1,
		help_text=(
			"The subject everything this source brings in is tagged with. Must be in "
			"the editor's site scope (else 404). The source belongs to the subject's team."
		),
	)
	link = serializers.CharField(
		max_length=2000,
		required=False,
		allow_blank=True,
		allow_null=True,
		help_text=(
			"`rss_papers`, `rss_trials`: the feed's address (required). Must be http or "
			"https and resolve to public addresses only."
		),
	)
	keyword_filter = serializers.CharField(
		max_length=2000,
		required=False,
		allow_blank=True,
		allow_null=True,
		help_text=(
			"`rss_papers`: keep only papers whose title or summary contains one of these "
			'keywords. Comma-separated; quote phrases: "multiple sclerosis", alzheimer. A keyword '
			"matches anywhere in a word; end it with `*` to match only words that start with it "
			'(`oligodendro*` finds "oligodendrocyte", not "preoligodendrocyte").'
		),
	)
	pubmed_query = serializers.CharField(
		max_length=4000,
		required=False,
		allow_blank=True,
		allow_null=True,
		help_text=(
			"`pubmed` (required): the PubMed search. No date clause: Gregory adds the "
			"creation-date window itself."
		),
	)
	excluded_publication_types = serializers.ListField(
		child=serializers.CharField(max_length=200),
		required=False,
		allow_null=True,
		help_text=(
			"`pubmed`: publication type names to leave out, e.g. `Review`. Excluding a "
			"type also excludes the types under it. An unknown name is a 400."
		),
	)
	ctgov_search_condition = serializers.CharField(
		max_length=500,
		required=False,
		allow_blank=True,
		allow_null=True,
		help_text="`ctgov`: searched in each trial's conditions. Fill in at least one of the three.",
	)
	ctgov_search_intervention = serializers.CharField(
		max_length=500,
		required=False,
		allow_blank=True,
		allow_null=True,
		help_text="`ctgov`: searched in each trial's interventions.",
	)
	ctgov_search_term = serializers.CharField(
		max_length=500,
		required=False,
		allow_blank=True,
		allow_null=True,
		help_text="`ctgov`: searched anywhere in a trial's record.",
	)
	name = serializers.CharField(
		max_length=255,
		required=False,
		allow_blank=True,
		allow_null=True,
		help_text=(
			"Create only (the preview ignores it). Defaults to `RSS | <subject>`, "
			"`PubMed | <subject>` or `CTGov | <subject>`."
		),
	)
	description = serializers.CharField(
		max_length=5000,
		required=False,
		allow_blank=True,
		allow_null=True,
		help_text="Create only (the preview ignores it). Notes for people; Gregory never reads them.",
	)

	def validate(self, attrs):
		errors = {}
		for key in self.initial_data:
			if key not in self.fields:
				errors[key] = ["Unknown field."]
		allowed = set(_SOURCE_COMMON_FIELDS) | set(source_types.editor_fields(attrs["source_type"]))
		for key, value in attrs.items():
			if key not in allowed and value not in ("", None, []):
				errors[key] = [f"Not used by source type {attrs['source_type']}."]
		if errors:
			raise serializers.ValidationError(errors)
		# Trimmed; a blank setting counts as not given.
		out = {}
		for key, value in attrs.items():
			if key not in allowed:
				continue
			if isinstance(value, str):
				value = value.strip()
			out[key] = value
		return out


class EditorSourceSerializer(serializers.Serializer):
	"""A source as the editor API lists it."""

	id = serializers.IntegerField(help_text="The source's ID.")
	name = serializers.CharField(allow_null=True)
	source_type = serializers.CharField(
		help_text=(
			"Type key: `rss_papers`, `rss_trials`, `pubmed`, `ctgov`, `ctis`, `epo_ops`, "
			"`scrape_*`, `manual_*`, or `legacy` for a combination no importer fetches."
		),
	)
	source_for = serializers.CharField(help_text="What the source produces: science paper, trials, news article or patents.")
	method = serializers.CharField(help_text="How the content arrives: rss, pubmed_api, ctgov_api, …")
	subject_id = serializers.IntegerField(allow_null=True)
	subject_name = serializers.CharField(allow_null=True)
	team_id = serializers.IntegerField(allow_null=True)
	team_name = serializers.CharField(allow_null=True)
	active = serializers.BooleanField()
	settings = serializers.DictField(
		help_text=(
			"The type's settings: `link` and `keyword_filter` (`rss_papers`), `link` "
			"(`rss_trials`), `pubmed_query` and `excluded_publication_types` (`pubmed`), "
			"`ctgov_search_condition`, `ctgov_search_intervention` and `ctgov_search_term` "
			"(`ctgov`). Empty for other types."
		),
	)
	last_successful_fetch_at = serializers.DateTimeField(
		allow_null=True,
		help_text="Start of the last fetch that completed fully. Null for RSS sources, which keep no anchor.",
	)


class EditorSourceListResponseSerializer(serializers.Serializer):
	count = serializers.IntegerField()
	results = EditorSourceSerializer(many=True)


class EditorSourceCountSerializer(serializers.Serializer):
	key = serializers.CharField()
	value = serializers.IntegerField()
	label = serializers.CharField(help_text="What the number counts.")


class EditorSourceSampleSerializer(serializers.Serializer):
	title = serializers.CharField()
	date = serializers.CharField(allow_null=True)
	link = serializers.CharField(allow_null=True)


class EditorSourcePubmedSerializer(serializers.Serializer):
	querytranslation = serializers.CharField(help_text="How PubMed read the search.")
	warnings = serializers.ListField(child=serializers.CharField(), help_text="Warnings PubMed gave about the search.")


class EditorSourceDuplicateSerializer(serializers.Serializer):
	source_id = serializers.IntegerField()
	name = serializers.CharField(allow_null=True)
	subject_id = serializers.IntegerField(allow_null=True)
	subject_name = serializers.CharField(allow_null=True)


class EditorSourcePreviewResponseSerializer(serializers.Serializer):
	source_type = serializers.CharField()
	subject_id = serializers.IntegerField()
	counts = EditorSourceCountSerializer(many=True, help_text="Empty when the preview was refused before anything was counted.")
	samples = EditorSourceSampleSerializer(many=True, help_text="Up to 5.")
	first_run = serializers.CharField(allow_blank=True, help_text="What the first pipeline run would bring in.")
	pubmed = EditorSourcePubmedSerializer(allow_null=True, help_text="PubMed sources only.")
	refused = serializers.CharField(
		allow_null=True,
		help_text="The reason create would refuse this source, or null when it would accept it.",
	)
	duplicate_of = EditorSourceDuplicateSerializer(
		allow_null=True, help_text="The active source of the same team that already does this, if any."
	)


class EditorSourceCreateResponseSerializer(serializers.Serializer):
	source = EditorSourceSerializer()
	counts = EditorSourceCountSerializer(many=True)
	samples = EditorSourceSampleSerializer(many=True)
	first_run = serializers.CharField(allow_blank=True)
	pubmed = EditorSourcePubmedSerializer(allow_null=True)
	first_fetch = serializers.CharField(help_text="When the source is first fetched.")


class EditorSourceConflictSerializer(serializers.Serializer):
	detail = serializers.CharField()
	duplicate_of = EditorSourceDuplicateSerializer()
