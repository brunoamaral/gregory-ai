"""
rss/feed_scope.py

Who may see which feed: the scope helpers every site-scoped feed in
rss/views.py shares.

A feed serves the REQUESTED SITE's CustomSetting.scope_subjects, not the
caller's own visibility -- these are crawler-and-reader-facing surfaces
like rss/sitemaps.py, not request-scoped ones like the rest of the API. A
feed reader has no identity and its response is cached, so the body must
not vary by who (or what) is asking. See docs/03-api-and-rss-feeds.md#rss-feeds
and rss/sitemaps.py's module docstring for the same reasoning applied to
sitemaps.

404 when the site doesn't exist, has no CustomSetting, or has
CustomSetting.rss_enabled=False -- matching how sitemaps 404 on
generate_sitemap=False -- or when the requested subject/category/author
does not resolve inside that site's scope.

Feeds are editorial content, so they are stricter than the API: retracted
articles never appear (article_base_qs). Those rules live here in rss/, not
in api/filters.py, so the API's behaviour stays a frontend concern.
"""

import logging

from django.contrib.sites.models import Site
from django.db.models import Exists, OuterRef
from django.http import Http404
from django.shortcuts import get_object_or_404

from gregory.models import Articles, Subject, TeamCategory, Trials
from sitesettings.models import CustomSetting

logger = logging.getLogger(__name__)


def site_feed_scope(site_id):
	"""Resolve the Site, its CustomSetting and its RSS-visible subject ids.

	404s exactly like rss/sitemaps.py's _site_sitemaps: unknown site, no
	CustomSetting row, or the feature switch off. Unlike sitemaps there is
	no "narrow to the public scope" step -- a feed's scope IS its site's
	scope_subjects regardless of whether that site is api_public, mirroring
	how a site-bound API key reads its own scope in
	gregory.visibility.visible_subject_ids.
	"""
	site = get_object_or_404(Site, pk=site_id)
	# CustomSetting.site is a plain FK (not unique) -- order explicitly so
	# the chosen row is deterministic if more than one ever exists for a site.
	settings_row = CustomSetting.objects.filter(site=site).order_by("setting_id").first()
	if settings_row is None or not settings_row.rss_enabled:
		raise Http404("RSS feed not enabled for this site.")
	subject_ids = set(settings_row.scope_subjects.values_list("id", flat=True))
	return site, settings_row, subject_ids


def resolve_scoped_subject(subject_ids, slug):
	"""The one Subject with this slug among the site's scope subjects.

	Subject.subject_slug is unique per TEAM (unique_team_subject_slug), not
	globally, so an unscoped lookup raises MultipleObjectsReturned (a 500) as
	soon as two teams share a slug. Looking the slug up only among the
	site's own scope subjects removes that. Two subjects in the SAME scope
	sharing a slug is a curation error: 404 and log it rather than silently
	guess which one the reader meant.
	"""
	matches = list(Subject.objects.filter(subject_slug=slug, id__in=subject_ids)[:2])
	if not matches:
		raise Http404("No such subject in this site's scope.")
	if len(matches) > 1:
		logger.warning(
			"Ambiguous feed subject slug %r: more than one subject in the site's scope %s uses it.",
			slug,
			sorted(subject_ids),
		)
		raise Http404("Ambiguous subject slug.")
	return matches[0]


def resolve_scoped_category(subject_ids, slug):
	"""(category, cat_subject_ids) for a category slug, or 404.

	TeamCategory.category_slug is globally unique. A category is in scope
	when its subjects overlap the site's scope (the rule CategoryViewSet
	uses); cat_subject_ids is that overlap, and is the subject set the feed
	must be built from. An article can sit in a category through a subject
	the site doesn't cover, and it must not leak through that way.
	"""
	category = TeamCategory.objects.filter(category_slug=slug).first()
	if category is None:
		raise Http404("No such category.")
	cat_subject_ids = set(category.subjects.values_list("id", flat=True)) & set(subject_ids)
	if not cat_subject_ids:
		raise Http404("Category is not in this site's scope.")
	return category, cat_subject_ids


def article_base_qs(subject_ids):
	"""Non-retracted articles tagged with at least one of subject_ids.

	Membership is an Exists() rather than a join + .distinct(), as in
	rss/sitemaps.py, so the outer query can order and slice cheaply.
	A retracted paper doesn't belong in a "new research" feed.
	"""
	return Articles.objects.filter(
		Exists(Articles.objects.filter(pk=OuterRef("pk"), subjects__in=subject_ids)),
		retracted=False,
	)


def trial_base_qs(subject_ids):
	"""Trials tagged with at least one of subject_ids (trials can't be retracted)."""
	return Trials.objects.filter(
		Exists(Trials.objects.filter(pk=OuterRef("pk"), subjects__in=subject_ids))
	)
