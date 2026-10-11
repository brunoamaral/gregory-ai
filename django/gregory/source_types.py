"""The kinds of Source that work, and what each one needs.

A source's kind is stored as two fields: `source_for` (what content it produces) and
`method` (how that content arrives). Only some pairs mean anything. Each importer
picks up exactly one pair, and any other pair is saved fine and then never fetched.
The admin form offers these types instead of the two raw fields, so only working
pairs can be chosen. Keep this list in step with the importers' source filters
(feedreader_articles, feedreader_trials, feedreader_trials_ctgov,
feedreader_trials_ctis, feedreader_patents_ops) and with api.views.post_article,
which accepts content for any source whose `source_for` matches.
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SourceType:
	key: str
	label: str
	source_for: str
	method: str
	fetched: bool
	# Connection fields the admin shows for this type, in order.
	fields: tuple
	# Fields that must be filled in. ClinicalTrials.gov needs any one of its three
	# search fields, which SourceAdminForm checks separately.
	required: tuple
	help: str


FETCHED_GROUP = "Fetched by Gregory"
RECEIVED_GROUP = "Received through the API, never fetched"
RECEIVED_HELP = (
	"Gregory never fetches this source. Content arrives through the API "
	"(POST /articles/post/) quoting this source's ID, or is added by hand in the admin."
)
RECEIVED_FIELDS = ("link", "api_submission_note")

SOURCE_TYPES = (
	SourceType(
		"rss_papers", "RSS feed of science papers", "science paper", "rss", True,
		("link", "ignore_ssl", "keyword_filter"), ("link",),
		"Gregory reads the feed on every pipeline run and keeps the new papers.",
	),
	SourceType(
		"rss_trials", "RSS feed of clinical trials", "trials", "rss", True,
		("link", "ignore_ssl"), ("link",),
		"Gregory reads the feed on every pipeline run and keeps the new or updated trials.",
	),
	SourceType(
		"ctgov", "ClinicalTrials.gov search", "trials", "ctgov_api", True,
		("ctgov_search_condition", "ctgov_search_intervention", "ctgov_search_term"), (),
		"Gregory runs this search on ClinicalTrials.gov on every pipeline run and keeps "
		"the new or updated trials.",
	),
	SourceType(
		"ctis", "EU CTIS search", "trials", "ctis_api", True,
		("ctis_search_criteria",), ("ctis_search_criteria",),
		"Gregory runs this search on the EU Clinical Trials Information System on every "
		"pipeline run.",
	),
	SourceType(
		"epo_ops", "EPO patent search", "patents", "epo_ops", True,
		("ops_cql_query",), ("ops_cql_query",),
		"Gregory runs this search on EPO Open Patent Services on every pipeline run, "
		"using the EPO credentials of the team's organisation.",
	),
	SourceType(
		"scrape_papers", "Science papers sent by an external scraper", "science paper",
		"scrape", False, RECEIVED_FIELDS, (), RECEIVED_HELP,
	),
	SourceType(
		"manual_papers", "Science papers added by hand or through the API", "science paper",
		"manual", False, RECEIVED_FIELDS, (), RECEIVED_HELP,
	),
	SourceType(
		"scrape_trials", "Clinical trials sent by an external scraper", "trials",
		"scrape", False, RECEIVED_FIELDS, (), RECEIVED_HELP,
	),
	SourceType(
		"manual_trials", "Clinical trials added by hand or through the API", "trials",
		"manual", False, RECEIVED_FIELDS, (), RECEIVED_HELP,
	),
	SourceType(
		"scrape_news", "News articles sent by an external scraper", "news article",
		"scrape", False, RECEIVED_FIELDS, (), RECEIVED_HELP,
	),
	SourceType(
		"manual_news", "News articles added by hand or through the API", "news article",
		"manual", False, RECEIVED_FIELDS, (), RECEIVED_HELP,
	),
)

BY_KEY = {source_type.key: source_type for source_type in SOURCE_TYPES}
BY_PAIR = {(source_type.source_for, source_type.method): source_type for source_type in SOURCE_TYPES}

# Every connection field any type shows, in display order.
CONNECTION_FIELDS = tuple(
	dict.fromkeys(field for source_type in SOURCE_TYPES for field in source_type.fields)
)

# An existing source whose pair matches no type above (e.g. a news feed saved as RSS).
# Editing it keeps the pair as it is until someone picks a real type.
LEGACY_KEY = "legacy"

CTIS_SEARCH_KEYS = ("medicalCondition", "sponsor", "number", "containAll", "status")

# feedreader_patents_ops adds `pd within "…"` itself; a second date clause in the
# stored query would silently narrow every run.
CQL_DATE_CLAUSE = re.compile(
	r"\b(pd|publicationdate)\s*(==|<=|>=|=|<|>|within\b|any\b|all\b)", re.IGNORECASE
)


def type_for(source_for, method):
	"""The SourceType for a stored pair, or None when the pair is never fetched."""
	return BY_PAIR.get((source_for, method))


def grouped_choices():
	"""Choices for the admin's "Source type" select, fetched types first."""
	return [
		(FETCHED_GROUP, [(t.key, t.label) for t in SOURCE_TYPES if t.fetched]),
		(RECEIVED_GROUP, [(t.key, t.label) for t in SOURCE_TYPES if not t.fetched]),
	]
