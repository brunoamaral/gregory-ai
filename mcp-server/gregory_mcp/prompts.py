"""Serves each tenant's own authored prompts (Phase 2: django/sitesettings
`SiteMcpPrompt`, published at `GET /tenants/`) — replacing the three
hard-coded prompts this module used to register.

Registered as raw request handlers (`server.py`'s `_replace_handler`), not
the `@server.prompt()` decorator: the decorator's registration is fixed at
construction time, once for the whole process, but this server's active
prompt set now varies per request — the resolved tenant's own rows.
"""

from __future__ import annotations

import logging
import string
from typing import Any

import mcp_types as types
from mcp.server.context import ServerRequestContext
from mcp.shared.exceptions import MCPError

from .site_context import get_current_tenant

logger = logging.getLogger("gregory_mcp.prompts")


async def list_prompts(
	ctx: ServerRequestContext[Any, Any], params: types.PaginatedRequestParams
) -> types.ListPromptsResult:
	tenant = get_current_tenant()
	prompts = tenant.prompts if tenant is not None else ()
	return types.ListPromptsResult(
		prompts=[
			types.Prompt(
				name=prompt.name,
				title=prompt.title,
				description=prompt.description,
				arguments=[
					types.PromptArgument(name=arg.name, description=arg.description, required=arg.required)
					for arg in prompt.arguments
				],
			)
			for prompt in prompts
		]
	)


async def get_prompt(
	ctx: ServerRequestContext[Any, Any], params: types.GetPromptRequestParams
) -> types.GetPromptResult:
	tenant = get_current_tenant()
	prompts = tenant.prompts if tenant is not None else ()
	prompt = next((p for p in prompts if p.name == params.name), None)
	if prompt is None:
		raise MCPError(types.INVALID_PARAMS, f"Unknown prompt: {params.name}")

	supplied = params.arguments or {}
	missing = [arg.name for arg in prompt.arguments if arg.required and supplied.get(arg.name) is None]
	if missing:
		raise MCPError(types.INVALID_PARAMS, f"Missing required argument(s): {', '.join(missing)}")

	values = {
		arg.name: supplied.get(arg.name) if isinstance(supplied.get(arg.name), str) else ""
		for arg in prompt.arguments
	}
	try:
		rendered = string.Template(prompt.template).substitute(values)
	except (KeyError, ValueError):
		# Django validates every template against its declared arguments at
		# save time (validate_prompt_template, sitesettings/models.py), so
		# this should be unreachable in practice -- but a row written
		# directly (a fixture, a migration, a bypassed clean()) could still
		# be broken, and a rendering failure must never look like a normal
		# tool-call crash to the caller.
		logger.warning("gregory_prompt_render_failed", extra={"prompt": prompt.name})
		raise MCPError(types.INTERNAL_ERROR, "This prompt could not be rendered.") from None

	return types.GetPromptResult(
		description=prompt.description,
		messages=[types.PromptMessage(role="user", content=types.TextContent(type="text", text=rendered))],
	)
