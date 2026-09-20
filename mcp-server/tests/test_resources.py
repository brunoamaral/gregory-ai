"""Tests for gregory_mcp/resources.py: list_resources()/
list_resource_templates()/read_resource(), the raw handlers that replaced
the old decorator-registered gregory:// resources. See
MCP-MULTI-TENANCY-PHASE-3-PLAN.md, task C5.
"""

from __future__ import annotations

import httpx2
import mcp_types as types
import pytest
from mcp.shared.exceptions import MCPError

from gregory_mcp.resources import (
	ABOUT_URI,
	CATEGORIES_URI,
	SUBJECTS_URI,
	list_resource_templates,
	list_resources,
	read_resource,
)
from gregory_mcp.site_context import _current_tenant
from gregory_mcp.tenants import Tenant, TenantDocument


def _make_tenant(*, documents=(), title="Example", site_id=1, domain="example.test", mcp_description="") -> Tenant:
	return Tenant(
		site_id=site_id,
		domain=domain,
		name="Example",
		title=title,
		api_public=True,
		mcp_description=mcp_description,
		subjects=((1, "Multiple Sclerosis"),),
		prompts=(),
		documents=documents,
	)


class _set_tenant:
	def __init__(self, tenant):
		self._tenant = tenant

	def __enter__(self):
		self._token = _current_tenant.set(self._tenant)
		return self._tenant

	def __exit__(self, *exc_info):
		_current_tenant.reset(self._token)


async def test_list_resources_returns_the_three_built_ins():
	result = await list_resources(None, types.PaginatedRequestParams())
	assert {r.uri for r in result.resources} == {SUBJECTS_URI, CATEGORIES_URI, ABOUT_URI}
	for resource in result.resources:
		assert "gregory" not in resource.description.lower().replace("-ai", "")


async def test_list_resources_descriptions_are_identical_for_every_tenant():
	with _set_tenant(_make_tenant(title="Tenant A")):
		result_a = await list_resources(None, types.PaginatedRequestParams())
	with _set_tenant(_make_tenant(title="Tenant B")):
		result_b = await list_resources(None, types.PaginatedRequestParams())

	assert [r.model_dump() for r in result_a.resources] == [r.model_dump() for r in result_b.resources]


async def test_list_resource_templates_returns_the_doc_template():
	result = await list_resource_templates(None, types.PaginatedRequestParams())
	assert {t.uri_template for t in result.resource_templates} == {"gregory-ai://doc/{slug}"}


async def test_read_subjects_resource(mock_gregory):
	mock_gregory.set_handler(
		lambda request: httpx2.Response(
			200, json={"next": None, "results": [{"id": 1, "subject_name": "Multiple Sclerosis"}]}
		)
	)
	result = await read_resource(None, types.ReadResourceRequestParams(uri=SUBJECTS_URI))
	[content] = result.contents
	assert content.mime_type == "application/json"
	assert "Multiple Sclerosis" in content.text


async def test_read_categories_resource(mock_gregory):
	mock_gregory.set_handler(
		lambda request: httpx2.Response(
			200, json={"next": None, "results": [{"id": 1, "category_name": "Remyelination", "category_slug": "remy"}]}
		)
	)
	result = await read_resource(None, types.ReadResourceRequestParams(uri=CATEGORIES_URI))
	[content] = result.contents
	assert content.mime_type == "application/json"
	assert "Remyelination" in content.text


async def test_read_about_resource_includes_title_and_subjects():
	tenant = _make_tenant(title="Brain Regeneration", mcp_description="")
	with _set_tenant(tenant):
		result = await read_resource(None, types.ReadResourceRequestParams(uri=ABOUT_URI))

	[content] = result.contents
	assert content.mime_type == "text/markdown"
	assert "Brain Regeneration" in content.text
	assert "Multiple Sclerosis" in content.text


async def test_read_about_resource_lists_documents():
	doc = TenantDocument(slug="glossary", title="Glossary", description="Terms.", mime_type="text/markdown", body="...")
	tenant = _make_tenant(documents=(doc,))
	with _set_tenant(tenant):
		result = await read_resource(None, types.ReadResourceRequestParams(uri=ABOUT_URI))

	text = result.contents[0].text
	assert "Glossary" in text
	assert "gregory-ai://doc/glossary" in text


async def test_read_document_by_slug():
	doc = TenantDocument(slug="glossary", title="Glossary", description="", mime_type="text/markdown", body="**bold**")
	tenant = _make_tenant(documents=(doc,))
	with _set_tenant(tenant):
		result = await read_resource(None, types.ReadResourceRequestParams(uri="gregory-ai://doc/glossary"))

	[content] = result.contents
	assert content.mime_type == "text/markdown"
	assert content.text == "**bold**"


async def test_unknown_slug_is_rejected():
	tenant = _make_tenant(documents=())
	with _set_tenant(tenant):
		with pytest.raises(MCPError) as exc_info:
			await read_resource(None, types.ReadResourceRequestParams(uri="gregory-ai://doc/nonexistent"))

	assert exc_info.value.code == types.INVALID_PARAMS
	assert "not found" in exc_info.value.message.lower()


async def test_old_gregory_scheme_address_is_rejected():
	with pytest.raises(MCPError) as exc_info:
		await read_resource(None, types.ReadResourceRequestParams(uri="gregory://subjects"))

	assert exc_info.value.code == types.INVALID_PARAMS


async def test_about_with_no_tenant_is_rejected():
	with _set_tenant(None):
		with pytest.raises(MCPError):
			await read_resource(None, types.ReadResourceRequestParams(uri=ABOUT_URI))


# --- isolation between tenants -------------------------------------------


async def test_two_tenants_documents_never_cross():
	doc_a = TenantDocument(slug="a-doc", title="A Doc", description="", mime_type="text/markdown", body="A body")
	doc_b = TenantDocument(slug="b-doc", title="B Doc", description="", mime_type="text/markdown", body="B body")
	tenant_a = _make_tenant(site_id=1, documents=(doc_a,))
	tenant_b = _make_tenant(site_id=2, documents=(doc_b,))

	with _set_tenant(tenant_a):
		result = await read_resource(None, types.ReadResourceRequestParams(uri="gregory-ai://doc/a-doc"))
		assert result.contents[0].text == "A body"
		with pytest.raises(MCPError):
			await read_resource(None, types.ReadResourceRequestParams(uri="gregory-ai://doc/b-doc"))

	with _set_tenant(tenant_b):
		result = await read_resource(None, types.ReadResourceRequestParams(uri="gregory-ai://doc/b-doc"))
		assert result.contents[0].text == "B body"
		with pytest.raises(MCPError):
			await read_resource(None, types.ReadResourceRequestParams(uri="gregory-ai://doc/a-doc"))


async def test_two_tenants_about_pages_never_cross():
	tenant_a = _make_tenant(site_id=1, title="Tenant A")
	tenant_b = _make_tenant(site_id=2, title="Tenant B")

	with _set_tenant(tenant_a):
		result_a = await read_resource(None, types.ReadResourceRequestParams(uri=ABOUT_URI))
	with _set_tenant(tenant_b):
		result_b = await read_resource(None, types.ReadResourceRequestParams(uri=ABOUT_URI))

	assert "Tenant A" in result_a.contents[0].text
	assert "Tenant A" not in result_b.contents[0].text
	assert "Tenant B" in result_b.contents[0].text
	assert "Tenant B" not in result_a.contents[0].text
