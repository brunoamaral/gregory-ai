"""Serves each tenant's own resources: the built-in subjects/categories
catalogs, a generated "about" page, and any authored reference documents
(Phase 2: django/sitesettings `SiteMcpDocument`, published at
`GET /tenants/`).

Registered as raw request handlers (`server.py`'s `_replace_handler`), not
the `@server.resource()` decorator — see `prompts.py`'s module docstring for
why: the active document set now varies per request.

Addresses use the `gregory-ai://` scheme (decision 5,
MCP-MULTI-TENANCY-PHASE-3-PLAN.md): renamed outright from `gregory://`
rather than accepting both during a transition, since only Bruno's own
tooling used the old one. An old `gregory://` address is rejected the same
as any other unknown URI.

No sponsors resource: at ~8,000 rows / ~700 KB, sponsors are not
catalog-shaped the way subjects (7 rows) and categories (~120 rows) are —
resources are cached and injected wholesale. The `list_sponsors` tool
(search + pagination) is the right shape for that data; use it instead.
"""

from __future__ import annotations

import json
from typing import Any

import mcp_types as types
from mcp.server.context import ServerRequestContext
from mcp.shared.exceptions import MCPError

from .cache import get_all_pages_cached
from .identity import instructions_for
from .site_context import get_current_tenant
from .tenants import Tenant

SUBJECTS_URI = "gregory-ai://subjects"
CATEGORIES_URI = "gregory-ai://categories"
ABOUT_URI = "gregory-ai://about"
DOC_URI_TEMPLATE = "gregory-ai://doc/{slug}"
_DOC_URI_PREFIX = "gregory-ai://doc/"


async def list_resources(
	ctx: ServerRequestContext[Any, Any], params: types.PaginatedRequestParams
) -> types.ListResourcesResult:
	# Descriptions are neutral and the same for every tenant (decision F) —
	# what varies per tenant is each URI's *content*, read via resources/read.
	return types.ListResourcesResult(
		resources=[
			types.Resource(
				uri=SUBJECTS_URI,
				name="subjects_catalog",
				title="Subjects catalog",
				description="The research subjects this server covers.",
				mime_type="application/json",
			),
			types.Resource(
				uri=CATEGORIES_URI,
				name="categories_catalog",
				title="Categories catalog",
				description="The research categories (topic tags) this server covers.",
				mime_type="application/json",
			),
			types.Resource(
				uri=ABOUT_URI,
				name="about",
				title="About this server",
				description="What this server covers, and an index of its reference documents.",
				mime_type="text/markdown",
			),
		]
	)


async def list_resource_templates(
	ctx: ServerRequestContext[Any, Any], params: types.PaginatedRequestParams
) -> types.ListResourceTemplatesResult:
	return types.ListResourceTemplatesResult(
		resource_templates=[
			types.ResourceTemplate(
				uri_template=DOC_URI_TEMPLATE,
				name="doc",
				title="Reference document",
				description="A reference document authored for this server.",
			)
		]
	)


def _about_markdown(tenant: Tenant) -> str:
	lines = [f"# {tenant.title}", "", instructions_for(tenant)]
	if tenant.documents:
		lines += ["", "## Documents", ""]
		for doc in tenant.documents:
			entry = f"- [{doc.title}]({DOC_URI_TEMPLATE.format(slug=doc.slug)})"
			if doc.description:
				entry += f": {doc.description}"
			lines.append(entry)
	return "\n".join(lines)


async def read_resource(
	ctx: ServerRequestContext[Any, Any], params: types.ReadResourceRequestParams
) -> types.ReadResourceResult:
	uri = params.uri
	tenant = get_current_tenant()

	if uri == SUBJECTS_URI:
		results = await get_all_pages_cached("/subjects/")
		text = json.dumps([{"id": s.get("id"), "subject_name": s.get("subject_name")} for s in results])
		return types.ReadResourceResult(
			contents=[types.TextResourceContents(uri=uri, mime_type="application/json", text=text)]
		)

	if uri == CATEGORIES_URI:
		results = await get_all_pages_cached("/categories/")
		text = json.dumps(
			[
				{"id": c.get("id"), "category_name": c.get("category_name"), "category_slug": c.get("category_slug")}
				for c in results
			]
		)
		return types.ReadResourceResult(
			contents=[types.TextResourceContents(uri=uri, mime_type="application/json", text=text)]
		)

	if uri == ABOUT_URI and tenant is not None:
		return types.ReadResourceResult(
			contents=[types.TextResourceContents(uri=uri, mime_type="text/markdown", text=_about_markdown(tenant))]
		)

	if uri.startswith(_DOC_URI_PREFIX) and tenant is not None:
		slug = uri[len(_DOC_URI_PREFIX) :]
		doc = next((d for d in tenant.documents if d.slug == slug), None)
		if doc is not None:
			return types.ReadResourceResult(
				contents=[types.TextResourceContents(uri=uri, mime_type=doc.mime_type, text=doc.body)]
			)

	raise MCPError(types.INVALID_PARAMS, f"Resource not found: {uri}")
