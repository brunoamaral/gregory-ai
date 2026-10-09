"""Editor tools: the writes (and the history read) offered on the editor address
only (MCP-AUTH-PLAN.md, "Editor tools").

Every article tool takes an article by `article_id` or by `doi`, never both;
the category tools take a `category_id` (see list_categories). Each
write goes to Django's `/editor/` routes as the signed-in person, applies
immediately, and returns what was stored plus who last changed it and when, so
the model can confirm the change without a second read. The site is the
sign-in's own: no tool takes a site, and none can reach another.

A failure the model should read raises `ToolError` (since mcp 2.1 anything else
reaches it only as "Error executing tool <name>"). The one exception is passing
both or neither of `article_id` and `doi`, which is a malformed call rather than
a failed one: it raises `MCPError(INVALID_PARAMS)`, which the SDK passes through
unchanged.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import quote

import mcp_types as types
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.exceptions import MCPError

from ..client import GregoryAPIError, get_client

logger = logging.getLogger("gregory_mcp.editor")

DEFAULT_HISTORY_LIMIT = 50
MAX_HISTORY_LIMIT = 200


def _log_edit(
	tool: str, article_id: int | None, fields: list[str], outcome: str, category_id: int | None = None
) -> None:
	"""One `mcp_edit` line per write: which tool, which article and/or category,
	which fields, and how it went. Field NAMES only, never values; the values
	live in Django's history, which has its own access control. The caller is
	already on the `mcp_request` line of the same call (user_id, tier)."""
	extra = {"tool": tool, "article_id": article_id, "fields": sorted(fields), "outcome": outcome}
	if category_id is not None:
		extra["category_id"] = category_id
	logger.info("mcp_edit", extra=extra)


def _explain(exc: GregoryAPIError, what: str) -> ToolError:
	"""A `ToolError` the model can act on, from an upstream failure of a write."""
	status = exc.status_code
	if status == 404:
		return ToolError(f"{what} was not found on this site.")
	if status == 403 and "category" in _detail_message(exc).lower():
		# Not a lost grant: a category shared with another site, which only the
		# admin may change. Django's own sentence says so.
		return ToolError(f"The edit was refused: {_detail_message(exc)}")
	if status in (401, 403):
		return ToolError(
			"This sign-in can no longer edit this site: the access was revoked or is read-only. "
			"Reconnect the connector to sign in again."
		)
	if status == 429:
		wait = f" Try again in about {exc.retry_after} seconds." if exc.retry_after else " Try again later."
		return ToolError(f"The edit limit for this site was reached.{wait} The edit was not applied.")
	if status == 400:
		return ToolError(f"The edit was rejected: {exc.detail}")
	if status == 409:
		return ToolError(f"The edit was refused: {_detail_message(exc)}")
	if status == 0:
		# A write is never retried, so a timeout may or may not have been applied.
		return ToolError(
			"The edit may not have been applied: the request to the server failed. "
			"Check the article's history before trying again."
		)
	return ToolError(f"The edit could not be applied (status {status}).")


async def _resolve_article(article_id: int | None, doi: str | None) -> int:
	"""The article id for a call that gave either `article_id` or `doi`."""
	if (article_id is None) == (not doi):
		raise MCPError(types.INVALID_PARAMS, "Pass exactly one of article_id and doi.")
	if article_id is not None:
		return article_id
	try:
		found = await get_client().get("/editor/articles/resolve/", {"doi": doi})
	except GregoryAPIError as exc:
		if exc.status_code == 404:
			raise ToolError(f"No article with DOI {doi} was found on this site.") from exc
		if exc.status_code == 409:
			raise ToolError(
				f"More than one article has DOI {doi}: {_conflicting_ids(exc)}. "
				"Use article_id to say which."
			) from exc
		raise
	return found["article_id"]


def _detail_message(exc: GregoryAPIError) -> str:
	"""Django's `detail` text, plus the existing category's id when it sent one."""
	try:
		body = json.loads(exc.detail)
	except (ValueError, TypeError):
		return exc.detail
	if not isinstance(body, dict):
		return exc.detail
	message = str(body.get("detail") or exc.detail)
	if body.get("category_id") is not None:
		message += f" It is category {body['category_id']}."
	return message


def _conflicting_ids(exc: GregoryAPIError) -> str:
	try:
		ids = json.loads(exc.detail).get("article_ids", [])
	except (ValueError, AttributeError):
		return "ids unavailable"
	return ", ".join(str(i) for i in ids) or "ids unavailable"


async def update_article_editorial(
	article_id: int | None = None,
	doi: str | None = None,
	takeaways: str | None = None,
	summary_plain_english: str | None = None,
) -> dict:
	"""Set this site's takeaways and/or plain-English summary of an article.

	Give the article by `article_id` or `doi`. Send only the fields to change;
	an empty string clears a field. The change is live immediately, recorded
	under the signed-in person's name, and affects only this site. Show the
	proposed text and get confirmation before calling.

	Args:
		article_id: The article's id. Give this or doi.
		doi: The article's DOI. Give this or article_id.
		takeaways: The key takeaways. Empty string clears them.
		summary_plain_english: A plain-English summary. Empty string clears it.
	"""
	article = await _resolve_article(article_id, doi)
	body = {
		key: value
		for key, value in (("takeaways", takeaways), ("summary_plain_english", summary_plain_english))
		if value is not None
	}
	if not body:
		raise ToolError("Pass takeaways and/or summary_plain_english.")
	try:
		stored = await get_client().patch(f"/editor/articles/{article}/editorial/", json=body)
	except GregoryAPIError as exc:
		_log_edit("update_article_editorial", article, list(body), "error")
		raise _explain(exc, f"Article {article}") from exc
	_log_edit("update_article_editorial", article, list(body), "ok")
	return stored


async def set_article_relevance(
	subject_id: int,
	is_relevant: bool | None,
	article_id: int | None = None,
	doi: str | None = None,
) -> dict:
	"""Set whether an article is relevant for one subject.

	`is_relevant` is true, false, or null for "not reviewed". The subject must
	be one this site covers. Relevance belongs to the article and the subject,
	not the site: the change applies on every site that covers that subject.
	The change is live immediately and recorded under the signed-in person's
	name.

	Args:
		subject_id: The subject's id (see list_subjects).
		is_relevant: true, false, or null for not reviewed.
		article_id: The article's id. Give this or doi.
		doi: The article's DOI. Give this or article_id.
	"""
	article = await _resolve_article(article_id, doi)
	try:
		stored = await get_client().put(
			f"/editor/articles/{article}/relevance/{subject_id}/", json={"is_relevant": is_relevant}
		)
	except GregoryAPIError as exc:
		_log_edit("set_article_relevance", article, ["is_relevant"], "error")
		raise _explain(exc, f"Article {article} or subject {subject_id}") from exc
	_log_edit("set_article_relevance", article, ["is_relevant"], "ok")
	return stored


async def link_trial_to_article(trial_id: int, article_id: int | None = None, doi: str | None = None) -> dict:
	"""Link a clinical trial to an article.

	Adds a link that automatic detection will not change or duplicate. Linking
	a pair that is already linked returns the existing link. The trial itself
	cannot be edited. Live immediately and recorded under the signed-in
	person's name.

	Args:
		trial_id: The trial's id.
		article_id: The article's id. Give this or doi.
		doi: The article's DOI. Give this or article_id.
	"""
	article = await _resolve_article(article_id, doi)
	try:
		stored = await get_client().post(f"/editor/articles/{article}/trials/", json={"trial_id": trial_id})
	except GregoryAPIError as exc:
		_log_edit("link_trial_to_article", article, ["trial_link"], "error")
		raise _explain(exc, f"Article {article} or trial {trial_id}") from exc
	_log_edit("link_trial_to_article", article, ["trial_link"], "ok")
	return stored


async def unlink_trial_from_article(trial_id: int, article_id: int | None = None, doi: str | None = None) -> dict:
	"""Remove the link between a clinical trial and an article.

	A link added by an editor is deleted. A link found automatically is hidden,
	so automatic detection does not bring it back. Live immediately and
	recorded under the signed-in person's name.

	Args:
		trial_id: The trial's id.
		article_id: The article's id. Give this or doi.
		doi: The article's DOI. Give this or article_id.
	"""
	article = await _resolve_article(article_id, doi)
	try:
		stored = await get_client().delete(f"/editor/articles/{article}/trials/{trial_id}/")
	except GregoryAPIError as exc:
		_log_edit("unlink_trial_from_article", article, ["trial_link"], "error")
		raise _explain(exc, f"The link between article {article} and trial {trial_id}") from exc
	_log_edit("unlink_trial_from_article", article, ["trial_link"], "ok")
	return stored


async def get_article_history(
	article_id: int | None = None, doi: str | None = None, limit: int = DEFAULT_HISTORY_LIMIT
) -> dict:
	"""Who changed an article's content on this site, and when.

	Lists, newest first, the changes to this site's takeaways and summary, to
	the article's relevance for this site's subjects, and to its links to
	trials, each with the values after the change, who made it and how
	(`mcp`, `api_key` or `admin`).

	Args:
		article_id: The article's id. Give this or doi.
		doi: The article's DOI. Give this or article_id.
		limit: How many entries to return (1 to 200, default 50).
	"""
	article = await _resolve_article(article_id, doi)
	clamped = max(1, min(int(limit), MAX_HISTORY_LIMIT))
	try:
		history: dict[str, Any] = await get_client().get(
			f"/editor/articles/{quote(str(article))}/history/", {"limit": clamped}
		)
	except GregoryAPIError as exc:
		if exc.status_code == 404:
			raise ToolError(f"Article {article} was not found on this site.") from exc
		raise
	return history


async def create_category(
	category_name: str,
	subject_ids: list[int],
	category_terms: list[str] | None = None,
	category_description: str | None = None,
	modality: str | None = None,
	category_slug: str | None = None,
	category_type: str = "automatic",
	match_scope: str = "title_summary",
) -> dict:
	"""Create a category: a tag the site uses to group articles and trials.

	An automatic category (the default) is filled by matching its terms
	against titles and summaries, case-insensitively, so choose terms that
	name the topic and nothing broader. It starts empty: the next pipeline run
	(every 12 hours) matches all content against it. The subjects must be ones
	this site covers and belong to one team. The slug defaults to the
	slugified name and is unique across every team; a taken slug is an error
	naming the existing category when this site can see it. Live immediately
	and recorded under the signed-in person's name. Show the proposed name,
	terms and subjects and get confirmation before calling.

	Args:
		category_name: The name shown on the site.
		subject_ids: Subjects it applies to (see list_subjects), from one team.
		category_terms: Words or phrases to match. At least one for an automatic category.
		category_description: Shown on the category's page. Markdown is allowed.
		modality: small_molecule, biologic_antibody, cell_gene_therapy, rehabilitation,
			device_neuromodulation, natural_product, research_topic or other.
		category_slug: The URL slug. Defaults to the slugified name.
		category_type: automatic (matched by terms) or manual (hand assignments only).
		match_scope: title_summary (default) or title (match titles only).
	"""
	body: dict[str, Any] = {
		"category_name": category_name,
		"subject_ids": subject_ids,
		"category_terms": category_terms or [],
		"category_type": category_type,
		"match_scope": match_scope,
	}
	for key, value in (
		("category_description", category_description),
		("modality", modality),
		("category_slug", category_slug),
	):
		if value is not None:
			body[key] = value
	try:
		stored = await get_client().post("/editor/categories/", json=body)
	except GregoryAPIError as exc:
		_log_edit("create_category", None, list(body), "error")
		raise _explain(exc, "A subject") from exc
	_log_edit("create_category", None, list(body), "ok", category_id=stored.get("id"))
	return stored


async def update_category(
	category_id: int,
	category_name: str | None = None,
	category_description: str | None = None,
	modality: str | None = None,
	match_scope: str | None = None,
	subject_ids: list[int] | None = None,
	category_terms: list[str] | None = None,
	add_terms: list[str] | None = None,
	remove_terms: list[str] | None = None,
) -> dict:
	"""Change a category. Send only the fields to change.

	`category_terms` replaces every term; `add_terms` and `remove_terms` edit
	the current list instead (ignoring case), and can't be combined with it.
	`subject_ids` replaces the subjects, which must be in the category's team.
	The slug and team never change. Only categories whose subjects are all on
	this site can be changed here. After a change to terms, subjects or match
	scope, the next pipeline run re-matches all content. Live immediately and
	recorded under the signed-in person's name. Show the proposed change and
	get confirmation before calling.

	Args:
		category_id: The category's id (see list_categories).
		category_name: New name.
		category_description: New description. Empty string clears it.
		modality: New modality (see create_category for the values).
		match_scope: title_summary or title.
		subject_ids: Replaces the subjects.
		category_terms: Replaces every term.
		add_terms: Terms to add.
		remove_terms: Terms to remove.
	"""
	body = {
		key: value
		for key, value in (
			("category_name", category_name),
			("category_description", category_description),
			("modality", modality),
			("match_scope", match_scope),
			("subject_ids", subject_ids),
			("category_terms", category_terms),
			("add_terms", add_terms),
			("remove_terms", remove_terms),
		)
		if value is not None
	}
	if not body:
		raise ToolError("Pass at least one field to change.")
	try:
		stored = await get_client().patch(f"/editor/categories/{category_id}/", json=body)
	except GregoryAPIError as exc:
		_log_edit("update_category", None, list(body), "error", category_id=category_id)
		raise _explain(exc, f"Category {category_id}") from exc
	_log_edit("update_category", None, list(body), "ok", category_id=category_id)
	return stored


async def assign_article_category(
	category_id: int, article_id: int | None = None, doi: str | None = None
) -> dict:
	"""Put an article in a category by hand.

	For an article the category's terms don't catch. A hand assignment is
	never removed by the pipeline; assigning an article the pipeline already
	matched turns it into a hand assignment. Assigning twice changes nothing.
	Live immediately and recorded under the signed-in person's name.

	Args:
		category_id: The category's id (see list_categories).
		article_id: The article's id. Give this or doi.
		doi: The article's DOI. Give this or article_id.
	"""
	article = await _resolve_article(article_id, doi)
	try:
		stored = await get_client().put(f"/editor/articles/{article}/categories/{category_id}/")
	except GregoryAPIError as exc:
		_log_edit("assign_article_category", article, ["category"], "error", category_id=category_id)
		raise _explain(exc, f"Article {article} or category {category_id}") from exc
	_log_edit("assign_article_category", article, ["category"], "ok", category_id=category_id)
	return stored


async def unassign_article_category(
	category_id: int, article_id: int | None = None, doi: str | None = None
) -> dict:
	"""Take an article out of a category it was assigned to by hand.

	An article the pipeline matched from the category's terms can't be taken
	out this way, because the next run would add it back: change the terms
	with update_category instead. Live immediately and recorded under the
	signed-in person's name.

	Args:
		category_id: The category's id (see list_categories).
		article_id: The article's id. Give this or doi.
		doi: The article's DOI. Give this or article_id.
	"""
	article = await _resolve_article(article_id, doi)
	try:
		stored = await get_client().delete(f"/editor/articles/{article}/categories/{category_id}/")
	except GregoryAPIError as exc:
		_log_edit("unassign_article_category", article, ["category"], "error", category_id=category_id)
		raise _explain(exc, f"Article {article} in category {category_id}") from exc
	_log_edit("unassign_article_category", article, ["category"], "ok", category_id=category_id)
	return stored
