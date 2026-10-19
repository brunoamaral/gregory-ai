"""The checks a source's settings must pass before it is saved.

Shared by the admin form (SourceAdminForm) and the editor API (api/editor_views.py),
so a source added from either place meets the same rules and gets the same messages.

The functions take a source type key (gregory/source_types.py) and a dict of the
settings, and return plain data: no form, no request. `check_settings` is local and
free; `check_pubmed_search` makes one call to PubMed.
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from django.utils import timezone

from . import source_types
from .utils.ncbi import ESearchResult, NcbiClient, NcbiError, NcbiQueryError
from .utils.pubmed_search import build_pubmed_term, unbalanced_query


def _filled(value):
	return bool(value.strip()) if isinstance(value, str) else bool(value)


def check_settings(type_key, values):
	"""Check the settings a source of this type needs, without any network call.

	`values` maps setting names (`link`, `ctgov_search_condition`, `pubmed_query`, …) to
	what the caller gave; a missing name counts as empty. Returns `{field: [messages]}`,
	empty when the settings are fine. An unknown type key returns no errors: the caller's
	own type validation already covers it.
	"""
	errors = {}

	def add(field, message):
		errors.setdefault(field, []).append(message)

	source_type = source_types.BY_KEY.get(type_key)
	if source_type is None:
		return errors

	if "link" in source_type.required and not _filled(values.get("link")):
		add("link", "Enter the feed's URL.")
	if source_type.key == "ctgov" and not any(
		_filled(values.get(f)) for f in source_type.fields
	):
		add(
			"ctgov_search_condition",
			"Fill in at least one of condition, intervention or other terms. With all "
			"three empty, ClinicalTrials.gov would return every trial in its registry.",
		)
	if source_type.key == "ctis":
		_check_ctis_criteria(values.get("ctis_search_criteria"), add)
	if source_type.key == "epo_ops":
		query = (values.get("ops_cql_query") or "").strip()
		if not query:
			add(
				"ops_cql_query",
				'Enter the EPO search, e.g. ta="multiple sclerosis" and cpc=/low A61P25/00.',
			)
		elif source_types.CQL_DATE_CLAUSE.search(query):
			add(
				"ops_cql_query",
				"Remove the publication-date clause (pd …). Gregory adds the date "
				"window itself on every run.",
			)
	if source_type.key == "pubmed":
		_check_pubmed_query(values.get("pubmed_query"), add)
	return errors


def _check_pubmed_query(query, add):
	query = (query or "").strip()
	if not query:
		add(
			"pubmed_query",
			'Enter the PubMed search, e.g. "multiple sclerosis"[MeSH Terms] AND remyelination.',
		)
		return
	if source_types.has_date_clause(query):
		add(
			"pubmed_query",
			"Remove the date clause ([dp], [crdt], last N days …). Gregory adds the "
			"creation-date window itself on every run.",
		)
		return
	problem = unbalanced_query(query)
	if problem:
		add("pubmed_query", f"Check the brackets and quotes. {problem}")


def _check_ctis_criteria(criteria, add):
	if not criteria:
		add(
			"ctis_search_criteria",
			'Enter the search criteria, e.g. {"medicalCondition": "Multiple Sclerosis"}. '
			"Without them this source is never fetched.",
		)
	elif not isinstance(criteria, dict):
		add(
			"ctis_search_criteria",
			'Enter a JSON object such as {"medicalCondition": "Multiple Sclerosis"}, '
			"not a list or a single value.",
		)
	else:
		unknown = sorted(set(criteria) - set(source_types.CTIS_SEARCH_KEYS))
		if unknown:
			add(
				"ctis_search_criteria",
				f"Unknown key(s): {', '.join(unknown)}. CTIS accepts "
				f"{', '.join(source_types.CTIS_SEARCH_KEYS)} (case-sensitive).",
			)


@dataclass
class PubmedSearchCheck:
	"""What asking PubMed about a search found. At most one of the three is set."""

	result: Optional[ESearchResult] = None
	# PubMed rejected the search; the admin shows it on the search field.
	rejection: Optional[str] = None
	# PubMed could not be reached, so the search was not checked.
	unreachable: bool = False


def check_pubmed_search(query, excluded_names, client=None):
	"""Ask PubMed what the importer's first run would find, so a typo or a search that
	is far too broad shows up now, not after a month of runs. Short timeout and one
	retry by default: callers run this inside a web request."""
	end = timezone.now().date()
	term = build_pubmed_term(query.strip(), excluded_names, end - timedelta(days=30), end)
	client = client or NcbiClient.for_site(timeout=10, max_retries=1)
	try:
		result = client.esearch(term, rettype="count")
	except NcbiQueryError as exc:
		return PubmedSearchCheck(rejection=f"PubMed rejected this search: {exc}")
	except NcbiError:
		return PubmedSearchCheck(unreachable=True)
	return PubmedSearchCheck(result=result)
