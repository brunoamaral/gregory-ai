"""The ClinicalTrials.gov search a source runs, shared by the importer and the editor API.

The importer (feedreader_trials_ctgov) and the preview of a new source
(gregory.source_checks.ctgov_preview) build their parameters here, so what a person is
shown is what runs. They differ only in the date window.
"""

# Source field -> ClinicalTrials.gov API query parameter. ClinicalTrials.gov combines
# the parameters with AND, so a trial must match every one that is set.
SEARCH_FIELDS = (
	("ctgov_search_condition", "query_cond"),
	("ctgov_search_intervention", "query_intr"),
	("ctgov_search_term", "query_term"),
)


def query_params(values):
	"""The query parameters set in `values` (a mapping of source field names); empty
	when it sets none. Blank values are left out."""
	params = {}
	for field, param in SEARCH_FIELDS:
		value = (values.get(field) or "").strip()
		if value:
			params[param] = value
	return params


def build_search_params(values, window_start=None):
	"""API search parameters for the three search fields in `values`.

	`window_start` (a date or datetime) limits the search to studies updated on or after
	that day; None searches without a date filter. The description of a source is notes
	for people and is never part of the search.
	"""
	params = {
		"page_size": 100,
		"sort": ["LastUpdatePostDate:desc"],  # Get newest updates first
		**query_params(values),
	}
	if window_start is not None:
		params["filter_advanced"] = (
			f"AREA[LastUpdatePostDate]RANGE[{window_start.strftime('%Y-%m-%d')},MAX]"
		)
	return params
