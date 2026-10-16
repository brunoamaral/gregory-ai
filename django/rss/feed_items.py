"""
rss/feed_items.py

What a feed item looks like: description builders, the item_* mixins and
the hydration helpers that load a page of items with a fixed number of
queries.

Feeds pick the ids of the items first (a cheap ordered, sliced query on the
base queryset), then hydrate_articles()/hydrate_trials() load the full rows
with prefetches, so a 100-item feed costs the same handful of queries as a
3-item one instead of several per item.

Every piece of database text that ends up inside a description is
HTML-escaped first. Editorial text is free text and registry/abstract text
comes from third parties; neither may be able to inject markup into a feed
that readers render as HTML.
"""

from django.db.models import Prefetch
from django.template.defaultfilters import linebreaksbr
from django.utils.html import escape, format_html, format_html_join

from gregory.models import (
	Articles,
	ArticleSiteContent,
	Subject,
	TeamCategory,
	Trials,
)

# dc:creator shows at most this many names, then "et al."
MAX_FEED_AUTHORS = 3


def trial_url(trial, site, settings_row):
	"""Where a trial item should link to.

	The site's own trial page when the site has trial pages
	(CustomSetting.sitemap_include_trials, which means "this frontend
	publishes /trials/<trial_id>/"), the registry link otherwise.
	"""
	if settings_row.sitemap_include_trials:
		return f"https://{site.domain}/trials/{trial.trial_id}/"
	return trial.link


def _text_block(text):
	"""Escape database text and turn its line breaks into <br>."""
	return linebreaksbr(text)


def _section(heading, body):
	return format_html("<p><strong>{}</strong><br>{}</p>", heading, body)


def _join_sections(sections):
	return format_html_join("", "{}", ((section,) for section in sections))


def author_label(names):
	"""First MAX_FEED_AUTHORS names joined with ", ", plus " et al." when there are more."""
	names = [name for name in names if name]
	if not names:
		return None
	label = ", ".join(names[:MAX_FEED_AUTHORS])
	if len(names) > MAX_FEED_AUTHORS:
		label += " et al."
	return label


def article_description(article):
	"""HTML description for an article item, sections omitted when empty.

	Order: plain-English summary, key takeaways, a metadata row, abstract.
	The editorial sections are THIS site's ArticleSiteContent only (see
	hydrate_articles). With no editorial content the item is just the
	metadata row and the abstract, which is the common case.
	"""
	content = article._feed_site_content[0] if article._feed_site_content else None
	sections = []

	if content and content.summary_plain_english:
		sections.append(_section("In plain English", _text_block(content.summary_plain_english)))
	if content and content.takeaways:
		sections.append(_section("Key takeaways", _text_block(content.takeaways)))

	meta = []
	journal = article.container_title or article.publisher
	if journal:
		meta.append(journal)
	if article.published_date:
		meta.append(article.published_date.strftime("%Y-%m-%d"))
	if article.access == "open":
		meta.append("Open access")
	if meta:
		sections.append(format_html("<p>{}</p>", " · ".join(meta)))

	if article.summary:
		sections.append(_section("Abstract", _text_block(article.summary)))

	return _join_sections(sections)


def trial_description(trial):
	"""HTML description for a trial item. Every interpolated value is escaped."""
	parts = []

	if trial.summary:
		parts.append(format_html("<p>{}</p>", _text_block(trial.summary)))

	metadata = []

	def add(label, value):
		metadata.append(format_html("<strong>{}:</strong> {}", label, value))

	if trial.recruitment_status_normalized or trial.recruitment_status:
		# Prefer the canonical label; for "other" the raw registry string is more
		# informative than the label.
		status_display = (
			trial.get_recruitment_status_normalized_display()
			if trial.recruitment_status_normalized
			and trial.recruitment_status_normalized != "other"
			else trial.recruitment_status
			or trial.get_recruitment_status_normalized_display()
		)
		add("Status", status_display)

	if trial.phase_normalized or trial.phase:
		# Prefer the canonical label; for "other" the raw registry string
		# (e.g. "Retrospective study") is more informative than the label.
		phase_display = (
			trial.get_phase_normalized_display()
			if trial.phase_normalized and trial.phase_normalized != "other"
			else trial.phase or trial.get_phase_normalized_display()
		)
		add("Phase", phase_display)

	if trial.study_type:
		add("Study Type", trial.study_type)
	if trial.primary_sponsor:
		add("Sponsor", trial.primary_sponsor)
	if trial.countries:
		add("Countries", trial.countries)
	if trial.condition:
		add("Condition", trial.condition)
	if trial.intervention:
		add("Intervention", trial.intervention)

	eligibility = []
	if trial.inclusion_gender:
		eligibility.append(f"Gender: {trial.inclusion_gender}")
	if trial.inclusion_agemin and trial.inclusion_agemax:
		eligibility.append(f"Age: {trial.inclusion_agemin} - {trial.inclusion_agemax}")
	elif trial.inclusion_agemin:
		eligibility.append(f"Min Age: {trial.inclusion_agemin}")
	elif trial.inclusion_agemax:
		eligibility.append(f"Max Age: {trial.inclusion_agemax}")
	if eligibility:
		add("Eligibility", ", ".join(eligibility))

	if trial.target_size:
		add("Target Size", trial.target_size)
	if trial.date_registration:
		add("Registration Date", trial.date_registration.strftime("%Y-%m-%d"))
	if trial.source_register:
		add("Registry", trial.source_register)

	if metadata:
		parts.append(
			format_html(
				"<p>{}</p>",
				format_html_join(" | ", "{}", ((item,) for item in metadata)),
			)
		)

	if parts:
		return _join_sections(parts)
	return escape(trial.title)


def hydrate_articles(ids, site, subject_ids):
	"""Load the articles in `ids`, in that order, with a fixed number of queries.

	Per-item data is prefetched once for the whole page:
	  - the item's IN-SCOPE subjects and categories (a subject the site
	    doesn't cover must not show up as a <category>)
	  - this site's ArticleSiteContent only: another site's editorial text
	    for the same article must never reach this site's readers
	  - authors, in insertion order of the M2M through table. Articles.authors
	    has no position field, so the through row id is the closest thing to
	    author order.
	"""
	articles = list(
		Articles.objects.filter(pk__in=ids)
		.defer("noun_phrases")
		.prefetch_related(
			Prefetch(
				"subjects",
				queryset=Subject.objects.filter(id__in=subject_ids).order_by("subject_name", "pk"),
				to_attr="_feed_subjects",
			),
			Prefetch(
				"team_categories",
				queryset=TeamCategory.objects.filter(subjects__id__in=subject_ids)
				.distinct()
				.order_by("category_name", "pk"),
				to_attr="_feed_categories",
			),
			Prefetch(
				"site_contents",
				queryset=ArticleSiteContent.objects.filter(site=site),
				to_attr="_feed_site_content",
			),
		)
	)

	author_names = {}
	through = Articles.authors.through
	for row in (
		through.objects.filter(articles_id__in=ids)
		.select_related("authors")
		.order_by("id")
	):
		author_names.setdefault(row.articles_id, []).append(str(row.authors))

	by_id = {article.pk: article for article in articles}
	ordered = []
	for pk in ids:
		article = by_id.get(pk)
		if article is None:
			continue
		article._feed_author_names = author_names.get(pk, [])
		# item_link() below only receives the item, not obj -- stash the
		# requested site's domain on each item rather than on the shared
		# Feed instance, so concurrent requests don't interfere.
		article._feed_site_domain = site.domain
		ordered.append(article)
	return ordered


def hydrate_trials(ids, site, settings_row, subject_ids):
	"""Load the trials in `ids`, in that order, with a fixed number of queries."""
	trials = list(
		Trials.objects.filter(pk__in=ids).prefetch_related(
			Prefetch(
				"subjects",
				queryset=Subject.objects.filter(id__in=subject_ids).order_by("subject_name", "pk"),
				to_attr="_feed_subjects",
			)
		)
	)
	by_id = {trial.pk: trial for trial in trials}
	ordered = []
	for pk in ids:
		trial = by_id.get(pk)
		if trial is None:
			continue
		trial._feed_link = trial_url(trial, site, settings_row)
		ordered.append(trial)
	return ordered


class ArticleFeedItemMixin:
	"""item_* methods shared by every feed of articles.

	Items must come from hydrate_articles(); the methods read the attributes
	it sets.
	"""

	def item_title(self, item):
		return item.title

	def item_description(self, item):
		return article_description(item)

	def item_link(self, item):
		return f"https://{item._feed_site_domain}/articles/{item.pk}/"

	def item_guid(self, item):
		if item.doi:
			return f"doi:{item.doi}"
		return f"urn:gregory:article:{item.pk}"

	item_guid_is_permalink = False

	def item_pubdate(self, item):
		# When WE found the article, not when the journal published it, so a
		# reader that re-sorts by date agrees with the feed's own order. The
		# journal's date is in the description. (The author feed overrides
		# this -- see SiteArticlesByAuthorFeed.)
		return item.discovery_date

	def item_updateddate(self, item):
		# Later of discovery and this site's editorial update, so a summary
		# added after the fact is reflected (Atom only; RSS readers ignore it).
		updated = item.discovery_date
		for content in item._feed_site_content:
			if content.updated_at and (updated is None or content.updated_at > updated):
				updated = content.updated_at
		return updated

	def item_author_name(self, item):
		return author_label(item._feed_author_names)

	def item_categories(self, item):
		return [subject.subject_name for subject in item._feed_subjects] + [
			category.category_name for category in item._feed_categories
		]


class TrialFeedItemMixin:
	"""item_* methods shared by subject-scoped trial feeds.

	Items must come from hydrate_trials().
	"""

	def item_title(self, item):
		return item.title

	def item_description(self, item):
		return trial_description(item)

	def item_link(self, item):
		return item._feed_link

	def item_guid(self, item):
		return f"urn:gregory:trial:{item.pk}"

	item_guid_is_permalink = False

	def item_pubdate(self, item):
		# discovery_date, matching the feed's order (published_date would put
		# a newly found old trial below the cut-off, out of sight).
		return item.discovery_date

	def item_updateddate(self, item):
		return item.last_updated

	def item_categories(self, item):
		categories = []
		if item.phase_normalized:
			categories.append(item.get_phase_normalized_display())
		if item.recruitment_status_normalized:
			categories.append(item.get_recruitment_status_normalized_display())
		categories.extend(subject.subject_name for subject in item._feed_subjects)
		return [category for category in categories if category]
