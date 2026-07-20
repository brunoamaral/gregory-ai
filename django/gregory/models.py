import datetime
import re

from cryptography.fernet import Fernet
from django_countries.fields import CountryField
from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import GinIndex, OpClass
from django.db import IntegrityError, models, transaction
from django.db.models import GeneratedField, Max, OuterRef, Q, Subquery
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Upper
from django.utils.text import slugify
from django.utils import timezone
from organizations.models import Organization, OrganizationUser
from simple_history.models import HistoricalRecords
import base64
from django.db.models.functions import Lower
from gregory.utils.trial_field_normalizers import (
	NORMALIZED_TRIAL_FIELDS,
	SponsorType,
	TrialPhase,
	TrialRecruitmentStatus,
	TrialStudyType,
	map_sponsor_type,
	normalize_countries,
	normalize_sponsor_key,
	raw_field_names,
)

# (curated > ctgov > ctis > rules) — see Trials._update_sponsor_type_from_trial() and
# TRIALS-SPONSOR-CANONICALIZATION-PLAN.md. A non-curated sponsor_type is only overwritten
# by a source of equal or higher priority; "curated" is never overwritten automatically.
_SPONSOR_TYPE_SOURCE_PRIORITY = {"curated": 3, "ctgov": 2, "ctis": 1, "rules": 0}


class Authors(models.Model):
	author_id = models.AutoField(primary_key=True)
	family_name = models.CharField(blank=False, null=False, max_length=150)
	given_name = models.CharField(blank=False, null=False, max_length=150)
	full_name = models.CharField(
		max_length=301,
		blank=True,
		null=True,
		db_index=True,
		help_text="Auto-generated from given_name and family_name",
	)
	ORCID = models.CharField(blank=True, null=True, max_length=150, unique=True)
	country = CountryField(blank=True, null=True)  # New field
	biography = models.TextField(blank=True, null=True)
	orcid_check = models.DateTimeField(blank=True, null=True)
	credit_name = models.CharField(
		max_length=301,
		blank=True,
		null=True,
		help_text="Researcher's preferred display name from ORCID (person.name.credit-name).",
	)
	emails = models.JSONField(
		default=list,
		blank=True,
		help_text="Publicly visible emails from ORCID. Never exposed via the API.",
	)
	orcid_keywords = models.JSONField(
		default=list, blank=True, help_text="Research keywords from ORCID."
	)
	external_ids = models.JSONField(
		default=list,
		blank=True,
		help_text="External identifiers from ORCID, e.g. [{'type', 'value', 'url'}].",
	)
	researcher_urls = models.JSONField(
		default=list,
		blank=True,
		help_text="Researcher URLs from ORCID, e.g. [{'name', 'url'}].",
	)
	current_affiliation = models.CharField(
		max_length=255,
		blank=True,
		null=True,
		help_text="Organization name of the author's current (or most recent) employment from ORCID.",
	)
	orcid_claimed = models.BooleanField(
		null=True, blank=True, help_text="Whether the ORCID record has been claimed by the researcher."
	)
	orcid_verified_email = models.BooleanField(
		null=True, blank=True, help_text="Whether the ORCID record has a verified email."
	)
	# Optimized uppercase column for fast text search
	ufull_name = GeneratedField(
		expression=Upper("full_name"), output_field=models.TextField(), db_persist=True
	)
	history = HistoricalRecords()

	def save(self, *args, **kwargs):
		# Auto-populate full_name from given_name and family_name
		self.full_name = f"{self.given_name} {self.family_name}".strip()
		super().save(*args, **kwargs)

	def __str__(self):
		return self.full_name or f"{self.given_name} {self.family_name}"

	class Meta:
		verbose_name_plural = "authors"
		db_table = "authors"
		indexes = [
			GinIndex(
				fields=["ufull_name"],
				opclasses=["gin_trgm_ops"],
				name="authors_ufull_name_gin_idx",
			),
			GinIndex(
				OpClass(Upper("ORCID"), name="gin_trgm_ops"),
				name="authors_uorcid_gin_idx",
			),
		]


class CategoryType(models.TextChoices):
	MANUAL = "manual", "Manual"
	AUTOMATIC = "automatic", "Automatic"


class CategoryModality(models.TextChoices):
	SMALL_MOLECULE = "small_molecule", "Small-molecule drug"
	BIOLOGIC_ANTIBODY = "biologic_antibody", "Antibody / biologic"
	CELL_GENE_THERAPY = "cell_gene_therapy", "Cell / gene therapy"
	REHABILITATION = "rehabilitation", "Rehabilitation / physical"
	DEVICE_NEUROMODULATION = "device_neuromodulation", "Device / neuromodulation"
	NATURAL_PRODUCT = "natural_product", "Natural product / supplement"
	RESEARCH_TOPIC = "research_topic", "Research topic (not an intervention)"
	OTHER = "other", "Other"


class CategoryMatchScope(models.TextChoices):
	TITLE = "title", "Title only"
	TITLE_SUMMARY = "title_summary", "Title and summary"


# Default per-field score weights used when matching content to a category.
# These reproduce the historical hard-coded scoring so existing categories keep
# behaving exactly as before until someone edits them. A fixed bonus of 2 points
# per unique matched term is added on top of these (see rebuild_categories).
DEFAULT_ARTICLE_MATCH_WEIGHTS = {
	"title": 3,
	"summary": 1,
}
DEFAULT_TRIAL_MATCH_WEIGHTS = {
	"title": 3,
	"summary": 2,
	"scientific_title": 2,
	"intervention": 2,
	"primary_outcome": 1,
	"secondary_outcome": 1,
	"therapeutic_areas": 1,
}
MATCH_WEIGHT_DEFAULTS = {
	"article": DEFAULT_ARTICLE_MATCH_WEIGHTS,
	"trial": DEFAULT_TRIAL_MATCH_WEIGHTS,
}


def default_match_weights():
	"""Default for TeamCategory.match_weights (a fresh copy each time)."""
	return {
		content_type: dict(weights)
		for content_type, weights in MATCH_WEIGHT_DEFAULTS.items()
	}


class TeamCategory(models.Model):
	team = models.ForeignKey(
		"Team",
		on_delete=models.CASCADE,
		related_name="team_categories",
		null=False,
		blank=False,
	)
	subjects = models.ManyToManyField(
		"Subject", related_name="team_subjects", blank=False
	)
	category_name = models.CharField(max_length=200)
	category_description = models.TextField(blank=True, null=True)
	category_slug = models.SlugField(blank=True, null=True, unique=True)
	category_terms = ArrayField(
		models.CharField(max_length=100),
		default=list,
		verbose_name="Terms to include in category (comma separated)",
		help_text="Add terms separated by commas.",
	)
	category_type = models.CharField(
		max_length=10,
		choices=CategoryType.choices,
		default=CategoryType.AUTOMATIC,
		help_text=(
			"Automatic categories are populated by the rebuild_categories command from the term list "
			"(manual assignments are still allowed and preserved). Manual categories are curated entirely "
			"by hand and are never touched by the command."
		),
	)
	modality = models.CharField(
		max_length=25,
		null=True,
		blank=True,
		choices=CategoryModality.choices,
		db_index=True,
		help_text=(
			"Intervention modality group. Curated by hand (seeded by "
			"sync_category_modalities); null means not yet classified."
		),
	)
	match_scope = models.CharField(
		max_length=20,
		choices=CategoryMatchScope.choices,
		default=CategoryMatchScope.TITLE_SUMMARY,
		help_text=(
			"Which fields are searched and scored when matching content to this category. "
			"'Title only' scores just the title; 'Title and summary' also scores the summary "
			"(and, for trials, the scientific title, intervention, outcomes and therapeutic areas)."
		),
	)
	match_min_score_articles = models.PositiveSmallIntegerField(
		default=3,
		help_text=(
			"Minimum score an article must reach to be assigned to this category. "
			"Articles are scored on up to 2 fields (title, summary)."
		),
	)
	match_min_score_trials = models.PositiveSmallIntegerField(
		default=3,
		help_text=(
			"Minimum score a trial must reach to be assigned to this category. "
			"Trials are scored on up to 7 fields (title, summary, scientific title, "
			"intervention, primary outcome, secondary outcome, therapeutic areas)."
		),
	)
	match_weights = models.JSONField(
		default=default_match_weights,
		blank=True,
		help_text=(
			"Per-field score weights keyed by content type ('article', 'trial'). Higher "
			"weights make a matched field count for more. A fixed bonus of 2 points per "
			"unique matched term is always added."
		),
	)
	match_config_hash = models.CharField(
		max_length=64,
		blank=True,
		null=True,
		editable=False,
		help_text=(
			"Fingerprint of the matching configuration (terms, subjects, score threshold) at the last "
			"rebuild_categories sync. When the configuration changes, the next incremental run performs "
			"a full re-match for this category."
		),
	)
	last_synced_at = models.DateTimeField(
		blank=True,
		null=True,
		editable=False,
		help_text="When rebuild_categories last synced this category.",
	)

	def save(self, *args, **kwargs):
		if not self.category_slug:
			self.category_slug = slugify(self.category_name)
		super().save(*args, **kwargs)

	def get_match_weights(self, content_type):
		"""Return the per-field weights for 'article' or 'trial'.

		Falls back to the historical defaults for any field missing from the
		stored configuration and ignores unknown fields, so a partial or legacy
		``match_weights`` value can never break matching.
		"""
		defaults = MATCH_WEIGHT_DEFAULTS.get(content_type, {})
		stored = {}
		if isinstance(self.match_weights, dict):
			candidate = self.match_weights.get(content_type)
			if isinstance(candidate, dict):
				stored = candidate
		weights = {}
		for field, default in defaults.items():
			try:
				weights[field] = int(stored.get(field, default))
			except (TypeError, ValueError):
				weights[field] = default
		return weights

	def get_scored_fields(self, content_type):
		"""Return {field: weight} actually used for scoring, honouring match_scope.

		In 'title only' scope every field except the title is dropped.
		"""
		weights = self.get_match_weights(content_type)
		if self.match_scope == CategoryMatchScope.TITLE:
			return {"title": weights.get("title", 0)}
		return weights

	def __str__(self):
		return f"{self.team.name} - {self.category_name}"

	def article_count(self):
		return self.articles.count()

	def trials_count(self):
		return self.trials.count()

	class Meta:
		constraints = [
			models.UniqueConstraint(
				fields=["team", "category_slug"], name="unique_team_category_slug"
			)
		]
		verbose_name_plural = "team categories"
		db_table = "team_categories"


class CategoryAssignmentSource(models.TextChoices):
	MANUAL = "manual", "Manual"
	AUTOMATIC = "automatic", "Automatic"


class ArticleCategoryAssignment(models.Model):
	"""Through model for Articles.team_categories.

	Maps onto the table Django originally auto-created for the implicit M2M.
	`source` records whether the link was made by a person (admin, API, shell —
	the default) or by the rebuild_categories command, which only adds and
	removes rows marked automatic and never touches manual assignments.
	"""

	articles = models.ForeignKey(
		"Articles", on_delete=models.CASCADE, related_name="category_assignments"
	)
	teamcategory = models.ForeignKey(
		"TeamCategory", on_delete=models.CASCADE, related_name="article_assignments"
	)
	source = models.CharField(
		max_length=10,
		choices=CategoryAssignmentSource.choices,
		default=CategoryAssignmentSource.MANUAL,
	)

	def __str__(self):
		return f"{self.articles_id} → {self.teamcategory} ({self.source})"

	class Meta:
		db_table = "articles_team_categories"
		unique_together = (("articles", "teamcategory"),)
		verbose_name = "article category assignment"


class TrialCategoryAssignment(models.Model):
	"""Through model for Trials.team_categories. See ArticleCategoryAssignment."""

	trials = models.ForeignKey(
		"Trials", on_delete=models.CASCADE, related_name="category_assignments"
	)
	teamcategory = models.ForeignKey(
		"TeamCategory", on_delete=models.CASCADE, related_name="trial_assignments"
	)
	source = models.CharField(
		max_length=10,
		choices=CategoryAssignmentSource.choices,
		default=CategoryAssignmentSource.MANUAL,
	)

	def __str__(self):
		return f"{self.trials_id} → {self.teamcategory} ({self.source})"

	class Meta:
		db_table = "trials_team_categories"
		unique_together = (("trials", "teamcategory"),)
		verbose_name = "trial category assignment"


class Entities(models.Model):
	entity = models.TextField()
	label = models.TextField()

	class Meta:
		managed = True
		verbose_name_plural = "entities"
		db_table = "entities"


class Subject(models.Model):
	ML_CONSENSUS_CHOICES = [
		("any", "Any Model (at least one predicts relevant)"),
		("majority", "Majority Vote (at least 2 out of 3 agree)"),
		("all", "Unanimous (all models must agree)"),
	]

	subject_name = models.CharField(blank=False, null=False, max_length=50)
	description = models.TextField(blank=True, null=True)
	subject_slug = models.SlugField(editable=True)
	auto_predict = models.BooleanField(
		default=False, help_text="Enable automatic ML prediction for new articles"
	)
	ml_consensus_type = models.CharField(
		max_length=10,
		choices=ML_CONSENSUS_CHOICES,
		default="any",
		help_text="How ML models should agree for an article to be considered relevant",
	)
	team = models.ForeignKey(
		"Team",
		on_delete=models.CASCADE,  # Not sure which would be the best option here
		null=True,
		blank=False,
		related_name="subjects",  # Helps in querying from the Team model, e.g., team.subjects.all()
	)
	history = HistoricalRecords()

	def __str__(self):
		# More readable subject representation
		if self.team:
			# Keep it short - just show the subject name and team name (no organization)
			return f"{self.subject_name} [{self.team.name}]"
		else:
			return self.subject_name

	class Meta:
		managed = True
		verbose_name_plural = "subjects"
		db_table = "subjects"
		constraints = [
			models.UniqueConstraint(
				fields=["team", "subject_slug"], name="unique_team_subject_slug"
			)
		]

	def get_full_name(self):
		"""Return a full representation of the subject including team and organization"""
		if self.team and self.team.organization:
			return f"{self.subject_name} - {self.team.name} ({self.team.organization.name})"
		elif self.team:
			return f"{self.subject_name} - {self.team.name}"
		else:
			return self.subject_name


class Sources(models.Model):
	TABLES = [
		("science paper", "Science Paper"),
		("trials", "Trials"),
		("news article", "News Article"),
	]
	METHODS = [
		("rss", "RSS"),
		("scrape", "Scrape"),
		("manual", "Manual submission"),
		("ctgov_api", "ClinicalTrials.gov API"),
		("ctis_api", "CTIS Public API"),
	]
	active = models.BooleanField(default=True)
	source_id = models.AutoField(primary_key=True)
	source_for = models.CharField(
		choices=TABLES, max_length=50, default="science paper"
	)
	name = models.CharField(max_length=255, blank=True, null=True)
	link = models.URLField(max_length=2000, blank=True, null=True)
	subject = models.ForeignKey(
		Subject, on_delete=models.SET_NULL, null=True, blank=True, unique=False
	)
	method = models.CharField(choices=METHODS, max_length=10, default="rss")
	ignore_ssl = models.BooleanField(default=False)
	description = models.TextField(blank=True, null=True)
	keyword_filter = models.TextField(
		blank=True,
		null=True,
		help_text='Keywords to filter articles. Use comma-separated values for multiple keywords, or quoted strings for exact phrases (e.g., "multiple sclerosis", alzheimer, parkinson). Applies to supported feed sources like bioRxiv and PNAS.',
	)
	ctgov_search_condition = models.TextField(
		blank=True,
		null=True,
		verbose_name="ClinicalTrials.gov Search condition/Disease",
		help_text='Search condition/Disease for ClinicalTrials.gov API. Enter conditions/diseases to search (e.g., "multiple sclerosis"). Only used when method is "ClinicalTrials.gov API".',
	)
	ctis_search_criteria = models.JSONField(
		blank=True,
		null=True,
		verbose_name="CTIS Public API search criteria",
		help_text=(
			"Verbatim searchCriteria dict POSTed to the CTIS public API, e.g. "
			'{"medicalCondition": "Multiple Sclerosis"}. Supported keys: medicalCondition, '
			"sponsor, number, containAll, status. Only used when method is \"CTIS Public API\"."
		),
	)
	team = models.ForeignKey(
		"Team",
		on_delete=models.CASCADE,  # Not sure which would be the best option here
		null=True,
		blank=False,
		related_name="sources",  # Helps in querying from the Team model, e.g., team.sources.all()
	)
	last_successful_fetch_at = models.DateTimeField(
		blank=True,
		null=True,
		help_text=(
			"Start time of the last fetch that completed fully (every page consumed, "
			"no errors, result cap not hit). Anchors the incremental ClinicalTrials.gov "
			"window; a failed or capped run must not advance it."
		),
	)

	def get_latest_article_date(self):
		"""
		Returns the date of the most recent article from this source.
		"""
		latest_article = self.articles_set.order_by("-published_date").first()
		if latest_article:
			return latest_article.published_date
		return None

	def get_article_count(self):
		"""
		Returns the count of articles from this source.
		"""
		return self.articles_set.count()

	def get_latest_trial_date(self):
		"""
		Returns the date of the most recent trial from this source.
		"""
		latest_trial = self.trials_set.order_by("-last_updated").first()
		if latest_trial:
			return latest_trial.last_updated
		return None

	def get_trial_count(self):
		"""
		Returns the count of trials from this source.
		"""
		return self.trials_set.count()

	def get_health_status(self):
		"""
		Returns the health status of the source based on the latest article/trial date.
		Uses the same status logic for both article and trial sources.
		"""
		if not self.active:
			return "inactive"

		# Get the latest article or trial date depending on source type
		if self.source_for == "trials":
			# For trial sources, check the Trials model
			latest_date = self.get_latest_trial_date()
		else:
			# For article sources, check the Articles model
			latest_date = self.get_latest_article_date()

		if not latest_date:
			return "no_content"

		# Same status logic for both types of sources
		now = timezone.now()
		days_since_last_update = (now - latest_date).days

		if days_since_last_update > 60:
			return "error"
		elif days_since_last_update > 30:
			return "warning"
		else:
			return "healthy"

	def __str__(self):
		return self.name or ""

	class Meta:
		managed = True
		verbose_name_plural = "sources"
		db_table = "sources"


class ApiKeyHistoryMixin(models.Model):
	"""Abstract mixin that adds API-key attribution fields to historical models.

	Attach to HistoricalRecords via bases=[ApiKeyHistoryMixin] so every
	historical row can record which API key triggered the change.

	Two complementary fields:
	- api_access_scheme (FK, SET_NULL): live link; NULL for admin/shell saves
	  or after key deletion.
	- api_access_scheme_label (CharField): snapshot of the key's client_name at
	  save time.  Preserved permanently even after the key or its organisation
	  is deleted, keeping the audit trail readable.

	Both fields are populated automatically by the
	``stamp_api_access_scheme_on_history`` signal handler in gregory/signals.py.
	"""

	api_access_scheme = models.ForeignKey(
		"api.APIAccessScheme",
		null=True,
		blank=True,
		on_delete=models.SET_NULL,
		related_name="+",
	)
	api_access_scheme_label = models.CharField(
		max_length=200,
		blank=True,
		help_text="Snapshot of APIAccessScheme.client_name at the time of the change. "
		"Preserved after key deletion.",
	)

	class Meta:
		abstract = True


class Articles(models.Model):
	KINDS = [("science paper", "Science Paper"), ("news article", "News Article")]
	ACCESS_OPTIONS = [
		("unknown", "Unknown"),
		("open", "Open"),
		("restricted", "Restricted"),
	]
	article_id = models.AutoField(primary_key=True)
	# Deliberately NOT unique: distinct papers can share a title (errata,
	# corrections, preprint vs published). Dedup is enforced in the feedreader
	# by DOI-first lookup; unique_article_title_link still guards exact dupes.
	title = models.TextField(blank=False, null=False)
	link = models.URLField(
		blank=False,
		null=False,
		max_length=2000,
		help_text='First URL seen for this article; stable after first import. Corresponds to "link" in the API response.',
	)
	links = models.JSONField(
		blank=True,
		null=True,
		help_text='All known URLs for this article, keyed by registry slug (e.g. "ctgov") for known registries or by hostname otherwise. Managed automatically. Corresponds to "links" in the API response.',
	)
	doi = models.CharField(max_length=280, blank=True, null=True, db_index=True)
	summary = models.TextField(blank=True, null=True)

	# Persisted uppercase columns for performant case-insensitive search
	utitle = GeneratedField(
		expression=Upper("title"), output_field=models.TextField(), db_persist=True
	)
	usummary = GeneratedField(
		expression=Upper("summary"), output_field=models.TextField(), db_persist=True
	)

	sources = models.ManyToManyField(Sources, blank=True)
	published_date = models.DateTimeField(blank=True, null=True, db_index=True)
	discovery_date = models.DateTimeField(auto_now_add=True, db_index=True)
	last_updated = models.DateTimeField(auto_now=True, null=True, db_index=True)
	authors = models.ManyToManyField(Authors, blank=True)
	team_categories = models.ManyToManyField(
		"TeamCategory",
		related_name="articles",
		blank=True,
		through="ArticleCategoryAssignment",
	)
	entities = models.ManyToManyField("Entities")
	ml_predictions = models.ManyToManyField("MLPredictions", blank=True)
	noun_phrases = models.JSONField(blank=True, null=True)
	# Enrichment backoff markers: each pipeline enrichment task re-checks an
	# article only when its *_next_check is due (NULL = never attempted). A
	# fruitless COMPLETED attempt (API responded, nothing gained) pushes
	# next_check out by min(2^attempts, 30) days; a network failure advances
	# nothing; success clears the marker. See gregory/utils/enrichment.py.
	doi_lookup_next_check = models.DateTimeField(blank=True, null=True)
	doi_lookup_attempts = models.PositiveSmallIntegerField(default=0)
	authors_next_check = models.DateTimeField(blank=True, null=True)
	authors_attempts = models.PositiveSmallIntegerField(default=0)
	details_next_check = models.DateTimeField(blank=True, null=True)
	details_attempts = models.PositiveSmallIntegerField(default=0)
	kind = models.CharField(choices=KINDS, max_length=50, default="science paper")
	access = models.CharField(
		choices=ACCESS_OPTIONS, max_length=50, default=None, null=True
	)
	publisher = models.CharField(max_length=150, blank=True, null=True, default=None)
	container_title = models.CharField(
		max_length=150, blank=True, null=True, default=None
	)
	crossref_check = models.DateTimeField(blank=True, null=True)
	pdf_link = models.URLField(max_length=2000, blank=True, null=True)
	history = HistoricalRecords(
		excluded_fields=[
			"crossref_check",
			"crossref_retraction_check",
			"utitle",
			"usummary",
			"ml_score",
			"relevant",
		],
		bases=[ApiKeyHistoryMixin],
		m2m_fields=["sources", "subjects", "teams"],
	)
	subjects = models.ManyToManyField(
		"Subject", related_name="articles"
	)  # Ensuring that article has one or more subjects
	teams = models.ManyToManyField(
		"Team", related_name="articles"
	)  # Allows an article to belong to one or more teams
	retracted = models.BooleanField(
		default=False,
		db_index=True,
		help_text="Whether the article has been retracted. Used for filtering and display purposes.",
	)
	crossref_retraction_check = models.DateTimeField(
		blank=True,
		null=True,
		help_text="Timestamp of the last CrossRef retraction check.",
	)
	ml_score = models.FloatField(
		null=True,
		blank=True,
		default=None,
		db_index=True,
		help_text="Average ML probability score across the latest prediction per (algorithm, subject) pair. Updated automatically when predictions are saved.",
	)
	relevant = models.BooleanField(
		default=False, db_index=True,
		help_text="Denormalized: manually relevant for any subject, or ML consensus "
				  "at the 0.8 threshold for any auto_predict subject. Maintained by "
				  "signals + refresh_article_relevance.",
	)

	def is_ml_relevant_for_subject(self, subject, threshold=0.8):
		"""
		Check if this article is ML-relevant for a specific subject based on the subject's consensus type
		and probability threshold.

		Only the latest prediction per algorithm for this (article, subject) pair counts —
		a retired model_version's stale score must not keep an article "relevant" forever
		after a retrain. Ties on created_date are all considered: the algorithm qualifies
		if any tied latest row does, matching api.filters._get_ml_relevant_articles_query
		and gregory.relevance.recompute_article_relevance.

		Args:
			subject: Subject instance to check relevance for
			threshold: Minimum probability score required (default: 0.8)

		Returns:
			bool: True if article meets the ML consensus criteria for the subject
		"""
		# Latest created_date per algorithm for this article+subject pair. Filtering
		# on created_date equal to that maximum keeps every tied latest row, unlike
		# DISTINCT ON, which would pick one tied row arbitrarily.
		latest_date_per_algorithm = (
			MLPredictions.objects.filter(
				article=self,
				subject=subject,
				algorithm=OuterRef("algorithm"),
			)
			.values("algorithm")
			.annotate(latest=Max("created_date"))
			.values("latest")[:1]
		)

		# Count unique algorithms whose *latest* prediction predicted relevant with
		# sufficient confidence.
		relevant_algorithms = set(
			self.ml_predictions_detail.filter(
				subject=subject,
				predicted_relevant=True,
				probability_score__gte=threshold,
				created_date=Subquery(latest_date_per_algorithm),
			).values_list("algorithm", flat=True)
		)
		total_predictions = len(relevant_algorithms)

		if total_predictions == 0:
			return False

		# Apply consensus logic based on subject's ml_consensus_type
		if subject.ml_consensus_type == "any":
			return total_predictions >= 1
		elif subject.ml_consensus_type == "majority":
			return total_predictions >= 2
		elif subject.ml_consensus_type == "all":
			return total_predictions >= 3
		else:
			# Default to 'any' if unknown consensus type
			return total_predictions >= 1

	def is_ml_relevant_any_subject(self, threshold=0.8):
		"""
		Check if this article is ML-relevant for any of its associated subjects.

		Args:
			threshold: Minimum probability score required (default: 0.8)

		Returns:
			bool: True if article meets ML consensus criteria for at least one subject
		"""
		for subject in self.subjects.filter(auto_predict=True):
			if self.is_ml_relevant_for_subject(subject, threshold):
				return True
		return False

	def __str__(self):
		return str(self.article_id)

	class Meta:
		managed = True
		constraints = [
			models.UniqueConstraint(
				fields=["title", "link"], name="unique_article_title_link"
			),
			# One article per DOI (case-insensitive). NULL/empty DOIs are exempt so
			# rows awaiting a find_doi lookup coexist. This is the backstop that
			# survives any code path (management commands, one-off scripts, future
			# indexers) that forgets the application-level guard. Unlike `title`,
			# which is deliberately non-unique, a DOI identifies exactly one paper.
			models.UniqueConstraint(
				Lower("doi"),
				condition=Q(doi__isnull=False) & ~Q(doi=""),
				name="unique_article_doi",
			),
		]
		indexes = [
			# GIN indexes for fast text search on uppercase columns
			GinIndex(
				fields=["utitle"],
				name="articles_utitle_gin_idx",
				opclasses=["gin_trgm_ops"],
			),
			GinIndex(
				fields=["usummary"],
				name="articles_usummary_gin_idx",
				opclasses=["gin_trgm_ops"],
			),
		]
		verbose_name_plural = "articles"
		db_table = "articles"
		ordering = ["-discovery_date"]


class Sponsor(models.Model):
	"""Canonical trial sponsor entity — see TRIALS-SPONSOR-CANONICALIZATION-PLAN.md.
	Resolved from the raw ``Trials.primary_sponsor`` string via SponsorAlias; never set
	directly on a trial by an importer."""

	name = models.CharField(max_length=500, unique=True)
	slug = models.SlugField(max_length=200, unique=True)
	sponsor_type = models.CharField(
		max_length=20, null=True, blank=True,
		choices=SponsorType.choices, db_index=True,
	)
	# Where sponsor_type came from: "curated" (set via sync_sponsor_seeds/admin, never
	# overwritten automatically), "ctgov" (leadSponsor.class), "ctis" (raw sponsor_type),
	# "rules" (keyword classifier on the name). See _SPONSOR_TYPE_SOURCE_PRIORITY above.
	sponsor_type_source = models.CharField(max_length=10, null=True, blank=True)
	# EMA OMS organisation id ("ORG-100001445"), available for CTIS trials via the public
	# API's retrieve endpoint. Nullable, filled opportunistically; a future enrichment
	# pass can anchor/merge sponsors by it. Not populated by this PR.
	oms_id = models.CharField(max_length=20, null=True, blank=True, unique=True)

	def __str__(self):
		return self.name

	class Meta:
		ordering = ["name"]


class SponsorAlias(models.Model):
	"""One raw spelling variant that resolves to a canonical Sponsor. ``key`` (the
	normalize_sponsor_key() output) is the only lookup index sponsor resolution needs —
	see Trials._resolve_primary_sponsor()."""

	sponsor = models.ForeignKey(Sponsor, related_name="aliases", on_delete=models.CASCADE)
	key = models.CharField(max_length=500, unique=True)
	raw_sample = models.TextField()

	def __str__(self):
		return self.raw_sample


class SponsorMergeCandidateBasis(models.TextChoices):
	SUFFIX_VARIANT = "suffix_variant", "Suffix variant"
	CONTAINMENT = "containment", "Containment"


class SponsorMergeCandidateStatus(models.TextChoices):
	PENDING = "pending", "Pending"
	MERGED = "merged", "Merged"
	DISMISSED = "dismissed", "Dismissed"


class SponsorMergeCandidate(models.Model):
	"""A suggested duplicate pair, generated by find_sponsor_merge_candidates.
	Dismissals persist so a reviewed pair never reappears.

	sponsor_a/sponsor_b are SET_NULL, not CASCADE: once SponsorMergeCandidateAdmin's
	"Merge" action absorbs one side into the other, that side's Sponsor row is deleted
	— CASCADE would delete this audit row right along with it, losing the "merged"
	record entirely. SET_NULL lets the row survive with the absorbed side's FK cleared;
	absorbed_sponsor_name snapshots that side's name before deletion so the audit trail
	stays readable. A plain (sponsor_a, sponsor_b) unique constraint still guards
	against duplicate *pending* pairs — Postgres never treats a NULL-containing row as
	equal to another, so a nulled-out merged row can never collide with a fresh pair,
	including a second merge into the same surviving target."""

	sponsor_a = models.ForeignKey(
		Sponsor, related_name="+", null=True, blank=True, on_delete=models.SET_NULL
	)
	sponsor_b = models.ForeignKey(
		Sponsor, related_name="+", null=True, blank=True, on_delete=models.SET_NULL
	)
	basis = models.CharField(max_length=20, choices=SponsorMergeCandidateBasis.choices)
	shared_key = models.CharField(max_length=500)  # the fold key / shared prefix
	status = models.CharField(
		max_length=10,
		choices=SponsorMergeCandidateStatus.choices,
		default=SponsorMergeCandidateStatus.PENDING,
	)
	# Snapshot of the absorbed sponsor's name, set by the "Merge" admin action right
	# before merge_sponsors() deletes it — see the SET_NULL note above.
	absorbed_sponsor_name = models.CharField(max_length=500, blank=True, default="")
	created_at = models.DateTimeField(auto_now_add=True)
	decided_at = models.DateTimeField(null=True, blank=True)

	def __str__(self):
		return f"{self.sponsor_a_id} <-> {self.sponsor_b_id} ({self.basis})"

	class Meta:
		constraints = [
			models.UniqueConstraint(
				fields=["sponsor_a", "sponsor_b"], name="unique_sponsor_candidate_pair"
			)
		]


def _unique_sponsor_slug(name: str) -> str:
	"""slugify(name), truncated to 190 chars, with a numeric suffix appended on
	collision (-2, -3, ...). Slug is for URLs/filters only; never parsed back."""
	base = slugify(name)[:190] or "sponsor"
	slug = base
	n = 2
	while Sponsor.objects.filter(slug=slug).exists():
		suffix = f"-{n}"
		slug = f"{base[: 190 - len(suffix)]}{suffix}"
		n += 1
	return slug


def _create_sponsor_for_key(key: str, display: str) -> "SponsorAlias":
	"""First-sight creation of a Sponsor + its SponsorAlias for *key*, used by
	Trials._resolve_primary_sponsor(). Handles two rare races: (a) a concurrent process
	won the same alias key first — re-fetch and reuse its sponsor; (b) *display* collides
	with an existing Sponsor.name under a different key (e.g. two source strings that
	normalize differently but happen to render identically after whitespace collapse) —
	suffix the name/slug deterministically and retry once."""
	try:
		with transaction.atomic():
			sponsor = Sponsor.objects.create(name=display, slug=_unique_sponsor_slug(display))
			return SponsorAlias.objects.create(sponsor=sponsor, key=key, raw_sample=display)
	except IntegrityError:
		existing_alias = SponsorAlias.objects.select_related("sponsor").filter(key=key).first()
		if existing_alias is not None:
			return existing_alias
		suffixed_name = f"{display} ({key[:40]})"[:500]
		try:
			with transaction.atomic():
				sponsor = Sponsor.objects.create(
					name=suffixed_name, slug=_unique_sponsor_slug(suffixed_name)
				)
				return SponsorAlias.objects.create(sponsor=sponsor, key=key, raw_sample=display)
		except IntegrityError:
			# Same rare race hit twice in a row: someone else won this key while we were
			# retrying. Reuse their alias rather than raising — this is the same
			# concurrent-importer scenario the outer except already handles once.
			existing_alias = SponsorAlias.objects.select_related("sponsor").filter(
				key=key
			).first()
			if existing_alias is not None:
				return existing_alias
			raise


def _update_sponsor_type_from_trial(sponsor: "Sponsor", trial: "Trials") -> None:
	"""Derive sponsor_type from this trial's own signals (lead_sponsor_class -> raw
	sponsor_type -> name keyword rules, see map_sponsor_type) and apply it to *sponsor*
	only when sponsor_type_source is not "curated" and the new source's priority is
	equal to or higher than the sponsor's current source. Called on sponsor creation and
	whenever a trial's sponsor resolution changes — never on the save() happy path where
	the trial already resolves to the same sponsor (see Trials._resolve_primary_sponsor)."""
	if sponsor.sponsor_type_source == "curated":
		return
	new_type, new_source = map_sponsor_type(
		trial.lead_sponsor_class, trial.sponsor_type, sponsor.name
	)
	if new_type is None:
		return
	current_priority = _SPONSOR_TYPE_SOURCE_PRIORITY.get(sponsor.sponsor_type_source, -1)
	new_priority = _SPONSOR_TYPE_SOURCE_PRIORITY.get(new_source, -1)
	if new_priority < current_priority:
		return
	if sponsor.sponsor_type == new_type and sponsor.sponsor_type_source == new_source:
		return
	sponsor.sponsor_type = new_type
	sponsor.sponsor_type_source = new_source
	sponsor.save(update_fields=["sponsor_type", "sponsor_type_source"])


class Trials(models.Model):
	trial_id = models.AutoField(primary_key=True)
	discovery_date = models.DateTimeField(blank=True, null=True)
	last_updated = models.DateTimeField(auto_now=True, null=True, db_index=True)
	title = models.TextField(blank=False, null=False)
	summary = models.TextField(blank=True, null=True)

	# Persisted uppercase columns for performant case-insensitive search
	utitle = GeneratedField(
		expression=Upper("title"), output_field=models.TextField(), db_persist=True
	)
	usummary = GeneratedField(
		expression=Upper("summary"), output_field=models.TextField(), db_persist=True
	)

	link = models.URLField(blank=False, null=False, max_length=2000)
	# All known registry URLs for this trial, keyed by registry slug (e.g.
	# {"ctgov": "https://clinicaltrials.gov/study/NCT…", "ctis": "…"}). ``link``
	# holds the canonical one: the first registry URL discovered, kept for good
	# (see gregory.utils.registry_utils.canonical_link) so importers running later
	# can no longer overwrite it.
	links = models.JSONField(
		blank=True,
		null=True,
		help_text='Registry URLs keyed by registry slug; "link" holds the canonical one',
	)
	published_date = models.DateTimeField(blank=True, null=True, db_index=True)
	sources = models.ManyToManyField("Sources", blank=True)
	team_categories = models.ManyToManyField(
		"TeamCategory", related_name="trials", through="TrialCategoryAssignment"
	)
	identifiers = models.JSONField(blank=True, null=True)
	teams = models.ManyToManyField("Team", related_name="trials")
	subjects = models.ManyToManyField("Subject", related_name="trials")
	history = HistoricalRecords(
		bases=[ApiKeyHistoryMixin],
		m2m_fields=["sources", "teams", "subjects"],
	)

	# WHO Fields
	export_date = models.DateTimeField(null=True, blank=True)
	internal_number = models.CharField(max_length=100, null=True, blank=True)
	last_refreshed_on = models.DateField(null=True, blank=True)
	scientific_title = models.TextField(null=True, blank=True)
	primary_sponsor = models.TextField(null=True, blank=True)
	prospective_registration = models.CharField(max_length=10, null=True, blank=True)
	date_registration = models.DateField(null=True, blank=True)
	source_register = models.CharField(max_length=200, null=True, blank=True)
	recruitment_status = models.CharField(
		max_length=200, null=True, blank=True, db_index=True
	)
	# Canonical recruitment status derived from `recruitment_status` by
	# gregory.utils.trial_field_normalizers. Recomputed on every save() below — never set
	# this directly.
	recruitment_status_normalized = models.CharField(
		max_length=30,
		null=True,
		blank=True,
		choices=TrialRecruitmentStatus.choices,
		db_index=True,
		editable=False,
		help_text="Canonical recruitment status derived from the raw 'recruitment_status' value; recomputed on every save.",
	)
	inclusion_agemin = models.CharField(max_length=100, null=True, blank=True)
	inclusion_agemax = models.CharField(max_length=100, null=True, blank=True)
	inclusion_gender = models.CharField(max_length=500, null=True, blank=True)
	date_enrollement = models.DateField(null=True, blank=True)
	target_size = models.TextField(null=True, blank=True)
	study_type = models.TextField(null=True, blank=True)
	# Canonical study type derived from `study_type` by
	# gregory.utils.trial_field_normalizers. Recomputed on every save() below — never set
	# this directly.
	study_type_normalized = models.CharField(
		max_length=20,
		null=True,
		blank=True,
		choices=TrialStudyType.choices,
		db_index=True,
		editable=False,
		help_text="Canonical study type derived from the raw 'study_type' value; recomputed on every save.",
	)
	study_design = models.TextField(null=True, blank=True)  # Changed to TextField
	phase = models.TextField(null=True, blank=True)  # Changed to TextField
	# Canonical phase derived from `phase` by gregory.utils.trial_field_normalizers.
	# Recomputed on every save() below — never set this directly.
	phase_normalized = models.CharField(
		max_length=20,
		null=True,
		blank=True,
		choices=TrialPhase.choices,
		db_index=True,
		editable=False,
		help_text="Canonical trial phase derived from the raw 'phase' value; recomputed on every save.",
	)
	countries = models.TextField(null=True, blank=True)
	# Per-source raw countries values, keyed by registry slug (gregory.utils.registry_utils
	# .REGISTRY_DOMAINS, e.g. "ctgov", "ictrp"). Each importer writes only its own key
	# (mirroring Trials.links / registry_utils.merge_links) so two differently-delimited
	# per-source strings are never merged into one. EU CTIS writes its own "ctis" key too —
	# sourced from the retrieve endpoint's `rowCountriesInfo` (feedreader_trials_ctis
	# enrichment hook) — since that is the only source of non-EEA participating countries
	# for CTIS trials; `country_status`/`countries_decision_date` remain EEA-only.
	# `countries` (above) keeps its legacy last-writer-wins behaviour for API compatibility;
	# deprecated in favour of this field plus `trial_countries` below. See
	# docs/trials-multi-source-merge.md.
	countries_by_source = models.JSONField(
		blank=True,
		null=True,
		help_text='Raw countries value per source registry, e.g. {"ctgov": "France, United States", "ictrp": "France;Iran (Islamic Republic of)"}. Deprecated "countries" column keeps last-writer-wins behaviour.',
	)
	# Canonical region slugs derived from the normalized country set (trial_countries) plus
	# any literal region/continent tokens in the raw `countries` text. Recomputed on every
	# save() below via NORMALIZED_TRIAL_FIELDS — never set this directly.
	regions_normalized = models.JSONField(
		blank=True,
		null=True,
		editable=False,
		help_text="Canonical region slugs (africa, asia, europe, north_america, south_america, oceania) derived from the trial's countries; recomputed on every save.",
	)
	contact_firstname = models.TextField(null=True, blank=True)
	contact_lastname = models.TextField(null=True, blank=True)
	contact_address = models.TextField(null=True, blank=True)
	contact_email = models.EmailField(max_length=2000, null=True, blank=True)
	contact_tel = models.TextField(null=True, blank=True)
	contact_affiliation = models.TextField(null=True, blank=True)
	inclusion_criteria = models.TextField(null=True, blank=True)  # Changed to TextField
	exclusion_criteria = models.TextField(null=True, blank=True)  # Changed to TextField
	condition = models.TextField(null=True, blank=True)  # Changed to TextField
	intervention = models.TextField(null=True, blank=True)
	primary_outcome = models.TextField(null=True, blank=True)
	secondary_outcome = models.TextField(null=True, blank=True)
	secondary_id = models.TextField(null=True, blank=True)
	source_support = models.TextField(null=True, blank=True)
	ethics_review_status = models.TextField(null=True, blank=True)
	ethics_review_approval_date = models.DateField(null=True, blank=True)
	ethics_review_contact_name = models.EmailField(
		max_length=1000, null=True, blank=True
	)
	ethics_review_contact_address = models.TextField(null=True, blank=True)
	ethics_review_contact_phone = models.TextField(null=True, blank=True)
	ethics_review_contact_email = models.EmailField(
		max_length=1000, null=True, blank=True
	)
	results_date_completed = models.DateField(null=True, blank=True)
	results_url_link = models.URLField(null=True, blank=True, max_length=2000)
	acronym = models.CharField(max_length=200, null=True, blank=True)
	secondary_sponsor = models.TextField(null=True, blank=True)
	results_yes_no = models.CharField(max_length=10, null=True, blank=True)
	results_ipd_plan = models.CharField(max_length=10, null=True, blank=True)
	results_ipd_description = models.TextField(null=True, blank=True)
	ml_predictions = models.ManyToManyField("MLPredictions", blank=True)

	# Fields for euclinicaltrials.eu data
	therapeutic_areas = models.TextField(null=True, blank=True)
	country_status = models.TextField(null=True, blank=True)
	trial_region = models.CharField(max_length=500, null=True, blank=True)
	results_posted = models.BooleanField(default=False)
	overall_decision_date = models.DateField(null=True, blank=True)
	countries_decision_date = models.JSONField(null=True, blank=True)
	countries_recruitment_date = models.JSONField(
		null=True, blank=True,
		help_text='Per-country recruitment start date from the CTIS retrieve endpoint, '
			'keyed by ISO 3166-1 alpha-2 code, e.g. {"IT": "2026-06-26"}. Mirrors '
			'countries_decision_date.',
	)
	sponsor_type = models.CharField(max_length=500, null=True, blank=True)

	# New field added
	other_records = models.CharField(max_length=200, null=True, blank=True)

	# ClinicalTrials.gov API specific fields
	ctg_detailed_description = models.TextField(
		null=True,
		blank=True,
		help_text="Detailed description from ClinicalTrials.gov API",
	)
	# Verbatim protocolSection.sponsorCollaboratorsModule.leadSponsor.class value from the
	# ClinicalTrials.gov API (INDUSTRY, NIH, FED, OTHER_GOV, INDIV, NETWORK, AMBIG, OTHER,
	# UNKNOWN) — same source-fidelity rule as every other raw column. Feeds sponsor_type
	# derivation via gregory.utils.trial_field_normalizers.map_sponsor_type.
	lead_sponsor_class = models.CharField(max_length=20, null=True, blank=True)

	# Canonical sponsor entity resolved from the raw `primary_sponsor` value via
	# SponsorAlias — recomputed on every save() below, see _resolve_primary_sponsor().
	# Never set this directly. PROTECT: sponsors are only deleted via
	# gregory.utils.sponsor_merge.merge_sponsors(), which repoints trials first — used by
	# the merge_sponsors command, sync_sponsor_seeds' fold path, and
	# recompute_sponsor_alias_keys.
	primary_sponsor_normalized = models.ForeignKey(
		"Sponsor", null=True, blank=True, on_delete=models.PROTECT,
		related_name="trials", editable=False,
		help_text="Canonical sponsor entity resolved from the raw 'primary_sponsor' value; recomputed on every save.",
	)

	def _resolve_primary_sponsor(self):
		"""Resolve primary_sponsor_normalized from the raw `primary_sponsor` string,
		auto-creating a new Sponsor + SponsorAlias on first sight of a key. Mirrors
		sync_trial_countries(), but must run before super().save() since it sets a
		local FK field rather than syncing a related model. See
		TRIALS-SPONSOR-CANONICALIZATION-PLAN.md PR 1 §3."""
		key = normalize_sponsor_key(self.primary_sponsor)
		if key is None:
			self.primary_sponsor_normalized = None
			return

		# Compare against the FK id, not self.primary_sponsor_normalized (dereferencing
		# the descriptor would lazy-load the related Sponsor row on every save of an
		# already-resolved trial — an avoidable extra query on the happy path).
		current_id = self.primary_sponsor_normalized_id
		if current_id is not None and SponsorAlias.objects.filter(
			key=key, sponsor_id=current_id
		).exists():
			return  # already resolved to the right entity — skip writes

		alias = SponsorAlias.objects.select_related("sponsor").filter(key=key).first()
		if alias is None:
			display = re.sub(r"\s+", " ", self.primary_sponsor).strip()[:500]
			alias = _create_sponsor_for_key(key, display)

		self.primary_sponsor_normalized = alias.sponsor
		_update_sponsor_type_from_trial(alias.sponsor, self)

	def save(self, *args, **kwargs):
		# Keep every derived field in lockstep with its raw counterpart(s) on every write path
		# (feedreader_trials, feedreader_trials_ctgov, importWHOXML, TrialSerializer.create/update
		# all go through .create()/.save()). bulk_update bypasses this — the backfill command
		# and the admin "Recompute normalized fields" action handle that explicitly. See
		# gregory.utils.trial_field_normalizers.NORMALIZED_TRIAL_FIELDS for the (raw field(s),
		# derived field, normalizer) registry driving this loop — raw_field_names() normalizes
		# the raw-field slot (a single field name or a tuple, for multi-input fields like
		# regions_normalized) to a tuple either way.
		update_fields = kwargs.get("update_fields")
		extra_update_fields = []
		for raw_fields, derived_field, normalizer in NORMALIZED_TRIAL_FIELDS:
			names = raw_field_names(raw_fields)
			setattr(self, derived_field, normalizer(*(getattr(self, name) for name in names)))
			if (
				update_fields is not None
				and any(name in update_fields for name in names)
				and derived_field not in update_fields
			):
				extra_update_fields.append(derived_field)

		# Sponsor resolution is not in NORMALIZED_TRIAL_FIELDS (it needs DB lookups, not a
		# pure raw->derived mapping) and — unlike that loop — it can have DB side effects
		# (creating a Sponsor/SponsorAlias on first sight of a key). So it is gated by
		# update_fields scope entirely, not just its persistence: a scoped save that
		# doesn't touch primary_sponsor must never create sponsor rows as a side effect of
		# an unrelated update (e.g. backfill_trial_countries' update_fields=["countries",
		# "countries_by_source"], which deliberately avoids writing anything else).
		if update_fields is None or "primary_sponsor" in update_fields:
			self._resolve_primary_sponsor()
			if (
				update_fields is not None
				and "primary_sponsor_normalized" not in update_fields
			):
				extra_update_fields.append("primary_sponsor_normalized")

		if extra_update_fields:
			kwargs["update_fields"] = [*update_fields, *extra_update_fields]
		super().save(*args, **kwargs)
		# The per-country TrialCountry rows need a pk, so this runs after super().save().
		# Recomputes the full per-country row set from the raw country columns and replaces
		# it — see docs/trials-field-normalization.md. bulk_update bypasses save() entirely,
		# so the backfill command and the admin recompute action call
		# sync_trial_countries() explicitly for those paths.
		self.sync_trial_countries()

	def sync_trial_countries(self):
		"""Recompute this trial's TrialCountry rows from its raw country columns and replace
		the existing set (create/update/delete as needed). Called automatically from save();
		call directly after a bulk_update-based write, which bypasses save()."""
		rows = (
			normalize_countries(
				self.countries_by_source,
				self.countries,
				self.country_status,
				self.countries_decision_date,
				self.countries_recruitment_date,
			)
			or []
		)
		by_code = {row["country"]: row for row in rows}
		existing = {str(tc.country): tc for tc in self.trial_countries.all()}

		stale_ids = [tc.pk for code, tc in existing.items() if code not in by_code]
		if stale_ids:
			TrialCountry.objects.filter(pk__in=stale_ids).delete()

		for code, row in by_code.items():
			decision_date = None
			raw_decision_date = row.get("decision_date")
			if raw_decision_date:
				try:
					decision_date = datetime.date.fromisoformat(raw_decision_date)
				except (TypeError, ValueError):
					decision_date = None
			recruitment_start_date = None
			raw_recruitment_start_date = row.get("recruitment_start_date")
			if raw_recruitment_start_date:
				try:
					recruitment_start_date = datetime.date.fromisoformat(
						raw_recruitment_start_date
					)
				except (TypeError, ValueError):
					recruitment_start_date = None
			sources = row.get("sources") or []

			existing_row = existing.get(code)
			if existing_row is None:
				TrialCountry.objects.create(
					trial=self,
					country=code,
					status=row.get("status"),
					status_raw=row.get("status_raw"),
					decision_date=decision_date,
					recruitment_start_date=recruitment_start_date,
					sources=sources,
				)
				continue

			changed = False
			if existing_row.status != row.get("status"):
				existing_row.status = row.get("status")
				changed = True
			if existing_row.status_raw != row.get("status_raw"):
				existing_row.status_raw = row.get("status_raw")
				changed = True
			if existing_row.decision_date != decision_date:
				existing_row.decision_date = decision_date
				changed = True
			if existing_row.recruitment_start_date != recruitment_start_date:
				existing_row.recruitment_start_date = recruitment_start_date
				changed = True
			if existing_row.sources != sources:
				existing_row.sources = sources
				changed = True
			if changed:
				existing_row.save()

	def __str__(self):
		return str(self.trial_id)

	class Meta:
		managed = True
		verbose_name_plural = "trials"
		db_table = "trials"
		constraints = [
			# Partial unique constraints per major registry key.
			# Replacing the old case-insensitive title constraint that caused false
			# merges when two different trials shared the same title.
			# See docs/trials-identity-dedup.md – Phase 1 migration.
			models.UniqueConstraint(
				Upper(KeyTextTransform("nct", "identifiers")),
				condition=Q(identifiers__has_key="nct"),
				name="uniq_trial_nct",
			),
			models.UniqueConstraint(
				Upper(KeyTextTransform("euctr", "identifiers")),
				condition=Q(identifiers__has_key="euctr"),
				name="uniq_trial_euctr",
			),
			models.UniqueConstraint(
				Upper(KeyTextTransform("eudract", "identifiers")),
				condition=Q(identifiers__has_key="eudract"),
				name="uniq_trial_eudract",
			),
			models.UniqueConstraint(
				Upper(KeyTextTransform("ctis", "identifiers")),
				condition=Q(identifiers__has_key="ctis"),
				name="uniq_trial_ctis",
			),
		]
		indexes = [
			# Adopts the index migration 0022 already created as raw SQL
			# (CREATE INDEX idx_trials_discovery_date ...). Same name, so
			# Django's model state now matches reality without rebuilding
			# it -- see migration 0076's SeparateDatabaseAndState.
			models.Index(
				fields=["discovery_date"],
				name="idx_trials_discovery_date",
			),
			# Non-unique index on lower(title) to preserve fast title lookups.
			models.Index(
				Lower("title"),
				name="trials_lower_title_idx",
			),
			# GIN indexes for fast text search on uppercase columns
			GinIndex(
				fields=["utitle"],
				name="trials_utitle_gin_idx",
				opclasses=["gin_trgm_ops"],
			),
			GinIndex(
				fields=["usummary"],
				name="trials_usummary_gin_idx",
				opclasses=["gin_trgm_ops"],
			),
			# Non-partial expression indexes on the registry-identifier keys used by
			# the /trials/ identifier filters (api.filters.TrialFilter). A dedicated
			# non-partial index is needed per key for one of two reasons:
			#  - nct/eudract/euctr/ctis already have a *partial* unique index (the
			#    constraints above), but Postgres won't use a partial index for
			#    `Upper(identifiers->>'key') = …` — it can't prove the index's
			#    `identifiers ? 'key'` predicate;
			#  - euct has no unique constraint at all, so it had no index to begin with.
			# Non-partial indexes keep every branch — and the BitmapOr behind the
			# umbrella ?identifiers= filter — off a seq scan.
			models.Index(
				Upper(KeyTextTransform("nct", "identifiers")), name="trials_unct_idx"
			),
			models.Index(
				Upper(KeyTextTransform("eudract", "identifiers")),
				name="trials_ueudract_idx",
			),
			models.Index(
				Upper(KeyTextTransform("euct", "identifiers")), name="trials_ueuct_idx"
			),
			models.Index(
				Upper(KeyTextTransform("euctr", "identifiers")),
				name="trials_ueuctr_idx",
			),
			models.Index(
				Upper(KeyTextTransform("ctis", "identifiers")), name="trials_uctis_idx"
			),
		]


class TrialCountry(models.Model):
	"""Normalized per-country row for a trial — see docs/trials-field-normalization.md.
	Kept in sync with the raw
	`countries`/`countries_by_source`/`country_status`/`countries_decision_date` columns
	by ``Trials.sync_trial_countries()`` (called from ``Trials.save()``; the backfill
	command and admin recompute action call it explicitly for bulk_update paths).
	"""

	trial = models.ForeignKey(
		Trials, related_name="trial_countries", on_delete=models.CASCADE
	)
	country = CountryField()
	# EU CTIS per-country regulatory status (from country_status), same vocabulary as
	# Trials.recruitment_status_normalized. Null for countries only known via a site
	# location (ClinicalTrials.gov) or a WHO ICTRP recruitment-country list.
	status = models.CharField(
		max_length=30,
		null=True,
		blank=True,
		choices=TrialRecruitmentStatus.choices,
	)
	status_raw = models.CharField(max_length=200, null=True, blank=True)
	# EU CTIS per-country decision date (from countries_decision_date). Null for
	# countries not sourced from CTIS.
	decision_date = models.DateField(null=True, blank=True)
	# EU CTIS per-country recruitment start date (from countries_recruitment_date, sourced
	# from the retrieve endpoint's authorizedPartsII[].mscInfo.trialRecruitmentPeriod —
	# earliest date when a country reports more than one period). Null for countries not
	# sourced from CTIS's retrieve enrichment.
	recruitment_start_date = models.DateField(null=True, blank=True)
	# Registry slugs that mentioned this country for this trial, e.g. ["ctgov", "ctis"].
	sources = models.JSONField(default=list, blank=True)

	def __str__(self):
		return f"{self.trial_id}/{self.country}"

	class Meta:
		verbose_name = "trial country"
		verbose_name_plural = "trial countries"
		constraints = [
			models.UniqueConstraint(
				fields=["trial", "country"], name="unique_trial_country"
			)
		]
		indexes = [
			models.Index(fields=["country"], name="trialcountry_country_idx"),
		]


class TrialSite(models.Model):
	"""Per-site row for a trial, sourced from the CTIS retrieve endpoint
	(authorizedPartsII[].trialSites[]) and/or ClinicalTrials.gov's
	contactsLocationsModule.locations[] — see TRIAL-GEOGRAPHY-PLAN.md PR G2.
	Replaced wholesale per-source on each enrichment run (delete only that
	source's rows + bulk_create the new set — gregory.utils.trial_site_sync
	.replace_trial_sites) — no in-place merging, since sites are a small
	per-trial set with no natural update semantics. A trial captured by both
	registries carries both sets side by side, distinguished by `sources`,
	which is why the replace is scoped to one source rather than the whole
	trial. Investigator names are public registry data (CTIS/CTGov both
	publish them); their phone/email are deliberately never stored."""

	trial = models.ForeignKey(Trials, related_name="trial_sites", on_delete=models.CASCADE)
	# Nullable: CTIS requires organisation.name and skips sites without one, but
	# CTGov's facility is occasionally absent — city/coordinates carry the row instead
	# (see ClinicalTrialsGovAPI.extract_sites).
	name = models.CharField(max_length=500, null=True, blank=True)  # organisation.name (CTIS) / facility (CTGov)
	site_type = models.CharField(max_length=200, null=True, blank=True)  # organisation.type label; CTGov has no equivalent, always null
	address = models.TextField(null=True, blank=True)  # address.oneLine; CTGov has no equivalent, always null
	city = models.CharField(max_length=200, null=True, blank=True)
	state = models.CharField(max_length=200, null=True, blank=True)  # CTGov "state"; CTIS leaves null
	postcode = models.CharField(max_length=50, null=True, blank=True)
	country = CountryField(null=True, blank=True)  # address.countryName (CTIS) / country (CTGov), via the name->code helper
	investigator_name = models.CharField(max_length=300, null=True, blank=True)
	# personInfo firstName + " " + lastName (CTIS); CTGov puts PIs in overallOfficials, not per-site, so always null here
	latitude = models.FloatField(null=True, blank=True)  # geoPoint.lat (CTGov only; CTIS has no coordinates)
	longitude = models.FloatField(null=True, blank=True)  # geoPoint.lon (CTGov only; CTIS has no coordinates)
	sources = models.JSONField(default=list, blank=True)  # ["ctis"] or ["ctgov"]

	def __str__(self):
		# name is nullable (CTGov's facility is occasionally absent) — fall back to
		# city, then a generic label, so this never renders "123/None".
		return f"{self.trial_id}/{self.name or self.city or 'site'}"

	class Meta:
		verbose_name = "trial site"
		verbose_name_plural = "trial sites"


class ArticleOrgContent(models.Model):
	"""Per-organisation editorial content for an article."""

	article = models.ForeignKey(
		Articles,
		on_delete=models.CASCADE,
		related_name="org_contents",
	)
	organization = models.ForeignKey(
		Organization,
		on_delete=models.CASCADE,
		related_name="article_contents",
	)
	takeaways = models.TextField(blank=True, null=True)
	summary_plain_english = models.TextField(blank=True, null=True)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)
	history = HistoricalRecords(bases=[ApiKeyHistoryMixin])

	def __str__(self):
		return f"{self.article_id}/{self.organization_id}"

	class Meta:
		constraints = [
			models.UniqueConstraint(
				fields=["article", "organization"], name="unique_article_org_content"
			)
		]
		verbose_name = "article org content"
		verbose_name_plural = "article org contents"


class TrialOrgContent(models.Model):
	"""Per-organisation editorial content for a trial."""

	trial = models.ForeignKey(
		Trials,
		on_delete=models.CASCADE,
		related_name="org_contents",
	)
	organization = models.ForeignKey(
		Organization,
		on_delete=models.CASCADE,
		related_name="trial_contents",
	)
	takeaways = models.TextField(blank=True, null=True)
	summary_plain_english = models.TextField(blank=True, null=True)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)
	history = HistoricalRecords(bases=[ApiKeyHistoryMixin])

	def __str__(self):
		return f"{self.trial_id}/{self.organization_id}"

	class Meta:
		constraints = [
			models.UniqueConstraint(
				fields=["trial", "organization"], name="unique_trial_org_content"
			)
		]
		verbose_name = "trial org content"
		verbose_name_plural = "trial org contents"


def get_fernet():
	try:
		secret_key = settings.FERNET_SECRET_KEY
		return Fernet(secret_key)
	except Exception as e:
		raise ValueError(
			"FERNET_SECRET_KEY is not properly configured in settings."
		) from e


class EncryptedTextField(models.TextField):
	"""
	Custom TextField that encrypts data before saving to the database
	and decrypts it when reading from the database.
	"""

	def from_db_value(self, value, expression, connection):
		if value is None:
			return value
		fernet = get_fernet()
		return fernet.decrypt(base64.b64decode(value)).decode()

	def get_prep_value(self, value):
		if value is None:
			return value
		if not isinstance(value, str):
			raise ValueError("Only strings can be encrypted.")
		fernet = get_fernet()
		return base64.b64encode(fernet.encrypt(value.encode())).decode()


class ActiveTeamManager(models.Manager):
	"""Default manager: returns only active (non-soft-deleted) teams."""

	def get_queryset(self):
		return super().get_queryset().filter(is_active=True)


class Team(models.Model):
	organization = models.ForeignKey(
		Organization, on_delete=models.CASCADE, related_name="teams"
	)
	name = models.CharField(
		max_length=200, help_text="Team name within the organization"
	)
	slug = models.SlugField(unique=True, editable=True)
	site = models.ForeignKey(
		"sites.Site",
		on_delete=models.PROTECT,
		null=True,
		blank=True,
		help_text="The website (Site) this team sends emails from. Overrides the global SITE_ID setting.",
	)
	is_active = models.BooleanField(
		default=True,
		help_text="Inactive teams are soft-deleted: their data is preserved and can be reassigned to another team.",
		db_index=True,
	)

	# Default manager returns only active teams; all_objects returns everything.
	objects = ActiveTeamManager()
	all_objects = models.Manager()

	class Meta:
		constraints = [
			models.UniqueConstraint(
				fields=["organization", "name"], name="unique_organization_team_name"
			)
		]  # Ensure unique team names within each organization

	members = models.ManyToManyField(
		"auth.User",
		blank=True,
		related_name="teams_membership",
		help_text="Users who are members of this team.",
	)

	def __str__(self):
		label = self.name
		if self.organization:
			if self.name.lower() != self.organization.name.lower():
				label = f"{self.name} ({self.organization.name})"
		if not self.is_active:
			label = f"[Inactive] {label}"
		return label

	def delete(self, using=None, keep_parents=False):
		"""Soft-delete: mark as inactive instead of removing from the database."""
		self.is_active = False
		self.save(using=using, update_fields=["is_active"])

	def hard_delete(self, using=None, keep_parents=False):
		"""Physically remove the team row. Only safe once all related objects have been reassigned or removed."""
		super().delete(using=using, keep_parents=keep_parents)


class OrganizationCredentials(models.Model):
	organization = models.OneToOneField(
		Organization,
		on_delete=models.CASCADE,
		related_name="credentials",
		help_text="The organization associated with these credentials.",
	)
	orcid_client_id = EncryptedTextField(
		blank=True, null=True, help_text="ORCID Client ID for this organization."
	)
	orcid_client_secret = EncryptedTextField(
		blank=True, null=True, help_text="ORCID Client Secret for this organization."
	)
	postmark_api_token = EncryptedTextField(
		blank=True, null=True, help_text="Postmark API Token for this organization."
	)
	postmark_api_url = models.URLField(
		max_length=200,
		blank=True,
		null=True,
		default="https://api.postmarkapp.com/email",
		help_text="Postmark API URL for this organization.",
	)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	def __str__(self):
		return f"Credentials for Organization: {self.organization.name}"

	class Meta:
		verbose_name = "Organization Credential"
		verbose_name_plural = "Organization Credentials"


class OrganizationSite(models.Model):
	organization = models.ForeignKey(
		Organization,
		on_delete=models.CASCADE,
		related_name="organization_sites",
		help_text="The organization this site belongs to.",
	)
	site = models.ForeignKey(
		"sites.Site",
		on_delete=models.CASCADE,
		related_name="organization_sites",
		help_text="The Django site associated with this organization.",
	)
	is_default = models.BooleanField(
		default=False,
		help_text="Mark this as the default site for the organization. Used when a team has no site configured.",
	)

	class Meta:
		verbose_name = "Organization Site"
		verbose_name_plural = "Organization Sites"
		unique_together = [("organization", "site")]
		constraints = [
			models.UniqueConstraint(
				fields=["organization"],
				condition=models.Q(is_default=True),
				name="unique_default_site_per_organization",
			)
		]

	def __str__(self):
		default_label = " (default)" if self.is_default else ""
		return f"{self.site.domain}{default_label} — {self.organization.name}"


class OrganizationApiSettings(models.Model):
	organization = models.OneToOneField(
		Organization,
		on_delete=models.CASCADE,
		related_name="api_settings",
		help_text="Organisation whose API exposure these settings govern.",
	)
	make_api_public = models.BooleanField(
		default=False,
		help_text="When true, anonymous API and RSS consumers can see this org's data.",
	)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	def __str__(self):
		return f"API settings for {self.organization.name}"

	class Meta:
		verbose_name = "Organisation API settings"
		verbose_name_plural = "Organisation API settings"


class TeamMember(OrganizationUser):
	class Meta:
		proxy = True


class MLPredictions(models.Model):
	ALGORITHM_CHOICES = [
		("pubmed_bert", "PubMed BERT"),
		("lgbm_tfidf", "LGBM TF-IDF"),
		("lstm", "LSTM"),
		("unknown", "Unknown"),
	]

	created_date = models.DateTimeField(auto_now_add=True)
	subject = models.ForeignKey(
		"Subject", on_delete=models.CASCADE, related_name="ml_subject_predictions"
	)
	article = models.ForeignKey(
		"Articles",
		on_delete=models.CASCADE,
		null=True,
		related_name="ml_predictions_detail",
	)
	model_version = models.CharField(
		max_length=100,
		null=True,
		blank=True,
		help_text="Version identifier of the ML model used",
	)
	algorithm = models.CharField(
		max_length=20,
		choices=ALGORITHM_CHOICES,
		default="unknown",
		help_text="ML algorithm used for prediction",
	)
	probability_score = models.FloatField(
		null=True,
		blank=True,
		help_text="Probability score from the ML model prediction",
	)
	predicted_relevant = models.BooleanField(
		null=True,
		blank=True,
		help_text="Whether the ML model predicted this article as relevant",
	)

	class Meta:
		constraints = [
			models.UniqueConstraint(
				fields=["article", "subject", "model_version", "algorithm"],
				name="unique_article_subject_prediction",
			)
		]
		indexes = [
			models.Index(
				fields=["article", "subject", "-created_date"],
				name="mlpred_art_subj_date_idx",
			),
		]

	@classmethod
	def get_latest_prediction(cls, article, subject, model_version=None):
		"""
		Get the latest prediction for a given article and subject, optionally filtered by model version.

		Args:
			article: Articles instance or ID
			subject: Subject instance or ID
			model_version: Optional model version string to filter by

		Returns:
			Latest MLPredictions instance or None if no predictions exist
		"""
		query = cls.objects.filter(article=article, subject=subject)

		if model_version:
			query = query.filter(model_version=model_version)

		return query.order_by("-created_date").first()


class ArticleSubjectRelevance(models.Model):
	article = models.ForeignKey(
		Articles, related_name="article_subject_relevances", on_delete=models.CASCADE
	)
	subject = models.ForeignKey("Subject", on_delete=models.CASCADE)
	is_relevant = models.BooleanField(
		null=True,
		blank=True,
		default=None,
		help_text="Indicates if the article is relevant for the subject. NULL means not reviewed.",
	)

	class Meta:
		constraints = [
			models.UniqueConstraint(
				fields=["article", "subject"], name="unique_article_subject_relevance"
			)
		]
		verbose_name_plural = "article subject relevances"

	def __str__(self):
		if self.is_relevant is True:
			relevance_status = "Relevant"
		elif self.is_relevant is False:
			relevance_status = "Not Relevant"
		else:
			relevance_status = "Not Reviewed"
		return f"{self.article.title} - {self.subject.subject_name}: {relevance_status}"


class ArticleTrialReference(models.Model):
	"""
	Represents a relationship between an Article and a Trial, where the Article's summary
	contains an identifier from the Trial's identifiers field.
	"""

	article = models.ForeignKey(
		"Articles", on_delete=models.CASCADE, related_name="trial_references"
	)
	trial = models.ForeignKey(
		"Trials", on_delete=models.CASCADE, related_name="article_references"
	)
	identifier_type = models.CharField(
		max_length=50, help_text="Which identifier was found (e.g., 'nct_id', 'isrctn')"
	)
	identifier_value = models.CharField(
		max_length=100, help_text="The actual identifier value"
	)
	discovered_date = models.DateTimeField(auto_now_add=True)

	class Meta:
		constraints = [
			models.UniqueConstraint(
				fields=["article", "trial", "identifier_type"],
				name="unique_article_trial_identifier",
			)
		]
		verbose_name_plural = "article trial references"
		db_table = "article_trial_references"
		indexes = [
			models.Index(fields=["identifier_type", "identifier_value"]),
		]

	def __str__(self):
		return f"Article {self.article.article_id} references Trial {self.trial.trial_id} via {self.identifier_type}"


class PredictionRunLog(models.Model):
	"""
	Logs both training and prediction runs for machine learning models.
	"""

	RUN_TYPE_CHOICES = [("train", "Training"), ("predict", "Prediction")]

	ALGORITHM_CHOICES = [
		("pubmed_bert", "PubMed BERT"),
		("lgbm_tfidf", "LGBM TF-IDF"),
		("lstm", "LSTM"),
		("unknown", "Unknown"),
	]

	team = models.ForeignKey(
		"Team", on_delete=models.CASCADE, related_name="prediction_run_logs"
	)
	subject = models.ForeignKey(
		"Subject", on_delete=models.CASCADE, related_name="prediction_run_logs"
	)
	model_version = models.CharField(
		max_length=100, help_text="Version identifier for the model used"
	)
	algorithm = models.CharField(
		max_length=20,
		choices=ALGORITHM_CHOICES,
		default="unknown",
		help_text="ML algorithm used for the run",
	)
	run_type = models.CharField(
		max_length=10,
		choices=RUN_TYPE_CHOICES,
		help_text="Type of run: training or prediction",
	)
	run_started = models.DateTimeField(
		auto_now_add=True, help_text="When the run was started"
	)
	run_finished = models.DateTimeField(
		null=True, blank=True, help_text="When the run was completed"
	)
	success = models.BooleanField(
		null=True, blank=True, help_text="Whether the run was successful"
	)
	triggered_by = models.CharField(
		max_length=100,
		null=True,
		blank=True,
		help_text="User or system that triggered the run",
	)
	error_message = models.TextField(
		null=True, blank=True, help_text="Error message if the run failed"
	)

	class Meta:
		verbose_name = "Prediction Run Log"
		verbose_name_plural = "Prediction Run Logs"
		indexes = [
			models.Index(fields=["team", "subject", "run_finished"]),
			models.Index(fields=["run_type", "success"]),
		]

	def __str__(self):
		status = (
			"Successful"
			if self.success
			else "Failed"
			if self.success is False
			else "Running"
		)
		return f"{self.get_run_type_display()} run for {self.team} - {self.subject} ({status})"

	@classmethod
	def get_latest_run(cls, team, subject, run_type=None):
		"""
		Get the latest completed run for a team/subject combination.

		Args:
			team: Team instance
			subject: Subject instance
			run_type: Optional filter by run type ('train' or 'predict')

		Returns:
			Latest PredictionRunLog instance or None if no completed runs
		"""
		query = cls.objects.filter(
			team=team, subject=subject, run_finished__isnull=False
		)

		if run_type:
			query = query.filter(run_type=run_type)

		return query.order_by("-run_finished").first()
