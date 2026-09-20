"""Tests for gregory_mcp/prompts.py: list_prompts()/get_prompt(), the raw
handlers that replaced the old hard-coded, decorator-registered prompts. See
MCP-MULTI-TENANCY-PHASE-3-PLAN.md, task C5.
"""

from __future__ import annotations

import mcp_types as types
import pytest
from mcp.shared.exceptions import MCPError

from gregory_mcp.prompts import get_prompt, list_prompts
from gregory_mcp.site_context import _current_tenant
from gregory_mcp.tenants import Tenant, TenantPrompt, TenantPromptArgument


def _make_tenant(*, prompts=(), site_id=1, domain="example.test") -> Tenant:
	return Tenant(
		site_id=site_id,
		domain=domain,
		name="Example",
		title="Example",
		api_public=True,
		mcp_description="",
		subjects=(),
		prompts=prompts,
		documents=(),
	)


def _make_prompt(**overrides) -> TenantPrompt:
	defaults = dict(
		name="research_topic",
		title="Research a topic",
		description="Survey recent work.",
		template="Research $topic.",
		arguments=(TenantPromptArgument(name="topic", description="The topic.", required=True),),
	)
	return TenantPrompt(**{**defaults, **overrides})


class _set_tenant:
	"""Context manager: set site_context's current tenant for the block,
	then restore it -- mirrors SiteMiddleware's own set/reset-on-token
	pattern (site.py), used directly here since these handlers are tested
	below the middleware chain."""

	def __init__(self, tenant):
		self._tenant = tenant

	def __enter__(self):
		self._token = _current_tenant.set(self._tenant)
		return self._tenant

	def __exit__(self, *exc_info):
		_current_tenant.reset(self._token)


async def test_list_prompts_reads_the_current_tenants_prompts():
	tenant = _make_tenant(prompts=(_make_prompt(name="a"), _make_prompt(name="b")))
	with _set_tenant(tenant):
		result = await list_prompts(None, types.PaginatedRequestParams())

	assert {p.name for p in result.prompts} == {"a", "b"}


async def test_list_prompts_with_no_tenant_is_empty():
	with _set_tenant(None):
		result = await list_prompts(None, types.PaginatedRequestParams())

	assert result.prompts == []


async def test_list_prompts_includes_arguments():
	tenant = _make_tenant(prompts=(_make_prompt(),))
	with _set_tenant(tenant):
		result = await list_prompts(None, types.PaginatedRequestParams())

	[prompt] = result.prompts
	[arg] = prompt.arguments
	assert arg.name == "topic"
	assert arg.required is True


async def test_get_prompt_renders_with_required_argument():
	tenant = _make_tenant(prompts=(_make_prompt(),))
	with _set_tenant(tenant):
		result = await get_prompt(
			None, types.GetPromptRequestParams(name="research_topic", arguments={"topic": "remyelination"})
		)

	[message] = result.messages
	assert message.content.text == "Research remyelination."
	assert message.role == "user"


async def test_get_prompt_renders_omitted_optional_argument_as_blank():
	prompt = _make_prompt(
		template="Research $topic$suffix.",
		arguments=(
			TenantPromptArgument(name="topic", description="", required=True),
			TenantPromptArgument(name="suffix", description="", required=False),
		),
	)
	tenant = _make_tenant(prompts=(prompt,))
	with _set_tenant(tenant):
		result = await get_prompt(None, types.GetPromptRequestParams(name="research_topic", arguments={"topic": "MS"}))

	assert result.messages[0].content.text == "Research MS."


async def test_get_prompt_unknown_name_raises_invalid_params():
	tenant = _make_tenant(prompts=(_make_prompt(),))
	with _set_tenant(tenant):
		with pytest.raises(MCPError) as exc_info:
			await get_prompt(None, types.GetPromptRequestParams(name="does_not_exist"))

	assert exc_info.value.code == types.INVALID_PARAMS
	assert "does_not_exist" in exc_info.value.message


async def test_get_prompt_missing_required_argument_raises_invalid_params():
	tenant = _make_tenant(prompts=(_make_prompt(),))
	with _set_tenant(tenant):
		with pytest.raises(MCPError) as exc_info:
			await get_prompt(None, types.GetPromptRequestParams(name="research_topic", arguments={}))

	assert exc_info.value.code == types.INVALID_PARAMS
	assert "topic" in exc_info.value.message


async def test_get_prompt_a_broken_template_raises_internal_error(caplog):
	# Bypasses validate_prompt_template() -- a row written directly around
	# Django's clean() -- to exercise the defensive path: an undeclared
	# placeholder that string.Template.substitute() can't resolve.
	prompt = _make_prompt(template="Research $missing_arg.", arguments=())
	tenant = _make_tenant(prompts=(prompt,))
	with _set_tenant(tenant):
		with pytest.raises(MCPError) as exc_info:
			await get_prompt(None, types.GetPromptRequestParams(name="research_topic", arguments={}))

	assert exc_info.value.code == types.INTERNAL_ERROR
	assert "rendered" in exc_info.value.message.lower()
	assert any(r.getMessage() == "gregory_prompt_render_failed" for r in caplog.records)


async def test_unknown_prompt_with_no_tenant_still_raises_invalid_params():
	with _set_tenant(None):
		with pytest.raises(MCPError) as exc_info:
			await get_prompt(None, types.GetPromptRequestParams(name="anything"))

	assert exc_info.value.code == types.INVALID_PARAMS


# --- isolation between tenants -------------------------------------------


async def test_two_tenants_prompts_never_cross():
	tenant_a = _make_tenant(site_id=1, prompts=(_make_prompt(name="a_only"),))
	tenant_b = _make_tenant(site_id=2, prompts=(_make_prompt(name="b_only"),))

	with _set_tenant(tenant_a):
		result_a = await list_prompts(None, types.PaginatedRequestParams())
	with _set_tenant(tenant_b):
		result_b = await list_prompts(None, types.PaginatedRequestParams())

	assert {p.name for p in result_a.prompts} == {"a_only"}
	assert {p.name for p in result_b.prompts} == {"b_only"}

	# tenant B can't get_prompt tenant A's prompt, and vice versa.
	with _set_tenant(tenant_b):
		with pytest.raises(MCPError):
			await get_prompt(None, types.GetPromptRequestParams(name="a_only", arguments={"topic": "x"}))
	with _set_tenant(tenant_a):
		with pytest.raises(MCPError):
			await get_prompt(None, types.GetPromptRequestParams(name="b_only", arguments={"topic": "x"}))
