from io import StringIO

from django.contrib import admin, messages
from django.apps import apps
from django.core.management import call_command
from django.core.exceptions import FieldDoesNotExist, ObjectDoesNotExist, PermissionDenied
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from django.http import HttpResponse
from django.shortcuts import render, redirect
from django.urls import path, reverse
import csv
import logging
from simple_history.admin import SimpleHistoryAdmin  # Import SimpleHistoryAdmin
from .admin_filters import DateRangeFilter, SourceHealthFilter
from django.db import models, transaction  # Add this import for models.Count
from django.db.models import OuterRef, Subquery
from django.utils import timezone
from django import forms
from django.utils.html import format_html, mark_safe
from organizations.models import Organization, OrganizationUser
from organizations.admin import OrganizationAdmin as BaseOrganizationAdmin

from .models import (
	Articles,
	Trials,
	Sources,
	Entities,
	Authors,
	Subject,
	ArticleSubjectRelevance,
	TeamCategory,
	PredictionRunLog,
	Team,
	ArticleTrialReference,
	OrganizationCredentials,
	OrganizationSite,
	OrganizationApiSettings,
	ArticleOrgContent,
	TrialOrgContent,
	ArticleCategoryAssignment,
	TrialCategoryAssignment,
	CategoryType,
	default_match_weights,
)
from .widgets import MLPredictionsWidget
from .fields import MLPredictionsField


def get_user_organizations(user):
	"""Get the list of organization IDs for a user."""
	if user.is_superuser:
		return None  # None means all organizations
	return user.organizations_organizationuser.values_list(
		"organization__id", flat=True
	)


class OrganizationRestrictedFieldListFilter(admin.RelatedFieldListFilter):
	"""Custom filter that restricts related field choices to user's organization."""

	def field_choices(self, field, request, model_admin):
		"""Override to filter choices based on user's organization."""
		choices = super().field_choices(field, request, model_admin)

		if request.user.is_superuser:
			return choices

		user_orgs = get_user_organizations(request.user)

		# Filter the choices based on organization
		filtered_choices = []
		for choice_value, choice_label in choices:
			if choice_value is None:
				# Include the empty choice
				filtered_choices.append((choice_value, choice_label))
				continue

			try:
				# Get the object for this choice
				obj = field.related_model.objects.get(pk=choice_value)

				# Check if it belongs to user's organization
				if hasattr(obj, "organization"):
					if obj.organization_id in user_orgs:
						filtered_choices.append((choice_value, choice_label))
				elif hasattr(obj, "team"):
					if obj.team.organization_id in user_orgs:
						filtered_choices.append((choice_value, choice_label))
				else:
					# If no organization field, include it
					filtered_choices.append((choice_value, choice_label))
			except (ObjectDoesNotExist, AttributeError, ValueError, TypeError) as e:
				# Fail closed: if a choice's org can't be resolved, omit it rather
				# than risk leaking it; logged so a systematic failure stays visible.
				logging.getLogger(__name__).warning(
					"OrganizationRestrictedFieldListFilter: skipping choice %r (%s)",
					choice_value,
					e,
				)

		return filtered_choices


class BaseOrganizationFilter(admin.SimpleListFilter):
	"""Base filter that lists organisations scoped to the current user's access."""

	title = "organisation"
	parameter_name = "organisation"

	def lookups(self, request, model_admin):
		if request.user.is_superuser:
			orgs = Organization.objects.all().order_by("name")
		else:
			user_org_ids = get_user_organizations(request.user)
			orgs = Organization.objects.filter(id__in=user_org_ids).order_by("name")
		return [(org.pk, org.name) for org in orgs]


class ArticleOrganizationFilter(BaseOrganizationFilter):
	"""Filter articles by organisation (via teams M2M → organization)."""

	def queryset(self, request, queryset):
		if self.value():
			return queryset.filter(teams__organization__id=self.value()).distinct()
		return queryset


class SourceOrganizationFilter(BaseOrganizationFilter):
	"""Filter sources by organisation (via team FK → organization)."""

	def queryset(self, request, queryset):
		if self.value():
			return queryset.filter(team__organization_id=self.value())
		return queryset


class OrganizationFilterMixin:
	"""
	Mixin to restrict admin queryset visibility based on user's organization.
	Superusers see everything; staff users only see objects from their organization.
	"""

	def get_queryset(self, request):
		"""Filter queryset by organization for non-superusers."""
		qs = super().get_queryset(request)

		# Superusers see everything
		if request.user.is_superuser:
			return qs

		# Staff users only see objects from their organization
		user_orgs = get_user_organizations(request.user)

		# Try to filter by organization directly
		if hasattr(qs.model, "organization"):
			return qs.filter(organization__id__in=user_orgs)

		# If the model has a team relationship, filter by team's organization
		if hasattr(qs.model, "team"):
			return qs.filter(team__organization__id__in=user_orgs)

		# If the model has multiple teams (M2M), filter by any team's organization
		try:
			# Check if 'teams' is a M2M field
			qs.model._meta.get_field("teams")
		except FieldDoesNotExist:
			# Model has no 'teams' field; fall through to the unscoped queryset.
			return qs
		return qs.filter(teams__organization__id__in=user_orgs).distinct()


class ArticleTrialReferenceInline(admin.TabularInline):
	model = ArticleTrialReference
	extra = 0
	readonly_fields = [
		"trial",
		"identifier_type",
		"identifier_value",
		"discovered_date",
	]
	can_delete = False
	verbose_name_plural = "Referenced Trials"
	verbose_name = "Trial Reference"
	classes = ["collapse"]

	def has_add_permission(self, request, obj=None):
		return False  # Prevent manual adding, should be done by the detect_trial_references command


class TrialArticleReferenceInline(admin.TabularInline):
	model = ArticleTrialReference
	extra = 0
	readonly_fields = [
		"article",
		"identifier_type",
		"identifier_value",
		"discovered_date",
	]
	can_delete = False
	verbose_name_plural = "Referencing Articles"
	verbose_name = "Article Reference"
	classes = ["collapse"]

	def has_add_permission(self, request, obj=None):
		return False  # Prevent manual adding, should be done by the detect_trial_references command


class RelevanceRadioWidget(forms.RadioSelect):
	"""Custom radio widget with horizontal layout and emojis"""

	def render(self, name, value, attrs=None, renderer=None):
		from django.utils.safestring import mark_safe

		if attrs is None:
			attrs = {}

		choices_html = []
		for choice_value, choice_label in self.choices:
			# Handle None, True, False comparison properly
			if choice_value is None and value is None:
				checked = "checked"
			elif choice_value == value:
				checked = "checked"
			elif str(choice_value) == str(value):
				checked = "checked"
			else:
				checked = ""

			choice_id = f"{attrs.get('id', name)}_{choice_value or 'none'}"

			choice_html = f'''
				<label style="margin-right: 15px; white-space: nowrap; cursor: pointer;">
					<input type="radio" id="{choice_id}" name="{name}" value="{choice_value if choice_value is not None else ""}" {checked} style="margin-right: 5px;">
					{choice_label}
				</label>
			'''
			choices_html.append(choice_html)

		final_html = f'<div style="display: flex; align-items: center; gap: 10px;">{"".join(choices_html)}</div>'
		return mark_safe(final_html)


class SubjectDisplayWidget(forms.Widget):
	"""Shows subject name as bold text (with hidden pk) for existing rows."""

	def render(self, name, value, attrs=None, renderer=None):
		if value:
			try:
				subject = Subject.objects.get(pk=value)
				return mark_safe(
					f"<strong>{subject}</strong>"
					f'<input type="hidden" name="{name}" value="{value}">'
				)
			except Subject.DoesNotExist:
				pass
		return mark_safe(f'<input type="hidden" name="{name}" value="">')


class ArticleSubjectRelevanceForm(forms.ModelForm):
	RELEVANCE_CHOICES = [
		(None, "⚪ Not Reviewed"),
		(True, "✅ Relevant"),
		(False, "❌ Not Relevant"),
	]

	is_relevant = forms.ChoiceField(
		choices=RELEVANCE_CHOICES,
		widget=RelevanceRadioWidget,
		required=False,
		initial=None,
		label="Relevance",
	)

	class Meta:
		model = ArticleSubjectRelevance
		fields = ["subject", "is_relevant"]
		widgets = {
			"subject": SubjectDisplayWidget(),
		}

	def clean_is_relevant(self):
		"""Convert string choice to boolean/None value"""
		value = self.cleaned_data.get("is_relevant")
		# Convert string representations to actual values
		if value == "True" or value is True:
			return True
		elif value == "False" or value is False:
			return False
		elif value == "None" or value == "" or value is None:
			return None
		else:
			return None  # Default to "Not Reviewed"


class ArticleSubjectRelevanceFormSet(forms.BaseInlineFormSet):
	"""Custom formset that shows the subject dropdown only on new (unsaved) rows."""

	def add_fields(self, form, index):
		super().add_fields(form, index)
		if not form.instance.pk:
			# New row: show a subject select dropdown
			form.fields["subject"].widget = forms.Select()
			form.fields["subject"].queryset = Subject.objects.all().order_by(
				"subject_name"
			)
		else:
			# Existing row: show subject name as text with embedded hidden pk
			form.fields["subject"].widget = SubjectDisplayWidget()


class ArticleSubjectRelevanceInline(admin.TabularInline):
	model = ArticleSubjectRelevance
	form = ArticleSubjectRelevanceForm
	formset = ArticleSubjectRelevanceFormSet
	can_delete = True
	fields = ["subject", "is_relevant"]

	def get_extra(self, request, obj=None, **kwargs):
		"""Superusers get one extra empty form to add new subject relevances"""
		if request.user.is_superuser:
			return 1
		return 0

	def get_formset(self, request, obj=None, **kwargs):
		"""Pre-populate with subjects for the article's teams.
		Superusers get all subjects; other users only get auto_predict subjects.
		"""
		if obj and obj.pk:  # If editing existing article
			if request.user.is_superuser:
				team_subjects = (
					Subject.objects.filter(team__in=obj.teams.all())
					.distinct()
					.order_by("subject_name")
				)
			else:
				team_subjects = (
					Subject.objects.filter(team__in=obj.teams.all(), auto_predict=True)
					.distinct()
					.order_by("subject_name")
				)

			# Create ArticleSubjectRelevance instances for any missing subjects
			for subject in team_subjects:
				ArticleSubjectRelevance.objects.get_or_create(
					article=obj, subject=subject, defaults={"is_relevant": None}
				)

		return super().get_formset(request, obj, **kwargs)

	def get_queryset(self, request):
		"""Superusers see all subjects; other users see only auto_predict subjects"""
		qs = super().get_queryset(request).select_related("subject")
		if not request.user.is_superuser:
			qs = qs.filter(subject__auto_predict=True)
		return qs.order_by("subject__subject_name")


class _BaseOrgContentInline(admin.StackedInline):
	"""Shared inline behaviour for ArticleOrgContent / TrialOrgContent.

	- Staff users only see/edit rows for their own organisation(s).
	- Superusers see every row and can add new rows for any organisation.
	- For staff, the organisation field is pre-filled and locked so they can
	  never reassign content between orgs.
	- Empty extra rows (both textareas blank) are skipped at save time, so
	  opening an article without typing anything does not pollute the table.
	"""

	extra = 0
	fields = ("organization", "takeaways", "summary_plain_english", "updated_at")
	readonly_fields = ("updated_at",)

	def get_queryset(self, request):
		qs = super().get_queryset(request).select_related("organization")
		if request.user.is_superuser:
			return qs
		user_orgs = get_user_organizations(request.user)
		return qs.filter(organization_id__in=user_orgs)

	def has_add_permission(self, request, obj=None):
		if request.user.is_superuser:
			return True
		return bool(get_user_organizations(request.user))

	def _missing_org_ids(self, request, obj):
		"""Return user-orgs that don't yet have a content row for this parent."""
		if obj is None or not obj.pk:
			return []
		user_orgs = list(get_user_organizations(request.user) or [])
		if not user_orgs:
			return []
		existing = set(
			obj.org_contents.filter(organization_id__in=user_orgs).values_list(
				"organization_id", flat=True
			)
		)
		return [oid for oid in user_orgs if oid not in existing]

	def get_extra(self, request, obj=None, **kwargs):
		if obj is None or not obj.pk:
			return 0
		if request.user.is_superuser:
			return 1
		return len(self._missing_org_ids(request, obj))

	def get_max_num(self, request, obj=None, **kwargs):
		if request.user.is_superuser:
			return None
		# Cap at user's org count so they can't create rows for orgs they don't belong to.
		user_orgs = list(get_user_organizations(request.user) or [])
		return len(user_orgs)

	def get_formset(self, request, obj=None, **kwargs):
		formset_class = super().get_formset(request, obj, **kwargs)
		is_superuser = request.user.is_superuser

		if is_superuser:
			allowed_org_qs = Organization.objects.all().order_by("name")
			extra_org_ids = []
		else:
			user_org_ids = list(get_user_organizations(request.user) or [])
			allowed_org_qs = Organization.objects.filter(id__in=user_org_ids).order_by(
				"name"
			)
			extra_org_ids = self._missing_org_ids(request, obj)

		class ScopedOrgContentFormSet(formset_class):
			def _construct_form(self, i, **form_kwargs):
				form = super()._construct_form(i, **form_kwargs)
				form.fields["organization"].queryset = allowed_org_qs
				if form.instance.pk:
					# Existing row: lock the org so content can't be reassigned.
					form.fields["organization"].disabled = True
				elif not is_superuser:
					# Extra row for staff: pre-fill and lock to their next missing org.
					extra_index = i - self.initial_form_count()
					if 0 <= extra_index < len(extra_org_ids):
						form.fields["organization"].initial = extra_org_ids[extra_index]
						form.fields["organization"].disabled = True
				return form

		return ScopedOrgContentFormSet


class ArticleOrgContentInline(_BaseOrgContentInline):
	model = ArticleOrgContent
	verbose_name = "Editorial content (per organisation)"
	verbose_name_plural = "Editorial content (per organisation)"


class TrialOrgContentInline(_BaseOrgContentInline):
	model = TrialOrgContent
	verbose_name = "Editorial content (per organisation)"
	verbose_name_plural = "Editorial content (per organisation)"


class ArticleCategoryAssignmentInline(admin.TabularInline):
	model = ArticleCategoryAssignment
	extra = 0
	autocomplete_fields = ["teamcategory"]
	verbose_name = "Category assignment"
	verbose_name_plural = "Category assignments"

	def get_queryset(self, request):
		return (
			super()
			.get_queryset(request)
			.select_related("teamcategory", "teamcategory__team")
		)


class TrialCategoryAssignmentInline(admin.TabularInline):
	model = TrialCategoryAssignment
	extra = 0
	autocomplete_fields = ["teamcategory"]
	verbose_name = "Category assignment"
	verbose_name_plural = "Category assignments"

	def get_queryset(self, request):
		return (
			super()
			.get_queryset(request)
			.select_related("teamcategory", "teamcategory__team")
		)


class SourceActionForm(forms.Form):
	"""Form for the 'Add source to selected' / 'Remove source from selected' actions."""

	source = forms.ModelChoiceField(
		queryset=Sources.objects.none(),
		label="Source",
	)


class SourceBulkActionMixin:
	"""
	Admin mixin that adds 'Add source to selected…' and 'Remove source from
	selected…' bulk actions for models with a `sources` ManyToManyField
	(Articles, Trials).

	Subclasses must set `source_for_values` to the list of Sources.source_for
	values that are valid for their model (e.g. ["trials"]), and their model
	must have a `sources` M2M field to Sources with the default reverse
	accessor (`<model_name>_set`).
	"""

	source_for_values = []

	def _get_source_queryset(self, request):
		qs = Sources.objects.filter(source_for__in=self.source_for_values).order_by(
			"name"
		)
		if request.user.is_superuser:
			return qs
		user_orgs = get_user_organizations(request.user)
		return qs.filter(team__organization__id__in=user_orgs)

	def _source_bulk_action(self, request, queryset, *, action_name, verb):
		source_qs = self._get_source_queryset(request)

		if "apply" not in request.POST:
			form = SourceActionForm()
			form.fields["source"].queryset = source_qs
			return render(
				request,
				"admin/gregory/source_bulk_action_intermediate.html",
				{
					"title": f"{verb} source",
					"objects": queryset,
					"form": form,
					"action_checkbox_name": admin.helpers.ACTION_CHECKBOX_NAME,
					"model_name": queryset.model._meta.verbose_name_plural,
					"action": action_name,
					"verb": verb,
				},
			)

		form = SourceActionForm(request.POST)
		form.fields["source"].queryset = source_qs

		if not form.is_valid():
			self.message_user(
				request, "Invalid form — please try again.", level=messages.ERROR
			)
			return

		source = form.cleaned_data["source"]
		model_plural = queryset.model._meta.verbose_name_plural

		# Add/remove from the forward (obj.sources) side, one object at a time,
		# inside a single transaction so a mid-loop failure (e.g. a signal or DB
		# error) can't leave a partially applied change across the selection.
		# `.only("pk").iterator()` avoids materializing every selected object in
		# memory at once. django-simple-history tracks `sources` via m2m_fields
		# on Articles/Trials' HistoricalRecords; the m2m_changed signal it relies
		# on carries whichever instance initiated the change, so adding via the
		# Sources reverse accessor (source.articles_set.add(...)) fires the
		# signal with a Sources instance, which has no `.history` manager and
		# breaks history recording.
		with transaction.atomic():
			pk_queryset = queryset.prefetch_related(None).only("pk")
			if action_name == "add_source_action":
				already_linked = queryset.filter(sources=source).count()
				for obj in pk_queryset.iterator():
					obj.sources.add(source)
				newly_linked = queryset.count() - already_linked
				self.message_user(
					request,
					f"Added '{source}' to {newly_linked} {model_plural} "
					f"({already_linked} already had it).",
				)
			else:
				linked = queryset.filter(sources=source).count()
				for obj in pk_queryset.iterator():
					obj.sources.remove(source)
				self.message_user(
					request, f"Removed '{source}' from {linked} {model_plural}."
				)

	@admin.action(description="Add source to selected…")
	def add_source_action(self, request, queryset):
		return self._source_bulk_action(
			request, queryset, action_name="add_source_action", verb="Add"
		)

	@admin.action(description="Remove source from selected…")
	def remove_source_action(self, request, queryset):
		return self._source_bulk_action(
			request, queryset, action_name="remove_source_action", verb="Remove"
		)


class ArticleAdminForm(forms.ModelForm):
	ml_predictions_display = MLPredictionsField(required=False)

	class Meta:
		model = Articles
		fields = "__all__"
		widgets = {
			"ml_predictions": MLPredictionsWidget(),
		}
		labels = {
			"link": "Article URL (canonical)",
			"links": "All source URLs",
		}
		help_texts = {
			"link": (
				"First URL seen for this article; stable after first import. "
				'Corresponds to "link" in the API response.'
			),
			"links": (
				'All known URLs for this article, keyed by registry slug (e.g. "ctgov") '
				"for known registries or by hostname otherwise. "
				"Managed automatically — do not edit. "
				'Corresponds to "links" in the API response.'
			),
		}

	def __init__(self, *args, **kwargs):
		self.request = kwargs.pop("request", None)
		super().__init__(*args, **kwargs)

		if self.instance and self.instance.pk:
			self.fields[
				"ml_predictions_display"
			].initial = self.instance.ml_predictions_detail.all()

		# Filter team and subject choices based on user's organization
		if self.request:
			if self.request.user.is_superuser:
				self.fields["teams"].queryset = Team.objects.all()
				self.fields["subjects"].queryset = Subject.objects.all()
			else:
				user_orgs = get_user_organizations(self.request.user)
				self.fields["teams"].queryset = Team.objects.filter(
					organization__id__in=user_orgs
				)
				self.fields["subjects"].queryset = Subject.objects.filter(
					team__organization__id__in=user_orgs
				)


class ArticleAdmin(OrganizationFilterMixin, SourceBulkActionMixin, SimpleHistoryAdmin):
	form = ArticleAdminForm
	source_for_values = ["science paper", "news article"]
	actions = ["add_source_action", "remove_source_action"]
	inlines = [
		ArticleOrgContentInline,
		ArticleSubjectRelevanceInline,
		ArticleTrialReferenceInline,
		ArticleCategoryAssignmentInline,
	]
	fieldsets = (
		(
			"Article Information",
			{
				"fields": (
					"title",
					"link",
					"links",
					"doi",
					"summary",
					"teams",
					"subjects",
					"sources",
					"published_date",
					"discovery_date",
					"authors",
					"entities",
					"kind",
					"access",
					"pdf_link",
					"publisher",
					"container_title",
					"crossref_check",
					"retracted",
					"crossref_retraction_check",
				),
				"description": "This section contains general information about the article",
			},
		),
		(
			"Machine Learning Relevancy Predictions per Subject",
			{
				"fields": ("ml_predictions_display",),
				"description": "Grouping machine learning prediction indicators",
				"classes": ("ml-predictions-section",),
			},
		),
	)
	list_display = ["article_id", "title", "discovery_date", "display_sources"]
	ordering = ["-discovery_date"]

	@admin.display(description="Sources")
	def display_sources(self, obj):
		"""Display sources as comma-separated list."""
		return ", ".join([source.name for source in obj.sources.all()])

	def get_queryset(self, request):
		"""Optimize queryset with prefetch for sources."""
		qs = super().get_queryset(request)
		return qs.prefetch_related("sources")

	readonly_fields = ["entities", "discovery_date", "links"]
	search_fields = ["article_id", "title", "doi"]
	list_filter = [
		ArticleOrganizationFilter,
		("teams", OrganizationRestrictedFieldListFilter),
		("subjects", OrganizationRestrictedFieldListFilter),
		("sources", OrganizationRestrictedFieldListFilter),
	]
	raw_id_fields = ("authors",)

	def get_form(self, request, obj=None, **kwargs):
		"""Pass the request to the form so it can filter field choices."""
		form_class = super().get_form(request, obj, **kwargs)

		class FormWithRequest(form_class):
			def __init__(self, *args, **form_kwargs):
				form_kwargs["request"] = request
				super().__init__(*args, **form_kwargs)

		return FormWithRequest

	def get_urls(self):
		from django.urls import path
		from .admin_views import (
			article_review_status_view,
			update_article_relevance_ajax,
			add_article_by_doi_view,
		)

		urls = super().get_urls()
		custom_urls = [
			path(
				"review-status/",
				self.admin_site.admin_view(article_review_status_view),
				name="article_review_status",
			),
			path(
				"update-article-relevance/",
				self.admin_site.admin_view(update_article_relevance_ajax),
				name="update_article_relevance",
			),
			path(
				"add-by-doi/",
				self.admin_site.admin_view(add_article_by_doi_view),
				name="article_add_by_doi",
			),
		]
		return custom_urls + urls

	def changelist_view(self, request, extra_context=None):
		"""Override changelist view to add buttons above the article list"""
		extra_context = extra_context or {}
		extra_context["review_status_url"] = reverse("admin:article_review_status")
		extra_context["add_by_doi_url"] = reverse("admin:article_add_by_doi")
		return super().changelist_view(request, extra_context=extra_context)

	class Media:
		css = {
			"all": ["admin/css/ml_predictions.css"],
		}


class TrialAdminForm(forms.ModelForm):
	"""Adds plain-language labels and help text for fields that use clinical-trial jargon."""

	class Meta:
		model = Trials
		fields = "__all__"
		labels = {
			# Titles & identity
			"title": "Public title",
			"scientific_title": "Scientific title",
			"acronym": "Study acronym",
			"link": "Registry web page (canonical)",
			"links": "All registry URLs",
			"identifiers": "Trial IDs",
			"internal_number": "Registry internal number",
			"secondary_id": "Other IDs",
			# Dates & registry metadata
			"published_date": "Headline date",
			"date_registration": "Registration date",
			"date_enrollement": "Enrolment start date",
			"last_refreshed_on": "Registry last updated on",
			"export_date": "Exported from registry on",
			"source_register": "Source registry",
			"other_records": "Listed in other registries?",
			"prospective_registration": "Registered before it started?",
			# Study details
			"study_type": "Study type",
			"study_design": "Study design",
			"phase": "Trial phase",
			"recruitment_status": "Recruitment status",
			"target_size": "Target enrolment",
			"countries": "Countries",
			# Conditions & interventions
			"condition": "Health condition",
			"intervention": "Intervention",
			"primary_outcome": "Primary outcome",
			"secondary_outcome": "Secondary outcomes",
			# Eligibility
			"inclusion_criteria": "Who can take part (inclusion)",
			"exclusion_criteria": "Who cannot take part (exclusion)",
			"inclusion_agemin": "Minimum age",
			"inclusion_agemax": "Maximum age",
			"inclusion_gender": "Eligible sex / gender",
			# Sponsors & contacts
			"primary_sponsor": "Main sponsor",
			"secondary_sponsor": "Co-sponsors",
			"source_support": "Funding source",
			"contact_firstname": "Contact first name",
			"contact_lastname": "Contact last name",
			"contact_address": "Contact address",
			"contact_email": "Contact email",
			"contact_tel": "Contact phone",
			"contact_affiliation": "Contact organisation",
			# Ethics review
			"ethics_review_status": "Ethics approval status",
			"ethics_review_approval_date": "Ethics approval date",
			"ethics_review_contact_name": "Ethics committee contact",
			"ethics_review_contact_address": "Ethics contact address",
			"ethics_review_contact_phone": "Ethics contact phone",
			"ethics_review_contact_email": "Ethics contact email",
			# Results
			"results_posted": "Results posted?",
			"results_date_completed": "Results completion date",
			"results_url_link": "Results link",
			"results_yes_no": "Results available?",
			"results_ipd_plan": "Plans to share participant data?",
			"results_ipd_description": "Data-sharing details",
		}
		help_texts = {
			# Titles & identity
			"title": "The plain-language title of the trial, intended for the general public. Sources: WHO ICTRP, ClinicalTrials.gov, EU CTIS.",
			"scientific_title": "The technical title of the trial, written using medical terminology. Sources: WHO ICTRP, ClinicalTrials.gov.",
			"acronym": "Short nickname or abbreviation for the trial (e.g. “IMPACT-MS”). Source: WHO ICTRP.",
			"link": (
				'The canonical registry URL for this trial - the first registry URL discovered, kept for good. Exposed as "link" in the API response and on the frontend. Managed automatically by importers; edit only to correct an incorrect URL. Sources: WHO ICTRP, ClinicalTrials.gov, EU CTIS.'
			),
			"links": (
				'All known registry URLs for this trial, keyed by registry slug (e.g. {"ctgov": "https://clinicaltrials.gov/...", "ctis": "https://euclinicaltrials.eu/..."}). Populated and merged automatically by importers - do not edit manually. Exposed as "links" in the API response.'
			),
			"identifiers": "Registry identifiers for this trial (e.g. NCT, ChiCTR, or EUCTR numbers). Sources: WHO ICTRP, ClinicalTrials.gov, EU CTIS.",
			"internal_number": "Internal record number assigned by the source registry. Rarely meaningful to patients. Source: WHO ICTRP.",
			"secondary_id": "Additional identifiers used by other registries or by the sponsor. Sources: WHO ICTRP, ClinicalTrials.gov.",
			# Dates & registry metadata
			"published_date": "The trial’s headline date — its exact meaning depends on the source: WHO ICTRP uses the “Date of registration”, ClinicalTrials.gov uses the study start date, and EU CTIS feeds use the date the trial was listed in the feed. Sources: WHO ICTRP, ClinicalTrials.gov, EU CTIS.",
			"date_registration": "The date the trial was registered with its registry, where the source provides one: from the WHO ICTRP “Date of registration” (mirrored into the headline date above) or the ClinicalTrials.gov “first submitted” date. Not available for EU CTIS trials. Sources: WHO ICTRP, ClinicalTrials.gov.",
			"date_enrollement": "Date the first participant was (or is expected to be) enrolled. Source: WHO ICTRP.",
			"last_refreshed_on": "Date the source registry last updated this record. Source: WHO ICTRP.",
			"export_date": "Date this record was exported from the WHO ICTRP database. Source: WHO ICTRP.",
			"source_register": "The registry this record came from (e.g. ClinicalTrials.gov, ChiCTR, EU-CTR). Sources: WHO ICTRP, ClinicalTrials.gov.",
			"other_records": "Whether the same trial is also registered in other registries. Source: WHO ICTRP.",
			"prospective_registration": "“Yes” means the trial was listed in a public registry before it enrolled any participants — the recommended practice. “No” means it was registered afterwards. Source: WHO ICTRP.",
			# Study details
			"study_type": "The kind of study — for example interventional (testing a treatment) or observational (only observing). Sources: WHO ICTRP, ClinicalTrials.gov.",
			"study_design": "How the study is structured — for example randomised, controlled, or single-group. Source: WHO ICTRP.",
			"phase": "The stage of testing (Phase 1–4). Early phases check safety in small groups; later phases test effectiveness in larger groups. “N/A” means not applicable. Sources: WHO ICTRP, ClinicalTrials.gov.",
			"recruitment_status": "Whether the trial is recruiting participants, not yet recruiting, completed, etc. Sources: WHO ICTRP, ClinicalTrials.gov.",
			"target_size": "The number of participants the trial aims to enrol. Sources: WHO ICTRP, ClinicalTrials.gov.",
			"countries": "Countries where the trial takes place. Sources: WHO ICTRP, ClinicalTrials.gov.",
			# Conditions & interventions
			"condition": "The disease or health condition being studied. Sources: WHO ICTRP, ClinicalTrials.gov, EU CTIS.",
			"intervention": "The treatment, drug, device, or procedure being tested or compared. Sources: WHO ICTRP, ClinicalTrials.gov.",
			"primary_outcome": "The main result the trial is designed to measure. Sources: WHO ICTRP, ClinicalTrials.gov, EU CTIS.",
			"secondary_outcome": "Additional results the trial measures beyond the main one. Sources: WHO ICTRP, ClinicalTrials.gov, EU CTIS.",
			# Eligibility
			"inclusion_criteria": "Requirements a person must meet to take part in the trial. Sources: WHO ICTRP, ClinicalTrials.gov.",
			"exclusion_criteria": "Conditions that prevent a person from taking part in the trial. Source: WHO ICTRP (ClinicalTrials.gov combines this into the inclusion criteria).",
			"inclusion_agemin": "Youngest age eligible to participate. Sources: WHO ICTRP, ClinicalTrials.gov.",
			"inclusion_agemax": "Oldest age eligible to participate. Sources: WHO ICTRP, ClinicalTrials.gov.",
			"inclusion_gender": "Which sexes / genders can take part (e.g. both, female, male). Sources: WHO ICTRP, ClinicalTrials.gov.",
			# Sponsors & contacts
			"primary_sponsor": "The lead organisation responsible for the trial. Sources: WHO ICTRP, ClinicalTrials.gov, EU CTIS.",
			"secondary_sponsor": "Additional organisations funding or running the trial, besides the main sponsor. May list several, separated by semicolons. Source: WHO ICTRP.",
			"source_support": "Organisations providing funding or material support for the trial. Source: WHO ICTRP.",
			"contact_firstname": "First name of the public contact person for the trial. Sources: WHO ICTRP, ClinicalTrials.gov.",
			"contact_lastname": "Last name of the public contact person for the trial. Sources: WHO ICTRP, ClinicalTrials.gov.",
			"contact_address": "Postal address of the trial’s contact person. Source: WHO ICTRP.",
			"contact_email": "Email address for enquiries about the trial. Sources: WHO ICTRP, ClinicalTrials.gov.",
			"contact_tel": "Phone number for enquiries about the trial. Sources: WHO ICTRP, ClinicalTrials.gov.",
			"contact_affiliation": "The organisation the contact person belongs to. Source: WHO ICTRP.",
			# Ethics review
			"ethics_review_status": "Whether the trial has been approved by an ethics committee / institutional review board. Source: WHO ICTRP.",
			"ethics_review_approval_date": "Date the ethics committee approved the trial. Source: WHO ICTRP.",
			"ethics_review_contact_name": "Name (or email) of the ethics committee contact. Source: WHO ICTRP.",
			"ethics_review_contact_address": "Postal address of the ethics committee. Source: WHO ICTRP.",
			"ethics_review_contact_phone": "Phone number of the ethics committee. Source: WHO ICTRP.",
			"ethics_review_contact_email": "Email address of the ethics committee. Source: WHO ICTRP.",
			# Results
			"results_posted": "Whether results have been posted/published for the trial. Sources: ClinicalTrials.gov (from the “has results” flag), EU CTIS (from the “Results posted” field).",
			"results_date_completed": "When the trial’s results became available. For WHO ICTRP this is the source “results completed” date; for ClinicalTrials.gov it is the date results were first posted. Sources: WHO ICTRP, ClinicalTrials.gov.",
			"results_url_link": "Link to where the trial’s results are published. Source: ClinicalTrials.gov.",
			"results_yes_no": "Whether the trial’s results have been published or posted. Source: WHO ICTRP.",
			"results_ipd_plan": "Whether the researchers plan to share de-identified data about each participant (IPD) with other researchers. Source: WHO ICTRP.",
			"results_ipd_description": "Free-text explanation of how and when the individual participant data will be shared. Source: WHO ICTRP.",
		}


class TrialAdmin(OrganizationFilterMixin, SourceBulkActionMixin, SimpleHistoryAdmin):
	form = TrialAdminForm
	source_for_values = ["trials"]
	actions = ["add_source_action", "remove_source_action"]
	list_display = [
		"trial_id",
		"title",
		"display_identifiers",
		"discovery_date",
		"last_updated",
	]
	exclude = ["ml_predictions"]
	readonly_fields = ["last_updated", "links"]
	inlines = [
		TrialOrgContentInline,
		TrialArticleReferenceInline,
		TrialCategoryAssignmentInline,
	]
	search_fields = [
		"trial_id",
		"title",
		"summary",
		"scientific_title",
		"primary_sponsor",
		"source_register",
		"recruitment_status",
		"condition",
		"intervention",
		"primary_outcome",
		"secondary_outcome",
		"inclusion_criteria",
		"exclusion_criteria",
		"study_type",
		"study_design",
		"phase",
		"countries",
		"contact_firstname",
		"contact_lastname",
		"contact_affiliation",
		"therapeutic_areas",
		"sponsor_type",
		"internal_number",
		"secondary_id",
		"identifiers",
		"ctg_detailed_description",
	]
	list_filter = [
		("teams", OrganizationRestrictedFieldListFilter),
		("subjects", OrganizationRestrictedFieldListFilter),
		("sources", OrganizationRestrictedFieldListFilter),
	]
	fieldsets = (
		(
			None,
			{
				"fields": (
					"title",
					"acronym",
					"scientific_title",
					"link",
					"links",
					"identifiers",
					"discovery_date",
					"published_date",
					"last_updated",
				)
			},
		),
		(
			"Description",
			{
				"fields": ("summary", "ctg_detailed_description"),
				"classes": ("collapse",),
			},
		),
		(
			"Study Details",
			{
				"fields": (
					"study_type",
					"study_design",
					"phase",
					"recruitment_status",
					"target_size",
					"date_enrollement",
					"date_registration",
					"prospective_registration",
				),
				"classes": ("collapse",),
			},
		),
		(
			"Conditions & Interventions",
			{
				"fields": (
					"condition",
					"intervention",
					"primary_outcome",
					"secondary_outcome",
				),
				"classes": ("collapse",),
			},
		),
		(
			"Eligibility",
			{
				"fields": (
					"inclusion_criteria",
					"exclusion_criteria",
					"inclusion_agemin",
					"inclusion_agemax",
					"inclusion_gender",
				),
				"classes": ("collapse",),
			},
		),
		(
			"Sponsors & Contacts",
			{
				"fields": (
					"primary_sponsor",
					"secondary_sponsor",
					"source_support",
					"sponsor_type",
					"contact_firstname",
					"contact_lastname",
					"contact_address",
					"contact_email",
					"contact_tel",
					"contact_affiliation",
				),
				"classes": ("collapse",),
			},
		),
		(
			"Location & Registry",
			{
				"fields": (
					"countries",
					"source_register",
					"secondary_id",
					"internal_number",
					"other_records",
					"last_refreshed_on",
					"export_date",
				),
				"classes": ("collapse",),
			},
		),
		(
			"Relationships",
			{
				"fields": ("sources", "teams", "subjects"),
			},
		),
		(
			"EU Clinical Trials",
			{
				"fields": (
					"therapeutic_areas",
					"country_status",
					"trial_region",
					"overall_decision_date",
					"countries_decision_date",
				),
				"classes": ("collapse",),
			},
		),
		(
			"Ethics Review",
			{
				"fields": (
					"ethics_review_status",
					"ethics_review_approval_date",
					"ethics_review_contact_name",
					"ethics_review_contact_address",
					"ethics_review_contact_phone",
					"ethics_review_contact_email",
				),
				"classes": ("collapse",),
			},
		),
		(
			"Results",
			{
				"fields": (
					"results_posted",
					"results_date_completed",
					"results_url_link",
					"results_yes_no",
					"results_ipd_plan",
					"results_ipd_description",
				),
				"classes": ("collapse",),
			},
		),
	)

	def display_identifiers(self, obj):
		# Customize this depending on how you want to display the JSON
		if obj.identifiers:
			return ", ".join([f"{k}: {v}" for k, v in obj.identifiers.items()])
		return "No Identifiers"

	display_identifiers.short_description = "Identifiers"


class SourceInline(admin.StackedInline):
	model = Sources
	extra = 1


class SourceAdminForm(forms.ModelForm):
	"""Custom form for Source admin with organization-based field access"""

	class Meta:
		model = Sources
		fields = "__all__"

	def __init__(self, *args, **kwargs):
		self.request = kwargs.pop("request", None)
		super().__init__(*args, **kwargs)

		# Filter team and subject choices based on user's organization
		if self.request:
			if self.request.user.is_superuser:
				self.fields["team"].queryset = Team.objects.all()
				self.fields["subject"].queryset = Subject.objects.all()
			else:
				user_orgs = get_user_organizations(self.request.user)
				self.fields["team"].queryset = Team.objects.filter(
					organization__id__in=user_orgs
				)
				self.fields["subject"].queryset = Subject.objects.filter(
					team__organization__id__in=user_orgs
				)


class ReassignToTeamForm(forms.Form):
	"""Simple form for granular per-object 'reassign to team' actions."""

	target_team = forms.ModelChoiceField(
		queryset=Team.objects.none(),
		label="Target team",
		help_text="All selected objects will be moved to this team.",
	)


class ReassignToTeamMixin:
	"""
	Admin mixin that adds a 'Reassign to team' bulk action.

	Subclasses must ensure their model has a ``team`` ForeignKey field.
	"""

	def _get_reassign_template(self):
		return "admin/gregory/reassign_to_team_intermediate.html"

	@admin.action(description="Reassign selected to another team…")
	def reassign_to_team_action(self, request, queryset):
		# Safety: all selected objects must belong to the same organisation.
		team_ids = queryset.values_list("team_id", flat=True).distinct()
		org_ids = list(
			Team.all_objects.filter(pk__in=team_ids)
			.values_list("organization_id", flat=True)
			.distinct()
		)
		if len(org_ids) != 1:
			self.message_user(
				request,
				"Selected objects span multiple organisations. "
				"Please select only objects from a single organisation.",
				level=messages.ERROR,
			)
			return

		target_qs = Team.objects.filter(organization_id=org_ids[0])

		if "apply" not in request.POST:
			form = ReassignToTeamForm()
			form.fields["target_team"].queryset = target_qs
			return render(
				request,
				self._get_reassign_template(),
				{
					"title": "Reassign to team",
					"objects": queryset,
					"form": form,
					"action_checkbox_name": admin.helpers.ACTION_CHECKBOX_NAME,
					"model_name": queryset.model._meta.verbose_name_plural,
				},
			)

		form = ReassignToTeamForm(request.POST)
		form.fields["target_team"].queryset = target_qs

		if not form.is_valid():
			self.message_user(
				request, "Invalid form — please try again.", level=messages.ERROR
			)
			return

		to_team = form.cleaned_data["target_team"]
		# Final guard: target team must belong to the same organisation.
		if to_team.organization_id != org_ids[0]:
			self.message_user(
				request,
				"Target team does not belong to the same organisation as the selected objects.",
				level=messages.ERROR,
			)
			return

		count = queryset.count()
		queryset.update(team=to_team)
		self.message_user(request, f"{count} object(s) reassigned to '{to_team}'.")


class SourceAdmin(OrganizationFilterMixin, ReassignToTeamMixin, admin.ModelAdmin):
	form = SourceAdminForm
	list_display = [
		"name",
		"active",
		"source_for",
		"subject",
		"last_article_date",
		"article_count",
		"health_status_indicator",
	]
	list_filter = [
		"active",
		"source_for",
		"method",
		SourceHealthFilter,
		SourceOrganizationFilter,
		("team", OrganizationRestrictedFieldListFilter),
		("subject", OrganizationRestrictedFieldListFilter),
	]
	search_fields = ["name", "link", "description", "keyword_filter"]
	actions = ["activate_sources", "deactivate_sources", "reassign_to_team_action"]
	fieldsets = (
		(
			"Basic Information",
			{
				"fields": (
					"name",
					"source_for",
					"method",
					"active",
					"link",
					"ignore_ssl",
					"description",
				)
			},
		),
		("Organization", {"fields": ("team", "subject")}),
		(
			"Filtering (bioRxiv and medRxiv)",
			{
				"fields": ("keyword_filter",),
				"classes": ("keyword-filter-settings",),
				"description": 'For bioRxiv and medRxiv sources, specify keywords to filter articles. Use comma-separated values for multiple keywords, or quoted strings for exact phrases (e.g., "multiple sclerosis", alzheimer, parkinson).',
			},
		),
		(
			"ClinicalTrials.gov API Settings",
			{
				"fields": ("ctgov_search_condition",),
				"classes": ("ctgov-settings",),
				"description": "Settings for ClinicalTrials.gov API sources. Enter the condition/disease to search for clinical trials.",
			},
		),
	)

	class Media:
		js = ("admin/js/source_for_toggle.js",)

	def get_form(self, request, obj=None, **kwargs):
		"""Pass the request to the form so it can filter field choices."""
		form_class = super().get_form(request, obj, **kwargs)

		class FormWithRequest(form_class):
			def __init__(self, *args, **form_kwargs):
				form_kwargs["request"] = request
				super().__init__(*args, **form_kwargs)

		return FormWithRequest

	def save_model(self, request, obj, form, change):
		"""Warn admin if source has no team assigned."""
		super().save_model(request, obj, form, change)
		if not obj.team:
			messages.warning(
				request,
				f"Source '{obj.name}' has no team assigned. "
				f"Feedreaders will skip team association for content from this source.",
			)

	def last_article_date(self, obj):
		"""Display the date of the latest article or trial from this source."""
		if obj.source_for == "trials":
			latest_date = obj.get_latest_trial_date()
			if latest_date:
				days_since = (timezone.now() - latest_date).days
				return f"{latest_date.strftime('%Y-%m-%d')} ({days_since} days ago)"
			return "No trials"
		else:
			latest_date = obj.get_latest_article_date()
			if latest_date:
				days_since = (timezone.now() - latest_date).days
				return f"{latest_date.strftime('%Y-%m-%d')} ({days_since} days ago)"
			return "No articles"

	last_article_date.short_description = "Last Content"
	last_article_date.short_description = "Last Article"

	def article_count(self, obj):
		"""Display the count of articles or trials from this source."""
		if obj.source_for == "trials":
			return obj.get_trial_count()
		else:
			return obj.get_article_count()

	article_count.short_description = "Content Count"

	def health_status_indicator(self, obj):
		"""Display a visual indicator of the source's health status."""
		status = obj.get_health_status()

		if status == "healthy":
			return format_html(
				'<span style="color: green; font-size: 14px;">{}</span>', "●"
			)
		elif status == "warning":
			return format_html(
				'<span style="color: orange; font-size: 14px;">{}</span>', "●"
			)
		elif status == "error":
			return format_html(
				'<span style="color: red; font-size: 14px;">{}</span>', "●"
			)
		elif status == "inactive":
			return format_html(
				'<span style="color: gray; font-size: 14px;">{}</span>', "●"
			)
		else:  # no_content
			return format_html(
				'<span style="color: blue; font-size: 14px;">{}</span>', "●"
			)

	health_status_indicator.short_description = "Status"

	def activate_sources(self, request, queryset):
		"""Admin action to activate selected sources."""
		updated_count = queryset.update(active=True)
		self.message_user(request, f"Successfully activated {updated_count} source(s).")

	activate_sources.short_description = "Activate selected sources"

	def deactivate_sources(self, request, queryset):
		"""Admin action to deactivate selected sources."""
		updated_count = queryset.update(active=False)
		self.message_user(
			request, f"Successfully deactivated {updated_count} source(s)."
		)

	deactivate_sources.short_description = "Deactivate selected sources"

	def get_urls(self):
		from django.urls import path
		from .admin_views import (
			source_detail_view,
			source_activity_json,
			sources_overview_view,
		)

		urls = super().get_urls()
		custom_urls = [
			path(
				"overview/",
				self.admin_site.admin_view(sources_overview_view),
				name="sources_overview",
			),
			path(
				"<int:source_id>/detail/",
				self.admin_site.admin_view(source_detail_view),
				name="sources_detail",
			),
			path(
				"<int:source_id>/detail/activity.json/",
				self.admin_site.admin_view(source_activity_json),
				name="sources_activity_json",
			),
		]
		return custom_urls + urls

	def changelist_view(self, request, extra_context=None):
		extra_context = extra_context or {}
		extra_context["sources_overview_url"] = reverse("admin:sources_overview")
		return super().changelist_view(request, extra_context=extra_context)


class SourcesInline(admin.StackedInline):
	"""Inline admin for managing sources within a subject"""

	model = Sources
	extra = 1  # Show 1 empty form by default for adding new sources
	fields = [
		"name",
		"link",
		"source_for",
		"method",
		"active",
		"keyword_filter",
		"description",
	]
	verbose_name = "New Source"
	verbose_name_plural = "Add New Source"

	def get_queryset(self, request):
		"""Return empty queryset to hide existing sources from inline forms"""
		return super().get_queryset(request).none()

	def save_formset(self, request, form, formset, change):
		"""Automatically set subject and team when saving sources"""
		instances = formset.save(commit=False)
		for instance in instances:
			# Auto-populate subject and team from the parent Subject object
			instance.subject = form.instance
			instance.team = form.instance.team
			instance.save()
		formset.save_m2m()


class SubjectAdminForm(forms.ModelForm):
	"""Custom form for Subject admin with organization-based team access"""

	class Meta:
		model = Subject
		fields = "__all__"

	def __init__(self, *args, **kwargs):
		# Get the request from kwargs if passed
		self.request = kwargs.pop("request", None)
		super().__init__(*args, **kwargs)

		# Filter teams based on user's organization
		if self.request:
			if self.request.user.is_superuser:
				self.fields["team"].queryset = Team.objects.all()
			else:
				user_orgs = get_user_organizations(self.request.user)
				self.fields["team"].queryset = Team.objects.filter(
					organization__id__in=user_orgs
				)


@admin.register(Subject)
class SubjectAdmin(OrganizationFilterMixin, ReassignToTeamMixin, admin.ModelAdmin):
	list_display = [
		"formatted_subject_name",
		"description",
		"article_count",
		"trial_count",
		"view_sources",
		"team",
	]  # Updated list display
	readonly_fields = ["linked_sources"]  # Display in the edit form
	list_filter = [
		("team", OrganizationRestrictedFieldListFilter)
	]  # Add the team filter
	form = SubjectAdminForm
	inlines = [SourcesInline]  # Add the inline for managing sources
	actions = ["reassign_to_team_action"]

	def get_queryset(self, request):
		qs = super().get_queryset(request)
		article_subq = (
			Articles.objects.filter(subjects=OuterRef("pk"))
			.values("subjects")
			.annotate(c=models.Count("pk"))
			.values("c")
		)
		trial_subq = (
			Trials.objects.filter(subjects=OuterRef("pk"))
			.values("subjects")
			.annotate(c=models.Count("pk"))
			.values("c")
		)
		return qs.prefetch_related("sources_set").annotate(
			article_count=Subquery(article_subq),
			trial_count=Subquery(trial_subq),
		)

	def article_count(self, obj):
		return obj.article_count

	article_count.short_description = "Articles"
	article_count.admin_order_field = "article_count"

	def trial_count(self, obj):
		return obj.trial_count

	trial_count.short_description = "Trials"
	trial_count.admin_order_field = "trial_count"

	def formatted_subject_name(self, obj):
		"""Display subject name with emphasis"""
		return format_html("<strong>{}</strong>", obj.subject_name)

	formatted_subject_name.short_description = "Subject"
	formatted_subject_name.admin_order_field = "subject_name"

	def get_form(self, request, obj=None, **kwargs):
		"""Pass the request to the form so it can check user permissions"""
		form_class = super().get_form(request, obj, **kwargs)

		# Create a wrapper that passes the request to the form
		class FormWithRequest(form_class):
			def __init__(self, *args, **form_kwargs):
				form_kwargs["request"] = request
				super().__init__(*args, **form_kwargs)

		return FormWithRequest

	def view_sources(self, obj):
		"""Display sources as clickable links in the list view."""
		sources = obj.sources_set.all()
		if sources.exists():
			links = [
				format_html(
					'<a href="{}">{}</a>',
					reverse("admin:gregory_sources_change", args=[source.source_id]),
					source.name,
				)
				for source in sources
			]
			return mark_safe("<br>".join(links))
		return "No sources"

	view_sources.short_description = "Linked Sources"

	def linked_sources(self, obj):
		"""Display sources as clickable links in the form view."""
		sources = obj.sources_set.all()
		if sources.exists():
			links = [
				format_html(
					'<a href="{}">{}</a>',
					reverse("admin:gregory_sources_change", args=[source.source_id]),
					source.name,
				)
				for source in sources
			]
			return mark_safe("<br>".join(links))
		return "No sources"

	linked_sources.short_description = "Linked Sources"

	def delete_view(self, request, object_id, extra_context=None):
		from django.contrib.admin.utils import unquote

		obj = self.get_object(request, unquote(object_id))

		if obj:
			can_view_sources = request.user.has_perm("gregory.view_sources")
			can_delete_sources = request.user.has_perm("gregory.delete_sources")

			orphaned_sources = Sources.objects.filter(subject=obj)

			if orphaned_sources.exists() and request.method == "POST":
				if request.POST.get("delete_orphaned_sources") == "yes":
					if can_delete_sources:
						orphaned_sources.delete()
					else:
						self.message_user(
							request,
							"You do not have permission to delete sources. The subject was deleted but the sources were kept.",
							level=messages.WARNING,
						)

			extra_context = extra_context or {}
			if orphaned_sources.exists() and can_view_sources:
				extra_context["orphaned_sources"] = orphaned_sources
				extra_context["can_delete_sources"] = can_delete_sources

		return super().delete_view(request, object_id, extra_context=extra_context)

	def get_urls(self):
		from django.urls import path
		from .admin_views import (
			subject_analytics_view,
			subject_analytics_data,
			subject_analytics_orgs,
			subject_analytics_teams,
			subject_analytics_subjects,
		)

		urls = super().get_urls()
		custom_urls = [
			path(
				"analytics/",
				self.admin_site.admin_view(subject_analytics_view),
				name="gregory_subject_analytics",
			),
			path(
				"analytics/data/",
				self.admin_site.admin_view(subject_analytics_data),
				name="gregory_subject_analytics_data",
			),
			path(
				"analytics/orgs/",
				self.admin_site.admin_view(subject_analytics_orgs),
				name="gregory_subject_analytics_orgs",
			),
			path(
				"analytics/teams/",
				self.admin_site.admin_view(subject_analytics_teams),
				name="gregory_subject_analytics_teams",
			),
			path(
				"analytics/subjects/",
				self.admin_site.admin_view(subject_analytics_subjects),
				name="gregory_subject_analytics_subjects",
			),
		]
		return custom_urls + urls

	def changelist_view(self, request, extra_context=None):
		extra_context = extra_context or {}
		extra_context["subject_analytics_url"] = reverse(
			"admin:gregory_subject_analytics"
		)
		return super().changelist_view(request, extra_context=extra_context)


class AuthorArticlesInline(admin.TabularInline):
	model = Articles.authors.through
	verbose_name = "Article"
	verbose_name_plural = "Author's Articles"
	extra = 0
	can_delete = False
	fields = ["article_info", "article_summary", "article_doi_link", "admin_link"]
	readonly_fields = [
		"article_info",
		"article_summary",
		"article_doi_link",
		"admin_link",
	]
	ordering = ["-articles__published_date"]  # Order by most recent articles first

	def article_info(self, obj):
		try:
			article = obj.articles
			title = article.title
			published_date = article.published_date
			if published_date:
				return format_html(
					'{}<br/><span style="color: #666; font-size: 0.8em;">Published: {}</span>',
					title,
					published_date.strftime("%Y-%m-%d"),
				)
			return title
		except Exception as e:
			return f"Error accessing article: {str(e)}"

	article_info.short_description = "Title"

	def article_summary(self, obj):
		try:
			article = obj.articles
			summary = article.summary
			if summary:
				return summary[:200] + "..." if len(summary) > 200 else summary
			return "-"
		except Exception as e:
			return f"Error accessing article summary: {str(e)}"

	article_summary.short_description = "Summary"

	def article_doi_link(self, obj):
		try:
			article = obj.articles
			if article.doi:
				doi_link = f"https://doi.org/{article.doi}"
				return format_html(
					'<a href="{}" target="_blank">{}</a>', doi_link, article.doi
				)
			elif article.link:
				return format_html(
					'<a href="{}" target="_blank">Link to article</a>', article.link
				)
			return "-"
		except Exception as e:
			return f"Error accessing article link: {str(e)}"

	article_doi_link.short_description = "External Link"

	def admin_link(self, obj):
		try:
			article = obj.articles
			url = reverse("admin:gregory_articles_change", args=[article.pk])
			return format_html('<a href="{}" target="_blank">View Article</a>', url)
		except Exception as e:
			return f"Error generating admin link: {str(e)}"

	admin_link.short_description = "Admin Link"

	def has_add_permission(self, request, obj=None):
		return False


class ArticleCountFilter(admin.SimpleListFilter):
	title = "Number of Articles"
	parameter_name = "article_count"

	def lookups(self, request, model_admin):
		return (
			("0", "No articles"),
			("1-5", "1 to 5 articles"),
			("6-10", "6 to 10 articles"),
			("11+", "More than 10 articles"),
		)

	def queryset(self, request, queryset):
		if self.value() == "0":
			# Authors with no articles
			return queryset.annotate(count=models.Count("articles_set")).filter(count=0)
		elif self.value() == "1-5":
			# Authors with 1-5 articles
			return queryset.annotate(count=models.Count("articles_set")).filter(
				count__gte=1, count__lte=5
			)
		elif self.value() == "6-10":
			# Authors with 6-10 articles
			return queryset.annotate(count=models.Count("articles_set")).filter(
				count__gte=6, count__lte=10
			)
		elif self.value() == "11+":
			# Authors with more than 10 articles
			return queryset.annotate(count=models.Count("articles_set")).filter(
				count__gt=10
			)
		return queryset


class AuthorsAdmin(admin.ModelAdmin):
	search_fields = ["family_name", "given_name", "ORCID"]
	list_display = [
		"given_name",
		"family_name",
		"display_orcid",
		"country",
		"article_count",
	]
	list_filter = ["country", ArticleCountFilter]
	inlines = [AuthorArticlesInline]
	readonly_fields = ["biography", "recheck_orcid_button"]

	def display_orcid(self, obj):
		if obj.ORCID:
			orcid_url = f"https://orcid.org/{obj.ORCID}"
			return format_html(
				'<a href="{}" target="_blank">{}</a>', orcid_url, obj.ORCID
			)
		return "-"

	display_orcid.short_description = "ORCID"
	display_orcid.admin_order_field = "ORCID"

	def article_count(self, obj):
		return obj.articles_count

	article_count.short_description = "Number of Articles"
	article_count.admin_order_field = "articles_count"

	def recheck_orcid_button(self, obj):
		if not obj.pk:
			return "-"
		if not obj.ORCID:
			return "Author has no ORCID iD."
		url = reverse("admin:gregory_authors_recheck_orcid", args=[obj.pk])
		return format_html(
			'<a class="button" href="{}">Recheck ORCID now</a>', url
		)

	recheck_orcid_button.short_description = "Refresh from ORCID"

	def get_queryset(self, request):
		queryset = super().get_queryset(request)
		queryset = queryset.annotate(articles_count=models.Count("articles"))
		return queryset

	def get_inline_instances(self, request, obj=None):
		if not obj:  # If we're adding a new object, don't display inlines
			return []
		return super().get_inline_instances(request, obj)

	def get_urls(self):
		custom = [
			path(
				"<int:author_id>/recheck-orcid/",
				self.admin_site.admin_view(self.recheck_orcid_view),
				name="gregory_authors_recheck_orcid",
			),
		]
		return custom + super().get_urls()

	def recheck_orcid_view(self, request, author_id):
		"""Refresh country/biography for a single author from the ORCID public API.

		GET renders a confirmation page; the actual API call and save only
		happen on POST, since this is a state-changing action.
		"""
		author = self.get_object(request, author_id)
		if author is None:
			self.message_user(request, "Author not found.", level=messages.ERROR)
			return redirect("admin:gregory_authors_changelist")

		if not self.has_change_permission(request, author):
			raise PermissionDenied

		change_url = reverse("admin:gregory_authors_change", args=[author_id])

		if request.method != "POST":
			return render(
				request,
				"admin/gregory/authors/recheck_orcid_confirmation.html",
				{
					"title": f"Recheck ORCID for {author}",
					"author": author,
					"opts": self.model._meta,
				},
			)

		import orcid
		import requests
		from gregory.functions import normalize_orcid
		from gregory.services.orcid_sync import apply_orcid_record_to_author
		from subscriptions.management.commands.utils.get_credentials import (
			get_orcid_credentials,
		)

		if not author.ORCID:
			self.message_user(
				request, "This author has no ORCID iD.", level=messages.ERROR
			)
			return redirect(change_url)

		author_orcid_number = normalize_orcid(author.ORCID)
		if not author_orcid_number:
			self.message_user(
				request,
				"This author has an invalid ORCID iD value.",
				level=messages.ERROR,
			)
			return redirect(change_url)

		org = (
			Organization.objects.filter(teams__articles__authors=author)
			.distinct()
			.first()
		)
		if org is None:
			self.message_user(
				request,
				"Could not determine an organisation for this author (no articles assigned to a team).",
				level=messages.ERROR,
			)
			return redirect(change_url)

		orcid_key, orcid_secret = get_orcid_credentials(organization=org)
		if not orcid_key or not orcid_secret:
			self.message_user(
				request,
				f"ORCID credentials are not configured for organisation '{org}'.",
				level=messages.ERROR,
			)
			return redirect(change_url)

		try:
			orcid_api = orcid.PublicAPI(orcid_key, orcid_secret, sandbox=False)
			token = orcid_api.get_search_token_from_orcid()
			record = orcid_api.read_record_public(author_orcid_number, "record", token)
		except requests.exceptions.HTTPError as e:
			self.message_user(
				request,
				f"Failed to refresh data from ORCID: {e}",
				level=messages.ERROR,
			)
			return redirect(change_url)

		result = apply_orcid_record_to_author(
			author, record, change_reason_suffix="(manual recheck)"
		)
		if result.changed_fields:
			self.message_user(
				request, f"Refreshed {' and '.join(result.changed_fields)} from ORCID."
			)
		else:
			self.message_user(request, "No new data found on ORCID.")

		return redirect(change_url)


# Maps each stored weight (content type → field) to the admin form field that
# edits it. Keeps the JSON ``match_weights`` column out of the form in favour of
# friendly per-field integer inputs.
TEAMCATEGORY_WEIGHT_FIELDS = {
	"article": {
		"title": "weight_article_title",
		"summary": "weight_article_summary",
	},
	"trial": {
		"title": "weight_trial_title",
		"summary": "weight_trial_summary",
		"scientific_title": "weight_trial_scientific_title",
		"intervention": "weight_trial_intervention",
		"primary_outcome": "weight_trial_primary_outcome",
		"secondary_outcome": "weight_trial_secondary_outcome",
		"therapeutic_areas": "weight_trial_therapeutic_areas",
	},
}


class TeamCategoryAdminForm(forms.ModelForm):
	"""Edits TeamCategory matching settings, exposing the per-field weights stored
	in the ``match_weights`` JSON column as friendly integer inputs."""

	weight_article_title = forms.IntegerField(min_value=0, label="Title")
	weight_article_summary = forms.IntegerField(min_value=0, label="Summary")
	weight_trial_title = forms.IntegerField(min_value=0, label="Title")
	weight_trial_summary = forms.IntegerField(min_value=0, label="Summary")
	weight_trial_scientific_title = forms.IntegerField(
		min_value=0, label="Scientific title"
	)
	weight_trial_intervention = forms.IntegerField(min_value=0, label="Intervention")
	weight_trial_primary_outcome = forms.IntegerField(
		min_value=0, label="Primary outcome"
	)
	weight_trial_secondary_outcome = forms.IntegerField(
		min_value=0, label="Secondary outcome"
	)
	weight_trial_therapeutic_areas = forms.IntegerField(
		min_value=0, label="Therapeutic areas"
	)

	class Meta:
		model = TeamCategory
		exclude = ["match_weights"]

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		if self.instance and self.instance.pk:
			weights = {
				content_type: self.instance.get_match_weights(content_type)
				for content_type in TEAMCATEGORY_WEIGHT_FIELDS
			}
		else:
			weights = default_match_weights()
		for content_type, fmap in TEAMCATEGORY_WEIGHT_FIELDS.items():
			ct_weights = weights.get(content_type, {})
			for field_key, form_field in fmap.items():
				self.fields[form_field].initial = ct_weights.get(field_key, 0)

	def save(self, commit=True):
		instance = super().save(commit=False)
		instance.match_weights = {
			content_type: {
				field_key: self.cleaned_data[form_field]
				for field_key, form_field in fmap.items()
			}
			for content_type, fmap in TEAMCATEGORY_WEIGHT_FIELDS.items()
		}
		if commit:
			instance.save()
			self.save_m2m()
		return instance


@admin.register(TeamCategory)
class TeamCategoryAdmin(OrganizationFilterMixin, ReassignToTeamMixin, admin.ModelAdmin):
	form = TeamCategoryAdminForm
	list_display = (
		"category_name",
		"team",
		"category_type",
		"display_match_scope",
		"article_count",
		"display_subjects",
	)
	search_fields = ("category_name", "team__name", "subjects__subject_name")
	list_filter = [
		"category_type",
		"match_scope",
		("team", OrganizationRestrictedFieldListFilter),
		("subjects", OrganizationRestrictedFieldListFilter),
	]
	filter_horizontal = ("subjects",)
	actions = ["reassign_to_team_action"]
	fieldsets = (
		(
			None,
			{
				"fields": (
					"team",
					"category_name",
					"category_slug",
					"category_description",
					"subjects",
					"category_type",
				)
			},
		),
		(
			"Matching",
			{
				"fields": (
					"category_terms",
					"match_scope",
					"match_min_score_articles",
					"match_min_score_trials",
				),
				"description": (
					"Automatic categories match content whose in-scope fields contain these "
					"terms and whose score reaches the minimum. A fixed bonus of 2 points per "
					"unique matched term is added on top of the field weights below. In "
					"'Title only' scope every field except the title is ignored. "
					"Articles are scored on up to 2 fields; trials on up to 7, so separate "
					"thresholds let you tune both independently."
				),
			},
		),
		(
			"Article score weights",
			{"fields": ("weight_article_title", "weight_article_summary")},
		),
		(
			"Trial score weights",
			{
				"classes": ("collapse",),
				"fields": (
					"weight_trial_title",
					"weight_trial_summary",
					"weight_trial_scientific_title",
					"weight_trial_intervention",
					"weight_trial_primary_outcome",
					"weight_trial_secondary_outcome",
					"weight_trial_therapeutic_areas",
				),
			},
		),
	)

	# Form fields that affect which articles/trials match the category
	MATCHING_CONFIG_FIELDS = {
		"category_terms",
		"subjects",
		"category_type",
		"match_scope",
		"match_min_score_articles",
		"match_min_score_trials",
		"weight_article_title",
		"weight_article_summary",
		"weight_trial_title",
		"weight_trial_summary",
		"weight_trial_scientific_title",
		"weight_trial_intervention",
		"weight_trial_primary_outcome",
		"weight_trial_secondary_outcome",
		"weight_trial_therapeutic_areas",
	}

	def save_related(self, request, form, formsets, change):
		"""Re-match an automatic category as soon as its configuration is saved.

		Runs after the subjects M2M is saved, so the matcher sees the full
		configuration. New automatic categories are backfilled immediately;
		edited ones are re-matched only when a field that affects matching
		changed. Manual categories are never touched.
		"""
		super().save_related(request, form, formsets, change)
		category = form.instance
		if category.category_type != CategoryType.AUTOMATIC:
			return
		if change and not self.MATCHING_CONFIG_FIELDS & set(form.changed_data):
			return
		verb = "Re-matched" if change else "Backfilled"
		try:
			call_command("rebuild_categories", category=category.pk, stdout=StringIO())
			self.message_user(
				request,
				f"{verb} '{category.category_name}': {category.articles.count()} articles "
				f"and {category.trials.count()} trials assigned.",
				messages.SUCCESS,
			)
		except Exception as exc:
			self.message_user(
				request,
				f"Could not re-match '{category.category_name}' now ({exc}); "
				"the pipeline will categorize it on its next run.",
				messages.WARNING,
			)

	def get_queryset(self, request):
		"""Add prefetch_related to avoid multiple DB queries"""
		return super().get_queryset(request).prefetch_related("subjects", "articles")

	def article_count(self, obj):
		"""Display number of articles in this category"""
		return obj.articles.count()

	article_count.short_description = "Articles"

	@admin.display(description="Match scope", ordering="match_scope")
	def display_match_scope(self, obj):
		"""Human-readable label for the category's match scope."""
		return obj.get_match_scope_display()

	def display_subjects(self, obj):
		"""Display subjects as a comma-separated list"""
		subjects = list(obj.subjects.all())
		if len(subjects) == 0:
			return "-"
		elif len(subjects) <= 3:
			return ", ".join(str(subject) for subject in subjects)
		else:
			return f"{', '.join(str(subject) for subject in subjects[:3])} (+{len(subjects) - 3})"

	display_subjects.short_description = "Subjects"


class TeamSubjectInline(admin.TabularInline):
	model = Subject
	extra = 0
	fields = ("subject_name", "subject_slug", "auto_predict", "ml_consensus_type")
	readonly_fields = (
		"subject_name",
		"subject_slug",
		"auto_predict",
		"ml_consensus_type",
	)
	show_change_link = True
	verbose_name = "Subject"
	verbose_name_plural = "Subjects"
	classes = ("collapse",)
	can_delete = False

	def has_add_permission(self, request, obj=None):
		return False


class OrganizationSiteInline(admin.TabularInline):
	"""Inline to manage sites associated with an organization."""

	model = OrganizationSite
	extra = 1
	fields = ("site", "is_default")
	verbose_name = "Site"
	verbose_name_plural = "Sites"


class OrganizationCredentialsInline(admin.StackedInline):
	"""Inline to manage Postmark/ORCID credentials for an organization."""

	model = OrganizationCredentials
	extra = 0
	max_num = 1
	fields = (
		"postmark_api_token",
		"postmark_api_url",
		"orcid_client_id",
		"orcid_client_secret",
	)
	verbose_name = "Credentials"
	verbose_name_plural = "Credentials"


class OrganizationApiSettingsInline(admin.StackedInline):
	"""Inline to manage API visibility settings for an organization."""

	model = OrganizationApiSettings
	extra = 0
	max_num = 1
	fields = ("make_api_public",)
	verbose_name = "API settings"
	verbose_name_plural = "API settings"
	can_delete = False

	def has_add_permission(self, request, obj=None):
		return False


class OrganizationTeamInline(admin.TabularInline):
	"""Inline to display teams belonging to an organization."""

	model = Team
	extra = 0
	fields = ("name", "slug")
	readonly_fields = ("name", "slug")
	show_change_link = True
	verbose_name = "Team"
	verbose_name_plural = "Teams"
	can_delete = False

	def has_add_permission(self, request, obj=None):
		return False


admin.site.unregister(Organization)


@admin.register(Organization)
class OrganizationAdmin(BaseOrganizationAdmin):
	"""Custom Organization admin that shows associated teams."""

	list_display = ["name", "slug", "teams_count"]
	readonly_fields = ("lists_display",)

	def get_fieldsets(self, request, obj=None):
		fieldsets = list(super().get_fieldsets(request, obj))
		if obj is not None:
			fieldsets.append(("Lists", {"fields": ("lists_display",)}))
		return fieldsets

	def get_inline_instances(self, request, obj=None):
		inlines = super().get_inline_instances(request, obj)
		if obj is not None:
			inlines.append(OrganizationTeamInline(self.model, self.admin_site))
			inlines.append(OrganizationSiteInline(self.model, self.admin_site))
			inlines.append(OrganizationCredentialsInline(self.model, self.admin_site))
			inlines.append(OrganizationApiSettingsInline(self.model, self.admin_site))
		return inlines

	def teams_count(self, obj):
		return obj.teams.count()

	teams_count.short_description = "Teams"

	def lists_display(self, obj):
		from subscriptions.models import Lists

		lists = list(
			Lists.objects.filter(team__organization=obj)
			.select_related("team")
			.order_by("team__name", "list_name")
		)
		if not lists:
			return "-"
		from django.utils.html import format_html_join

		items = format_html_join(
			"",
			'<li><a href="{}">{}</a> <span style="color:#666;font-size:0.9em">({})</span></li>',
			(
				(
					reverse("admin:subscriptions_lists_change", args=[lst.pk]),
					lst.list_name,
					lst.team.name,
				)
				for lst in lists
			),
		)
		return format_html('<ul style="margin:0;padding-left:1.2em">{}</ul>', items)

	lists_display.short_description = "Lists"


class TeamSourceInline(admin.TabularInline):
	model = Sources
	extra = 0
	fields = ("name", "source_for", "method", "subject", "active")
	readonly_fields = ("name", "source_for", "method", "subject", "active")
	show_change_link = True
	verbose_name = "Source"
	verbose_name_plural = "Sources"
	classes = ("collapse",)
	can_delete = False

	def has_add_permission(self, request, obj=None):
		return False


class TeamCategoryInline(admin.TabularInline):
	model = TeamCategory
	extra = 0
	fields = ("category_name", "category_slug", "category_description")
	readonly_fields = ("category_name", "category_slug", "category_description")
	show_change_link = True
	verbose_name = "Category"
	verbose_name_plural = "Categories"
	classes = ("collapse",)
	can_delete = False

	def has_add_permission(self, request, obj=None):
		return False


class TeamAdminForm(forms.ModelForm):
	"""Custom form for Team admin that allows creating organization and team together"""

	team_name = forms.CharField(
		max_length=200,
		required=False,
		help_text="Enter team name, or select an existing organization below.",
	)

	class Meta:
		model = Team
		fields = [
			"organization",
			"slug",
		]  # Exclude 'name' since we use 'team_name' instead

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)

		# If editing an existing team, populate the team_name field with the actual team name
		if self.instance and self.instance.pk:
			self.fields["team_name"].initial = self.instance.name
			# For existing teams, team_name updates the actual team name
			self.fields["team_name"].help_text = "Team name within the organization."

		# Make organization field optional for new teams
		if not self.instance.pk:
			self.fields["organization"].required = False
			self.fields[
				"organization"
			].help_text = "Select an existing organization, or leave blank to create a new one with the team name above."

	def clean(self):
		cleaned_data = super().clean()
		team_name = cleaned_data.get("team_name")
		organization = cleaned_data.get("organization")

		# For new teams, require either team_name or organization
		if not self.instance.pk:
			if not team_name and not organization:
				raise forms.ValidationError(
					"Please provide either a team name or select an existing organization."
				)
		else:
			# For existing teams, always require a team name
			if not team_name:
				raise forms.ValidationError("Team name is required.")

		# Check for unique team name within organization
		if team_name and organization:
			existing_team = Team.objects.filter(
				organization=organization, name=team_name
			).exclude(pk=self.instance.pk if self.instance.pk else None)

			if existing_team.exists():
				raise forms.ValidationError(
					f"A team named '{team_name}' already exists in organization '{organization.name}'."
				)

		return cleaned_data

	def save(self, commit=True):
		from django.utils.text import slugify
		from organizations.models import Organization
		from gregory.models import OrganizationSite

		team_name = self.cleaned_data.get("team_name")
		organization = self.cleaned_data.get("organization")

		# For new teams, create organization if team_name is provided and no organization selected
		if not self.instance.pk:
			if organization:
				# If organization is selected, use it
				self.instance.organization = organization
				self.instance.name = team_name or organization.name
				# Auto-generate slug if not provided
				if not self.instance.slug:
					self.instance.slug = slugify(
						f"{organization.name}-{team_name or 'team'}"
					)
			elif team_name:
				# If only team_name is provided, create new organization
				organization = Organization.objects.create(name=team_name.strip())
				self.instance.organization = organization
				self.instance.name = team_name.strip()

				# Auto-generate slug if not provided
				if not self.instance.slug:
					self.instance.slug = slugify(team_name)
		else:
			# For existing teams, update the name
			if team_name:
				self.instance.name = team_name.strip()

		# Default site to the organisation's default site when not explicitly set
		if not self.instance.site_id and self.instance.organization_id:
			default_org_site = (
				OrganizationSite.objects.filter(
					organization_id=self.instance.organization_id,
					is_default=True,
				)
				.select_related("site")
				.first()
			)
			if default_org_site:
				self.instance.site = default_org_site.site

		return super().save(commit)


class TeamMembersInline(admin.TabularInline):
	model = Team.members.through
	extra = 1
	verbose_name = "Member"
	verbose_name_plural = "Members"
	classes = ("collapse",)
	autocomplete_fields = ["user"]


class TeamListsInline(admin.TabularInline):
	"""Inline to display lists belonging to a team."""

	model = apps.get_model("subscriptions", "Lists")
	extra = 0
	fields = ("list_name", "list_description", "weekly_digest", "admin_summary")
	readonly_fields = (
		"list_name",
		"list_description",
		"weekly_digest",
		"admin_summary",
	)
	show_change_link = True
	verbose_name = "List"
	verbose_name_plural = "Lists"
	classes = ("collapse",)
	can_delete = False

	def has_add_permission(self, request, obj=None):
		return False


def _ensure_user_in_organization(user, organization):
	"""Add user to organization if not already a member."""
	if not OrganizationUser.objects.filter(
		user=user, organization=organization
	).exists():
		OrganizationUser.objects.create(user=user, organization=organization)


class ReassignTeamForm(forms.Form):
	"""Intermediate form for the 'Reassign to team' admin action."""

	target_team = forms.ModelChoiceField(
		queryset=Team.objects.none(),  # filled dynamically
		label="Target team",
		help_text="All objects will be moved to this team. Must be in the same organisation.",
	)
	conflict = forms.ChoiceField(
		choices=[
			("skip", "Skip — leave conflicting subjects on the old team"),
			("rename", "Rename — append a suffix to conflicting subject slugs"),
			("merge", "Merge — fold conflicting subjects into the existing one"),
		],
		initial="skip",
		label="Subject slug conflict handling",
	)


@admin.register(Team)
class TeamAdmin(OrganizationFilterMixin, admin.ModelAdmin):
	form = TeamAdminForm
	inlines = [
		TeamMembersInline,
		TeamSubjectInline,
		TeamCategoryInline,
		TeamSourceInline,
		TeamListsInline,
	]
	list_display = [
		"id",
		"formatted_team_name",
		"organization_link",
		"slug",
		"subjects_count",
		"sources_count",
		"active_badge",
	]
	list_display_links = ["id", "formatted_team_name"]
	list_filter = ["organization", "is_active"]
	search_fields = ["name", "organization__name", "slug"]
	actions = [
		"soft_delete_teams",
		"reassign_to_team",
		"hard_delete_empty_inactive_teams",
	]

	fieldsets = (
		(None, {"fields": ("team_name", "organization", "slug", "is_active")}),
	)
	readonly_fields = ("organization_link",)

	def get_queryset(self, request):
		# Show all teams (active and inactive) in the admin.
		qs = Team.all_objects.select_related("organization").prefetch_related(
			"subjects", "sources", "members"
		)
		# Apply organisation-based filtering from OrganizationFilterMixin if needed.
		if not request.user.is_superuser:
			user_orgs = get_user_organizations(request.user)
			if user_orgs is not None:
				qs = qs.filter(organization__in=user_orgs)
		return qs

	# ------------------------------------------------------------------ #
	# Display helpers                                                      #
	# ------------------------------------------------------------------ #

	def organization_link(self, obj):
		if obj.organization_id:
			url = reverse(
				"admin:organizations_organization_change", args=[obj.organization_id]
			)
			return format_html('<a href="{}">{}</a>', url, obj.organization.name)
		return "-"

	organization_link.short_description = "Organisation"
	organization_link.admin_order_field = "organization__name"

	def formatted_team_name(self, obj):
		if obj.is_active:
			return format_html("<strong>{}</strong>", obj.name)
		return format_html(
			'<strong style="color:#999;text-decoration:line-through;">{}</strong> '
			'<span style="color:#c0392b;font-size:0.85em;">[inactive]</span>',
			obj.name,
		)

	formatted_team_name.short_description = "Team Name"
	formatted_team_name.admin_order_field = "name"

	def active_badge(self, obj):
		if obj.is_active:
			return format_html(
				'<span style="color:green;font-weight:bold;">{}</span>', "✓ Active"
			)
		return format_html(
			'<span style="color:#c0392b;font-weight:bold;">{}</span>', "✗ Inactive"
		)

	active_badge.short_description = "Status"
	active_badge.admin_order_field = "is_active"

	def subjects_count(self, obj):
		return obj.subjects.count()

	subjects_count.short_description = "Subjects"

	def sources_count(self, obj):
		return obj.sources.count()

	sources_count.short_description = "Sources"

	# ------------------------------------------------------------------ #
	# Soft-delete: override delete_model and delete_queryset               #
	# ------------------------------------------------------------------ #

	def delete_model(self, request, obj):
		"""Soft-delete a single team from the change view."""
		obj.delete()  # calls Team.delete() which sets is_active=False

	def delete_queryset(self, request, queryset):
		"""Soft-delete all selected teams from the list view."""
		for team in queryset:
			team.delete()

	# ------------------------------------------------------------------ #
	# Admin actions                                                        #
	# ------------------------------------------------------------------ #

	@admin.action(description="Deactivate selected teams (soft delete)")
	def soft_delete_teams(self, request, queryset):
		count = 0
		for team in queryset:
			if team.is_active:
				team.delete()
				count += 1
		self.message_user(request, f"{count} team(s) deactivated.")

	@admin.action(description="Reassign all objects to another team…")
	def reassign_to_team(self, request, queryset):
		from gregory.services.team_reassignment import reassign_team

		# Step 1 — show the intermediate form.
		if "apply" not in request.POST:
			# Build queryset of valid target teams: active, not in the selection,
			# but sharing the same organisation as the selected teams.
			org_ids = queryset.values_list("organization_id", flat=True).distinct()
			target_qs = Team.objects.filter(organization_id__in=org_ids).exclude(
				pk__in=queryset.values_list("pk", flat=True)
			)
			form = ReassignTeamForm()
			form.fields["target_team"].queryset = target_qs
			return render(
				request,
				"admin/gregory/team/reassign_intermediate.html",
				{
					"title": "Reassign team objects",
					"teams": queryset,
					"form": form,
					"action_checkbox_name": admin.helpers.ACTION_CHECKBOX_NAME,
				},
			)

		# Step 2 — process the form.
		org_ids = queryset.values_list("organization_id", flat=True).distinct()
		target_qs = Team.objects.filter(organization_id__in=org_ids).exclude(
			pk__in=queryset.values_list("pk", flat=True)
		)
		form = ReassignTeamForm(request.POST)
		form.fields["target_team"].queryset = target_qs

		if not form.is_valid():
			self.message_user(
				request, "Invalid form — please try again.", level=messages.ERROR
			)
			return

		to_team = form.cleaned_data["target_team"]
		conflict = form.cleaned_data["conflict"]

		errors = []
		for from_team in queryset:
			try:
				report = reassign_team(
					from_team=from_team, to_team=to_team, conflict=conflict
				)
				self.message_user(
					request,
					f"Reassigned '{from_team.name}' → '{to_team.name}': "
					f"{len(report.subjects_moved)} subjects, {report.sources_moved} sources, "
					f"{report.lists_moved} lists moved.",
				)
			except ValueError as exc:
				errors.append(str(exc))

		if errors:
			self.message_user(
				request, "Errors: " + "; ".join(errors), level=messages.ERROR
			)

	@admin.action(description="Hard-delete selected inactive teams (only if empty)")
	def hard_delete_empty_inactive_teams(self, request, queryset):
		deleted = 0
		skipped = []
		for team in queryset:
			if team.is_active:
				skipped.append(f"'{team.name}' is still active")
				continue
			has_objects = (
				team.subjects.exists()
				or team.sources.exists()
				or team.team_categories.exists()
				or team.lists.exists()
				or team.prediction_run_logs.exists()
			)
			if has_objects:
				skipped.append(
					f"'{team.name}' still has related objects — reassign first"
				)
				continue
			team.hard_delete()
			deleted += 1

		if deleted:
			self.message_user(request, f"{deleted} team(s) permanently deleted.")
		if skipped:
			self.message_user(
				request, "Skipped: " + "; ".join(skipped), level=messages.WARNING
			)

	# ------------------------------------------------------------------ #
	# URLs for custom views                                                #
	# ------------------------------------------------------------------ #

	def get_urls(self):
		from django.urls import path as url_path

		urls = super().get_urls()
		custom = [
			url_path(
				"<int:team_id>/reassign/",
				self.admin_site.admin_view(self.reassign_view),
				name="gregory_team_reassign",
			),
		]
		return custom + urls

	def reassign_view(self, request, team_id):
		"""Detail-level reassign view (accessible from the change form)."""
		from gregory.services.team_reassignment import reassign_team

		from_team = Team.all_objects.get(pk=team_id)
		org_id = from_team.organization_id
		target_qs = Team.objects.filter(organization_id=org_id).exclude(pk=team_id)

		if request.method == "POST":
			form = ReassignTeamForm(request.POST)
			form.fields["target_team"].queryset = target_qs
			if form.is_valid():
				to_team = form.cleaned_data["target_team"]
				conflict = form.cleaned_data["conflict"]
				try:
					report = reassign_team(
						from_team=from_team, to_team=to_team, conflict=conflict
					)
					self.message_user(
						request, f"Reassignment complete.\n{report.summary()}"
					)
				except ValueError as exc:
					self.message_user(request, str(exc), level=messages.ERROR)
				return self._response_post_save(request, from_team)
		else:
			form = ReassignTeamForm()
			form.fields["target_team"].queryset = target_qs

		return render(
			request,
			"admin/gregory/team/reassign_intermediate.html",
			{
				"title": f'Reassign objects from "{from_team}"',
				"teams": [from_team],
				"form": form,
				"action_checkbox_name": admin.helpers.ACTION_CHECKBOX_NAME,
				"original": from_team,
				"opts": self.model._meta,
			},
		)

	def save_related(self, request, form, formsets, change):
		super().save_related(request, form, formsets, change)
		# Auto-add new members to the team's organization
		team = form.instance
		if team.organization_id:
			for user in team.members.all():
				_ensure_user_in_organization(user, team.organization)


@admin.register(PredictionRunLog)
class PredictionRunLogAdmin(OrganizationFilterMixin, admin.ModelAdmin):
	list_display = [
		"id",
		"team",
		"subject",
		"run_type",
		"algorithm",
		"model_version",
		"run_started",
		"run_finished",
		"status_label",
		"triggered_by",
	]
	list_filter = [
		DateRangeFilter,
		"team",
		"subject",
		"run_type",
		"algorithm",
		"success",
		"model_version",
	]
	search_fields = [
		"team__organization__name",
		"subject__subject_name",
		"model_version",
		"triggered_by",
		"algorithm",
	]
	readonly_fields = ["run_started"]  # Auto-populated field
	date_hierarchy = "run_started"
	actions = ["mark_as_failed", "mark_as_successful", "export_as_csv"]

	fieldsets = (
		(
			"Run Information",
			{
				"fields": (
					"team",
					"subject",
					"run_type",
					"algorithm",
					"model_version",
					"triggered_by",
				),
			},
		),
		(
			"Status",
			{
				"fields": ("run_started", "run_finished", "success", "error_message"),
			},
		),
	)

	def status_label(self, obj):
		if obj.success is True:
			color, label = "green", "Success"
		elif obj.success is False:
			color, label = "red", "Failed"
		else:
			color, label = "orange", "Running"
		return format_html(
			'<span style="color: {}; font-weight: bold;">{}</span>',
			color,
			label,
		)

	status_label.short_description = "Status"

	def get_queryset(self, request):
		# Order by most recent runs first
		return super().get_queryset(request).order_by("-run_started")

	def has_change_permission(self, request, obj=None):
		# Logs should generally not be modified after creation
		# But admins might need to update status or error messages
		return True

	def has_delete_permission(self, request, obj=None):
		# Allow deletion for admins
		return True

	def mark_as_failed(self, request, queryset):
		"""Mark selected unfinished runs as failed"""
		from django.utils import timezone

		# Only update runs that are still in progress (success is None)
		updated = queryset.filter(success__isnull=True).update(
			success=False,
			run_finished=timezone.now(),
			error_message="Manually marked as failed by admin.",
		)

		if updated == 0:
			self.message_user(request, "No unfinished runs were selected.")
		else:
			self.message_user(
				request, f"Successfully marked {updated} run(s) as failed."
			)

	mark_as_failed.short_description = "Mark selected unfinished runs as failed"

	def mark_as_successful(self, request, queryset):
		"""Mark selected unfinished runs as successful"""
		from django.utils import timezone

		# Only update runs that are still in progress (success is None)
		updated = queryset.filter(success__isnull=True).update(
			success=True, run_finished=timezone.now()
		)

		if updated == 0:
			self.message_user(request, "No unfinished runs were selected.")
		else:
			self.message_user(
				request, f"Successfully marked {updated} run(s) as successful."
			)

	mark_as_successful.short_description = "Mark selected unfinished runs as successful"

	def export_as_csv(self, request, queryset):
		"""Export selected logs to CSV file"""
		meta = self.model._meta
		field_names = [
			"id",
			"team",
			"subject",
			"run_type",
			"algorithm",
			"model_version",
			"run_started",
			"run_finished",
			"success",
			"triggered_by",
			"error_message",
		]

		response = HttpResponse(content_type="text/csv")
		response["Content-Disposition"] = (
			f"attachment; filename={meta.verbose_name_plural}.csv"
		)

		writer = csv.writer(response)
		writer.writerow([field for field in field_names])

		for obj in queryset:
			row = []
			for field in field_names:
				if field == "team":
					value = str(getattr(obj, field))
				elif field == "subject":
					value = str(getattr(obj, field))
				elif field == "run_type":
					value = obj.get_run_type_display()
				else:
					value = getattr(obj, field)
				row.append(value)
			writer.writerow(row)

		return response

	export_as_csv.short_description = "Export selected logs to CSV"

	def changelist_view(self, request, extra_context=None):
		# Add dashboard statistics to the context
		extra_context = extra_context or {}

		# Training runs statistics
		extra_context["training_success_count"] = PredictionRunLog.objects.filter(
			run_type="train", success=True
		).count()
		extra_context["training_failed_count"] = PredictionRunLog.objects.filter(
			run_type="train", success=False
		).count()
		extra_context["training_running_count"] = PredictionRunLog.objects.filter(
			run_type="train", success__isnull=True
		).count()

		# Prediction runs statistics
		extra_context["prediction_success_count"] = PredictionRunLog.objects.filter(
			run_type="predict", success=True
		).count()
		extra_context["prediction_failed_count"] = PredictionRunLog.objects.filter(
			run_type="predict", success=False
		).count()
		extra_context["prediction_running_count"] = PredictionRunLog.objects.filter(
			run_type="predict", success__isnull=True
		).count()

		# Recent runs (last 10)
		extra_context["recent_runs"] = PredictionRunLog.objects.all().order_by(
			"-run_started"
		)[:10]

		return super().changelist_view(request, extra_context=extra_context)

	def get_urls(self):
		urls = super().get_urls()
		custom_urls = [
			path(
				"ml-coverage/",
				self.admin_site.admin_view(self.ml_coverage_view),
				name="predictionrunlog_ml_coverage",
			),
		]
		return custom_urls + urls

	def ml_coverage_view(self, request):
		"""View to show ML coverage across teams and subjects"""
		# Get all teams
		teams = Team.objects.prefetch_related("subjects").all()

		# Create dictionaries to store the latest runs per subject
		training_data = {}
		prediction_data = {}

		# Get all subjects
		all_subjects = Subject.objects.all()

		# For each subject, get its latest training and prediction runs
		for subject in all_subjects:
			latest_training = PredictionRunLog.get_latest_run(
				subject.team, subject, run_type="train"
			)
			latest_prediction = PredictionRunLog.get_latest_run(
				subject.team, subject, run_type="predict"
			)

			if latest_training:
				training_data[subject.id] = latest_training

			if latest_prediction:
				prediction_data[subject.id] = latest_prediction

		context = {
			"title": "ML Coverage Report",
			"teams": teams,
			"training_data": training_data,
			"prediction_data": prediction_data,
		}

		# Render the template
		return render(
			request, "admin/gregory/predictionrunlog/ml_coverage.html", context
		)


admin.site.register(Articles, ArticleAdmin)
admin.site.register(Authors, AuthorsAdmin)
admin.site.register(Entities)
admin.site.register(Sources, SourceAdmin)
admin.site.register(Trials, TrialAdmin)


class _BaseOrgContentAdmin(OrganizationFilterMixin, SimpleHistoryAdmin):
	"""Shared admin for ArticleOrgContent / TrialOrgContent standalone pages.

	Use the parent Article/Trial admin for routine editing; this admin exists
	so users can browse audit history and so superusers can clean up rows
	across organisations.
	"""

	list_filter = ("organization",)
	readonly_fields = ("created_at", "updated_at")
	search_fields = ("takeaways", "summary_plain_english")

	def get_form(self, request, obj=None, **kwargs):
		form_class = super().get_form(request, obj, **kwargs)
		if request.user.is_superuser:
			return form_class

		user_org_ids = list(get_user_organizations(request.user) or [])

		class ScopedOrgForm(form_class):
			def __init__(self, *args, **form_kwargs):
				super().__init__(*args, **form_kwargs)
				if "organization" in self.fields:
					self.fields["organization"].queryset = Organization.objects.filter(
						id__in=user_org_ids
					).order_by("name")
					if self.instance and self.instance.pk:
						self.fields["organization"].disabled = True

		return ScopedOrgForm


@admin.register(ArticleOrgContent)
class ArticleOrgContentAdmin(_BaseOrgContentAdmin):
	list_display = ("article", "organization", "updated_at")
	raw_id_fields = ("article",)
	search_fields = _BaseOrgContentAdmin.search_fields + (
		"article__title",
		"article__doi",
	)

	def has_module_perms(self, request):
		return False


@admin.register(TrialOrgContent)
class TrialOrgContentAdmin(_BaseOrgContentAdmin):
	list_display = ("trial", "organization", "updated_at")
	raw_id_fields = ("trial",)
	search_fields = _BaseOrgContentAdmin.search_fields + ("trial__title",)


# --- Custom UserAdmin with Team membership inline ---


class UserTeamInline(admin.TabularInline):
	model = Team.members.through
	extra = 1
	verbose_name = "Team membership"
	verbose_name_plural = "Team memberships"
	autocomplete_fields = ["team"]


class CustomUserAdmin(BaseUserAdmin):
	inlines = list(BaseUserAdmin.inlines) + [UserTeamInline]

	def save_related(self, request, form, formsets, change):
		super().save_related(request, form, formsets, change)
		# Auto-add user to each team's organization
		user = form.instance
		for team in Team.objects.filter(members=user):
			if team.organization_id:
				_ensure_user_in_organization(user, team.organization)


try:
	admin.site.unregister(User)
except admin.sites.NotRegistered:
	pass
admin.site.register(User, CustomUserAdmin)

# Register ArticleTrialReference model with default admin
# @admin.register(ArticleTrialReference)
# class ArticleTrialReferenceAdmin(admin.ModelAdmin):
#     list_display = ['article', 'trial', 'identifier_type', 'identifier_value', 'discovered_date']
#     list_filter = ['identifier_type', 'discovered_date']
#     search_fields = ['article__title', 'trial__title', 'identifier_value']
#     date_hierarchy = 'discovered_date'
#     readonly_fields = ['discovered_date']
