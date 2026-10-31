"""
rss/medicine_feeds.py

Medicine event feeds (EMA and FDA), under /feed/sites/<site_id>/medicines/:

  category/<slug>/       regulatory events of the medicines linked to a category
  (the root)             the same, for every category in the site's scope
  subject/<slug>/        regulatory events of every medicine approved for a condition

An item is one MedicineEvent: at EMA a CHMP opinion, an authorisation, a new European
Commission decision, a withdrawal; at the FDA an approval, a new indication or use, a
discontinuation. The feeds are how a partner hears about an approval without polling the
API (rss/feed_scope.py has the rules as code). Where this module and the article and
trial feeds disagree, this one wins for medicines:

  * Both regulators, one feed. Every feed lists the events of EMA's and the FDA's
    medicines together; ``?regulator=ema`` or ``?regulator=fda`` narrows it to one
    (an empty value counts as absent; anything else is a 404). cache_page keys on the full URL, so each value has its
    own cached copy and the narrowed feed never serves the unfiltered one.

  * BOTH switches: a medicine feed needs CustomSetting.rss_enabled AND
    medicines_enabled, which is off until a site is switched on. With either
    off it is a 404, as the patent feeds are. A category outside the site's
    scope is a 404 too (resolve_scoped_category).
  * Which medicines: those with a NON-SUPPRESSED link to the category, where
    the category has at least one subject in the site's scope. Feeds are
    editorial, so an editor's suppression hides a medicine, the same as on
    the API. Generic and biosimilar products are left out, and so are grouped
    copies (one medicine EMA authorises under several names): the lead of the
    group reports the event for all of them.
  * The subject feed is the one feed that includes UNTRACKED medicines: those
    with a live category link to a category of the subject, or a non-suppressed
    MedicineSubjectLink to it (EMA approved them for the subject's condition,
    no category needed; the FDA rows have no subject links, so they reach this feed
    through a category only). The site and category feeds stay curated: category
    links only. The subject must be in the site's scope (resolve_scoped_subject).
  * Which events: ``observed`` events always, ``reconstructed`` events only
    from the last 90 days, for either regulator. A reconstructed event is read off
    the regulator's dates (EMA's date fields, the FDA's submission dates) the first
    time a medicine is seen, so without the cut-off the first sync would flood every
    feed with approvals back to 1995. Within the window a reconstructed event is as
    real as an observed one.
  * Ordered by event date, newest first (ties by newest event id), 50 items.
    <pubDate> is the event date at midnight UTC.
  * Processed text only: the regulator's own cleaned indication (for the FDA, the
    label's) and the source line of the event's regulator, "Source: European
    Medicines Agency" or "Source: U.S. Food and Drug Administration", which each
    asks for wherever its data is served. An FDA description that carries the
    label's indication text credits its publisher as well: "Source: U.S. Food and
    Drug Administration; label text from DailyMed (U.S. National Library of
    Medicine)". FDA titles read like EMA's: the product name capitalised (openFDA
    stores it in capitals) and the lowercase substances in the brackets. The link is the medicine's EPAR page, or
    for the FDA its Drugs@FDA page (a CBER biologic: its DailyMed label).
  * No relevance judgement exists for medicines, so there are no /relevant/
    variants, and there is no search feed.
"""

from types import SimpleNamespace

from django.contrib.syndication.views import Feed

from rss.feed_items import MedicineEventFeedItemMixin, hydrate_medicine_events
from rss.feed_scope import (
	category_subject_map,
	medicine_event_base_qs,
	medicine_subject_event_base_qs,
	regulator_of,
	resolve_scoped_category,
	resolve_scoped_subject,
	site_feed_scope,
)
from rss.views import _homepage, _page_ids, _SelfLinkMixin

MEDICINE_FEED_LIMIT = 50

# The sentence every medicine channel carries, by the regulator the feed is narrowed to
# (None: both).
_SOURCE_NOTES = {
	None: (
		"Regulatory events of medicines, from the European Medicines Agency "
		"(https://www.ema.europa.eu/en/medicines) and the U.S. Food and Drug "
		"Administration (https://www.accessdata.fda.gov/scripts/cder/daf/). Generic and "
		"biosimilar products are left out."
	),
	"ema": (
		"Regulatory events of centrally authorised medicines, from the European Medicines "
		"Agency (https://www.ema.europa.eu/en/medicines). Generic and biosimilar products "
		"are left out."
	),
	"fda": (
		"Regulatory events of drugs and biologics approved in the US (NDAs and BLAs), from "
		"the U.S. Food and Drug Administration "
		"(https://www.accessdata.fda.gov/scripts/cder/daf/). Generic and biosimilar "
		"products are left out."
	),
}
_TITLE_TAGS = {None: "", "ema": " (EMA)", "fda": " (FDA)"}


def _event_order(queryset):
	"""Newest event first; the event id breaks ties deterministically."""
	return queryset.order_by("-event_date", "-id")


class _RegulatorSelfLinkMixin(_SelfLinkMixin):
	"""``<atom:link rel="self">`` keeps ``?regulator=`` when the feed is narrowed, so a
	reader that follows it gets the same feed. ``get_object`` reads the regulator once
	(``regulator_of``, which 404s on an unknown value) and passes it here."""

	@staticmethod
	def _feed_url(request, regulator):
		url = request.build_absolute_uri(request.path)
		return url if regulator is None else f"{url}?regulator={regulator}"


class SiteMedicineEventsByCategoryFeed(
	_RegulatorSelfLinkMixin, MedicineEventFeedItemMixin, Feed
):
	"""feed/sites/<site_id>/medicines/category/<category_slug>/ -- see module docstring."""

	def get_object(self, request, site_id, category_slug):
		site, _settings_row, scope = site_feed_scope(site_id, require_medicines=True)
		category, cat_subject_ids = resolve_scoped_category(scope, category_slug)

		# Per-request state goes on obj, not the shared Feed instance.
		category._site = site
		category._cat_subject_ids = cat_subject_ids
		category._regulator = regulator_of(request)
		category._feed_url = self._feed_url(request, category._regulator)
		return category

	def title(self, obj):
		return f"Medicines{_TITLE_TAGS[obj._regulator]}: {obj.category_name} — {obj._site.name}"

	def link(self, obj):
		return _homepage(obj._site)

	def description(self, obj):
		return f"New regulatory events on {obj.category_name}. {_SOURCE_NOTES[obj._regulator]}"

	def items(self, obj):
		queryset = medicine_event_base_qs({obj.pk: obj._cat_subject_ids}, obj._regulator)
		return hydrate_medicine_events(_page_ids(_event_order(queryset), MEDICINE_FEED_LIMIT))


class SiteMedicineEventsBySubjectFeed(
	_RegulatorSelfLinkMixin, MedicineEventFeedItemMixin, Feed
):
	"""feed/sites/<site_id>/medicines/subject/<subject_slug>/ -- see module docstring."""

	def get_object(self, request, site_id, subject_slug):
		site, _settings_row, scope = site_feed_scope(site_id, require_medicines=True)
		subject = resolve_scoped_subject(scope, subject_slug)

		# Per-request state goes on obj, not the shared Feed instance.
		subject._site = site
		subject._regulator = regulator_of(request)
		subject._feed_url = self._feed_url(request, subject._regulator)
		return subject

	def title(self, obj):
		return f"Medicines{_TITLE_TAGS[obj._regulator]}: {obj.subject_name} — {obj._site.name}"

	def link(self, obj):
		return _homepage(obj._site)

	def description(self, obj):
		return (
			f"New regulatory events of the medicines approved for {obj.subject_name}, "
			f"whether or not they're filed under a category. {_SOURCE_NOTES[obj._regulator]}"
		)

	def items(self, obj):
		queryset = medicine_subject_event_base_qs(obj.pk, obj._regulator)
		return hydrate_medicine_events(_page_ids(_event_order(queryset), MEDICINE_FEED_LIMIT))


class SiteMedicineEventsFeed(_RegulatorSelfLinkMixin, MedicineEventFeedItemMixin, Feed):
	"""feed/sites/<site_id>/medicines/ -- every category that overlaps the site's scope."""

	def get_object(self, request, site_id):
		site, _settings_row, scope = site_feed_scope(site_id, require_medicines=True)

		# There is no subject or category to hang per-request state on, so a
		# small object carries it instead of the shared Feed instance.
		regulator = regulator_of(request)
		return SimpleNamespace(
			_site=site,
			_categories=category_subject_map(scope),
			_feed_url=self._feed_url(request, regulator),
			_regulator=regulator,
		)

	def title(self, obj):
		return f"Medicine approvals{_TITLE_TAGS[obj._regulator]} — {obj._site.name}"

	def link(self, obj):
		return _homepage(obj._site)

	def description(self, obj):
		return (
			f"New regulatory events across the site's categories. {_SOURCE_NOTES[obj._regulator]}"
		)

	def items(self, obj):
		queryset = medicine_event_base_qs(obj._categories, obj._regulator)
		return hydrate_medicine_events(_page_ids(_event_order(queryset), MEDICINE_FEED_LIMIT))
