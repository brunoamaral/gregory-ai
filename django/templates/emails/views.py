"""
Django views for email template rendering and preview functionality.
Provides endpoints for previewing email templates with real or mock data.
"""

import types
import uuid
from datetime import date as date_type, timedelta

from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.sites.models import Site
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.template.loader import get_template
from django.utils import timezone
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.http import require_http_methods

from gregory.models import Articles, Trials
from sitesettings.models import CustomSetting
from subscriptions.management.commands.utils.get_credentials import (
	build_unsubscribe_base_url,
)
from subscriptions.management.commands.utils.subscription import (
	get_articles_for_list,
	get_trials_for_list,
	rank_and_limit_articles,
	select_digest_articles,
)
from subscriptions.models import Lists, Subscribers
from templates.emails.components.content_organizer import get_optimized_email_context

# ── Helpers ─────────────────────────────────────────────────────────────────


def _make_mock_subscriber():
	"""Return a SimpleNamespace that satisfies all template attribute accesses."""
	return types.SimpleNamespace(
		subscriber_id=0,
		first_name="Preview",
		last_name="User",
		email="preview@example.com",
		active=True,
		unsubscribe_token=uuid.uuid4(),
	)


def _resolve_date_range(request, default_days=30):
	"""
	Parse GET params into (start_date, end_date).
	Priority: explicit start/end > days param > default_days.
	"""
	start_str = request.GET.get("start", "")
	end_str = request.GET.get("end", "")
	if start_str and end_str:
		try:
			start_date = date_type.fromisoformat(start_str)
			end_date = date_type.fromisoformat(end_str)
			if end_date < start_date:
				start_date, end_date = end_date, start_date
			return start_date, end_date
		except ValueError:
			pass

	try:
		days = int(request.GET.get("days", default_days))
	except (ValueError, TypeError):
		days = default_days
	end_date = timezone.now().date()
	start_date = end_date - timedelta(days=days - 1)
	return start_date, end_date


def _resolve_days_to_look_back(request, list_obj):
	"""
	Days-back window for list-scoped selection, mirroring how the send
	commands resolve it: an explicit `days` GET param overrides everything
	(like each command's `--days`/CLI override), otherwise the list's own
	`lookback_days` is used — never a hardcoded default — so a relevancy-mode
	list previews with the same window a real send would use.
	"""
	days_param = request.GET.get("days")
	if days_param:
		try:
			return int(days_param)
		except (ValueError, TypeError):
			pass
	if list_obj is not None:
		return list_obj.lookback_days
	return 30


def _get_site_and_settings(list_obj=None):
	"""Resolve site + CustomSetting, mirroring the management command fallback chain."""
	if list_obj is not None and list_obj.site_id:
		site = list_obj.site
	else:
		site = Site.objects.get_current()
	try:
		custom_settings = CustomSetting.objects.get(site=site)
	except CustomSetting.DoesNotExist:
		custom_settings = None
	return site, custom_settings


def _build_preview_context(request, template_name):
	"""
	Core logic shared by the HTML preview and JSON context endpoints.
	Accepts GET params: list_id, subscriber_id, days, start, end.
	Returns a context dict or raises ValueError for unknown template names.
	"""
	if template_name not in (
		"weekly_summary",
		"admin_summary",
		"trial_notification",
		"test_components",
	):
		raise ValueError(f"Unknown template: {template_name}")

	# --- Subscriber ---
	subscriber_id = request.GET.get("subscriber_id")
	if subscriber_id:
		try:
			subscriber = Subscribers.objects.get(pk=int(subscriber_id))
		except (Subscribers.DoesNotExist, ValueError, TypeError):
			subscriber = _make_mock_subscriber()
	else:
		subscriber = _make_mock_subscriber()

	# --- List ---
	list_id = request.GET.get("list_id")
	list_obj = None
	if list_id:
		try:
			list_obj = (
				Lists.objects.select_related("team__organization")
				.prefetch_related("subjects")
				.get(pk=int(list_id))
			)
		except (Lists.DoesNotExist, ValueError, TypeError):
			pass

	organization = (
		list_obj.team.organization if list_obj is not None and list_obj.team else None
	)

	# --- Site & settings ---
	site, custom_settings = _get_site_and_settings(list_obj)

	email_type = (
		template_name if template_name != "test_components" else "weekly_summary"
	)

	# --- Articles & Trials ---
	# Mirrors the real send path per email type — same lookback window,
	# article_sort_order, limits, and staleness filter the command uses —
	# rather than a generic date-range query, so a relevancy-mode list
	# previews the same article set a real send would select. Falls back to
	# a generic recent-content query only when no list is selected (nothing
	# for a list-scoped query to be relative to).
	if list_obj is not None and list_obj.subjects.exists():
		days_to_look_back = _resolve_days_to_look_back(request, list_obj)
		article_limit = getattr(list_obj, "article_limit", 15) or 15
		trial_limit = getattr(list_obj, "trial_limit", 15) or 15

		if email_type == "weekly_summary":
			candidate_articles, priority_scores = select_digest_articles(
				list_obj,
				days_to_look_back,
				all_articles=False,
				threshold=list_obj.ml_threshold,
			)
			if candidate_articles.count() > article_limit:
				article_qs = rank_and_limit_articles(
					candidate_articles,
					article_limit,
					list_obj.article_sort_order,
					False,
					priority_scores,
				)
			else:
				article_qs = list(candidate_articles)
		elif email_type == "admin_summary":
			article_qs = list(
				get_articles_for_list(list_obj, days=days_to_look_back)
				.prefetch_related("authors")
				.order_by("-discovery_date")[:article_limit]
			)
		else:  # trial_notification carries no article content
			article_qs = []

		trial_qs = list(
			get_trials_for_list(list_obj, days=days_to_look_back).order_by(
				"-discovery_date"
			)[:trial_limit]
		)
	else:
		start_date, end_date = _resolve_date_range(request)
		article_qs = list(
			Articles.objects.filter(
				discovery_date__date__gte=start_date,
				discovery_date__date__lte=end_date,
			)
			.prefetch_related(
				"authors",
				"ml_predictions__subject",
				"article_subject_relevances__subject",
			)
			.order_by("-discovery_date")[:50]
		)
		trial_qs = list(
			Trials.objects.filter(
				discovery_date__date__gte=start_date,
				discovery_date__date__lte=end_date,
			).order_by("-discovery_date")[:20]
		)

	context = get_optimized_email_context(
		email_type=email_type,
		articles=article_qs,
		trials=trial_qs,
		subscriber=subscriber,
		list_obj=list_obj,
		site=site,
		custom_settings=custom_settings,
		organization=organization,
	)

	# Inject unsubscribe footer helpers (same as management commands post-call)
	if list_obj:
		context["list_id"] = list_obj.list_id
		context["header_title"] = list_obj.header_title or ""
		context["header_tagline"] = list_obj.header_tagline or ""
		context["show_header_tagline"] = list_obj.show_header_tagline
	context["unsubscribe_base_url"] = build_unsubscribe_base_url(site, custom_settings)
	context["subscriber"] = subscriber

	return context


# ── Public endpoints ─────────────────────────────────────────────────────────


@staff_member_required
def email_preview_dashboard(request):
	"""Dashboard for previewing email templates. Requires staff authentication."""
	context = {
		"email_types": [
			("weekly_summary", "Weekly Summary"),
			("admin_summary", "Admin Summary"),
			("trial_notification", "Clinical Trials"),
			("test_components", "Component Test"),
		]
	}
	return render(request, "emails/email_preview.html", context)


@staff_member_required
@xframe_options_exempt
@require_http_methods(["GET"])
def email_template_preview(request, template_name):
	"""
	Render an email template with real or mock data.
	GET params: list_id, subscriber_id, days (default 30), start (YYYY-MM-DD), end (YYYY-MM-DD)
	"""
	try:
		context = _build_preview_context(request, template_name)
	except ValueError as exc:
		return HttpResponse(str(exc), status=404)
	except Exception as exc:
		return HttpResponse(f"Error building preview context: {exc}", status=500)

	try:
		tmpl = get_template(f"emails/{template_name}.html")
		rendered = tmpl.render(context)
		return HttpResponse(rendered, content_type="text/html")
	except Exception as exc:
		return HttpResponse(f"Error rendering template: {exc}", status=500)


@staff_member_required
@require_http_methods(["GET"])
def email_template_json_context(request, template_name):
	"""
	Return the context that would be used for a template as JSON.
	Same GET params as email_template_preview.
	"""
	try:
		context = _build_preview_context(request, template_name)
	except ValueError as exc:
		return JsonResponse({"error": str(exc)}, status=404)
	except Exception as exc:
		return JsonResponse({"error": str(exc)}, status=500)

	def _serialise(value):
		if hasattr(value, "isoformat"):
			return value.isoformat()
		if hasattr(value, "__dict__") and not isinstance(value, type):
			return str(value)
		if hasattr(value, "__iter__") and not isinstance(value, (str, bytes, dict)):
			return [_serialise(v) for v in value]
		return value

	serialised = {k: _serialise(v) for k, v in context.items()}
	return JsonResponse(serialised, json_dumps_params={"indent": 2})


@staff_member_required
@require_http_methods(["GET"])
def email_preview_lists(request):
	"""
	Return lists available for preview filtered by email type.
	GET param: email_type = weekly_summary | admin_summary | trial_notification
	"""
	email_type = request.GET.get("email_type", "weekly_summary")

	type_filter = {
		"weekly_summary": {"weekly_digest": True},
		"admin_summary": {"admin_summary": True},
		"trial_notification": {"clinical_trials_notifications": True},
	}.get(email_type, {"weekly_digest": True})

	qs = (
		Lists.objects.filter(**type_filter)
		.select_related("team")
		.prefetch_related("subjects")
		.order_by("list_name")
	)

	data = [
		{
			"id": lst.list_id,
			"name": lst.list_name,
			"team_name": lst.team.name if lst.team else "",
			"subject_names": [s.subject_name for s in lst.subjects.all()],
		}
		for lst in qs
	]
	return JsonResponse({"lists": data})


@staff_member_required
@require_http_methods(["GET"])
def email_preview_subscribers(request):
	"""
	Return active subscribers matching a search term and/or list (max 100).
	GET params: q (search string), list_id (optional)
	"""
	from django.db.models import Q as DQ

	q = request.GET.get("q", "").strip()
	list_id = request.GET.get("list_id", "")

	qs = Subscribers.objects.filter(active=True)

	if list_id:
		try:
			qs = qs.filter(
				list_subscriptions__list_id=int(list_id),
				list_subscriptions__is_active=True,
			).distinct()
		except (ValueError, TypeError):
			pass

	if q:
		qs = qs.filter(
			DQ(first_name__icontains=q)
			| DQ(last_name__icontains=q)
			| DQ(email__icontains=q)
		)

	qs = qs.order_by("first_name", "last_name")[:100]

	data = [
		{
			"id": s.subscriber_id,
			"display_name": f"{s.first_name} {s.last_name or ''}".strip(),
			"email": s.email,
		}
		for s in qs
	]
	return JsonResponse({"subscribers": data})


# ── Legacy compatibility helpers used by management commands ─────────────────


def get_email_context_for_management_command(
	email_type,
	articles=None,
	trials=None,
	subscriber=None,
	site=None,
	customsettings=None,
):
	return get_optimized_email_context(
		email_type=email_type,
		articles=articles,
		trials=trials,
		subscriber=subscriber,
		site=site,
		custom_settings=customsettings,
	)


def prepare_email_context(
	email_type,
	articles=None,
	trials=None,
	subscriber=None,
	list_obj=None,
	site=None,
	custom_settings=None,
	admin_email=None,
):
	return get_optimized_email_context(
		email_type=email_type,
		articles=articles,
		trials=trials,
		subscriber=subscriber,
		list_obj=list_obj,
		site=site,
		custom_settings=custom_settings,
	)
