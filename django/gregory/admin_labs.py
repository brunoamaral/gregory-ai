"""Admin for author labs. Superusers only: nothing here renders or accepts writes for
anyone else, and nothing is exposed outside the admin (no API, MCP, RSS or serializer).

Every edit made here is marked manual (``Lab.is_manual`` / ``LabMembership.source``),
so ``rebuild_labs`` leaves it alone.
"""

from collections import Counter, defaultdict
from urllib.parse import urlencode

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.widgets import AutocompleteSelect
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Exists, F, FilteredRelation, Max, OuterRef, Prefetch, Q
from django.forms.models import BaseInlineFormSet
from django.shortcuts import render
from django.urls import reverse
from django.utils.formats import date_format
from django.utils.html import format_html, format_html_join
from django.utils.text import capfirst
from django.utils.timezone import localtime

from .models import (
	ArticleAuthorship,
	Authors,
	Lab,
	LabCategory,
	LabMembership,
	LabSubject,
	Subject,
	TeamCategory,
)
from .services.labs import merge_labs

HEAD = LabMembership.Role.HEAD
MEMBER = LabMembership.Role.MEMBER
COLLABORATOR = LabMembership.Role.COLLABORATOR
MANUAL = LabMembership.Source.MANUAL


def _without_prefix(items, prefix):
	"""A copy of a nested delete-confirmation list, leaving out entries starting with ``prefix``."""
	kept = []
	for item in items:
		if isinstance(item, list):
			nested = _without_prefix(item, prefix)
			if nested:
				kept.append(nested)
		elif not str(item).startswith(prefix):
			kept.append(item)
	return kept


class HideLabLinksOnDeleteMixin:
	"""Keep lab links off a category's delete confirmation for anyone but a superuser. Editors
	can delete categories, and Django lists every row a delete takes with it."""

	def get_deleted_objects(self, objs, request):
		to_delete, model_count, perms_needed, protected = super().get_deleted_objects(objs, request)
		if not request.user.is_superuser:
			opts = LabCategory._meta
			to_delete = _without_prefix(to_delete, f"{capfirst(opts.verbose_name)}: ")
			model_count.pop(opts.verbose_name_plural, None)
		return to_delete, model_count, perms_needed, protected


class TopLabsAdminMixin:
	"""A read-only "Top labs" section on a subject's or category's change page, for superusers.

	Editors open these pages too, so nothing here runs for them: the block is added per
	request (never to the class-level lists) and is left out of their page, their form and
	the queries they cause. Set ``top_labs_link_model`` and ``top_labs_topic_field``, the link
	model's FK to the topic, which is also the name of the Labs list's filter.
	"""

	top_labs_link_model = None
	top_labs_topic_field = None
	top_labs_limit = 25

	def _shows_top_labs(self, request, obj):
		return bool(request.user.is_superuser and obj is not None and obj.pk)

	def get_readonly_fields(self, request, obj=None):
		readonly = list(super().get_readonly_fields(request, obj))
		if self._shows_top_labs(request, obj) and "top_labs_block" not in readonly:
			readonly.append("top_labs_block")
		return readonly

	def get_fieldsets(self, request, obj=None):
		# Copies throughout: the base may hand back the class-level fieldsets.
		fieldsets = []
		for name, options in super().get_fieldsets(request, obj):
			options = dict(options)
			options["fields"] = [f for f in options["fields"] if f != "top_labs_block"]
			fieldsets.append((name, options))
		if self._shows_top_labs(request, obj):
			fieldsets.append(("Top labs", {"fields": ("top_labs_block",)}))
		return fieldsets

	def top_labs_block(self, obj):
		topic = {self.top_labs_topic_field: obj.pk, "is_hidden": False}
		visible = self.top_labs_link_model.objects.filter(**topic)
		links = list(
			visible.select_related("lab__head", "lab__institution").order_by(
				"-relevant_count", "-article_count", "pk"
			)[: self.top_labs_limit]
		)
		if not links:
			return "No linked labs yet."
		total = len(links) if len(links) < self.top_labs_limit else visible.count()
		rows = format_html_join(
			"",
			"<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>",
			(
				(
					format_html(
						'<a href="{}">{}</a>',
						reverse("admin:gregory_lab_change", args=[link.lab_id]),
						str(link.lab),
					),
					author_name(link.lab.head) if link.lab.head_id else "-",
					link.lab.institution.name if link.lab.institution_id else "-",
					link.article_count,
					link.relevant_count,
					link.recent_article_count,
					link.latest_article_year or "-",
				)
				for link in links
			),
		)
		list_url = "{}?{}".format(
			reverse("admin:gregory_lab_changelist"), urlencode({self.top_labs_topic_field: obj.pk})
		)
		return format_html(
			"<table><thead><tr><th>Lab</th><th>Head</th><th>Institution</th><th>Papers</th>"
			"<th>Relevant</th><th>Recent</th><th>Latest year</th></tr></thead><tbody>{}</tbody></table>"
			'<p>{} in total. <a href="{}">See all in the Labs list</a></p>',
			rows,
			"1 linked lab" if total == 1 else f"{total} linked labs",
			list_url,
		)

	top_labs_block.short_description = "Top labs"


class SuperuserOnlyAdminMixin:
	"""Module, view, add, change and delete permission for superusers and nobody else."""

	def has_module_permission(self, request):
		return request.user.is_superuser

	def has_view_permission(self, request, obj=None):
		return request.user.is_superuser

	def has_add_permission(self, request, *args, **kwargs):
		return request.user.is_superuser

	def has_change_permission(self, request, obj=None):
		return request.user.is_superuser

	def has_delete_permission(self, request, obj=None):
		return request.user.is_superuser


def author_name(author):
	return author.full_name or f"{author.given_name} {author.family_name}".strip()


def core_label(same_institution):
	if same_institution is True:
		return "Core"
	if same_institution is False:
		return "External"
	return "Unknown"


def author_link(author_id, name):
	return format_html(
		'<a href="{}">{}</a>', reverse("admin:gregory_authors_change", args=[author_id]), name
	)


def article_link(article):
	return format_html(
		'<a href="{}">{}</a>',
		reverse("admin:gregory_articles_change", args=[article.pk]),
		article.title,
	)


# --- topic filters ---------------------------------------------------------------------


def _int_param(request, name):
	try:
		return int(request.GET.get(name, ""))
	except (TypeError, ValueError):
		return None


def _team_label(name, team):
	return f"{name} ({team.name})" if team else name


class LabSubjectFilter(admin.SimpleListFilter):
	title = "subject"
	parameter_name = "subject"

	def lookups(self, request, model_admin):
		subjects = Subject.objects.select_related("team").order_by("subject_name")
		return [(s.pk, _team_label(s.subject_name, s.team)) for s in subjects]

	def queryset(self, request, queryset):
		# Narrowing needs the link's counts too, so LabAdmin.get_queryset joins the link
		# and adds the columns and the order.
		return queryset


class LabCategoryFilter(admin.SimpleListFilter):
	title = "category"
	parameter_name = "category"
	template = "admin/gregory/lab/category_filter.html"

	def lookups(self, request, model_admin):
		# The chosen category stays listed after its last link is hidden, so the sidebar
		# still explains an empty list.
		visible_link = LabCategory.objects.filter(category=OuterRef("pk"), is_hidden=False)
		categories = (
			TeamCategory.objects.filter(
				Exists(visible_link) | Q(pk=_int_param(request, self.parameter_name))
			)
			.select_related("team")
			.order_by("category_name", "pk")
		)
		return [(c.pk, _team_label(c.category_name, c.team)) for c in categories]

	def queryset(self, request, queryset):
		# As for the subject filter: LabAdmin.get_queryset does the narrowing.
		return queryset


class LabCountryFilter(admin.SimpleListFilter):
	title = "country"
	parameter_name = "country"

	def lookups(self, request, model_admin):
		codes = (
			Lab.objects.exclude(institution__country_code="")
			.exclude(institution__isnull=True)
			.values_list("institution__country_code", flat=True)
			.distinct()
			.order_by("institution__country_code")
		)
		return [(code, code) for code in codes]

	def queryset(self, request, queryset):
		if self.value():
			return queryset.filter(institution__country_code=self.value())
		return queryset


class LabSizeFilter(admin.SimpleListFilter):
	title = "members"
	parameter_name = "size"
	RANGES = {"1": (1, 1), "2-5": (2, 5), "6-15": (6, 15), "16+": (16, None)}

	def lookups(self, request, model_admin):
		return [("1", "1"), ("2-5", "2 to 5"), ("6-15", "6 to 15"), ("16+", "16 or more")]

	def queryset(self, request, queryset):
		bounds = self.RANGES.get(self.value())
		if not bounds:
			return queryset
		low, high = bounds
		queryset = queryset.filter(member_count__gte=low)
		if high is not None:
			queryset = queryset.filter(member_count__lte=high)
		return queryset


# --- the lab page ------------------------------------------------------------------------


class LabAdminForm(forms.ModelForm):
	class Meta:
		model = Lab
		fields = ["name", "head", "institution", "notes"]

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.fields["head"].required = True

	def clean_head(self):
		head = self.cleaned_data["head"]
		other = Lab.objects.filter(head=head).exclude(pk=self.instance.pk).first()
		if other:
			raise ValidationError(
				"%(author)s already heads %(lab)s. An author can head only one lab: "
				"open that lab to change its head, or merge the two labs.",
				params={"author": author_name(head), "lab": str(other)},
			)
		return head


class LabMemberForm(forms.ModelForm):
	class Meta:
		model = LabMembership
		fields = ["author", "same_institution"]

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		if self.instance.pk:
			self.fields["author"].disabled = True

	def clean_author(self):
		author = self.cleaned_data["author"]
		if not self.instance.pk:
			membership = LabMembership.objects.filter(author=author).first()
			if membership and membership.role == HEAD:
				raise ValidationError(
					"%(author)s is the head of a lab. Change that lab's head first.",
					params={"author": author_name(author)},
				)
			if Lab.objects.filter(head=author).exists():
				raise ValidationError(
					"%(author)s heads a lab.", params={"author": author_name(author)}
				)
		return author

	def clean(self):
		cleaned = super().clean()
		# Set before the model is validated: a manual row is what the check constraint allows.
		self.instance.source = MANUAL
		return cleaned

	def validate_unique(self):
		# An author already in another lab is moved by LabMemberFormSet.save_new, so the
		# one-to-one on the author isn't a conflict here.
		pass


class LabMemberFormSet(BaseInlineFormSet):
	def save_new(self, form, commit=True):
		author = form.cleaned_data["author"]
		built = form.save(commit=False)  # also gives the form its save_m2m
		membership = LabMembership.objects.filter(author=author).first() or built
		membership.lab = self.instance
		membership.role = MEMBER
		membership.source = MANUAL
		membership.same_institution = form.cleaned_data.get("same_institution")
		if commit:
			membership.save()
		return membership


def save_manual_memberships(formset, *, pin_deleted=False):
	"""Save an inline formset of memberships, marking every added or changed row manual.

	A deleted row is deleted, which lets the next rebuild place the author again. With
	``pin_deleted`` it is kept as a manual row with no lab instead, so the rebuild doesn't
	put the author straight back.
	"""
	instances = formset.save(commit=False)
	for obj in formset.deleted_objects:
		if pin_deleted:
			obj.lab = None
			obj.same_institution = None
			obj.source = MANUAL
			obj.save()
		else:
			obj.delete()
	for obj in instances:
		obj.source = MANUAL
		obj.save()
	formset.save_m2m()


class LabMemberInline(SuperuserOnlyAdminMixin, admin.TabularInline):
	"""Members of the lab. The head is set on the lab itself; collaborators are listed
	read-only below."""

	model = LabMembership
	fk_name = "lab"
	form = LabMemberForm
	formset = LabMemberFormSet
	extra = 0
	autocomplete_fields = ["author"]
	verbose_name = "member"
	verbose_name_plural = (
		"members (adding or changing a row makes it manual; deleting a row pins the author to no lab)"
	)
	fields = [
		"author",
		"role",
		"same_institution",
		"shared_articles",
		"last_shared_year",
		"source",
		"orcid",
		"has_email",
	]
	readonly_fields = ["role", "shared_articles", "last_shared_year", "source", "orcid", "has_email"]

	def get_queryset(self, request):
		return (
			super()
			.get_queryset(request)
			.filter(role=MEMBER)
			.select_related("author")
			.order_by("-shared_articles", "author_id")
		)

	@admin.display(description="ORCID")
	def orcid(self, obj):
		return (obj.author.ORCID if obj.pk else None) or "-"

	@admin.display(description="Email held", boolean=True)
	def has_email(self, obj):
		return bool(obj.pk and obj.author.emails)


# --- subjects and categories on the lab page ---------------------------------------------


class SubjectChoiceField(forms.ModelChoiceField):
	def label_from_instance(self, obj):
		return _team_label(obj.subject_name, obj.team)


class LabTopicLinkForm(forms.ModelForm):
	"""One row of a lab's subjects or categories. The topic is chosen on new rows only.
	``hide`` (visible rows), ``unhide`` (hidden rows) and ``reset`` aren't model fields:
	LabTopicLinkFormSet.save_existing turns them into the two flags."""

	hide = forms.BooleanField(required=False, label="Hide")
	unhide = forms.BooleanField(required=False, label="Unhide")
	reset = forms.BooleanField(required=False, label="Reset")

	topic_field = ""

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		if self.instance.pk:
			# Only the chosen topic is rendered, not every subject or category.
			field = self.fields[self.topic_field]
			field.queryset = field.queryset.filter(pk=getattr(self.instance, f"{self.topic_field}_id"))
			field.disabled = True
		else:
			# A new row is always a visible, manual link: these only apply to stored ones.
			for name in ("hide", "unhide", "reset"):
				self.fields[name].disabled = True

	def validate_unique(self):
		# Adding a link the lab already has isn't an error: LabTopicLinkFormSet.save_new
		# unhides it and marks it manual. Two identical rows in one submission still fail,
		# in the formset's own check.
		pass

	def validate_constraints(self):
		pass


class LabSubjectLinkForm(LabTopicLinkForm):
	topic_field = "subject"

	class Meta:
		model = LabSubject
		fields = ["subject"]


class LabCategoryLinkForm(LabTopicLinkForm):
	topic_field = "category"

	class Meta:
		model = LabCategory
		fields = ["category"]


class LabTopicLinkFormSet(BaseInlineFormSet):
	"""Added links are manual and unhidden, even when the lab already had the link. Each
	existing row's hide, unhide and reset ticks become the two flags. Every write is a
	model save, so history records it."""

	def validate_unique(self):
		# Only rows added in this submission can clash, with each other. A new row for a
		# topic the lab already has is fine: save_new reuses the stored link.
		seen = set()
		for form in self.extra_forms:
			topic = form.cleaned_data.get(form.topic_field) if form.is_valid() else None
			if topic is None:
				continue
			if topic.pk in seen:
				raise ValidationError(
					"You added %(topic)s more than once.", params={"topic": str(topic)}
				)
			seen.add(topic.pk)

	def save_new(self, form, commit=True):
		link, _ = self.model.objects.update_or_create(
			lab=self.instance,
			**{form.topic_field: form.cleaned_data[form.topic_field]},
			defaults={"is_manual": True, "is_hidden": False},
		)
		return link

	def save_existing(self, form, instance, commit=True):
		cleaned = form.cleaned_data
		if not {"hide", "unhide", "reset"} & set(form.changed_data):
			return instance
		# The section saved before this one may have just changed this link.
		instance.refresh_from_db(fields=["is_manual", "is_hidden"])
		manual, hidden = instance.is_manual, instance.is_hidden
		if cleaned.get("reset"):
			manual, hidden = False, False
		elif cleaned.get("hide"):
			hidden = True
		elif cleaned.get("unhide"):
			hidden = False
		changed = (manual, hidden) != (instance.is_manual, instance.is_hidden)
		instance.is_manual, instance.is_hidden = manual, hidden
		if commit and changed:
			# Only the flags: a rebuild running now keeps the counts it writes.
			instance.save(update_fields=["is_manual", "is_hidden"])
		return instance


class LabTopicLinkInline(SuperuserOnlyAdminMixin, admin.TabularInline):
	"""A lab's visible, or hidden, links to one kind of topic. Counts are read-only."""

	formset = LabTopicLinkFormSet
	fk_name = "lab"
	extra = 0
	can_delete = False
	hidden = False
	topic_field = ""
	name_field = ""
	readonly_fields = ["papers", "relevant", "recent", "latest_year", "source"]

	def has_add_permission(self, request, obj=None):
		return request.user.is_superuser and not self.hidden

	def get_queryset(self, request):
		return (
			super()
			.get_queryset(request)
			.filter(is_hidden=self.hidden)
			.select_related(self.topic_field)
			.order_by(
				"-relevant_count", "-article_count", f"{self.topic_field}__{self.name_field}", "pk"
			)
		)

	@admin.display(description="Papers")
	def papers(self, obj):
		return obj.article_count if obj.pk else "-"

	@admin.display(description="Relevant")
	def relevant(self, obj):
		return obj.relevant_count if obj.pk else "-"

	@admin.display(description="Recent")
	def recent(self, obj):
		return obj.recent_article_count if obj.pk else "-"

	@admin.display(description="Latest year")
	def latest_year(self, obj):
		return (obj.latest_article_year or "-") if obj.pk else "-"

	@admin.display(description="Source")
	def source(self, obj):
		if not obj.pk:
			return "-"
		return "Manual" if obj.is_manual else "Computed"


class LabSubjectInline(LabTopicLinkInline):
	model = LabSubject
	form = LabSubjectLinkForm
	topic_field = "subject"
	name_field = "subject_name"
	verbose_name = "subject"
	verbose_name_plural = "subjects (adding one makes it manual; counts change with each pipeline run)"
	fields = ["subject", "papers", "relevant", "recent", "latest_year", "source", "hide", "reset"]

	def formfield_for_dbfield(self, db_field, request, **kwargs):
		if db_field.name == "subject":
			# A plain select: there are few subjects, and the admin's add and change
			# links aren't wanted here.
			return SubjectChoiceField(
				queryset=Subject.objects.select_related("team").order_by("subject_name", "pk"),
				label="Subject",
			)
		return super().formfield_for_dbfield(db_field, request, **kwargs)


class LabHiddenSubjectInline(LabSubjectInline):
	hidden = True
	verbose_name_plural = "hidden subjects (shown nowhere else; unhide to restore)"
	fields = ["subject", "papers", "relevant", "recent", "latest_year", "source", "unhide", "reset"]
	classes = ["collapse"]


class LabCategoryInline(LabTopicLinkInline):
	model = LabCategory
	form = LabCategoryLinkForm
	topic_field = "category"
	name_field = "category_name"
	autocomplete_fields = ["category"]
	verbose_name = "category"
	verbose_name_plural = "categories (adding one makes it manual; counts change with each pipeline run)"
	fields = ["category", "papers", "relevant", "recent", "latest_year", "source", "hide", "reset"]

	def formfield_for_dbfield(self, db_field, request, **kwargs):
		field = super().formfield_for_dbfield(db_field, request, **kwargs)
		if db_field.name == "category":
			# Linking a lab is no place to create or edit a category. The view link stays.
			field.widget.can_add_related = False
			field.widget.can_change_related = False
			field.widget.can_delete_related = False
		return field


class LabHiddenCategoryInline(LabCategoryInline):
	hidden = True
	verbose_name_plural = "hidden categories (shown nowhere else; unhide to restore)"
	fields = ["category", "papers", "relevant", "recent", "latest_year", "source", "unhide", "reset"]
	classes = ["collapse"]


class LabAdmin(SuperuserOnlyAdminMixin, admin.ModelAdmin):
	form = LabAdminForm
	list_select_related = ("head", "institution")
	list_display = [
		"name_display",
		"head",
		"institution",
		"country",
		"members_display",
		"collaborator_count",
		"last_author_articles",
		"topics",
		"needs_review",
		"is_manual",
	]
	list_display_links = ["name_display"]
	list_filter = [LabSubjectFilter, LabCategoryFilter, LabCountryFilter, "needs_review", "is_manual", LabSizeFilter]
	search_fields = ["name", "head__family_name", "head__given_name", "head__full_name", "institution__name"]
	ordering = ["-last_author_articles", "pk"]
	autocomplete_fields = ["head", "institution"]
	actions = ["merge_selected_labs"]
	readonly_fields = [
		"counts_display",
		"computed_at",
		"topic_counts_changed",
		"is_manual",
		"needs_review",
		"review_reason",
		"affiliations_block",
		"recent_papers_block",
		"duplicates_block",
		"collaborators_block",
	]

	fieldsets = (
		(None, {"fields": ("name", "head", "institution", "notes")}),
		(
			"Computed data",
			{
				"fields": (
					"counts_display",
					"computed_at",
					"topic_counts_changed",
					"is_manual",
					"needs_review",
					"review_reason",
				),
				"description": "Editing the name, head or institution makes the lab manual: "
				"a rebuild no longer renames, re-heads, re-homes or deletes it.",
			},
		),
		(
			"Naming hints",
			{"fields": ("affiliations_block",), "description": "The head's most common printed affiliations."},
		),
		("Recent papers", {"fields": ("recent_papers_block",)}),
		("Probable duplicates", {"fields": ("duplicates_block",)}),
		("Collaborators", {"fields": ("collaborators_block",), "classes": ("collapse",)}),
	)
	add_fieldsets = ((None, {"fields": ("name", "head", "institution", "notes")}),)

	# -- list -----------------------------------------------------------------------------

	TOPIC_FILTERS = {
		"subject": ("subject_links", "subject_id"),
		"category": ("category_links", "category_id"),
	}

	def get_queryset(self, request):
		# ModelAdmin.get_queryset orders before we could annotate, and the ranking order
		# names the annotations, so build the queryset here.
		queryset = self.model._default_manager.get_queryset()
		for kind, (relation, column) in self.TOPIC_FILTERS.items():
			topic_id = _int_param(request, kind)
			if topic_id is None:
				continue
			# The stored, visible link: one row per lab, because the pair is unique.
			link = f"{kind}_link"
			queryset = (
				queryset.annotate(
					**{
						link: FilteredRelation(
							relation,
							condition=Q(**{f"{relation}__{column}": topic_id, f"{relation}__is_hidden": False}),
						)
					}
				)
				.filter(**{f"{link}__isnull": False})
				.annotate(
					**{
						f"articles_in_{kind}": F(f"{link}__article_count"),
						f"relevant_in_{kind}": F(f"{link}__relevant_count"),
						f"recent_in_{kind}": F(f"{link}__recent_article_count"),
					}
				)
			)
		queryset = queryset.prefetch_related(
			Prefetch(
				"category_links",
				LabCategory.objects.filter(is_hidden=False)
				.select_related("category")
				.order_by("-relevant_count", "-article_count", "category__category_name", "pk"),
				to_attr="ranked_category_links",
			)
		)
		ordering = self.get_ordering(request)
		return queryset.order_by(*ordering) if ordering else queryset

	def get_list_display(self, request):
		display = list(super().get_list_display(request))
		for kind in self.TOPIC_FILTERS:
			if _int_param(request, kind) is not None:
				display += [f"articles_in_{kind}", f"relevant_in_{kind}", f"recent_in_{kind}"]
		return display

	def get_ordering(self, request):
		# The narrower choice ranks: the category when both are chosen.
		for kind in ("category", "subject"):
			if _int_param(request, kind) is not None:
				return [f"-relevant_in_{kind}", f"-articles_in_{kind}", "pk"]
		return super().get_ordering(request)

	@admin.display(description="Lab", ordering="name")
	def name_display(self, obj):
		return str(obj)

	@admin.display(description="Country", ordering="institution__country_code")
	def country(self, obj):
		return obj.institution.country_code if obj.institution_id else "-"

	@admin.display(description="Members (core / external)", ordering="member_count")
	def members_display(self, obj):
		return f"{obj.core_count} / {obj.member_count - obj.core_count}"

	@admin.display(description="Topics")
	def topics(self, obj):
		# The first three of the prefetched visible category links, best first.
		names = [link.category.category_name for link in obj.ranked_category_links[:3]]
		return ", ".join(names) or "-"

	@admin.display(description="Articles in subject", ordering="articles_in_subject")
	def articles_in_subject(self, obj):
		return obj.articles_in_subject

	@admin.display(description="Relevant in subject", ordering="relevant_in_subject")
	def relevant_in_subject(self, obj):
		return obj.relevant_in_subject

	@admin.display(description="Recent in subject", ordering="recent_in_subject")
	def recent_in_subject(self, obj):
		return obj.recent_in_subject

	@admin.display(description="Articles in category", ordering="articles_in_category")
	def articles_in_category(self, obj):
		return obj.articles_in_category

	@admin.display(description="Relevant in category", ordering="relevant_in_category")
	def relevant_in_category(self, obj):
		return obj.relevant_in_category

	@admin.display(description="Recent in category", ordering="recent_in_category")
	def recent_in_category(self, obj):
		return obj.recent_in_category

	# -- form -----------------------------------------------------------------------------

	def get_fieldsets(self, request, obj=None):
		return self.fieldsets if obj else self.add_fieldsets

	def get_inline_instances(self, request, obj=None):
		if obj is None:
			return []
		return super().get_inline_instances(request, obj)

	def get_inlines(self, request, obj):
		return [
			LabSubjectInline,
			LabHiddenSubjectInline,
			LabCategoryInline,
			LabHiddenCategoryInline,
			LabMemberInline,
		]

	def save_model(self, request, obj, form, change):
		changed = set(form.changed_data)
		if not change or changed & {"name", "head", "institution"}:
			obj.is_manual = True
		super().save_model(request, obj, form, change)
		if not change or "head" in changed:
			old_head_id = form.initial.get("head") if change else None
			self._rewrite_head_memberships(obj, old_head_id)

	def _rewrite_head_memberships(self, lab, old_head_id):
		if old_head_id and old_head_id != lab.head_id:
			for membership in LabMembership.objects.filter(author_id=old_head_id, lab=lab, role=HEAD):
				membership.role = MEMBER
				membership.source = MANUAL
				membership.save()
		membership = LabMembership.objects.filter(author_id=lab.head_id).first()
		if membership is None:
			membership = LabMembership(author_id=lab.head_id)
		membership.lab = lab
		membership.role = HEAD
		membership.source = MANUAL
		membership.save()

	def save_formset(self, request, form, formset, change):
		if formset.model is LabMembership:
			# Removing a member here means "not in this lab": pin them to no lab, or the
			# next rebuild would put them back.
			save_manual_memberships(formset, pin_deleted=True)
		elif formset.model in (LabSubject, LabCategory):
			# Not memberships: the formset itself applies add, hide, unhide and reset.
			formset.save()
		else:
			super().save_formset(request, form, formset, change)

	# -- read-only blocks -----------------------------------------------------------------

	def _head_author_ids(self, lab):
		ids = set(
			LabMembership.objects.filter(lab=lab, role=HEAD).values_list("author_id", flat=True)
		)
		if lab.head_id:
			ids.add(lab.head_id)
		return ids

	@admin.display(description="Topic counts last changed")
	def topic_counts_changed(self, obj):
		# Not "last computed": a rebuild that finds no change writes nothing.
		latest = [
			obj.subject_links.aggregate(latest=Max("computed_at"))["latest"],
			obj.category_links.aggregate(latest=Max("computed_at"))["latest"],
		]
		latest = [value for value in latest if value]
		return date_format(localtime(max(latest)), "DATETIME_FORMAT") if latest else "-"

	@admin.display(description="Counts")
	def counts_display(self, obj):
		return (
			f"{obj.member_count} members ({obj.core_count} core, "
			f"{obj.member_count - obj.core_count} external or unknown); "
			f"{obj.collaborator_count} collaborators; "
			f"{obj.last_author_articles} last-author papers for the head"
		)

	@admin.display(description="Printed affiliations")
	def affiliations_block(self, obj):
		lines = Counter()
		head_ids = self._head_author_ids(obj)
		raw = (
			ArticleAuthorship.objects.filter(author_id__in=head_ids)
			.exclude(raw_affiliation="")
			.values_list("raw_affiliation", flat=True)
		)
		for text in raw:
			lines.update({line.strip() for line in text.splitlines() if line.strip()})
		top = lines.most_common(3)
		if not top:
			return "-"
		return format_html_join(
			"", "<div>{} <small>({} papers)</small></div>", ((line, n) for line, n in top)
		)

	@admin.display(description="Ten latest last-author papers")
	def recent_papers_block(self, obj):
		head_ids = self._head_author_ids(obj)
		rows = list(
			ArticleAuthorship.objects.filter(
				author_id__in=head_ids, position=ArticleAuthorship.Position.LAST
			)
			.select_related("article")
			.order_by(F("article__published_date").desc(nulls_last=True), "-article_id")[:10]
		)
		if not rows:
			return "-"
		return format_html_join(
			"",
			"<div>{} <small>{}</small></div>",
			(
				(
					article_link(row.article),
					row.article.published_date.strftime("%Y-%m-%d") if row.article.published_date else "",
				)
				for row in rows
			),
		)

	@admin.display(description="Probable duplicates")
	def duplicates_block(self, obj):
		groups = defaultdict(list)
		rows = (
			LabMembership.objects.filter(lab=obj, author__openalex_id__isnull=False)
			.exclude(author__openalex_id="")
			.values_list("author_id", "author__openalex_id", "author__full_name")
			.order_by("author_id")
		)
		for author_id, openalex_id, name in rows:
			groups[openalex_id].append((author_id, name))
		blocks = []
		for openalex_id, members in groups.items():
			if len(members) < 2:
				continue
			ids = ",".join(str(author_id) for author_id, _ in members)
			url = f"{reverse('admin:gregory_authors_changelist')}?{urlencode({'q': ids})}"
			names = ", ".join(f"{name or '?'} ({author_id})" for author_id, name in members)
			blocks.append((url, names, openalex_id))
		if not blocks:
			return "None"
		return format_html_join(
			"",
			'<div><a href="{}">{}</a> <small>(OpenAlex {})</small></div>',
			blocks,
		)

	@admin.display(description="Collaborators")
	def collaborators_block(self, obj):
		rows = list(
			LabMembership.objects.filter(lab=obj, role=COLLABORATOR)
			.select_related("author")
			.order_by("-shared_articles", "author_id")
		)
		if not rows:
			return "None"
		body = format_html_join(
			"",
			"<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>",
			(
				(
					author_link(row.author_id, author_name(row.author)),
					row.shared_articles,
					row.last_shared_year or "-",
					core_label(row.same_institution),
				)
				for row in rows
			),
		)
		return format_html(
			"<table><thead><tr><th>Author</th><th>Shared papers</th><th>Last shared year</th>"
			"<th>Core or external</th></tr></thead><tbody>{}</tbody></table>",
			body,
		)

	# -- merge ----------------------------------------------------------------------------

	def has_merge_permission(self, request):
		return request.user.is_superuser

	@admin.action(permissions=["merge"], description="Merge selected labs")
	def merge_selected_labs(self, request, queryset):
		if not self.has_merge_permission(request):
			raise PermissionDenied
		labs = list(queryset.order_by("-member_count", "pk"))
		if len(labs) < 2:
			self.message_user(request, "Select at least two labs to merge.", level=messages.ERROR)
			return
		if "apply" in request.POST:
			by_id = {str(lab.pk): lab for lab in labs}
			keep = by_id.get(request.POST.get("keep_lab"))
			if keep is None:
				self.message_user(request, "Choose which lab to keep.", level=messages.ERROR)
				return
			others = [lab for lab in labs if lab.pk != keep.pk]
			with transaction.atomic():
				moved = merge_labs(keep, others)
			self.message_user(
				request,
				f"Merged {len(others)} lab(s) into {keep}; {moved} membership(s) moved. "
				"The lab is now manual.",
			)
			return
		return render(
			request,
			"admin/gregory/lab/merge_confirmation.html",
			{
				"title": "Merge selected labs",
				"labs": labs,
				"default_keep_id": labs[0].pk,
				"opts": self.model._meta,
			},
		)


# --- the author page and list ------------------------------------------------------------


class AuthorLabMembershipForm(forms.ModelForm):
	class Meta:
		model = LabMembership
		fields = ["lab", "role", "same_institution"]

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		is_head = bool(self.instance.pk) and self.instance.role == HEAD
		choices = [(v, label) for v, label in LabMembership.Role.choices if v != HEAD or is_head]
		if not self.instance.pk:
			# Blank, so that choosing a role is what makes a new row "changed": an empty
			# lab plus a role pins the author out of any lab.
			choices = [("", "---------")] + choices
			self.initial["role"] = ""
		self.fields["role"].choices = choices

	def clean(self):
		cleaned = super().clean()
		if self.instance.pk and self.instance.role == HEAD and self.has_changed():
			raise ValidationError(
				"A lab's head is changed on the lab's page, not here."
			)
		# Set before the model is validated: a manual row is what the check constraint
		# allows a null lab on.
		self.instance.source = MANUAL
		return cleaned


class AuthorLabMembershipFormSet(BaseInlineFormSet):
	def clean(self):
		super().clean()
		for form in self.forms:
			if (
				self._should_delete_form(form)
				and form.instance.pk
				and form.instance.role == HEAD
			):
				raise ValidationError(
					"A lab's head can't be removed here. Change the head on the lab's page."
				)


class AuthorLabMembershipInline(SuperuserOnlyAdminMixin, admin.StackedInline):
	model = LabMembership
	fk_name = "author"
	form = AuthorLabMembershipForm
	formset = AuthorLabMembershipFormSet
	max_num = 1
	extra = 1
	can_delete = True
	autocomplete_fields = ["lab"]
	verbose_name = "lab"
	verbose_name_plural = (
		"Lab: saving makes the placement manual; leave the lab empty and pick a role to pin "
		"the author out of any lab; delete the row to let the next rebuild decide again"
	)
	fields = ["lab", "role", "same_institution", "source"]
	readonly_fields = ["source"]


class HasLabFilter(admin.SimpleListFilter):
	title = "has a lab"
	parameter_name = "has_lab"

	def lookups(self, request, model_admin):
		return [
			("yes", "In a lab"),
			("no", "No placement"),
			("pinned", "Pinned to no lab"),
		]

	def queryset(self, request, queryset):
		if self.value() == "yes":
			return queryset.filter(lab_membership__lab__isnull=False)
		if self.value() == "no":
			return queryset.filter(lab_membership__isnull=True)
		if self.value() == "pinned":
			return queryset.filter(lab_membership__isnull=False, lab_membership__lab__isnull=True)
		return queryset


class ProbableDuplicateFilter(admin.SimpleListFilter):
	title = "probable duplicate (same OpenAlex author)"
	parameter_name = "openalex_duplicate"

	def lookups(self, request, model_admin):
		return [("yes", "Shares an OpenAlex author with another row")]

	def queryset(self, request, queryset):
		if self.value() != "yes":
			return queryset
		twin = Authors.objects.filter(openalex_id=OuterRef("openalex_id")).exclude(
			pk=OuterRef("pk")
		)
		return (
			queryset.exclude(openalex_id__isnull=True)
			.exclude(openalex_id="")
			.filter(Exists(twin))
		)


def author_lab_display(author):
	try:
		membership = author.lab_membership
	except ObjectDoesNotExist:
		return "-"
	if membership.lab_id is None:
		return "No lab (pinned)"
	return f"{membership.lab} ({membership.get_role_display().lower()})"


class AssignToLabForm(forms.Form):
	lab = forms.ModelChoiceField(queryset=Lab.objects.all())
	role = forms.ChoiceField(
		choices=[(MEMBER, "Member"), (COLLABORATOR, "Collaborator")], initial=MEMBER
	)

	def __init__(self, *args, admin_site, **kwargs):
		super().__init__(*args, **kwargs)
		self.fields["lab"] = forms.ModelChoiceField(
			queryset=Lab.objects.all(),
			widget=AutocompleteSelect(LabMembership._meta.get_field("lab"), admin_site),
		)


def assign_authors_to_lab(model_admin, request, queryset):
	"""The author action "Assign selected authors to a lab…"."""
	if not model_admin.has_lab_permission(request):
		raise PermissionDenied
	authors = list(queryset.order_by("author_id"))
	if "apply" in request.POST:
		form = AssignToLabForm(request.POST, admin_site=model_admin.admin_site)
		if form.is_valid():
			lab, role = form.cleaned_data["lab"], form.cleaned_data["role"]
			assigned, skipped = _write_memberships(authors, lab=lab, role=role)
			model_admin.message_user(
				request, f"Assigned {assigned} author(s) to {lab} as {role}s (manual)."
			)
			if skipped:
				model_admin.message_user(
					request,
					f"Skipped {len(skipped)} lab head(s): {', '.join(skipped)}. "
					"Change a lab's head on the lab's page.",
					level=messages.WARNING,
				)
			return None
	else:
		form = AssignToLabForm(admin_site=model_admin.admin_site)
	return render(
		request,
		"admin/gregory/authors/assign_to_lab.html",
		{
			"title": "Assign selected authors to a lab",
			"authors": authors,
			"form": form,
			"opts": model_admin.model._meta,
		},
	)


def remove_authors_from_lab(model_admin, request, queryset):
	"""The author action "Remove selected authors from their lab": pins them to no lab."""
	if not model_admin.has_lab_permission(request):
		raise PermissionDenied
	authors = list(queryset.order_by("author_id"))
	pinned, skipped = _write_memberships(authors, lab=None, role=MEMBER)
	model_admin.message_user(request, f"Pinned {pinned} author(s) to no lab (manual).")
	if skipped:
		model_admin.message_user(
			request,
			f"Skipped {len(skipped)} lab head(s): {', '.join(skipped)}. "
			"Change a lab's head on the lab's page.",
			level=messages.WARNING,
		)


def _write_memberships(authors, *, lab, role):
	"""Write manual memberships for ``authors``, leaving lab heads alone. Returns the
	number written and the names skipped. One save per row so history records each."""
	existing = {
		m.author_id: m for m in LabMembership.objects.filter(author__in=[a.pk for a in authors])
	}
	written, skipped = 0, []
	with transaction.atomic():
		for author in authors:
			membership = existing.get(author.pk)
			if (membership and membership.role == HEAD) or (
				lab is not None and lab.head_id == author.pk
			):
				skipped.append(author_name(author))
				continue
			if membership is None:
				membership = LabMembership(author=author)
			membership.lab = lab
			if lab is not None:
				membership.role = role
			elif membership.pk is None:
				membership.role = MEMBER
			membership.same_institution = None
			membership.source = MANUAL
			membership.save()
			written += 1
	return written, skipped


# --- the article page --------------------------------------------------------------------


def article_lab_line(article):
	"""The lab of the article's last author, linked, or a dash. One query at most."""
	if not article.pk:
		return "-"
	membership = (
		LabMembership.objects.filter(
			author__authorships__article=article,
			author__authorships__position=ArticleAuthorship.Position.LAST,
			lab__isnull=False,
		)
		.select_related("lab", "lab__head")
		.first()
	)
	if membership is None:
		return "-"
	return format_html(
		'<a href="{}">{}</a>',
		reverse("admin:gregory_lab_change", args=[membership.lab_id]),
		str(membership.lab),
	)
