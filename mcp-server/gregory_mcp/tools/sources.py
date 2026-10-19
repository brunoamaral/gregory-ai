"""Source tools: list the sources of this site's subjects, and add RSS feeds,
PubMed searches and ClinicalTrials.gov searches. Offered only to a sign-in with
the sources scope (`sources:add`, tool_access.py), on the editor address only.

Adding a source is a bigger step than editing a record, so `create_source` is
described as one: the source is live at once, it feeds every site that covers
the subject, and it brings in content from today on. `preview_source` runs every
check `create_source` runs and saves nothing; the model is told to preview, show
the person the numbers and samples, and get their confirmation before creating.

Both write tools post to Django's `/editor/sources/` routes, which run every check
themselves: nothing here is a safeguard that Django relies on. The two POSTs
reach out to a feed, PubMed or ClinicalTrials.gov, so the client gives them a
longer timeout (`GREGORY_SOURCE_REQUEST_TIMEOUT`, client.py).
"""

from __future__ import annotations

import json
from typing import Any, Literal

from mcp.server.mcpserver.exceptions import ToolError

from ..client import GregoryAPIError, get_client
from .editor import _detail_message, _log_edit

SourceType = Literal["rss_papers", "rss_trials", "pubmed", "ctgov"]

# The tools say `ctgov_condition`; Django's fields are `ctgov_search_condition`.
_CTGOV_PARAMS = {
	"ctgov_condition": "ctgov_search_condition",
	"ctgov_intervention": "ctgov_search_intervention",
	"ctgov_term": "ctgov_search_term",
}


def _source_body(
	source_type: str,
	subject_id: int,
	link: str | None,
	keyword_filter: str | None,
	pubmed_query: str | None,
	excluded_publication_types: list[str] | None,
	ctgov_condition: str | None,
	ctgov_intervention: str | None,
	ctgov_term: str | None,
) -> dict[str, Any]:
	"""The request body for a preview or create. Settings that were not given are
	left out, so Django can tell a setting that belongs to another type (refused)
	from one that was never sent."""
	body: dict[str, Any] = {"source_type": source_type, "subject_id": subject_id}
	for key, value in (
		("link", link),
		("keyword_filter", keyword_filter),
		("pubmed_query", pubmed_query),
		("excluded_publication_types", excluded_publication_types),
		(_CTGOV_PARAMS["ctgov_condition"], ctgov_condition),
		(_CTGOV_PARAMS["ctgov_intervention"], ctgov_intervention),
		(_CTGOV_PARAMS["ctgov_term"], ctgov_term),
	):
		if value is not None:
			body[key] = value
	return body


def _own_names(text: str) -> str:
	"""Django's field names for the ClinicalTrials.gov settings, in this tool's."""
	for ours, theirs in _CTGOV_PARAMS.items():
		text = text.replace(theirs, ours)
	return text


def _rejected(exc: GregoryAPIError) -> str:
	"""A 400's problems as `field: message` lines (the body is a dict of field to
	messages), or the raw text when it is not."""
	try:
		body = json.loads(exc.detail)
	except (ValueError, TypeError):
		return _own_names(exc.detail)
	if not isinstance(body, dict):
		return _own_names(exc.detail)
	lines = []
	for field, problems in body.items():
		messages = problems if isinstance(problems, list) else [problems]
		text = " ".join(str(m) for m in messages)
		lines.append(text if field in ("detail", "non_field_errors") else f"{field}: {text}")
	return _own_names("; ".join(lines))


def _duplicate(exc: GregoryAPIError) -> dict[str, Any] | None:
	"""The existing source a 409 names, or None when the body cannot be read."""
	try:
		found = json.loads(exc.detail).get("duplicate_of")
	except (ValueError, TypeError, AttributeError):
		return None
	return found if isinstance(found, dict) else None


def _describe(source: dict[str, Any]) -> str:
	name = f'"{source["name"]}"' if source.get("name") else "unnamed"
	subject = f" for {source['subject_name']}" if source.get("subject_name") else ""
	return f"source {source.get('source_id')}, {name}{subject}"


def _explain(exc: GregoryAPIError, *, saves: bool) -> ToolError:
	"""A `ToolError` the model can act on, from a failed preview or create.

	`saves` is True for create: its failures say that nothing was added, and a
	transport failure says it may have been (a write is never retried, and
	Django may have saved the source after the client gave up)."""
	status = exc.status_code
	outcome = " Nothing was added." if saves else " Nothing was saved."
	if status == 404:
		return ToolError(
			"That subject was not found on this site. Use a subject_id from list_subjects." + outcome
		)
	if status == 400:
		return ToolError(f"The source was not accepted: {_rejected(exc)}.{outcome}")
	if status in (401, 403):
		return ToolError(
			"This sign-in can no longer add sources: the access was revoked or does not include adding "
			"sources. Reconnect the connector to sign in again."
		)
	if status == 429:
		wait = f" Try again in about {exc.retry_after} seconds." if exc.retry_after else " Try again later."
		what = "adding sources" if saves else "previews"
		return ToolError(f"The limit for {what} on this site was reached.{wait}{outcome}")
	if status == 503:
		return ToolError(f"{_detail_message(exc)}{outcome}")
	if status == 0:
		if saves:
			return ToolError(
				"The source may have been added: the request to the server failed or took too long. "
				"Check list_sources before trying again; a second try for a source that was added is "
				"reported as already existing."
			)
		return ToolError("The preview failed: the request to the server failed or took too long. Nothing was saved.")
	return ToolError(f"The request could not be completed (status {status}).{outcome}")


async def list_sources(
	subject_id: int | None = None,
	source_type: str | None = None,
	active: bool | None = True,
) -> dict:
	"""List the sources that feed this site's subjects.

	Each source has its id, name, type, subject, team, whether it is active, its
	settings (the feed address or the search) and when it was last fetched
	successfully. Sources of every type are listed, including ones that cannot be
	added here; their settings are shown only for the four types that can. Check
	this before adding a source, to see what already feeds a subject.

	Args:
		subject_id: Only this subject's sources (see list_subjects).
		source_type: Only this type: rss_papers, rss_trials, pubmed, ctgov, ctis, epo_ops, or a scrape_ or manual_ type.
		active: true (default) for active sources only, false for paused ones, null for both.
	"""
	params: dict[str, Any] = {"subject_id": subject_id, "source_type": source_type}
	if active is not None:
		params["active"] = "true" if active else "false"
	try:
		return await get_client().get("/editor/sources/", params)
	except GregoryAPIError as exc:
		if exc.status_code == 400:
			raise ToolError(f"The list was not accepted: {_rejected(exc)}.") from exc
		if exc.status_code in (401, 403):
			raise _explain(exc, saves=False) from exc
		raise


async def preview_source(
	source_type: SourceType,
	subject_id: int,
	link: str | None = None,
	keyword_filter: str | None = None,
	pubmed_query: str | None = None,
	excluded_publication_types: list[str] | None = None,
	ctgov_condition: str | None = None,
	ctgov_intervention: str | None = None,
	ctgov_term: str | None = None,
) -> dict:
	"""Check a source and see what it would bring in, without adding it.

	Takes the same arguments as create_source (except name and description) and
	runs the same checks. It saves nothing, and it reaches out to the feed,
	PubMed or ClinicalTrials.gov, so it can take a while. Always run this before
	create_source, then show the person the counts, the sample titles and
	anything in `refused` or `duplicate_of`.

	Returns `counts` (each with what it counts), up to 5 `samples`, `first_run`
	(what the first run would bring in), PubMed's reading of the search under
	`pubmed`, `refused` (the reason create_source would refuse, or null) and
	`duplicate_of` (the active source of the same team that already does this).
	A source that would be refused still comes back as a normal answer, with
	`refused` set.

	Source types and what each needs:
	- rss_papers: an RSS feed of science papers. Needs `link`; `keyword_filter`
	  is optional (comma-separated, quote phrases: "multiple sclerosis", alzheimer).
	  A keyword matches anywhere in a word; end it with * to match only words that
	  start with it (oligodendro* finds "oligodendrocyte", not "preoligodendrocyte").
	  Counts the entries in the feed now and how many pass the filter.
	- rss_trials: an RSS feed of clinical trials. Needs `link`.
	- pubmed: a PubMed search. Needs `pubmed_query`, e.g. "multiple sclerosis"[MeSH
	  Terms] AND (remyelination OR myelin repair), with no date clause. Optional
	  `excluded_publication_types` by name, e.g. ["Review"]. Counts the papers
	  created in the last 30 days.
	- ctgov: a ClinicalTrials.gov search. Needs at least one of `ctgov_condition`
	  (e.g. "multiple sclerosis"), `ctgov_intervention` (e.g. "dihydroartemisinin")
	  and `ctgov_term` (e.g. "cell reprogramming"). Counts all matching trials and
	  those updated in the last 30 days.

	Settings that belong to another type are rejected. An RSS address must be http
	or https and public.

	Args:
		source_type: rss_papers, rss_trials, pubmed or ctgov.
		subject_id: The subject everything the source brings in is tagged with (see list_subjects).
		link: The feed's address (rss_papers, rss_trials).
		keyword_filter: Keep only papers containing one of these keywords; a trailing * matches words starting with it (rss_papers).
		pubmed_query: The PubMed search (pubmed).
		excluded_publication_types: Publication type names to leave out (pubmed).
		ctgov_condition: Condition or disease to search (ctgov).
		ctgov_intervention: Intervention or treatment to search (ctgov).
		ctgov_term: Terms to search anywhere in a trial's record (ctgov).
	"""
	body = _source_body(
		source_type,
		subject_id,
		link,
		keyword_filter,
		pubmed_query,
		excluded_publication_types,
		ctgov_condition,
		ctgov_intervention,
		ctgov_term,
	)
	try:
		found = await get_client().post("/editor/sources/preview/", json=body)
	except GregoryAPIError as exc:
		_log_edit("preview_source", None, list(body), "error", record_type="source")
		raise _explain(exc, saves=False) from exc
	_log_edit("preview_source", None, list(body), "refused" if found.get("refused") else "ok", record_type="source")
	return found


async def create_source(
	source_type: SourceType,
	subject_id: int,
	link: str | None = None,
	keyword_filter: str | None = None,
	pubmed_query: str | None = None,
	excluded_publication_types: list[str] | None = None,
	ctgov_condition: str | None = None,
	ctgov_intervention: str | None = None,
	ctgov_term: str | None = None,
	name: str | None = None,
	description: str | None = None,
) -> dict:
	"""Add a source: an RSS feed of papers or trials, a PubMed search or a
	ClinicalTrials.gov search, for one subject.

	The source is live immediately and recorded under the signed-in person's
	name: the next pipeline run (every 12 hours) fetches it. It feeds every site
	that covers the subject, not only this one. It brings in content from today
	on, not older content: a PubMed or ClinicalTrials.gov source starts from the
	day it is added, and an RSS source reads the feed's current entries. Nothing
	here edits or removes a source afterwards; that is done in the admin.

	Run preview_source first with the same arguments, show the person the counts
	and sample titles, and get their confirmation before calling this. Say which
	subject it will feed and that other sites with that subject get it too. There
	is no volume limit, so a search that is too broad is only caught by that
	preview. All the checks run again here. Adding a source that already exists
	changes nothing and says which one.

	Source types and what each needs:
	- rss_papers: an RSS feed of science papers. Needs `link`; `keyword_filter`
	  is optional (comma-separated, quote phrases: "multiple sclerosis", alzheimer).
	  A keyword matches anywhere in a word; end it with * to match only words that
	  start with it (oligodendro* finds "oligodendrocyte", not "preoligodendrocyte").
	- rss_trials: an RSS feed of clinical trials. Needs `link`.
	- pubmed: a PubMed search. Needs `pubmed_query`, e.g. "multiple sclerosis"[MeSH
	  Terms] AND (remyelination OR myelin repair), with no date clause. Optional
	  `excluded_publication_types` by name, e.g. ["Review"].
	- ctgov: a ClinicalTrials.gov search. Needs at least one of `ctgov_condition`
	  (e.g. "multiple sclerosis"), `ctgov_intervention` (e.g. "dihydroartemisinin")
	  and `ctgov_term` (e.g. "cell reprogramming").

	Settings that belong to another type are rejected. An RSS address must be http
	or https and public.

	Args:
		source_type: rss_papers, rss_trials, pubmed or ctgov.
		subject_id: The subject everything the source brings in is tagged with (see list_subjects).
		link: The feed's address (rss_papers, rss_trials).
		keyword_filter: Keep only papers containing one of these keywords; a trailing * matches words starting with it (rss_papers).
		pubmed_query: The PubMed search (pubmed).
		excluded_publication_types: Publication type names to leave out (pubmed).
		ctgov_condition: Condition or disease to search (ctgov).
		ctgov_intervention: Intervention or treatment to search (ctgov).
		ctgov_term: Terms to search anywhere in a trial's record (ctgov).
		name: The source's name. Defaults to "RSS | <subject>", "PubMed | <subject>" or "CTGov | <subject>".
		description: Notes for people. Gregory never reads them.
	"""
	body = _source_body(
		source_type,
		subject_id,
		link,
		keyword_filter,
		pubmed_query,
		excluded_publication_types,
		ctgov_condition,
		ctgov_intervention,
		ctgov_term,
	)
	for key, value in (("name", name), ("description", description)):
		if value is not None:
			body[key] = value
	try:
		stored = await get_client().post("/editor/sources/", json=body)
	except GregoryAPIError as exc:
		if exc.status_code == 409:
			# Not a failure: the source is already there, which is also what a second
			# try after a timed-out first one finds.
			_log_edit("create_source", None, list(body), "exists", record_type="source")
			existing = _duplicate(exc)
			result: dict[str, Any] = {
				"created": False,
				"already_exists": True,
				"message": (
					f"This source already exists ({_describe(existing)}), so nothing new was added."
					if existing
					else f"This source already exists, so nothing new was added. {_detail_message(exc)}"
				),
			}
			if existing:
				result["duplicate_of"] = existing
			return result
		_log_edit("create_source", None, list(body), "error", record_type="source")
		raise _explain(exc, saves=True) from exc
	_log_edit(
		"create_source", (stored.get("source") or {}).get("id"), list(body), "ok", record_type="source"
	)
	return stored
