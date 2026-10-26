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
from datetime import timedelta

from django.contrib.sites.models import Site
from django.db.models import Exists, OuterRef
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone

from api.medicine_scope import category_has_subject
from gregory.models import (
	Articles,
	MedicineCategoryAssignment,
	MedicineEvent,
	MedicineSubjectLink,
	Patents,
	Subject,
	TeamCategory,
	Trials,
)
from sitesettings.models import CustomSetting

logger = logging.getLogger(__name__)


#: A medicine feed lists a ``reconstructed`` event only while it is this recent.
#: Reconstructed events are read off EMA's date fields on a medicine's first sync,
#: so without the cut-off the first sync would flood a feed with every approval
#: back to 1995. ``observed`` events are always listed.
RECONSTRUCTED_EVENT_DAYS = 90


def site_feed_scope(site_id, require_patents=False, require_medicines=False):
	"""Resolve the Site, its CustomSetting and its RSS-visible subject ids.

	404s exactly like rss/sitemaps.py's _site_sitemaps: unknown site, no
	CustomSetting row, or the feature switch off. Unlike sitemaps there is
	no "narrow to the public scope" step -- a feed's scope IS its site's
	scope_subjects regardless of whether that site is api_public, mirroring
	how a site-bound API key reads its own scope in
	gregory.visibility.visible_subject_ids.

	require_patents=True (the patent feeds) also needs CustomSetting.patents_enabled:
	patents come from the EPO's OPS under a licence that only covers some
	sites, so a site with the switch off serves no patents anywhere -- not on
	the API, not in the MCP server and not here -- even with rss_enabled on and
	the subject in scope. See docs/patents.md, "Licence and the API".

	require_medicines=True (the medicine feeds) likewise needs
	CustomSetting.medicines_enabled, which is off until a site is switched on.
	"""
	site = get_object_or_404(Site, pk=site_id)
	# CustomSetting.site is a plain FK (not unique) -- order explicitly so
	# the chosen row is deterministic if more than one ever exists for a site.
	settings_row = CustomSetting.objects.filter(site=site).order_by("setting_id").first()
	if settings_row is None or not settings_row.rss_enabled:
		raise Http404("RSS feed not enabled for this site.")
	if require_patents and not settings_row.patents_enabled:
		raise Http404("Patents are not enabled for this site.")
	if require_medicines and not settings_row.medicines_enabled:
		raise Http404("Medicines are not enabled for this site.")
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


def patent_base_qs(subject_ids):
	"""Patent families tagged with at least one of subject_ids and published.

	A family with no earliest_publication_date can't be placed in a feed
	ordered by publication, so it is left out.

	Feeds order by earliest_publication_date, NOT discovery_date (the one
	place they don't). The importer backfills history back to 2016 priority
	dates, so discovery_date says nothing about when a patent is new: on dev
	all 2,649 families were discovered inside one month, and only 38 of them
	were published in the last 90 days. A discovery-ordered feed would be 50
	random old patents, reshuffled by every backfill run.
	"""
	return Patents.objects.filter(
		Exists(Patents.objects.filter(pk=OuterRef("pk"), subjects__in=subject_ids)),
		earliest_publication_date__isnull=False,
	)


def category_subject_map(subject_ids):
	"""{category id: the subject ids it shares with subject_ids}, one query.

	Every category with at least one subject in the site's scope, i.e. the
	categories a site-wide feed covers. The same overlap resolve_scoped_category
	returns for one category, for all of them.
	"""
	through = TeamCategory.subjects.through
	mapping = {}
	for category_id, subject_id in through.objects.filter(
		subject_id__in=subject_ids
	).values_list("teamcategory_id", "subject_id"):
		mapping.setdefault(category_id, set()).add(subject_id)
	return mapping


def _curated_medicine_events(*conditions):
	"""Events of the medicines a feed may list, narrowed by ``conditions``.

	The exclusions every medicine feed shares, in one place so a feed can't forget one:
	  * generic and biosimilar products: the feed is for news about the originator, not
	    for each copy of a drug that is already on the market;
	  * grouped copies (``grouped_under`` set): EMA authorises one medicine under several
	    names (Exelon, Prometax, Rivastigmine Sandoz), and only the lead reports an event,
	    so one European Commission decision isn't announced once per copy;
	  * ``reconstructed`` events older than RECONSTRUCTED_EVENT_DAYS (see above).

	``conditions`` are the filters that say which medicines the feed is about.
	"""
	cutoff = timezone.localdate() - timedelta(days=RECONSTRUCTED_EVENT_DAYS)
	return MedicineEvent.objects.filter(
		*conditions,
		medicine__generic=False,
		medicine__biosimilar=False,
		medicine__grouped_under__isnull=True,
	).exclude(basis=MedicineEvent.Basis.RECONSTRUCTED, event_date__lt=cutoff)


def medicine_event_base_qs(category_subject_map):
	"""Medicine events a feed may list, for the categories in category_subject_map.

	category_subject_map is {category id: its subject ids inside the site's
	scope}, as category_subject_map() and resolve_scoped_category() give it.
	A category whose set is empty is outside the scope and contributes nothing.

	An event is in when its medicine has a NON-SUPPRESSED link to one of those
	categories. Feeds are editorial, so a link an editor suppressed hides the
	medicine, as it does on the API. Membership is an Exists() rather than a
	join + .distinct(), as for the other feeds, so a medicine in several of the
	categories is not repeated and the outer query can order and slice cheaply.

	The site and category feeds are curated: a medicine EMA approved for a condition
	but that no category tracks is not in them (medicine_subject_event_base_qs has it).
	The shared exclusions (generics, biosimilars, grouped copies, old reconstructed
	events) are those of _curated_medicine_events.
	"""
	category_ids = [
		category_id for category_id, subjects in category_subject_map.items() if subjects
	]
	return _curated_medicine_events(
		Exists(
			MedicineCategoryAssignment.objects.filter(
				medicine=OuterRef("medicine"),
				teamcategory__in=category_ids,
				suppressed=False,
			)
		)
	)


def medicine_subject_event_base_qs(subject_id):
	"""Medicine events for one subject: every medicine EMA approved for its condition.

	An event is in when its medicine has EITHER
	  * a NON-SUPPRESSED link to a category that has this subject (a medicine an editor
	    tracks), OR
	  * a NON-SUPPRESSED MedicineSubjectLink to this subject (a medicine EMA approved for
	    one of the subject's conditions, with or without a category).

	The two suppressions are independent, as on the API (api/medicine_scope.py): a
	suppressed subject link next to a live category link leaves the medicine in, and the
	other way round. Both are Exists(), never a join + .distinct(), so a medicine reached
	by both routes is listed once per event.

	This is the one medicine feed that includes untracked medicines. The exclusions are
	those of _curated_medicine_events.
	"""
	return _curated_medicine_events(
		Exists(
			MedicineCategoryAssignment.objects.filter(
				category_has_subject([subject_id]),
				medicine=OuterRef("medicine"),
				suppressed=False,
			)
		)
		| Exists(
			MedicineSubjectLink.objects.filter(
				medicine=OuterRef("medicine"), subject_id=subject_id, suppressed=False
			)
		)
	)
