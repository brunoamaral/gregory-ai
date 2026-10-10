"""Rewrites each response's server identity and generated `instructions` to
the resolved tenant's own — decision F (parent plan, MCP-MULTI-TENANCY-PLAN.md):
someone adding a tenant's server to Claude Desktop, and the model they talk
to, should see that tenant and nothing else. No platform name, no sign of
other tenants, no "instance"/"tenant" vocabulary.

build_server() constructs the MCPServer with neutral defaults (name
"gregory-ai", title "Research assistant" — see server.py) that name no
platform. TenantIdentityMiddleware below is what replaces those with the
resolved tenant's own name/title/description/instructions on every response
that carries identity — a client only ever sees the neutral defaults if no
tenant resolved, and TenantGateMiddleware already refuses every such request
except `ping`/notifications, which carry no identity to rewrite.
"""

from __future__ import annotations

from typing import Any

import mcp_types as types
from mcp.server.context import CallNext, HandlerResult, ServerMiddleware, ServerRequestContext

from .site_context import EditorSession, get_current_tenant, get_editor_context
from .tenants import Tenant

# Matches build_server()'s own version= kwarg — one constant so the two
# can't drift apart the way CATALOG_CACHE_TTL_MS exists to prevent for the
# cache TTL/hint pair.
SERVER_VERSION = "0.1.0"


_PUBLIC_TIER_NOTE = "Editing needs editor access on this site; this sign-in can read only."

_EDIT_TOOLS = (
	"update_article_editorial (set an article's takeaways and plain-English summary for this site), "
	"set_article_relevance (mark an article relevant, not relevant or not reviewed for a subject), "
	"link_trial_to_article and unlink_trial_from_article (connect or disconnect an article and a clinical trial), "
	"create_category and update_category (add a category or change its name, terms or subjects), "
	"assign_article_category and unassign_article_category (put an article in a category by hand, or take it out)"
)


def _behavior_paragraph(session: EditorSession | None) -> str:
	"""How the tools behave, for the caller's tier.

	Anonymous callers and the public tier get the original wording: every tool
	is read-only. An editor is told what they can change, that changes are live
	at once and attributed to the signed-in person, and to show the proposed
	text before writing.
	"""
	anonymous = (
		"All tools are read-only. Searches and lists only return these "
		"subjects; a record outside them is reported as not found."
	)
	if session is None:
		return anonymous
	if not session.is_editor:
		return f"{anonymous} {_PUBLIC_TIER_NOTE}"
	scope = "Searches and lists only return these subjects; a record outside them is reported as not found."
	history = "get_article_history shows who changed an article's content, relevance and trial links, and when."
	if not session.can_edit:
		return f"You have read-only editor access to this site. {scope} {history} Editing is not available with this sign-in."
	return (
		f"You are signed in as an editor of this site and can change its article content with: {_EDIT_TOOLS}. "
		"Edits are live immediately and recorded under the signed-in person's name, so show the proposed text "
		"and get confirmation before writing it. Relevance belongs to the article and the subject, not the "
		"site: a change applies to every site that covers that subject. "
		f"{history} {scope}"
	)


def instructions_for(tenant: Tenant, session: EditorSession | None = None) -> str:
	"""Generated `instructions` for `tenant`: an introduction, the subjects
	covered, and how the tools behave. The middle paragraph is dropped when
	a tenant has no subjects (see below), so two paragraphs in that case.

	Decision 6 (MCP-MULTI-TENANCY-PHASE-3-PLAN.md, task B2) is this exact
	wording — edit here, not at a call site, if it needs to change. `session`
	is set only on the editor address (see `_behavior_paragraph` for what
	changes with the tier); without it the text is the anonymous one.

	Must never contain "GregoryAI", "instance" or "tenant" (decision F) —
	tests/test_identity.py checks every rendered tenant's text for this.
	"""
	intro = tenant.mcp_description.strip()
	if not intro:
		intro = (
			f"This is {tenant.title}'s research database: articles, clinical "
			"trials and researchers, curated to the subjects below."
		)
	behavior_paragraph = _behavior_paragraph(session)
	if not tenant.subjects:
		# Only reachable if every subject row failed to parse: the API lists
		# no tenant whose scope is empty. Drop the paragraph rather than
		# render "Subjects covered: ." at a model.
		return "\n\n".join([intro, behavior_paragraph])
	subject_names = ", ".join(name for _, name in tenant.subjects)
	return "\n\n".join([intro, f"Subjects covered: {subject_names}.", behavior_paragraph])


def _server_info(tenant: Tenant, session: EditorSession | None = None) -> dict[str, Any]:
	access = "Read and edit access" if session is not None and session.can_edit else "Read-only access"
	return {
		"name": tenant.domain,
		"title": tenant.title,
		"version": SERVER_VERSION,
		"description": (
			f"{access} to {tenant.title}'s research database: articles, "
			"clinical trials, authors, subjects, categories and sponsors."
		),
	}


class TenantIdentityMiddleware(ServerMiddleware[Any]):
	"""Stamps the resolved tenant's identity and `instructions` onto the
	already-serialized wire result.

	Must be registered innermost (see server.py): the SDK's runner
	serializes each result, including its default `_meta` `serverInfo`
	stamp, *inside* the middleware chain (mcp 2.3.0's `Runner._serialize`),
	so a middleware only sees that stamp on the dict `call_next` returns
	if it sits closer to the handler than the serialization step — which,
	among this server's own middleware, means last in `build_server()`'s
	`middleware=[...]` list.

	Two response shapes carry identity, handled differently:
	  - `initialize` (legacy handshake, protocol <= 2025-11-25): identity
	    and `instructions` are top-level keys on the result body itself.
	    Never stamped by the SDK ("handshake-era results are never
	    stamped" — Runner._stamp_server_info's docstring), so this always
	    writes them.
	  - Every other modern-era result: identity lives under
	    `_meta[SERVER_INFO_META_KEY]`, already filled with the neutral
	    default by the SDK's own stamping — this overwrites it. Never
	    *adds* a `_meta` stamp where the SDK put none (an extension result
	    that opted out, or one with no identity at all).
	`server/discover` additionally carries `instructions` as a plain
	top-level key, alongside (not instead of) the `_meta` stamp.

	A request with no resolved tenant is left untouched: TenantGateMiddleware
	already refused everything except `ping`/notifications, neither of which
	carries identity to rewrite.
	"""

	async def __call__(self, ctx: ServerRequestContext[Any, Any], call_next: CallNext) -> HandlerResult:
		result = await call_next(ctx)
		tenant = get_current_tenant()
		if tenant is None or not isinstance(result, dict):
			return result

		session = get_editor_context()
		info = _server_info(tenant, session)
		if ctx.method == "initialize":
			result["serverInfo"] = info
			result["instructions"] = instructions_for(tenant, session)
			return result

		if ctx.method == "server/discover":
			result["instructions"] = instructions_for(tenant, session)

		meta = result.get("_meta")
		if isinstance(meta, dict) and types.SERVER_INFO_META_KEY in meta:
			result["_meta"] = {**meta, types.SERVER_INFO_META_KEY: info}
		return result
