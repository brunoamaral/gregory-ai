from django_filters import rest_framework as filters
from django.db import models
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Upper
from django.utils import timezone
from django import forms
from datetime import datetime, timedelta
from gregory.models import Articles, Trials, Authors, Sources, TeamCategory, Subject, Sponsor
from gregory.utils.trial_field_normalizers import (
	SponsorType,
	TrialPhase,
	TrialRecruitmentStatus,
	TrialRegion,
)


def ml_relevant_articles_q(threshold=0.8, subject_ids=None):
	"""
	Build a database-level Q matching articles ML-relevant by consensus.

	Only the latest prediction per (article, subject, algorithm) counts toward
	consensus — a retired model_version's stale score must not keep an article
	"relevant" forever after a retrain. The whole thing stays an unevaluated Q
	of subqueries (no Python id materialization) so the caller's queryset compiles
	to a single SQL statement.

	subject_ids=None checks every auto_predict subject (existing behavior);
	otherwise consensus is restricted to the given subjects.
	"""
	from django.db.models import Count, Max, OuterRef, Q, Subquery

	from gregory.models import MLPredictions

	auto_predict_subjects = Subject.objects.filter(auto_predict=True)
	if subject_ids is not None:
		auto_predict_subjects = auto_predict_subjects.filter(id__in=subject_ids)
	auto_predict_subjects = auto_predict_subjects.values("id", "ml_consensus_type")

	combined_q = None
	for subject_data in auto_predict_subjects:
		subject_id = subject_data["id"]
		consensus_type = subject_data["ml_consensus_type"]

		# Determine minimum algorithm count based on consensus type
		min_algorithms = {"any": 1, "majority": 2, "all": 3}.get(
			consensus_type, 1
		)  # Default to 'any' if unknown

		# For each (article, algorithm) pair within this subject, find the most
		# recent prediction's created_date. Filtering on created_date equal to
		# that maximum isolates the latest prediction per pair (ties simply
		# match more than one row, which is harmless for the exists/count below).
		latest_date_per_pair = (
			MLPredictions.objects.filter(
				article=OuterRef("article"),
				subject_id=subject_id,
				algorithm=OuterRef("algorithm"),
			)
			.values("article", "algorithm")
			.annotate(latest=Max("created_date"))
			.values("latest")[:1]
		)

		# Find articles that meet consensus for this subject, using only the
		# latest prediction per (article, algorithm) pair.
		articles_for_subject = (
			MLPredictions.objects.filter(
				subject_id=subject_id,
				article__subjects__id=subject_id,
				algorithm__isnull=False,
				predicted_relevant=True,
				probability_score__gte=threshold,
				created_date=Subquery(latest_date_per_pair),
			)
			.values("article_id")
			.annotate(algorithm_count=Count("algorithm", distinct=True))
			.filter(algorithm_count__gte=min_algorithms)
			.values_list("article_id", flat=True)
		)

		subject_q = Q(article_id__in=articles_for_subject)
		combined_q = subject_q if combined_q is None else combined_q | subject_q

	if combined_q is None:
		# No auto_predict subjects (or none matching subject_ids): match nothing.
		return Q(article_id__in=[])
	return combined_q


class SubjectFilterMixin:
	"""
	Mixin that provides subject filters with AND and OR semantics.

	``subjects``     — AND: returns only objects tagged with *every* listed ID.
	``subjects_any`` — OR:  returns objects tagged with *any* of the listed IDs.
	"""

	def filter_subjects_any(self, queryset, name, value):
		if not value:
			return queryset
		ids = set()
		for raw in value:
			try:
				ids.add(int(raw))
			except (TypeError, ValueError):
				continue
		if not ids:
			return queryset.none()
		return queryset.filter(subjects__id__in=ids).distinct()

	def filter_subjects_all(self, queryset, name, value):
		if not value:
			return queryset
		ids = []
		for raw in value:
			try:
				ids.append(int(raw))
			except (TypeError, ValueError):
				continue
		if not ids:
			return queryset.none()
		unique_ids = set(ids)
		return (
			queryset.filter(subjects__id__in=unique_ids)
			.annotate(
				matched_subjects_count=models.Count(
					"subjects",
					filter=models.Q(subjects__id__in=unique_ids),
					distinct=True,
				)
			)
			.filter(matched_subjects_count=len(unique_ids))
			.distinct()
		)


class ArticleFilter(SubjectFilterMixin, filters.FilterSet):
	"""
	Filter class for Articles, allowing searching by title, summary,
	and combined search across both fields, plus filtering by author,
	category, journal, team, subject, and special article types.
	"""

	title = filters.CharFilter(method="filter_title", label="Title")
	summary = filters.CharFilter(method="filter_summary", label="Summary")
	search = filters.CharFilter(method="filter_search", label="Search")
	author_id = filters.NumberFilter(
		field_name="authors__author_id", lookup_expr="exact", label="Author ID"
	)
	doi = filters.CharFilter(method="filter_doi", label="DOI")
	category_slug = filters.CharFilter(
		field_name="team_categories__category_slug",
		lookup_expr="exact",
		label="Category Slug",
	)
	category_id = filters.NumberFilter(
		field_name="team_categories__id", lookup_expr="exact", label="Category ID"
	)
	journal_slug = filters.CharFilter(method="filter_journal", label="Journal")
	team_id = filters.NumberFilter(
		field_name="teams__id", lookup_expr="exact", label="Team ID"
	)
	site_id = filters.NumberFilter(method="filter_site", label="Site ID")
	subject_id = filters.NumberFilter(
		field_name="subjects__id", lookup_expr="exact", label="Subject ID"
	)
	subjects = filters.BaseInFilter(
		method="filter_subjects_all",
		label="All subject IDs (comma-separated, AND match)",
	)
	subjects_any = filters.BaseInFilter(
		method="filter_subjects_any",
		label="Any subject IDs (comma-separated, OR match)",
	)
	source_id = filters.NumberFilter(
		field_name="sources__source_id", lookup_expr="exact", label="Source ID"
	)

	# New parameters for special article types
	relevant = filters.BooleanFilter(method="filter_relevant", label="Relevant")
	ml_threshold = filters.NumberFilter(
		method="filter_ml_threshold",
		label="ML Threshold (0.0-1.0)",
		widget=forms.NumberInput(attrs={"step": "0.01", "min": "0.0", "max": "1.0"}),
	)
	open_access = filters.BooleanFilter(
		method="filter_open_access", label="Open Access"
	)
	last_days = filters.NumberFilter(method="filter_last_days", label="Last Days")
	week = filters.NumberFilter(method="filter_week", label="Week")
	year = filters.NumberFilter(method="filter_year", label="Year")
	has_clinical_trials = filters.BooleanFilter(
		method="filter_has_clinical_trials", label="Has Clinical Trials"
	)
	published_date_after = filters.DateFilter(
		method="filter_published_date_after",
		input_formats=["%Y-%m-%d"],
		label="Published date on or after (YYYY-MM-DD)",
	)
	published_date_before = filters.DateFilter(
		method="filter_published_date_before",
		input_formats=["%Y-%m-%d"],
		label="Published date on or before (YYYY-MM-DD)",
	)

	class Meta:
		model = Articles
		fields = [
			"title",
			"summary",
			"search",
			"author_id",
			"doi",
			"category_slug",
			"category_id",
			"journal_slug",
			"team_id",
			"site_id",
			"subject_id",
			"source_id",
			"relevant",
			"ml_threshold",
			"open_access",
			"last_days",
			"week",
			"year",
			"has_clinical_trials",
			"published_date_after",
			"published_date_before",
		]

	def filter_doi(self, queryset, name, value):
		"""
		Filter by one or more DOIs (case-insensitive).
		Accepts a single DOI or a comma-separated list, e.g. ?doi=10.1/a or ?doi=10.1/a,10.2/b
		"""
		dois = list({d.strip().upper() for d in value.split(",") if d.strip()})
		if not dois:
			return queryset
		if len(dois) == 1:
			return queryset.filter(doi__iexact=dois[0])
		return queryset.annotate(_doi_upper=Upper("doi")).filter(_doi_upper__in=dois)

	def filter_title(self, queryset, name, value):
		"""
		Search in title field using uppercase column for performance
		"""
		return queryset.filter(utitle__contains=value.upper())

	def filter_summary(self, queryset, name, value):
		"""
		Search in summary field using uppercase column for performance
		"""
		return queryset.filter(usummary__contains=value.upper())

	def filter_search(self, queryset, name, value):
		"""
		Boolean search across title and summary using GIN-indexed uppercase columns.

		Bare terms are AND-ed; uppercase OR/NOT and "quoted phrases" are supported.
		Single-term queries behave identically to before (substring match).
		"""
		from api.utils.search import build_search_q
		q = build_search_q(value)
		if q is None:
			return queryset
		return queryset.filter(q)

	def filter_site(self, queryset, name, value):
		"""
		Articles belonging to any team of the given Django Site.

		Uses Exists() rather than a join filter: multiple teams can share a
		site, and a plain teams__site_id filter would duplicate every article
		that belongs to two such teams (same reasoning as OrgVisibilityMixin).
		"""
		subquery = queryset.model.objects.filter(
			pk=models.OuterRef("pk"), teams__site_id=value
		)
		return queryset.filter(models.Exists(subquery))

	def filter_journal(self, queryset, name, value):
		"""
		Filter by journal using case-insensitive regex matching.
		Handles URL-encoded journal names.
		"""
		from urllib.parse import unquote

		journal_name = unquote(value)
		return queryset.filter(container_title__iregex=f"^{journal_name}$")

	def _parse_ml_threshold(self, default=0.8):
		"""
		Helper method to safely parse ml_threshold parameter from request.
		Returns default value if parameter is missing, empty, or invalid.
		"""
		try:
			threshold_param = self.request.GET.get("ml_threshold", str(default))
			if threshold_param == "" or threshold_param is None:
				return default
			else:
				threshold = float(threshold_param)
				if not 0.0 <= threshold <= 1.0:
					return default  # Use default if invalid range
				return threshold
		except (ValueError, TypeError):
			return default  # Use default if conversion fails

	def _get_ml_relevant_articles_query(self, threshold=0.8, filtered_subject_id=None):
		"""Build a database-level query to find ML-relevant articles based on consensus logic.

		If filtered_subject_id is provided, only check relevance for that specific subject.
		"""
		subject_ids = None if filtered_subject_id is None else [filtered_subject_id]
		return ml_relevant_articles_q(threshold, subject_ids)

	def filter_relevant(self, queryset, name, value):
		"""
		Filter for relevant articles (ML predictions with consensus or manual selection)
		Uses ml_threshold parameter if provided, otherwise defaults to 0.8

		When subject_id is provided, only checks relevance for that specific subject.
		"""
		# Get subject_id from request to scope relevance checks
		filtered_subject_id = self.request.GET.get("subject_id")
		if filtered_subject_id:
			try:
				filtered_subject_id = int(filtered_subject_id)
			except (ValueError, TypeError):
				filtered_subject_id = None

		if value:
			# Get ML threshold from request parameters, default to 0.8
			threshold = self._parse_ml_threshold(0.8)

			# Get articles that are either:
			# 1. Manually marked as relevant (scoped to subject if provided)
			if filtered_subject_id:
				manually_relevant = models.Q(
					article_subject_relevances__is_relevant=True,
					article_subject_relevances__subject_id=filtered_subject_id,
				)
			else:
				manually_relevant = models.Q(
					article_subject_relevances__is_relevant=True
				)

			# 2. ML-relevant based on subject-specific consensus settings and threshold
			ml_relevant_q = self._get_ml_relevant_articles_query(
				threshold, filtered_subject_id
			)

			return queryset.filter(manually_relevant | ml_relevant_q).distinct()
		else:
			# Exclude articles that are either manually relevant or ML-relevant
			threshold = self._parse_ml_threshold(0.8)
			if filtered_subject_id:
				manually_relevant = models.Q(
					article_subject_relevances__is_relevant=True,
					article_subject_relevances__subject_id=filtered_subject_id,
				)
			else:
				manually_relevant = models.Q(
					article_subject_relevances__is_relevant=True
				)
			ml_relevant_q = self._get_ml_relevant_articles_query(
				threshold, filtered_subject_id
			)

			return queryset.exclude(manually_relevant | ml_relevant_q).distinct()

	def filter_ml_threshold(self, queryset, name, value):
		"""
		Filter for articles with ML predictions above the specified threshold.
		Works independently or in combination with the relevant filter.
		"""
		try:
			threshold = float(value)
			if not 0.0 <= threshold <= 1.0:
				# Invalid threshold, return empty queryset
				return queryset.none()

			# Scope to subject_id if provided
			filtered_subject_id = self.request.GET.get("subject_id")
			if filtered_subject_id:
				try:
					filtered_subject_id = int(filtered_subject_id)
				except (ValueError, TypeError):
					filtered_subject_id = None

			# Use efficient database-level query instead of Python loop
			ml_relevant_q = self._get_ml_relevant_articles_query(
				threshold, filtered_subject_id
			)
			return queryset.filter(ml_relevant_q)

		except (ValueError, TypeError):
			# Invalid threshold format, return empty queryset
			return queryset.none()

	def filter_open_access(self, queryset, name, value):
		"""
		Filter for open access articles
		"""
		if value:
			return queryset.filter(access="open")
		else:
			return queryset.exclude(access="open")

	def filter_last_days(self, queryset, name, value):
		"""
		Filter for articles from the last X days
		"""
		if not value:
			return queryset

		try:
			# Convert to float first, then int (handles '10.5' -> 10)
			days = int(float(value))
			if days <= 0:
				return queryset

			days_ago = timezone.now() - timedelta(days=days)
			return queryset.filter(discovery_date__gte=days_ago)
		except (ValueError, TypeError, OverflowError):
			# Return unfiltered queryset if value is invalid
			return queryset

	def filter_week(self, queryset, name, value):
		"""
		Filter for articles from a specific week (requires year parameter)
		"""
		year = self.request.GET.get("year")
		if value and year:
			try:
				week_num = int(value)
				year_num = int(year)

				# Calculate first and last day of the week
				first_day_of_week = datetime.strptime(
					f"{year_num}-W{week_num - 1}-1", "%Y-W%W-%w"
				)
				last_day_of_week = first_day_of_week + timedelta(days=6.9)

				return queryset.filter(
					discovery_date__gte=first_day_of_week.replace(
						tzinfo=timezone.get_current_timezone()
					),
					discovery_date__lte=last_day_of_week.replace(
						tzinfo=timezone.get_current_timezone()
					),
				)
			except (ValueError, TypeError):
				pass
		return queryset

	def filter_year(self, queryset, name, value):
		"""
		Filter for articles from a specific year (used with week parameter)
		This filter doesn't modify the queryset directly - it's used by filter_week
		"""
		return queryset

	def filter_has_clinical_trials(self, queryset, name, value):
		"""
		Filter articles by whether they are linked to at least one clinical trial.
		When value is None (parameter absent or empty), all articles are returned.
		"""
		if value is None:
			return queryset
		if value:
			return queryset.filter(trial_references__isnull=False).distinct()
		return queryset.filter(trial_references__isnull=True)

	def filter_published_date_after(self, queryset, name, value):
		# Use datetime boundary so the btree index on published_date is usable.
		start = timezone.make_aware(datetime(value.year, value.month, value.day))
		return queryset.filter(published_date__gte=start)

	def filter_published_date_before(self, queryset, name, value):
		# Strict less-than start of next day keeps whole-day inclusive semantics
		# while allowing the btree index on published_date to be used.
		next_day = value + timedelta(days=1)
		end = timezone.make_aware(datetime(next_day.year, next_day.month, next_day.day))
		return queryset.filter(published_date__lt=end)


class TrialFilter(SubjectFilterMixin, filters.FilterSet):
	"""
	Filter class for Trials, allowing searching by title, summary,
	and combined search across both fields, plus filtering by recruitment status,
	team, and subject.
	"""

	# Core search filters
	title = filters.CharFilter(method="filter_title", label="Title")
	summary = filters.CharFilter(method="filter_summary", label="Summary")
	search = filters.CharFilter(method="filter_search", label="Search")

	# ID and relationship filters
	trial_id = filters.NumberFilter(
		field_name="trial_id", lookup_expr="exact", label="Trial ID"
	)
	team_id = filters.NumberFilter(
		field_name="teams__id", lookup_expr="exact", label="Team ID"
	)
	site_id = filters.NumberFilter(method="filter_site", label="Site ID")
	subject_id = filters.NumberFilter(
		field_name="subjects__id", lookup_expr="exact", label="Subject ID"
	)
	subjects = filters.BaseInFilter(
		method="filter_subjects_all",
		label="All subject IDs (comma-separated, AND match)",
	)
	subjects_any = filters.BaseInFilter(
		method="filter_subjects_any",
		label="Any subject IDs (comma-separated, OR match)",
	)
	category_slug = filters.CharFilter(
		field_name="team_categories__category_slug",
		lookup_expr="exact",
		label="Category Slug",
	)
	category_id = filters.NumberFilter(
		field_name="team_categories__id", lookup_expr="exact", label="Category ID"
	)
	source_id = filters.NumberFilter(
		field_name="sources__source_id", lookup_expr="exact", label="Source ID"
	)

	# Registry identifier filters
	# Each accepts one or more comma-separated values and returns trials whose
	# identifiers JSON (or acronym) matches *any* of them, case-insensitively.
	# ``identifiers`` is the umbrella param: a mixed list matched across every
	# registry key at once. The typed params below scope to a single registry.
	identifiers = filters.BaseInFilter(
		method="filter_identifiers",
		label="Mixed registry id(s), comma-separated; matches any across NCT/EudraCT/EUCT/EUCTR/CTIS (case-insensitive)",
	)
	nct = filters.BaseInFilter(
		method="filter_nct",
		label="NCT ID(s), comma-separated; matches any (case-insensitive)",
	)
	eudract = filters.BaseInFilter(
		method="filter_eudract", label="EudraCT number(s), comma-separated; matches any"
	)
	euct = filters.BaseInFilter(
		method="filter_euct",
		label="EU CT / EUCTR number(s), comma-separated; matches any",
	)
	ctis = filters.BaseInFilter(
		method="filter_ctis", label="CTIS number(s), comma-separated; matches any"
	)
	acronym = filters.BaseInFilter(
		method="filter_acronym",
		label="Trial acronym(s), comma-separated; matches any (case-insensitive)",
	)

	# Trial-specific filters
	recruitment_status = filters.CharFilter(
		field_name="recruitment_status", lookup_expr="iexact"
	)
	status = filters.CharFilter(
		field_name="recruitment_status", lookup_expr="iexact"
	)  # Legacy alias for backward compatibility
	recruitment_status_normalized = filters.ChoiceFilter(
		choices=TrialRecruitmentStatus.choices
	)
	internal_number = filters.CharFilter(
		field_name="internal_number", lookup_expr="icontains"
	)
	phase = filters.CharFilter(field_name="phase", lookup_expr="icontains")
	phase_normalized = filters.ChoiceFilter(choices=TrialPhase.choices)
	study_type = filters.CharFilter(field_name="study_type", lookup_expr="icontains")
	primary_sponsor = filters.CharFilter(
		field_name="primary_sponsor", lookup_expr="icontains"
	)  # Legacy: free-text on the raw registry string — prefer sponsor_id/sponsor_slug
	sponsor_id = filters.NumberFilter(
		field_name="primary_sponsor_normalized_id",
		lookup_expr="exact",
		label="Canonical sponsor ID (see /sponsors/)",
	)
	sponsor_slug = filters.CharFilter(
		field_name="primary_sponsor_normalized__slug",
		lookup_expr="exact",
		label="Canonical sponsor slug (see /sponsors/)",
	)
	source_register = filters.CharFilter(
		field_name="source_register", lookup_expr="icontains"
	)
	countries = filters.CharFilter(field_name="countries", lookup_expr="icontains")
	country = filters.CharFilter(
		method="filter_country",
		label="Normalized country: ISO 3166-1 alpha-2 code, e.g. ?country=DE",
	)
	region = filters.ChoiceFilter(
		method="filter_region",
		choices=TrialRegion.choices,
		label="Normalized region, derived from the trial's countries",
	)

	# Medical/research filters
	condition = filters.CharFilter(field_name="condition", lookup_expr="icontains")
	intervention = filters.CharFilter(
		field_name="intervention", lookup_expr="icontains"
	)
	therapeutic_areas = filters.CharFilter(
		field_name="therapeutic_areas", lookup_expr="icontains"
	)
	inclusion_agemin = filters.CharFilter(
		field_name="inclusion_agemin", lookup_expr="exact"
	)
	inclusion_agemax = filters.CharFilter(
		field_name="inclusion_agemax", lookup_expr="exact"
	)
	inclusion_gender = filters.CharFilter(
		field_name="inclusion_gender", lookup_expr="icontains"
	)

	# Results filters
	has_results = filters.BooleanFilter(
		method="filter_has_results",
		label="Has results posted (results_posted flag, results completion date, results link, or results available = Yes)",
	)

	# Date-range filters
	date_registration_after = filters.DateFilter(
		field_name="date_registration",
		lookup_expr="gte",
		input_formats=["%Y-%m-%d"],
		label="Date registered on or after (YYYY-MM-DD)",
	)
	date_registration_before = filters.DateFilter(
		field_name="date_registration",
		lookup_expr="lte",
		input_formats=["%Y-%m-%d"],
		label="Date registered on or before (YYYY-MM-DD)",
	)

	class Meta:
		model = Trials
		fields = [
			"trial_id",
			"title",
			"summary",
			"search",
			"recruitment_status",
			"status",
			"recruitment_status_normalized",
			"team_id",
			"site_id",
			"subject_id",
			"category_slug",
			"category_id",
			"source_id",
			"identifiers",
			"nct",
			"eudract",
			"euct",
			"ctis",
			"acronym",
			"internal_number",
			"phase",
			"phase_normalized",
			"study_type",
			"primary_sponsor",
			"sponsor_id",
			"sponsor_slug",
			"source_register",
			"countries",
			"country",
			"region",
			"condition",
			"intervention",
			"therapeutic_areas",
			"inclusion_agemin",
			"inclusion_agemax",
			"inclusion_gender",
			"has_results",
			"date_registration_after",
			"date_registration_before",
		]

	def filter_title(self, queryset, name, value):
		"""
		Search in title field using uppercase column for performance
		"""
		return queryset.filter(utitle__contains=value.upper())

	def filter_summary(self, queryset, name, value):
		"""
		Search in summary field using uppercase column for performance
		"""
		return queryset.filter(usummary__contains=value.upper())

	def filter_search(self, queryset, name, value):
		"""
		Boolean search across title and summary using GIN-indexed uppercase columns.

		Bare terms are AND-ed; uppercase OR/NOT and "quoted phrases" are supported.
		Single-term queries behave identically to before (substring match).
		"""
		from api.utils.search import build_search_q
		q = build_search_q(value)
		if q is None:
			return queryset
		return queryset.filter(q)

	def filter_site(self, queryset, name, value):
		"""
		Trials belonging to any team of the given Django Site.

		Uses Exists() rather than a join filter: multiple teams can share a
		site, and a plain teams__site_id filter would duplicate every trial
		that belongs to two such teams (same reasoning as OrgVisibilityMixin).
		"""
		subquery = queryset.model.objects.filter(
			pk=models.OuterRef("pk"), teams__site_id=value
		)
		return queryset.filter(models.Exists(subquery))

	def _match_identifier(self, queryset, value, keys):
		"""Return trials whose ``identifiers`` JSON has any of ``keys`` equal
		(case-insensitively) to any of the supplied values.

		``value`` is the list produced by ``BaseInFilter`` (comma-separated input).
		Values are stripped and upper-cased so the comparison lines up with the
		non-partial ``Upper(identifiers->>'<key>')`` expression indexes on the
		model (``trials_u<key>_idx`` for nct/eudract/euct/euctr/ctis), keeping
		each branch — and the BitmapOr behind the umbrella ``?identifiers=``
		filter — an index scan rather than a seq scan. (The model's partial
		*unique* indexes on the same expressions enforce integrity but are NOT
		used for these lookups: Postgres can't prove their ``identifiers ? 'key'``
		predicate.) Blank tokens (e.g. from a trailing comma) are ignored; an
		all-blank list is treated as "no filter" and leaves the queryset untouched.
		"""
		wanted = {v.strip().upper() for v in (value or []) if v and v.strip()}
		if not wanted:
			return queryset
		annotations = {}
		condition = models.Q()
		for key in keys:
			alias = f"_id_{key}"
			annotations[alias] = Upper(KeyTextTransform(key, "identifiers"))
			condition |= models.Q(**{f"{alias}__in": wanted})
		return queryset.annotate(**annotations).filter(condition)

	def filter_identifiers(self, queryset, name, value):
		"""Match a mixed list of registry id(s) against any registry key.

		Pools every comma-separated token and returns trials whose
		``identifiers`` JSON has any of nct/eudract/euct/euctr/ctis equal
		(case-insensitively) to any token. Acronym is intentionally excluded:
		acronyms are not unique, so acronym matching stays opt-in via the
		dedicated ``?acronym=`` param.
		"""
		return self._match_identifier(
			queryset, value, ["nct", "eudract", "euct", "euctr", "ctis"]
		)

	def filter_nct(self, queryset, name, value):
		"""Match ClinicalTrials.gov NCT id(s) against ``identifiers['nct']``."""
		return self._match_identifier(queryset, value, ["nct"])

	def filter_eudract(self, queryset, name, value):
		"""Match EudraCT number(s) against ``identifiers['eudract']``."""
		return self._match_identifier(queryset, value, ["eudract"])

	def filter_euct(self, queryset, name, value):
		"""Match EU CT number(s) against ``identifiers['euct']`` or ``['euctr']``.

		The two keys are used interchangeably across the ingestion pipeline for
		the EU Clinical Trials register, so both are checked.
		"""
		return self._match_identifier(queryset, value, ["euct", "euctr"])

	def filter_ctis(self, queryset, name, value):
		"""Match CTIS number(s) against ``identifiers['ctis']``."""
		return self._match_identifier(queryset, value, ["ctis"])

	def filter_acronym(self, queryset, name, value):
		"""Match trial acronym(s) against the ``acronym`` column (case-insensitive)."""
		wanted = {v.strip().upper() for v in (value or []) if v and v.strip()}
		if not wanted:
			return queryset
		return queryset.annotate(_uacronym=Upper("acronym")).filter(
			_uacronym__in=wanted
		)

	def filter_has_results(self, queryset, name, value):
		"""
		Filter trials by whether results have been posted.

		A trial is considered to have results when any of the following holds:
		- results_posted is True;
		- results completion date is set;
		- results link is set (not null and not an empty string);
		- "results available" is "Yes" (case-insensitive).

		?has_results=true  -> only trials with results
		?has_results=false -> only trials without results
		"""
		has_results_q = (
			models.Q(results_posted=True)
			| models.Q(results_date_completed__isnull=False)
			| (
				models.Q(results_url_link__isnull=False)
				& ~models.Q(results_url_link="")
			)
			| models.Q(results_yes_no__iexact="yes")
		)
		if value:
			return queryset.filter(has_results_q)
		return queryset.exclude(has_results_q)

	def filter_country(self, queryset, name, value):
		"""Filter to trials whose normalized country set (TrialCountry) includes *value*
		(an ISO 3166-1 alpha-2 code, case-insensitive). See
		docs/trials-field-normalization.md."""
		if not value:
			return queryset
		return queryset.filter(trial_countries__country__iexact=value).distinct()

	def filter_region(self, queryset, name, value):
		"""Filter to trials whose normalized regions (regions_normalized) include *value*."""
		if not value:
			return queryset
		return queryset.filter(regions_normalized__contains=[value])


class AuthorFilter(filters.FilterSet):
	"""Filter class for Authors, allowing searching by full name, given name, family name and filtering by author ID."""

	full_name = filters.CharFilter(method="filter_full_name", label="Full Name")
	given_name = filters.CharFilter(
		field_name="given_name", lookup_expr="icontains", label="Given Name"
	)
	family_name = filters.CharFilter(
		field_name="family_name", lookup_expr="icontains", label="Family Name"
	)
	author_id = filters.NumberFilter(
		field_name="author_id", lookup_expr="exact", label="Author ID"
	)
	orcid = filters.CharFilter(
		field_name="ORCID", lookup_expr="icontains", label="ORCID"
	)
	country = filters.CharFilter(
		field_name="country", lookup_expr="exact", label="Country"
	)

	class Meta:
		model = Authors
		fields = [
			"full_name",
			"given_name",
			"family_name",
			"author_id",
			"orcid",
			"country",
		]

	def filter_full_name(self, queryset, name, value):
		"""Search in the full_name database field using optimized uppercase column"""
		# Use uppercase search for better performance with GIN index
		upper_value = value.upper()
		return queryset.filter(ufull_name__contains=upper_value)


class SourceFilter(filters.FilterSet):
	"""
	Filter class for Sources, allowing filtering by team and subject.
	"""

	source_id = filters.NumberFilter(
		field_name="source_id", lookup_expr="exact", label="Source ID"
	)
	team_id = filters.NumberFilter(
		field_name="team__id", lookup_expr="exact", label="Team ID"
	)
	subject_id = filters.NumberFilter(
		field_name="subject__id", lookup_expr="exact", label="Subject ID"
	)
	active = filters.BooleanFilter(field_name="active", label="Active")
	source_for = filters.CharFilter(
		field_name="source_for", lookup_expr="exact", label="Source For"
	)
	link = filters.CharFilter(field_name="link", lookup_expr="icontains", label="Link")

	class Meta:
		model = Sources
		fields = ["source_id", "team_id", "subject_id", "active", "source_for", "link"]


class SponsorFilter(filters.FilterSet):
	"""
	Filter class for Sponsors (see /sponsors/).
	"""

	sponsor_type = filters.ChoiceFilter(choices=SponsorType.choices)

	class Meta:
		model = Sponsor
		fields = ["sponsor_type"]


class SubjectFilter(filters.FilterSet):
	"""
	Filter class for Subject, allowing filtering by team.
	"""

	team_id = filters.NumberFilter(
		field_name="team__id", lookup_expr="exact", label="Team ID"
	)

	class Meta:
		model = Subject
		fields = ["team_id"]


class CategoryFilter(filters.FilterSet):
	"""
	Filter class for TeamCategory, allowing filtering by team and subject.
	"""

	category_id = filters.NumberFilter(
		field_name="id", lookup_expr="exact", label="Category ID"
	)
	team_id = filters.NumberFilter(
		field_name="team__id", lookup_expr="exact", label="Team ID"
	)
	subject_id = filters.NumberFilter(
		field_name="subjects__id", lookup_expr="exact", label="Subject ID"
	)
	category_terms = filters.CharFilter(
		method="filter_category_terms", label="Category Terms"
	)

	class Meta:
		model = TeamCategory
		fields = ["category_id", "team_id", "subject_id", "category_terms"]

	def filter_category_terms(self, queryset, name, value):
		"""Filter by category terms using array overlap"""
		return queryset.filter(category_terms__icontains=value)
