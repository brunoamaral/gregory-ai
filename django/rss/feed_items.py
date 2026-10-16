"""
rss/feed_items.py

What a feed item looks like: description builders, the item_* mixins and
the hydration helpers that load a page of items with a fixed number of
queries.

Feeds pick the ids of the items first (a cheap ordered, sliced query on the
base queryset), then hydrate_articles()/hydrate_trials() load the full rows
with prefetches, so a 100-item feed costs the same handful of queries as a
3-item one instead of several per item.

Patent items follow stricter licence rules than articles and trials (see the
"Patent families" section below): processed fields only, never raw OPS text.

Every piece of database text that ends up inside a description is
HTML-escaped first. Editorial text is free text and registry/abstract text
comes from third parties; neither may be able to inject markup into a feed
that readers render as HTML.
"""

from datetime import datetime, time, timezone
from types import SimpleNamespace

from django.db.models import Prefetch
from django.http import QueryDict
from django.template.defaultfilters import linebreaksbr
from django.utils.html import escape, format_html, format_html_join
from django.utils.safestring import mark_safe
from django.utils.text import Truncator

from api.patent_links import visible_patent_trial_links_queryset
from gregory.models import (
	Articles,
	ArticleSiteContent,
	PatentApplicant,
	Patents,
	PatentSiteContent,
	Subject,
	TeamCategory,
	Trials,
)

# dc:creator shows at most this many names, then "et al."
MAX_FEED_AUTHORS = 3

# A patent item lists at most this many linked trials, then "and N more".
MAX_FEED_PATENT_TRIALS = 3
# A linked trial is named by its title (the /patents/ API identifies a linked
# trial by trial_id + trial_title, there is no registry identifier), cut to
# this many characters so one long registry title can't swamp the item.
MAX_FEED_TRIAL_TITLE = 120


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


# ---------------------------------------------------------------------------
# Patent families
#
# LICENCE: patent records come from the EPO's Open Patent Services. Under the
# OPS terms we may serve PROCESSED records (clause 3.1) but may not make "the
# data as such" public (clause 3.2). A feed is syndicated: aggregators copy
# items verbatim and keep them, which an API response isn't designed for. So a
# patent item is built ONLY from what Gregory adds to a record -- this site's
# editorial text, canonical sponsor names, dates, "Grant published",
# category-basis trial links and the Espacenet link the licence requires. It
# NEVER contains Patents.summary (the abstract), claims, inventors, CPC/IPC
# codes or publication-number lists, and hydrate_patents() defers those
# columns so they aren't even loaded. See docs/patents.md, "Licence and the
# API". Don't add them to a feed without a fresh look at the OPS terms.
# ---------------------------------------------------------------------------

# Columns that must never reach a feed item (see the licence note above).
_PATENT_RAW_FIELDS = ("summary", "usummary", "claims", "inventors", "cpc_classes", "ipc_classes")


def _http_url(url):
	"""`url` if it is an http(s) URL, else ''. Registry links are third-party text."""
	return url if url and url.startswith(("http://", "https://")) else ""


def unique_names(names):
	"""Names in order, without blanks or repeats (two applicants can share a sponsor)."""
	seen = set()
	out = []
	for name in names:
		if name and name not in seen:
			seen.add(name)
			out.append(name)
	return out


def patent_applicant_names(patent):
	return unique_names(applicant.sponsor.name for applicant in patent._feed_applicants)


def _patent_trial_line(link):
	trial = link.trial
	title = Truncator(trial.title).chars(MAX_FEED_TRIAL_TITLE)
	timing = "during" if link.timing == "during" else "after"
	href = _http_url(trial._feed_link)
	label = format_html('<a href="{}">{}</a>', href, title) if href else escape(title)
	return format_html("Filed {} trial: {} ({})", timing, label, link.sponsor.name)


def patent_description(patent):
	"""HTML description for a patent item, sections omitted when empty.

	Order: this site's plain-English summary and takeaways, a facts row, the
	linked trials, the Espacenet link. Built only from what Gregory adds to
	the record -- see the licence note above; never the abstract, claims,
	inventors or classification codes.
	"""
	content = patent._feed_site_content[0] if patent._feed_site_content else None
	sections = []

	if content and content.summary_plain_english:
		sections.append(_section("In plain English", _text_block(content.summary_plain_english)))
	if content and content.takeaways:
		sections.append(_section("Key takeaways", _text_block(content.takeaways)))

	facts = []
	applicants = patent_applicant_names(patent)
	if applicants:
		facts.append(", ".join(applicants))
	if patent.earliest_priority_date:
		facts.append(f"Priority {patent.earliest_priority_date:%Y-%m-%d}")
	if patent.earliest_publication_date:
		facts.append(f"First published {patent.earliest_publication_date:%Y-%m-%d}")
	if patent.has_grant:
		# Exactly this wording, never "Granted": has_grant is a heuristic on
		# kind codes until legal status is fetched, and "Granted" would claim
		# more than we know.
		facts.append("Grant published")
	if facts:
		sections.append(format_html("<p>{}</p>", " · ".join(facts)))

	links = patent._feed_trial_links
	if links:
		lines = [_patent_trial_line(link) for link in links[:MAX_FEED_PATENT_TRIALS]]
		if len(links) > MAX_FEED_PATENT_TRIALS:
			lines.append(f"and {len(links) - MAX_FEED_PATENT_TRIALS} more")
		sections.append(
			format_html(
				"<p>{}</p>",
				format_html_join(mark_safe("<br>"), "{}", ((line,) for line in lines)),
			)
		)

	href = _http_url(patent.link)
	if href:
		# Also the item <link>; repeated here so it is present in readers that
		# hide the item link. The licence requires it wherever a patent is shown.
		sections.append(format_html('<p><a href="{}">View on Espacenet</a></p>', href))

	return _join_sections(sections)


def hydrate_patents(ids, site, settings_row, subject_ids, scope=None):
	"""Load the patent families in `ids`, in that order, with a fixed number of queries.

	  - in-scope subjects and categories, as for articles (`subject_ids` is the
	    feed's subject set, so a category feed shows only that category's)
	  - this site's PatentSiteContent only
	  - applicants that resolved to a canonical sponsor and aren't individuals
	  - linked trials, through api.patent_links.visible_patent_trial_links_queryset,
	    the single rule for which links are visible: not suppressed,
	    basis="category" (subject-basis links are noisy for large sponsors) and
	    the trial in the SITE's scope (`scope`, defaulting to subject_ids) --
	    a link is a fact about the site, not about which feed it appears in.
	  - raw OPS text is deferred (see the licence note above)
	"""
	scope = set(subject_ids if scope is None else scope)
	patents = list(
		Patents.objects.filter(pk__in=ids)
		.defer(*_PATENT_RAW_FIELDS)
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
				queryset=PatentSiteContent.objects.filter(site=site),
				to_attr="_feed_site_content",
			),
			Prefetch(
				"patent_applicants",
				queryset=PatentApplicant.objects.filter(
					sponsor__isnull=False, is_individual=False
				)
				.select_related("sponsor")
				.order_by("sequence"),
				to_attr="_feed_applicants",
			),
		)
	)

	# The filter reads attributes off the request, not a real request. Both
	# sets are the site's scope, so a patent or trial outside it shows no link.
	shim = SimpleNamespace(
		GET=QueryDict(), visible_subject_ids=scope, visible_patent_subject_ids=scope
	)
	links_by_patent = {}
	for link in (
		visible_patent_trial_links_queryset(shim, include_subject_links=False)
		.filter(patent_id__in=ids)
		.select_related("trial", "sponsor")
		# "during" before "after" (the stronger signal), then a stable order.
		.order_by("-timing", "trial_id")
	):
		link.trial._feed_link = trial_url(link.trial, site, settings_row)
		links_by_patent.setdefault(link.patent_id, []).append(link)

	by_id = {patent.pk: patent for patent in patents}
	ordered = []
	for pk in ids:
		patent = by_id.get(pk)
		if patent is None:
			continue
		patent._feed_trial_links = links_by_patent.get(pk, [])
		ordered.append(patent)
	return ordered


class PatentFeedItemMixin:
	"""item_* methods shared by every feed of patent families.

	Items must come from hydrate_patents().
	"""

	def item_title(self, item):
		return item.title

	def item_description(self, item):
		return patent_description(item)

	def item_link(self, item):
		# The Espacenet page. The licence requires it wherever a patent is
		# shown, and the item link is the most prominent place for it.
		return item.link

	def item_guid(self, item):
		return f"urn:gregory:patent:{item.pk}"

	item_guid_is_permalink = False

	def item_pubdate(self, item):
		# First publication, not discovery: the importer backfills history, so
		# discovery_date is meaningless for "new" (see patent_base_qs). A date,
		# so midnight UTC.
		return datetime.combine(item.earliest_publication_date, time.min, tzinfo=timezone.utc)

	def item_updateddate(self, item):
		updated = item.last_updated
		for content in item._feed_site_content:
			if content.updated_at and (updated is None or content.updated_at > updated):
				updated = content.updated_at
		return updated

	def item_author_name(self, item):
		# Applicant SPONSOR names only (canonical, our processing). An applicant
		# that didn't resolve to a sponsor -- an individual -- is left out, and
		# inventors are never included.
		return author_label(patent_applicant_names(item))

	def item_categories(self, item):
		return [subject.subject_name for subject in item._feed_subjects] + [
			category.category_name for category in item._feed_categories
		]
