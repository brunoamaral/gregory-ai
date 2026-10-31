from typing import Optional

from drf_spectacular.utils import extend_schema_field, inline_serializer
from rest_framework import serializers
from gregory.models import (
	Articles,
	ArticleMeshHeading,
	Trials,
	Sources,
	Authors,
	Subject,
	Team,
	MLPredictions,
	ArticleSubjectRelevance,
	TeamCategory,
	ArticleTrialReference,
	TrialOrgContent,
	TrialCountry,
	TrialSite,
	Sponsor,
	Patents,
	PatentPublication,
	PatentApplicant,
	PatentTrialLink,
	Medicine,
	MedicineEvent,
	Regulator,
)
from api.medicine_scope import (
	source_for,
	visible_links_queryset,
	visible_subject_links_queryset,
)
from sitesettings.models import CustomSetting
from django.contrib.sites.models import Site
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Count, Q

from api.editorial import (
	article_site_contents,
	claims_requested,
	editorial_org_ids,
	editorial_org_names,
	editorial_requested,
	editorial_site_ids,
	editorial_sites,
	is_editor_request,
	patent_editorial_site_ids,
	patent_site_contents,
)
from api.serializers.mixins import ScopedSerializerMixin
from api.patent_links import visible_patent_trial_links_queryset


def get_custom_settings():
	try:
		return CustomSetting.objects.get(site=settings.SITE_ID)
	except ObjectDoesNotExist:
		return None


def get_site():
	try:
		return Site.objects.get_current()
	except ObjectDoesNotExist:
		return None


class TeamSerializer(serializers.ModelSerializer):
	class Meta:
		model = Team
		# Explicit whitelist -- `__all__` leaked `members` (Django auth User
		# IDs of everyone on the team) to any API caller. `site` is an
		# internal FK used for outbound email sending, not something API
		# consumers need.
		fields = ["id", "name", "slug", "organization", "is_active"]


class SubjectsSerializer(serializers.ModelSerializer):
	team_id = serializers.IntegerField(source="team.id", read_only=True)

	class Meta:
		model = Subject
		fields = ["id", "subject_name", "description", "team_id"]


class TeamCategorySerializer(serializers.ModelSerializer):
	class Meta:
		model = TeamCategory
		fields = [
			"id",
			"category_name",
			"category_description",
			"category_slug",
			"category_terms",
			"category_exclude_terms",
			"category_type",
			"modality",
		]


class ArticleSubjectRelevanceSerializer(serializers.ModelSerializer):
	subject = SubjectsSerializer(read_only=True)
	subject_id = serializers.PrimaryKeyRelatedField(
		queryset=Subject.objects.all(), source="subject", write_only=True
	)

	class Meta:
		model = ArticleSubjectRelevance
		fields = ["subject", "subject_id", "is_relevant"]


class MLPredictionSubjectSerializer(serializers.ModelSerializer):
	class Meta:
		model = Subject
		fields = ["id", "subject_name", "description"]


class MLPredictionsSerializer(serializers.ModelSerializer):
	subject = MLPredictionSubjectSerializer(read_only=True)

	class Meta:
		model = MLPredictions
		fields = [
			"id",
			"algorithm",
			"model_version",
			"probability_score",
			"predicted_relevant",
			"created_date",
			"subject",
		]


class CategoryTopAuthorSerializer(serializers.ModelSerializer):
	"""Serializer for top authors within a category"""

	articles_count = serializers.SerializerMethodField()
	country = serializers.SerializerMethodField()

	class Meta:
		model = Authors
		fields = [
			"author_id",
			"given_name",
			"family_name",
			"full_name",
			"ORCID",
			"country",
			"articles_count",
		]

	def get_articles_count(self, obj) -> int:
		return getattr(obj, "category_articles_count", 0)

	def get_country(self, obj) -> Optional[str]:
		return obj.country.code if obj.country else None


class CategorySerializer(serializers.ModelSerializer):
	article_count_total = serializers.SerializerMethodField()
	trials_count_total = serializers.SerializerMethodField()
	patents_count_total = serializers.SerializerMethodField()
	medicines_count = serializers.SerializerMethodField()
	authors_count = serializers.SerializerMethodField()
	top_authors = serializers.SerializerMethodField()
	monthly_counts = serializers.SerializerMethodField()

	class Meta:
		model = TeamCategory
		fields = [
			"id",
			"category_description",
			"category_name",
			"category_slug",
			"category_terms",
			"category_exclude_terms",
			"modality",
			"article_count_total",
			"trials_count_total",
			"patents_count_total",
			"medicines_count",
			"authors_count",
			"top_authors",
			"monthly_counts",
		]

	def get_article_count_total(self, obj) -> int:
		"""Prefer the queryset's Count(..., distinct=True) annotation; fall
		back to a live count for an un-annotated instance (e.g. built
		directly in a test)."""
		annotated = getattr(obj, "article_count_annotated", None)
		if annotated is not None:
			return annotated

		# Fallback to simple count query (avoid obj.article_count() which may be complex)
		return obj.articles.count()

	def get_trials_count_total(self, obj) -> int:
		"""Prefer the queryset's Count(..., distinct=True) annotation; fall
		back to a live count for an un-annotated instance."""
		annotated = getattr(obj, "trials_count_annotated", None)
		if annotated is not None:
			return annotated

		# Fallback to simple count query
		return obj.trials.count()

	def get_patents_count_total(self, obj) -> int:
		"""Patent families in the category within the caller's patent scope
		(only sites with patents enabled count)."""
		# From the queryset's annotation only. An instance without it reports
		# 0 rather than a live obj.patents.count(): the scope belongs to the
		# request, and a live count would hand a caller whose sites don't show
		# patents the global count.
		return getattr(obj, "patents_count_annotated", None) or 0

	def get_medicines_count(self, obj) -> int:
		"""EMA medicines linked to the category that the caller may see: live
		links only, generics and biosimilars left out as on /medicines/, and 0
		where the site's ``medicines_enabled`` is off."""
		# From the queryset's annotation only, for the reason given on
		# get_patents_count_total: the scope belongs to the request.
		return getattr(obj, "medicines_count_annotated", None) or 0

	def get_authors_count(self, obj) -> int:
		"""Optimized authors count avoiding complex JOINs"""
		# Use annotated count if available
		if hasattr(obj, "authors_count_annotated"):
			return obj.authors_count_annotated

		# Use a more efficient subquery approach instead of complex JOINs
		# This avoids the hanging queries by using EXISTS instead of JOIN + COUNT + GROUP BY
		from django.db.models import Exists, OuterRef
		from gregory.models import Authors

		return Authors.objects.filter(
			Exists(obj.articles.filter(authors=OuterRef("pk")))
		).count()

	def get_top_authors(self, obj) -> list:
		"""Optimized top authors calculation avoiding complex JOINs"""
		# Get author parameters from serializer context
		context = self.context
		author_params = context.get("author_params", {})

		# Check if author data should be included
		if not author_params.get("include_authors", False):
			return []

		# Get parameters
		max_authors = author_params.get("max_authors", 10)
		date_filters = author_params.get("date_filters", {})

		# Use a more efficient approach with subqueries instead of complex JOINs
		# Get article IDs for this category first
		article_ids = obj.articles.values_list("article_id", flat=True)

		# Apply date filters if needed
		if date_filters:
			articles_qs = obj.articles.all()
			# Adjust the date filter keys for the Articles model
			articles_date_filters = {}
			for key, value in date_filters.items():
				if key.startswith("articles__"):
					articles_date_filters[key.replace("articles__", "")] = value
				else:
					articles_date_filters[f"{key}"] = value
			if articles_date_filters:
				articles_qs = articles_qs.filter(**articles_date_filters)
			article_ids = articles_qs.values_list("article_id", flat=True)

		# Get authors with article counts using a simpler subquery approach
		# This avoids the complex GROUP BY queries that hang the database
		from django.db.models import Subquery
		from gregory.models import Authors

		top_authors = (
			Authors.objects.filter(articles__article_id__in=Subquery(article_ids))
			.annotate(
				category_articles_count=Count(
					"articles",
					filter=Q(articles__article_id__in=Subquery(article_ids)),
					distinct=True,
				)
			)
			.order_by("-category_articles_count")[:max_authors]
		)

		return CategoryTopAuthorSerializer(top_authors, many=True).data

	def get_monthly_counts(self, obj) -> Optional[dict]:
		"""Get monthly counts of articles and trials when requested"""
		# Get monthly counts parameters from serializer context
		context = self.context
		monthly_params = context.get("monthly_counts_params", {})

		# Check if monthly counts should be included
		if not monthly_params.get("include_monthly_counts", False):
			return None

		# Get ML threshold
		ml_threshold = monthly_params.get("ml_threshold", 0.5)

		# Import here to avoid circular imports
		from gregory.models import MLPredictions
		from django.db.models import Max, OuterRef, Subquery
		from django.db.models.functions import TruncMonth

		# Monthly article counts
		articles = obj.articles.all()
		articles = articles.annotate(month=TruncMonth("published_date"))
		article_counts = (
			articles.values("month")
			.annotate(count=Count("article_id"))
			.order_by("month")
		)
		article_counts = list(article_counts.values("month", "count"))

		# Get available ML models for this category. The distinct algorithms among the
		# latest predictions equal the distinct algorithms among ALL predictions for
		# these articles, because every existing (article, algorithm) pair has a
		# latest row by definition — so no per-pair "latest" lookup is needed here.
		# Ordered explicitly so the response (and the per-model dict below) is
		# deterministic — a bare DISTINCT has undefined order.
		available_models = list(
			MLPredictions.objects.filter(
				article__categories=obj, algorithm__isnull=False
			)
			.values_list("algorithm", flat=True)
			.distinct()
			.order_by("algorithm")
		)

		# Monthly articles with ML predictions above threshold for each model (latest
		# predictions only). Same set-based approach as the relevant_article_ids block
		# below: a correlated subquery finds the latest created_date per (article,
		# algorithm) pair, and an article qualifies for this model's series if any row
		# tied at that latest date meets the threshold — matching the old per-article
		# `.first()`-with-threshold lookup's tie behavior exactly, without the N+1.
		ml_counts_by_model = {}
		for model in available_models:
			latest_date_per_pair = (
				MLPredictions.objects.filter(
					article=OuterRef("article"),
					algorithm=OuterRef("algorithm"),
				)
				.values("article", "algorithm")
				.annotate(latest=Max("created_date"))
				.values("latest")[:1]
			)
			qualifying_article_ids = (
				MLPredictions.objects.filter(
					article__categories=obj,
					algorithm=model,
					probability_score__gte=ml_threshold,
					created_date=Subquery(latest_date_per_pair),
				)
				.values_list("article_id", flat=True)
				.distinct()
			)

			# Get articles with latest predictions above threshold for this model
			articles_with_ml = obj.articles.filter(
				article_id__in=qualifying_article_ids
			)
			articles_with_ml = articles_with_ml.annotate(
				month=TruncMonth("published_date")
			)
			ml_article_counts = (
				articles_with_ml.values("month")
				.annotate(count=Count("article_id", distinct=True))
				.order_by("month")
			)
			ml_counts_by_model[model] = list(ml_article_counts.values("month", "count"))

		# Deduplicated monthly counts of relevant articles: an article is relevant when
		# the latest prediction of at least one model clears the threshold, and it is
		# counted once per month no matter how many models flagged it. This is the
		# per-month size of the union of the per-model series above, so it can never
		# exceed monthly_article_counts.
		#
		# A correlated subquery selects, for each prediction, the maximum created_date
		# for the same (article, algorithm) pair. Filtering for created_date == that
		# maximum identifies the latest prediction(s) per pair. This works on any
		# Django-supported database backend.
		latest_date_per_pair = (
			MLPredictions.objects.filter(
				article=OuterRef("article"),
				algorithm=OuterRef("algorithm"),
			)
			.values("article", "algorithm")
			.annotate(latest=Max("created_date"))
			.values("latest")[:1]
		)

		relevant_article_ids = (
			MLPredictions.objects.filter(
				article__categories=obj,
				algorithm__isnull=False,
				probability_score__gte=ml_threshold,
				created_date=Subquery(latest_date_per_pair),
			)
			.values_list("article_id", flat=True)
			.distinct()
		)

		relevant_articles = obj.articles.filter(
			article_id__in=relevant_article_ids, published_date__isnull=False
		).annotate(month=TruncMonth("published_date"))
		relevant_counts = (
			relevant_articles.values("month")
			.annotate(count=Count("article_id", distinct=True))
			.order_by("month")
		)
		relevant_counts = list(relevant_counts.values("month", "count"))

		# Monthly trial counts
		trials = obj.trials.all()
		trials = trials.annotate(month=TruncMonth("published_date"))
		trial_counts = (
			trials.values("month").annotate(count=Count("trial_id")).order_by("month")
		)
		trial_counts = list(trial_counts.values("month", "count"))

		return {
			"ml_threshold": ml_threshold,
			"available_models": available_models,
			"monthly_article_counts": article_counts,
			"monthly_ml_article_counts_by_model": ml_counts_by_model,
			"monthly_relevant_article_counts": relevant_counts,
			"monthly_trial_counts": trial_counts,
		}


class ArticleAuthorSerializer(serializers.ModelSerializer):
	country = serializers.SerializerMethodField()
	full_name = serializers.SerializerMethodField()

	class Meta:
		model = Authors
		fields = [
			"author_id",
			"given_name",
			"family_name",
			"full_name",
			"ORCID",
			"country",
		]

	def get_country(self, obj) -> Optional[str]:
		# Return the country code or name
		return obj.country.code if obj.country else None

	def get_full_name(self, obj) -> str:
		return obj.full_name


class EditorialOrganizationSerializer(serializers.Serializer):
	id = serializers.IntegerField()
	name = serializers.CharField()


class EditorialEntrySerializer(serializers.Serializer):
	"""One organisation's editorial content for a record (``?include=editorial``)."""

	organization = EditorialOrganizationSerializer()
	takeaways = serializers.CharField(allow_null=True)
	summary_plain_english = serializers.CharField(allow_null=True)


class EditorialSiteSerializer(serializers.Serializer):
	id = serializers.IntegerField()
	domain = serializers.CharField()
	name = serializers.CharField()


class EditorialSiteEntrySerializer(serializers.Serializer):
	"""One site's editorial content for an article (``?include=editorial``).

	``updated_at`` and ``updated_by`` appear only for an MCP editor
	(``/editor/`` routes): when the entry last changed and who changed it.
	"""

	site = EditorialSiteSerializer()
	takeaways = serializers.CharField(allow_null=True)
	summary_plain_english = serializers.CharField(allow_null=True)
	updated_at = serializers.DateTimeField(allow_null=True, required=False)
	updated_by = serializers.CharField(allow_null=True, required=False)


class _EditorialIncludeMixin:
	"""Pops ``editorial`` from the output unless the caller opted in with
	``?include=editorial``. ``editorial`` is declared as a field so it appears
	in the schema; the ``get_editorial()`` of each subclass returns
	immediately when it was not requested, so default responses cost nothing.
	See api/editorial.py and EDITORIAL-API-SPEC.md."""

	def to_representation(self, instance):
		ret = super().to_representation(instance)
		if not editorial_requested(self.context.get("request")):
			ret.pop("editorial", None)
		return ret


class SiteEditorialFieldMixin(_EditorialIncludeMixin):
	"""``?include=editorial`` for articles and patents: one entry per site,
	since their editorial content is per site (``ArticleSiteContent``,
	``PatentSiteContent``). Patents override the two attributes below."""

	#: Name of the content model's foreign key to the record being serialised.
	site_contents_fk = "article"

	@staticmethod
	def site_contents_for(request):
		return article_site_contents(request)

	@staticmethod
	def site_ids_for(request):
		return editorial_site_ids(request)

	@extend_schema_field(EditorialSiteEntrySerializer(many=True))
	def get_editorial(self, obj) -> list:
		"""One entry per editorial site (sorted by id); null fields when that
		site has no content row. Uses ``_prefetched_site_contents`` when the
		view attached it, else one query per object (single-object
		serialisation)."""
		request = self.context.get("request")
		if not editorial_requested(request):
			return []  # popped in to_representation
		site_ids = self.site_ids_for(request)
		if not site_ids:
			return []
		sites = editorial_sites(request)
		prefetched = getattr(obj, "_prefetched_site_contents", None)
		rows = prefetched if prefetched is not None else self.site_contents_for(
			request
		).filter(**{self.site_contents_fk: obj})
		by_site = {row.site_id: row for row in rows}
		with_audit = is_editor_request(request)
		entries = []
		for site_id in site_ids:
			row = by_site.get(site_id)
			entry = {
				"site": sites.get(site_id, {"id": site_id, "domain": "", "name": ""}),
				"takeaways": row.takeaways if row else None,
				"summary_plain_english": row.summary_plain_english if row else None,
			}
			if with_audit:
				entry["updated_at"] = row.updated_at if row else None
				entry["updated_by"] = getattr(row, "last_editor", None) if row else None
			entries.append(entry)
		return entries


class EditorialFieldMixin(_EditorialIncludeMixin):
	"""``?include=editorial`` for trials: one entry per organisation, since
	trial editorial content is still per organisation (``TrialOrgContent``)."""

	@extend_schema_field(EditorialEntrySerializer(many=True))
	def get_editorial(self, obj) -> list:
		"""One entry per editorial org (sorted by id); null fields when that org
		has no content row. Uses ``_prefetched_org_contents`` when the view
		attached it, else one query per object (single-object serialisation)."""
		request = self.context.get("request")
		if not editorial_requested(request):
			return []  # popped in to_representation
		org_ids = editorial_org_ids(request)
		if not org_ids:
			return []
		names = editorial_org_names(request)
		prefetched = getattr(obj, "_prefetched_org_contents", None)
		rows = prefetched if prefetched is not None else obj.org_contents.filter(
			organization_id__in=org_ids
		)
		by_org = {row.organization_id: row for row in rows}
		entries = []
		for org_id in org_ids:
			row = by_org.get(org_id)
			entries.append(
				{
					"organization": {"id": org_id, "name": names.get(org_id, "")},
					"takeaways": row.takeaways if row else None,
					"summary_plain_english": row.summary_plain_english if row else None,
				}
			)
		return entries


class ArticleMeshHeadingSerializer(serializers.ModelSerializer):
	descriptor = serializers.CharField(source="descriptor.name", read_only=True)
	qualifier = serializers.CharField(
		source="qualifier.name", read_only=True, default=None, allow_null=True
	)

	class Meta:
		model = ArticleMeshHeading
		fields = ["descriptor", "qualifier", "major_topic"]


class ArticleSerializer(
	SiteEditorialFieldMixin,
	ScopedSerializerMixin, serializers.HyperlinkedModelSerializer
):
	sources = serializers.SlugRelatedField(many=True, read_only=True, slug_field="name")
	categories = TeamCategorySerializer(many=True, read_only=True)
	authors = ArticleAuthorSerializer(many=True, read_only=True)
	teams = TeamSerializer(many=True, read_only=True)
	subjects = SubjectsSerializer(many=True, read_only=True)
	ml_predictions = MLPredictionsSerializer(
		many=True, read_only=True, source="ml_predictions_detail"
	)
	article_subject_relevances = ArticleSubjectRelevanceSerializer(
		many=True, read_only=True
	)
	clinical_trials = serializers.SerializerMethodField()
	editorial = serializers.SerializerMethodField()
	publication_types = serializers.SerializerMethodField()
	keywords = serializers.ListField(child=serializers.CharField(), read_only=True)
	mesh = ArticleMeshHeadingSerializer(
		source="mesh_headings", many=True, read_only=True
	)

	class Meta:
		model = Articles
		depth = 1
		fields = [
			"article_id",
			"title",
			"summary",
			"link",
			"links",
			"published_date",
			"sources",
			"teams",
			"subjects",
			"publisher",
			"container_title",
			"authors",
			"discovery_date",
			"article_subject_relevances",
			"doi",
			"pmid",
			"pmcid",
			"publication_types",
			"mesh",
			"keywords",
			"access",
			"editorial",
			"categories",
			"ml_predictions",
			"ml_score",
			"clinical_trials",
		]
		read_only_fields = ("discovery_date", "ml_predictions", "ml_score", "clinical_trials")

	@extend_schema_field(serializers.ListField(child=serializers.CharField()))
	def get_publication_types(self, obj):
		"""PubMed publication type names, e.g. ``["Journal Article", "Review"]``."""
		return [t.name for t in obj.publication_types.all()]

	def get_clinical_trials(self, obj) -> list:
		"""Get trials referenced in the article"""
		references = obj.trial_references.all()
		# The view's prefetch already drops suppressed links; this keeps an
		# unprefetched caller from listing one.
		trials = [ref.trial for ref in references if not ref.suppressed]
		return TrialReferenceSerializer(trials, many=True).data


class TrialPatentLinkSerializer(serializers.ModelSerializer):
	"""A patent family linked to a trial, as nested on the trial detail
	(``patent_links``). The mirror of ``PatentTrialLinkSerializer``."""

	title = serializers.CharField(source="patent.title", read_only=True)
	representative_publication = serializers.CharField(
		source="patent.representative_publication", read_only=True
	)

	class Meta:
		model = PatentTrialLink
		fields = [
			"patent_id",
			"title",
			"representative_publication",
			"timing",
			"days_after_completion",
			"basis",
		]


class PatentTrialLinkSerializer(serializers.ModelSerializer):
	"""A trial linked to a patent family, as nested on the patent detail
	(``trial_links``). The mirror of ``TrialPatentLinkSerializer``."""

	trial_title = serializers.CharField(source="trial.title", read_only=True)

	class Meta:
		model = PatentTrialLink
		fields = [
			"trial_id",
			"trial_title",
			"sponsor_id",
			"timing",
			"days_after_completion",
			"basis",
		]


class PatentPublicationSerializer(serializers.ModelSerializer):
	class Meta:
		model = PatentPublication
		fields = [
			"publication_number",
			"country",
			"kind",
			"publication_date",
			"application_number",
			"application_date",
		]


class PatentApplicantSerializer(serializers.ModelSerializer):
	"""An applicant as published. ``sponsor_*`` is null for applicants not
	linked to a canonical sponsor, individuals among them."""

	sponsor_name = serializers.CharField(
		source="sponsor.name", read_only=True, default=None
	)
	sponsor_slug = serializers.CharField(
		source="sponsor.slug", read_only=True, default=None
	)

	class Meta:
		model = PatentApplicant
		fields = ["raw_name", "sponsor_id", "sponsor_name", "sponsor_slug", "is_individual"]


class PatentListSerializer(
	SiteEditorialFieldMixin, ScopedSerializerMixin, serializers.ModelSerializer
):
	"""One patent family (``/patents/``). Relies on PatentViewSet.get_queryset()
	prefetching ``patent_applicants``, ``categories`` and ``subjects`` and
	annotating the two counts, so a page costs a constant number of queries.

	``link`` is the Espacenet page for the family's representative publication
	and is always present: showing where a record came from is a condition of
	the EPO OPS licence this data is fetched under.
	"""

	applicants = PatentApplicantSerializer(
		source="patent_applicants", many=True, read_only=True
	)
	categories = TeamCategorySerializer(many=True, read_only=True)
	subjects = SubjectsSerializer(many=True, read_only=True)
	publications_count = serializers.IntegerField(read_only=True)
	trial_links_count = serializers.IntegerField(read_only=True)
	editorial = serializers.SerializerMethodField()

	site_contents_fk = "patent"

	@staticmethod
	def site_contents_for(request):
		return patent_site_contents(request)

	@staticmethod
	def site_ids_for(request):
		return patent_editorial_site_ids(request)

	class Meta:
		model = Patents
		fields = [
			"patent_id",
			"family_id",
			"title",
			"summary",
			"representative_publication",
			"link",
			"earliest_priority_date",
			"earliest_publication_date",
			"has_grant",
			"cpc_classes",
			"applicants",
			"categories",
			"subjects",
			"publications_count",
			"trial_links_count",
			"editorial",
		]


class PatentDetailSerializer(PatentListSerializer):
	"""``PatentListSerializer`` plus the member publications, inventors, IPC
	classes, source links and linked trials. ``claims`` is left out of the
	response unless the caller asks for ``?include=claims``: it averages
	~12k characters and runs past 200k."""

	publications = PatentPublicationSerializer(many=True, read_only=True)
	trial_links = serializers.SerializerMethodField()
	claims = serializers.CharField(read_only=True, allow_null=True)

	class Meta(PatentListSerializer.Meta):
		fields = PatentListSerializer.Meta.fields + [
			"publications",
			"inventors",
			"ipc_classes",
			"links",
			"trial_links",
			"claims",
		]

	@extend_schema_field(PatentTrialLinkSerializer(many=True))
	def get_trial_links(self, obj) -> list:
		"""Trials linked to this family that the caller may see; see
		``visible_patent_trial_links_queryset``. PatentViewSet attaches the
		rows as ``_visible_trial_links`` (a scoped Prefetch); without it the
		same queryset runs for this patent."""
		links = getattr(obj, "_visible_trial_links", None)
		if links is None:
			request = self.context.get("request")
			links = (
				visible_patent_trial_links_queryset(request)
				.filter(patent=obj)
				.select_related("trial")
			)
		return PatentTrialLinkSerializer(links, many=True).data

	@property
	def _readable_fields(self):
		"""Leave ``claims`` out unless asked for. Skipped here, rather than
		popped from the output afterwards, so a row whose claims were deferred
		(PatentViewSet does that) is never read and never triggers a query.
		The field stays declared, so the OpenAPI schema still documents it."""
		wanted = claims_requested(self.context.get("request"))
		for field in super()._readable_fields:
			if field.field_name == "claims" and not wanted:
				continue
			yield field


class TrialCountrySerializer(serializers.ModelSerializer):
	"""The normalized per-country rows (see docs/trials-field-normalization.md).
	``country`` is rendered as its ISO 3166-1 alpha-2 code."""

	country = serializers.SerializerMethodField()

	class Meta:
		model = TrialCountry
		fields = [
			"country",
			"status",
			"decision_date",
			"recruitment_start_date",
			"sources",
		]

	def get_country(self, obj) -> Optional[str]:
		return obj.country.code if obj.country else None


class TrialSponsorSerializer(serializers.ModelSerializer):
	"""The canonical sponsor entity a trial's raw ``primary_sponsor`` resolves to —
	see docs/trials-field-normalization.md. Nested on TrialSerializer as ``sponsor``;
	null when the raw string hasn't been resolved yet."""

	class Meta:
		model = Sponsor
		fields = ["id", "slug", "name", "sponsor_type"]


class SponsorSerializer(serializers.ModelSerializer):
	"""Used by SponsorViewSet (``/sponsors/``). ``trials_count`` and
	``patents_count`` are populated by annotations on the viewset's queryset,
	not model fields."""

	trials_count = serializers.IntegerField(read_only=True)
	patents_count = serializers.IntegerField(read_only=True)

	class Meta:
		model = Sponsor
		fields = ["id", "slug", "name", "sponsor_type", "trials_count", "patents_count"]


class TrialSerializer(EditorialFieldMixin, ScopedSerializerMixin, serializers.HyperlinkedModelSerializer):
	subjects = SubjectsSerializer(many=True, read_only=True)
	sources = serializers.SlugRelatedField(many=True, read_only=True, slug_field="name")
	categories = TeamCategorySerializer(many=True, read_only=True)
	articles = serializers.SerializerMethodField()
	# Normalized per-country breakdown (country, status, decision_date, sources) — see
	# docs/trials-field-normalization.md. Relies on TrialViewSet.get_queryset()
	# prefetching "trial_countries" to avoid one query per trial on list responses.
	trial_countries = TrialCountrySerializer(many=True, read_only=True)
	countries_normalized = serializers.SerializerMethodField()
	# Canonical sponsor entity resolved from primary_sponsor — see
	# docs/trials-field-normalization.md. The raw primary_sponsor/secondary_sponsor/
	# sponsor_type fields stay untouched; this is purely additive. Null when
	# unresolved. Relies on TrialViewSet.get_queryset() select_related'ing
	# primary_sponsor_normalized to avoid one query per trial on list responses.
	sponsor = TrialSponsorSerializer(source="primary_sponsor_normalized", read_only=True)
	editorial = serializers.SerializerMethodField()

	class Meta:
		model = Trials
		fields = [
			"trial_id",
			"title",
			"summary",
			"ctg_detailed_description",
			"published_date",
			"discovery_date",
			"last_updated",
			"link",
			"links",
			"sources",
			"identifiers",
			"identifiers_normalized",
			"categories",
			"export_date",
			"internal_number",
			"last_refreshed_on",
			"acronym",
			"scientific_title",
			"primary_sponsor",
			"secondary_sponsor",
			"sponsor_type",
			"sponsor",
			"prospective_registration",
			"date_registration",
			"source_register",
			"recruitment_status",
			"recruitment_status_normalized",
			"other_records",
			"inclusion_agemin",
			"inclusion_agemax",
			"inclusion_age_min_years",
			"inclusion_age_max_years",
			"inclusion_gender",
			"inclusion_gender_normalized",
			"date_enrollement",
			"target_size",
			"study_type",
			"study_type_normalized",
			"study_design",
			"phase",
			"phase_normalized",
			"countries",
			"countries_normalized",
			"trial_countries",
			"regions_normalized",
			"inclusion_criteria",
			"exclusion_criteria",
			"condition",
			"intervention",
			"primary_outcome",
			"secondary_outcome",
			"secondary_id",
			"source_support",
			"ethics_review_status",
			"ethics_review_approval_date",
			"ethics_review_contact_name",
			"ethics_review_contact_address",
			"ethics_review_contact_phone",
			"ethics_review_contact_email",
			"results_date_completed",
			"results_url_link",
			"results_yes_no",
			"results_ipd_plan",
			"results_ipd_description",
			# EU Clinical Trials (CTIS) fields
			"therapeutic_areas",
			"country_status",
			"trial_region",
			"results_posted",
			"overall_decision_date",
			"countries_decision_date",
			"editorial",
			"articles",
			"subjects",
		]
		read_only_fields = ("discovery_date", "articles")

	def get_articles(self, obj) -> list:
		"""Get articles that reference this trial.

		Uses the prefetched ``article_references`` cache when available (populated
		by TrialViewSet via prefetch_related('article_references__article'))
		to avoid one query per trial on list responses.
		"""
		references = obj.article_references.all()
		articles = [ref.article for ref in references if not ref.suppressed]
		return ArticleReferenceSerializer(articles, many=True).data

	def get_countries_normalized(self, obj) -> Optional[list]:
		"""Flat, sorted list of normalized country codes (e.g. ["DE", "FR", "US"]) — the
		codes on trial_countries, without the per-country status/source detail. Uses the
		same prefetched cache as the `trial_countries` field."""
		codes = {tc.country.code for tc in obj.trial_countries.all() if tc.country}
		return sorted(codes) if codes else None


class TrialSiteSerializer(serializers.ModelSerializer):
	"""Per-site rows from CTIS retrieve enrichment and/or ClinicalTrials.gov — see
	TRIAL-GEOGRAPHY-PLAN.md PR G2/G3. ``country`` is rendered as its ISO 3166-1
	alpha-2 code. Nested only on the trial detail response (``trial_sites`` is not
	part of ``TrialSerializer``'s fields, so it never appears on the list endpoint)
	— use ``GET /trials/sites/`` for a bulk, flat listing across many trials."""

	country = serializers.SerializerMethodField()

	class Meta:
		model = TrialSite
		fields = [
			"name",
			"site_type",
			"address",
			"city",
			"state",
			"postcode",
			"country",
			"investigator_name",
			"latitude",
			"longitude",
			"sources",
		]

	def get_country(self, obj) -> Optional[str]:
		return obj.country.code if obj.country else None


class TrialDetailSerializer(TrialSerializer):
	"""TrialSerializer plus the per-site breakdown (``trial_sites``), used only for
	the retrieve (single-trial) response — see TrialViewSet.get_serializer_class.
	Kept off the list serializer because sites fan out (mean ~12/trial, max in the
	hundreds for large multi-country trials): nesting them on every list row would
	bloat list/CSV responses for a field most callers don't need there.
	TrialViewSet.get_queryset() prefetches "trial_sites" only for the retrieve
	action to back this field without an N+1."""

	trial_sites = TrialSiteSerializer(many=True, read_only=True)
	patent_links = serializers.SerializerMethodField()

	class Meta(TrialSerializer.Meta):
		fields = TrialSerializer.Meta.fields + ["trial_sites", "patent_links"]

	@extend_schema_field(TrialPatentLinkSerializer(many=True))
	def get_patent_links(self, obj) -> list:
		"""Patent families linked to this trial that the caller may see.

		Visibility, suppression and ``include_subject_links`` are decided by
		``visible_patent_trial_links_queryset``. TrialViewSet attaches that
		queryset as ``_visible_patent_links`` through a scoped Prefetch, so a
		list costs no query per trial; a trial on a site without the patents
		switch gets ``[]``. Without the attribute (single-object use) it
		runs the same queryset for this trial.
		"""
		links = getattr(obj, "_visible_patent_links", None)
		if links is None:
			request = self.context.get("request")
			links = (
				visible_patent_trial_links_queryset(request)
				.filter(trial=obj)
				.select_related("patent")
			)
		return TrialPatentLinkSerializer(links, many=True).data


class SourceSerializer(serializers.HyperlinkedModelSerializer):
	class Meta:
		model = Sources
		fields = [
			"source_id",
			"source_for",
			"name",
			"description",
			"link",
			"subject_id",
			"team_id",
		]


class AuthorSerializer(serializers.ModelSerializer):
	articles_count = serializers.SerializerMethodField()
	relevant_articles_count = serializers.SerializerMethodField()
	country = serializers.SerializerMethodField()
	articles_list = serializers.SerializerMethodField()

	class Meta:
		model = Authors
		fields = [
			"author_id",
			"given_name",
			"family_name",
			"full_name",
			"ORCID",
			"country",
			"biography",
			"credit_name",
			"orcid_keywords",
			"external_ids",
			"researcher_urls",
			"current_affiliation",
			"orcid_claimed",
			"orcid_verified_email",
			"articles_count",
			"relevant_articles_count",
			"articles_list",
		]

	def get_articles_count(self, obj) -> int:
		# The queryset always annotates a (correctly subject-scoped) article_count,
		# so prefer it and avoid a per-row COUNT query. Only fall back to a
		# live query if a caller passes in an un-annotated instance directly.
		annotated = getattr(obj, "article_count", None)
		if annotated is not None:
			return annotated
		qs = obj.articles_set.all()
		request = self.context.get("request")
		if request is not None and hasattr(request, "visible_subject_ids"):
			return (
				qs.filter(subjects__in=request.visible_subject_ids)
				.distinct()
				.count()
			)
		return qs.count()

	def get_relevant_articles_count(self, obj) -> int:
		annotated = getattr(obj, "relevant_articles_count", None)
		if annotated is not None:
			return annotated
		qs = obj.articles_set.filter(relevant=True)
		request = self.context.get("request")
		if request is not None and hasattr(request, "visible_subject_ids"):
			qs = qs.filter(subjects__in=request.visible_subject_ids)
		return qs.distinct().count()

	def get_country(self, obj) -> Optional[str]:
		# Return the country code or name
		return obj.country.code if obj.country else None

	def get_articles_list(self, obj) -> str:
		"""Absolute URL of this author's articles, on the requested host.

		One instance serves several frontends, so the link is built from the
		host the request was made to rather than a single configured domain.
		The scheme follows X-Forwarded-Proto when a proxy sets it.
		"""
		# drf-spectacular publishes the docstring above into the public
		# OpenAPI schema, so the reasoning lives down here instead: a URL
		# built from settings.SITE_ID (1) handed every caller site 1's
		# domain, so a request to api.brain-regeneration.com came back with
		# an api.gregory-ms.com link, pointing consumers at a different
		# site's API. The request already arrived at the API host, so its
		# own host is the answer -- no "api." prefix inference needed. The
		# Site fallback below still runs when there is no request in context
		# (a serializer used directly by a command or a test), which is the
		# only place that prefixing rule is still required.
		request = self.context.get("request")
		if request is not None:
			# The scheme is request.scheme, which trusts nginx's X-Forwarded-Proto
			# (SECURE_PROXY_SSL_HEADER in admin/settings.py): https behind TLS
			# termination, and the same answer the `next` link of this very
			# response gives. This used to read the header by hand, from before
			# that setting existed, and disagreed with `next` on a header such
			# as "HTTPS".
			return request.build_absolute_uri(f"/articles/?author_id={obj.author_id}")

		site = get_site()
		if not site:
			return ""

		# If the domain already has 'api.' subdomain, use it as-is
		# Otherwise, add 'api.' prefix
		domain = site.domain
		if domain.startswith("api."):
			base_url = f"https://{domain}/articles/?author_id="
		else:
			base_url = f"https://api.{domain}/articles/?author_id="

		return base_url + str(obj.author_id)


class CoauthorSerializer(serializers.ModelSerializer):
	country = serializers.SerializerMethodField()
	shared_articles = serializers.IntegerField(read_only=True)
	articles_count = serializers.IntegerField(read_only=True)
	relevant_articles_count = serializers.IntegerField(read_only=True)

	class Meta:
		model = Authors
		fields = ["author_id", "given_name", "family_name", "full_name",
				  "ORCID", "country", "shared_articles", "articles_count",
				  "relevant_articles_count"]

	def get_country(self, obj) -> Optional[str]:
		return obj.country.code if obj.country else None


# Simple Trial serializer for use in Article references
class TrialReferenceSerializer(serializers.ModelSerializer):
	class Meta:
		model = Trials
		fields = ["trial_id", "title", "summary", "link"]


# Simple Article serializer for use in Trial references
class ArticleReferenceSerializer(serializers.ModelSerializer):
	class Meta:
		model = Articles
		fields = ["article_id", "title", "summary", "link"]


class PublicSiteSerializer(serializers.Serializer):
	"""A publicly readable site, for the GET /sites/ discovery endpoint.

	Deliberately minimal: just enough for a caller to learn which site IDs
	exist so it can pass ?site_id=. Everything here is already public.

	Separate BY DESIGN from GET /tenants/ (McpTenantSerializer below), and
	the two must not converge: /sites/ is unscoped public discovery, and
	stays exactly as it is. /tenants/ carries MCP configuration -- prompts,
	documents, a description written for the model -- and, for a caller
	holding a site-bound key, can include that caller's own PRIVATE site.
	Folding the two together would leak a private site's existence and
	authored content to anyone who could call the unscoped endpoint.
	"""

	site_id = serializers.IntegerField(source="site.id", read_only=True)
	domain = serializers.CharField(source="site.domain", read_only=True)
	name = serializers.CharField(source="site.name", read_only=True)


class McpTenantSubjectSerializer(serializers.Serializer):
	"""A subject as listed on GET /tenants/ -- deliberately just {id,
	subject_name}, no team_id. Not SubjectsSerializer: that one includes
	team_id, which this endpoint must never expose (parent plan decision E)."""

	id = serializers.IntegerField(read_only=True)
	subject_name = serializers.CharField(read_only=True)


class McpTenantPromptArgumentSerializer(serializers.Serializer):
	"""One canonical-form argument of a SiteMcpPrompt.template -- see
	sitesettings.models.validate_prompt_template()."""

	name = serializers.CharField(read_only=True)
	description = serializers.CharField(read_only=True, allow_blank=True)
	required = serializers.BooleanField(read_only=True)


class McpTenantPromptSerializer(serializers.Serializer):
	"""An active SiteMcpPrompt, for GET /tenants/. The template is raw --
	the MCP server substitutes arguments at prompts/get time, not here."""

	name = serializers.CharField(read_only=True)
	title = serializers.CharField(read_only=True)
	description = serializers.CharField(read_only=True, allow_blank=True)
	template = serializers.CharField(read_only=True)
	arguments = McpTenantPromptArgumentSerializer(many=True, read_only=True)


class McpTenantDocumentSerializer(serializers.Serializer):
	"""An active SiteMcpDocument, for GET /tenants/."""

	slug = serializers.CharField(read_only=True)
	title = serializers.CharField(read_only=True)
	description = serializers.CharField(read_only=True, allow_blank=True)
	mime_type = serializers.CharField(read_only=True)
	body = serializers.CharField(read_only=True)


class McpTenantSerializer(serializers.Serializer):
	"""One tenant on GET /tenants/ -- a site offering a research assistant
	(MCP). See api.views.McpTenantsView for who sees what and why this is a
	separate endpoint from GET /sites/.

	No team_id anywhere (parent plan decision E) -- subjects use
	McpTenantSubjectSerializer, not the nested serializers articles/trials
	use, which carry team_id.
	"""

	site_id = serializers.IntegerField(read_only=True)
	domain = serializers.CharField(read_only=True)
	name = serializers.CharField(read_only=True)
	title = serializers.CharField(read_only=True)
	api_public = serializers.BooleanField(read_only=True)
	mcp_description = serializers.CharField(read_only=True, allow_blank=True)
	patents_enabled = serializers.BooleanField(read_only=True)
	medicines_enabled = serializers.BooleanField(read_only=True)
	subjects = McpTenantSubjectSerializer(many=True, read_only=True)
	prompts = McpTenantPromptSerializer(many=True, read_only=True)
	documents = McpTenantDocumentSerializer(many=True, read_only=True)


class MedicineCategorySerializer(serializers.ModelSerializer):
	"""A category a medicine is linked to, as ``categories`` on a medicine."""

	class Meta:
		model = TeamCategory
		fields = ["id", "category_name", "category_slug"]


class MedicineSubjectSerializer(serializers.Serializer):
	"""A subject EMA approved a medicine for, as ``subjects`` on an EMA medicine.

	Built from a ``MedicineSubjectLink``: ``matched_on`` is the subject's EMA
	therapeutic area that matched."""

	id = serializers.IntegerField(source="subject_id")
	subject_name = serializers.CharField(source="subject.subject_name")
	subject_slug = serializers.CharField(source="subject.subject_slug")
	matched_on = serializers.CharField()


class MedicineGroupedProductSerializer(serializers.ModelSerializer):
	"""A further product of the same regulator for the same medicine, as
	``grouped_products`` on the lead product's detail."""

	class Meta:
		model = Medicine
		fields = ["id", "name", "url"]


class MedicineEventSerializer(serializers.ModelSerializer):
	"""One event in a medicine's regulatory life (``/medicines/events/``), EMA's or the FDA's.

	``kind_label`` is the same sentence the RSS feeds use (``MedicineEvent.in_words``),
	so a kind is never worded two ways. Relies on MedicineEventViewSet selecting
	the medicine.
	"""

	kind_label = serializers.CharField(source="in_words", read_only=True)
	medicine = serializers.SerializerMethodField()
	source = serializers.SerializerMethodField()

	class Meta:
		model = MedicineEvent
		fields = [
			"id",
			"kind",
			"kind_label",
			"event_date",
			"basis",
			"from_value",
			"to_value",
			"medicine",
			"source",
		]

	@extend_schema_field(
		inline_serializer(
			"MedicineEventMedicine",
			{
				"id": serializers.IntegerField(),
				"regulator": serializers.ChoiceField(choices=Regulator.choices),
				"name": serializers.CharField(),
				"inn": serializers.CharField(),
				"substance_key": serializers.CharField(),
				"url": serializers.CharField(),
			},
		)
	)
	def get_medicine(self, obj):
		medicine = obj.medicine
		return {
			"id": medicine.id,
			"regulator": medicine.regulator,
			"name": medicine.name,
			"inn": medicine.inn,
			"substance_key": medicine.substance_key,
			"url": medicine.url,
		}

	def get_source(self, obj) -> str:
		"""The attribution of the event's medicine: its own regulator."""
		return source_for(obj.medicine)


class MedicineHistoryEventSerializer(MedicineEventSerializer):
	"""An event inside a medicine's own detail: the medicine is the parent, so it
	and its attribution are not repeated on each event."""

	class Meta(MedicineEventSerializer.Meta):
		fields = [
			f for f in MedicineEventSerializer.Meta.fields if f not in ("medicine", "source")
		]


#: Columns that only mean something for one regulator. The other regulator's rows
#: carry ``null`` for them (``MedicineSerializer.to_representation``), and the schema
#: marks them nullable. EMA-only: EMA's product number, opinion and Commission
#: procedure, therapeutic areas, ATC code, designation flags and dates. FDA-only: the
#: application, centre, brands, priority review, original class and interchangeability.
MEDICINE_EMA_ONLY_FIELDS = (
	"ema_product_number",
	"opinion_status",
	"therapeutic_areas",
	"atc_code",
	"advanced_therapy",
	"orphan_medicine",
	"conditional_approval",
	"exceptional_circumstances",
	"prime_priority_medicine",
	"accelerated_assessment",
	"additional_monitoring",
	"patient_safety",
	"rolling_review_start_date",
	"evaluation_start_date",
	"opinion_date",
	"latest_ec_decision_date",
	"refusal_date",
	"application_withdrawal_date",
	"suspension_date",
	"end_date",
	"ema_first_published",
	"ema_last_updated",
)
MEDICINE_FDA_ONLY_FIELDS = (
	"fda_application_number",
	"fda_application_type",
	"fda_center",
	"brand_names",
	"fda_priority_review",
	"fda_original_class",
	"interchangeable",
)


class MedicineSerializer(serializers.ModelSerializer):
	"""One medicine from a regulator (``/medicines/``): an EMA centrally authorised human
	medicine, or an FDA application (an NDA, or a BLA from CDER or CBER).

	Relies on MedicineViewSet attaching the caller's visible category links as
	``_visible_links`` and visible subject links as ``_visible_subject_links``, so a
	page costs a constant number of queries. Without them (an instance built by hand)
	the same rule runs for that one medicine.

	``regulator`` says which (``ema`` or ``fda``) and ``source`` names it. A column that
	belongs to the other regulator is ``null``: see ``MEDICINE_EMA_ONLY_FIELDS`` and
	``MEDICINE_FDA_ONLY_FIELDS``. ``authorisation_date`` is the first approval: EMA's
	authorisation, or the FDA's original approval.

	``tracked`` is true when the medicine is filed under at least one category the
	caller can see (``categories`` is not empty). ``subjects`` lists the conditions
	EMA approved it for, which is how an untracked EMA medicine is visible at all (the
	FDA rows have none). ``grouped_under`` is the id of the lead product when this row
	is a duplicate of another product of the same regulator, else null.

	``latest_ec_decision_date`` is the most recent European Commission decision,
	usually a variation. It is NOT the approval date: ``authorisation_date`` is
	the first approval.

	``substance_key`` is the same for every record of one drug, whichever regulator
	it comes from: the substances without their salt (``fingolimod hydrochloride`` and
	``fingolimod`` are both ``fingolimod``), sorted and joined with ``+``. Group on it
	to show one entry per drug. It is read-only.
	"""

	categories = serializers.SerializerMethodField()
	tracked = serializers.SerializerMethodField()
	subjects = serializers.SerializerMethodField()
	source = serializers.SerializerMethodField()

	class Meta:
		model = Medicine
		fields = [
			"id",
			"regulator",
			"ema_product_number",
			"fda_application_number",
			"fda_application_type",
			"fda_center",
			"name",
			"brand_names",
			"inn",
			"active_substance",
			"substances",
			"substance_key",
			"is_combination",
			"status",
			"opinion_status",
			"therapeutic_areas",
			"atc_code",
			"pharmacotherapeutic_group",
			"indication",
			"holder",
			"advanced_therapy",
			"orphan_medicine",
			"conditional_approval",
			"exceptional_circumstances",
			"prime_priority_medicine",
			"accelerated_assessment",
			"additional_monitoring",
			"biosimilar",
			"generic",
			"interchangeable",
			"fda_priority_review",
			"fda_original_class",
			"patient_safety",
			"rolling_review_start_date",
			"evaluation_start_date",
			"opinion_date",
			"authorisation_date",
			"latest_ec_decision_date",
			"refusal_date",
			"application_withdrawal_date",
			"suspension_date",
			"end_date",
			"ema_first_published",
			"ema_last_updated",
			"url",
			"grouped_under",
			"tracked",
			"categories",
			"subjects",
			"source",
		]
		extra_kwargs = {
			name: {"allow_null": True}
			for name in MEDICINE_EMA_ONLY_FIELDS + MEDICINE_FDA_ONLY_FIELDS
		}
		extra_kwargs["substance_key"] = {"read_only": True}

	def _visible_links_of(self, obj):
		links = getattr(obj, "_visible_links", None)
		if links is None:
			links = list(
				visible_links_queryset(self.context.get("request"))
				.filter(medicine=obj)
				.select_related("teamcategory")
				.order_by("teamcategory__category_name", "teamcategory_id")
			)
			obj._visible_links = links
		return links

	@extend_schema_field(MedicineCategorySerializer(many=True))
	def get_categories(self, obj):
		"""The categories this medicine is linked to that the caller may see.
		Suppressed links, and links to categories outside the caller's scope,
		are left out."""
		return MedicineCategorySerializer(
			[link.teamcategory for link in self._visible_links_of(obj)], many=True
		).data

	def get_tracked(self, obj) -> bool:
		"""True when the medicine is filed under a category the caller can see."""
		return bool(self._visible_links_of(obj))

	@extend_schema_field(MedicineSubjectSerializer(many=True))
	def get_subjects(self, obj):
		"""The subjects EMA approved this medicine for that the caller may see.
		Suppressed links, and subjects outside the caller's scope, are left out."""
		links = getattr(obj, "_visible_subject_links", None)
		if links is None:
			links = list(
				visible_subject_links_queryset(self.context.get("request"))
				.filter(medicine=obj)
				.select_related("subject")
				.order_by("subject__subject_name", "subject_id")
			)
			obj._visible_subject_links = links
		return MedicineSubjectSerializer(links, many=True).data

	def get_source(self, obj) -> str:
		"""The regulator the row comes from, by name: keep it wherever you show the row."""
		return source_for(obj)

	def to_representation(self, instance):
		data = super().to_representation(instance)
		# A column that belongs to the other regulator is empty, not false or []: an
		# FDA row has no orphan flag, which is not the same as "not an orphan".
		not_applicable = (
			MEDICINE_FDA_ONLY_FIELDS
			if instance.regulator == Regulator.EMA
			else MEDICINE_EMA_ONLY_FIELDS
		)
		for name in not_applicable:
			data[name] = None
		return data


class MedicineDetailSerializer(MedicineSerializer):
	"""``MedicineSerializer`` plus the medicine's events, newest first, and, on a lead
	product, the further products of the same regulator for the same medicine
	(``grouped_products``, ordered by name; only those the caller can see)."""

	events = MedicineHistoryEventSerializer(many=True, read_only=True)
	grouped_products = MedicineGroupedProductSerializer(many=True, read_only=True)

	class Meta(MedicineSerializer.Meta):
		fields = MedicineSerializer.Meta.fields + ["grouped_products", "events"]


class MedicineDrugFirstAuthorisedSerializer(serializers.Serializer):
	"""The earliest ``authorisation_date`` of a drug's shown records, per regulator."""

	ema = serializers.DateField(allow_null=True, read_only=True)
	fda = serializers.DateField(allow_null=True, read_only=True)


class MedicineDrugSerializer(serializers.Serializer):
	"""One drug on ``/medicines/drugs/``: every visible record that shares a
	``substance_key``, from both regulators.

	Read from a dict built by ``MedicineDrugViewSet`` (``api.views._drug_item``), whose
	``records`` are ``Medicine`` instances carrying the prefetches ``MedicineSerializer``
	relies on. Each record is the same row as on ``/medicines/``."""

	substance_key = serializers.CharField(
		read_only=True,
		help_text="The drug's key: its substances without their salt, sorted and joined with '+'.",
	)
	name = serializers.CharField(
		read_only=True,
		help_text=(
			"The EMA product's name (the earliest authorised that is not a duplicate), else "
			"the FDA's first brand name in normal case."
		),
	)
	substances = serializers.ListField(
		child=serializers.CharField(),
		read_only=True,
		help_text="The EMA product's substances, else the key split on '+'.",
	)
	regulators = serializers.ListField(
		child=serializers.CharField(),
		read_only=True,
		help_text="The regulators that have a record shown, sorted: `ema`, `fda` or both.",
	)
	first_authorised = MedicineDrugFirstAuthorisedSerializer(read_only=True)
	records = MedicineSerializer(many=True, read_only=True)
