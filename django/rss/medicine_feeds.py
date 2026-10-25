"""
rss/medicine_feeds.py

EMA medicine event feeds, under /feed/sites/<site_id>/medicines/:

  category/<slug>/       regulatory events of the medicines linked to a category
  (the root)             the same, for every category in the site's scope

An item is one MedicineEvent: a CHMP opinion, an authorisation, a new European
Commission decision, a withdrawal. The feeds are how a partner hears about an
approval without polling the API (rss/feed_scope.py has the rules as code).
Where this module and the article and trial feeds disagree, this one wins for
medicines:

  * BOTH switches: a medicine feed needs CustomSetting.rss_enabled AND
    medicines_enabled, which is off until a site is switched on. With either
    off it is a 404, as the patent feeds are. A category outside the site's
    scope is a 404 too (resolve_scoped_category).
  * Which medicines: those with a NON-SUPPRESSED link to the category, where
    the category has at least one subject in the site's scope. Feeds are
    editorial, so an editor's suppression hides a medicine, the same as on
    the API. Generic and biosimilar products are left out.
  * Which events: ``observed`` events always, ``reconstructed`` events only
    from the last 90 days. A reconstructed event is read off EMA's date fields
    the first time a medicine is seen, so without the cut-off the first sync
    would flood every feed with approvals back to 1995. Within the window a
    reconstructed event is as real as an observed one.
  * Ordered by event date, newest first (ties by newest event id), 50 items.
    <pubDate> is the event date at midnight UTC.
  * Processed text only: EMA's own cleaned indication and the source line
    "Source: European Medicines Agency", which EMA's legal notice asks for
    wherever its data is served. The link is the medicine's EPAR page.
  * No relevance judgement exists for medicines, so there are no /relevant/
    variants, and there is no search feed.
"""

from types import SimpleNamespace

from django.contrib.syndication.views import Feed

from rss.feed_items import MedicineEventFeedItemMixin, hydrate_medicine_events
from rss.feed_scope import (
	category_subject_map,
	medicine_event_base_qs,
	resolve_scoped_category,
	site_feed_scope,
)
from rss.views import _homepage, _page_ids, _SelfLinkMixin

MEDICINE_FEED_LIMIT = 50

# The sentence every medicine channel carries.
_SOURCE_NOTE = (
	"Regulatory events of centrally authorised medicines, from the European Medicines "
	"Agency (https://www.ema.europa.eu/en/medicines). Generic and biosimilar products "
	"are left out."
)


def _event_order(queryset):
	"""Newest event first; the event id breaks ties deterministically."""
	return queryset.order_by("-event_date", "-id")


class SiteMedicineEventsByCategoryFeed(_SelfLinkMixin, MedicineEventFeedItemMixin, Feed):
	"""feed/sites/<site_id>/medicines/category/<category_slug>/ -- see module docstring."""

	def get_object(self, request, site_id, category_slug):
		site, _settings_row, scope = site_feed_scope(site_id, require_medicines=True)
		category, cat_subject_ids = resolve_scoped_category(scope, category_slug)

		# Per-request state goes on obj, not the shared Feed instance.
		category._site = site
		category._cat_subject_ids = cat_subject_ids
		category._feed_url = self._request_url(request)
		return category

	def title(self, obj):
		return f"Medicines: {obj.category_name} — {obj._site.name}"

	def link(self, obj):
		return _homepage(obj._site)

	def description(self, obj):
		return f"New regulatory events on {obj.category_name}. {_SOURCE_NOTE}"

	def items(self, obj):
		queryset = medicine_event_base_qs({obj.pk: obj._cat_subject_ids})
		return hydrate_medicine_events(_page_ids(_event_order(queryset), MEDICINE_FEED_LIMIT))


class SiteMedicineEventsFeed(_SelfLinkMixin, MedicineEventFeedItemMixin, Feed):
	"""feed/sites/<site_id>/medicines/ -- every category that overlaps the site's scope."""

	def get_object(self, request, site_id):
		site, _settings_row, scope = site_feed_scope(site_id, require_medicines=True)

		# There is no subject or category to hang per-request state on, so a
		# small object carries it instead of the shared Feed instance.
		return SimpleNamespace(
			_site=site,
			_categories=category_subject_map(scope),
			_feed_url=self._request_url(request),
		)

	def title(self, obj):
		return f"Medicine approvals — {obj._site.name}"

	def link(self, obj):
		return _homepage(obj._site)

	def description(self, obj):
		return f"New regulatory events across the site's categories. {_SOURCE_NOTE}"

	def items(self, obj):
		queryset = medicine_event_base_qs(obj._categories)
		return hydrate_medicine_events(_page_ids(_event_order(queryset), MEDICINE_FEED_LIMIT))
