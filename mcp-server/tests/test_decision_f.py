"""Decision F, scanned across every method the server answers: no
client-visible response may contain "GregoryAI", "instance" or "tenant".
See MCP-MULTI-TENANCY-PLAN.md decision F and
MCP-MULTI-TENANCY-PHASE-3-PLAN.md task C5.

Drives a real build_server() through Client for two distinct tenants (each
with its own prompt and document, so prompts/resources content is covered
too, not just identity), serializes every response to JSON, and scans the
whole text. Deliberately broad rather than per-field: decision F's own risk
note is that a stray word can slip back in through a description, a
docstring, or a new field nobody thought to check by hand.
"""

from __future__ import annotations

import dataclasses
import json

import httpx2
from mcp.client import Client

from gregory_mcp.server import build_server
from gregory_mcp.tenants import init_tenant_resolution
from tests.conftest import TEST_SETTINGS, route_by_path, tenants_payload

_BANNED_WORDS = ("gregoryai", "instance", "tenant")


def _dump(value):
	return value.model_dump() if hasattr(value, "model_dump") else value


def _assert_clean(label: str, payload) -> None:
	text = json.dumps(_dump(payload), default=str).lower()
	for banned in _BANNED_WORDS:
		assert banned not in text, f"{label} contains banned word {banned!r}: {text}"


def _tenant_row(site_id: int, domain: str, title: str) -> dict:
	return {
		"site_id": site_id,
		"domain": domain,
		"title": title,
		"mcp_description": "",
		"subjects": [{"id": 1, "subject_name": "Multiple Sclerosis"}],
		"prompts": [
			{
				"name": "research_topic",
				"title": "Research a topic",
				"description": "Survey recent work.",
				"template": "Research $topic.",
				"arguments": [{"name": "topic", "description": "The topic.", "required": True}],
			}
		],
		"documents": [
			{
				"slug": "glossary",
				"title": "Glossary",
				"description": "Key terms.",
				"mime_type": "text/markdown",
				"body": "**Remyelination**: repair of myelin.",
			}
		],
	}


async def _scan_every_method(mock_gregory, site_id: int, domain: str, title: str, mode: str) -> None:
	mock_gregory.set_handler(
		route_by_path(
			{
				"/tenants/": lambda request: httpx2.Response(
					200, json=tenants_payload(_tenant_row(1, "other.test", "Other Tenant"), _tenant_row(site_id, domain, title))
				),
				"/subjects/": lambda request: httpx2.Response(
					200, json={"next": None, "results": [{"id": 1, "subject_name": "Multiple Sclerosis"}]}
				),
				"/categories/": lambda request: httpx2.Response(
					200, json={"next": None, "results": [{"id": 1, "category_name": "Remyelination", "category_slug": "remy"}]}
				),
			}
		)
	)
	init_tenant_resolution(dataclasses.replace(TEST_SETTINGS, site_id_override=site_id))

	async with Client(build_server(), mode=mode) as client:
		_assert_clean("server_info", client.server_info.model_dump())
		_assert_clean("instructions", client.instructions)

		if mode != "legacy":
			discover = await client.session.send_discover(client.protocol_version)
			_assert_clean("server/discover", discover)

		tools = await client.list_tools()
		_assert_clean("tools/list", tools)

		prompts = await client.list_prompts()
		_assert_clean("prompts/list", prompts)

		prompt = await client.get_prompt("research_topic", {"topic": "remyelination"})
		_assert_clean("prompts/get", prompt)

		resources = await client.list_resources()
		_assert_clean("resources/list", resources)

		templates = await client.list_resource_templates()
		_assert_clean("resources/templates/list", templates)

		for uri in ("gregory-ai://subjects", "gregory-ai://categories", "gregory-ai://about", "gregory-ai://doc/glossary"):
			read = await client.read_resource(uri)
			_assert_clean(f"resources/read {uri}", read.model_dump())


async def test_decision_f_across_every_method_modern(mock_gregory):
	await _scan_every_method(mock_gregory, site_id=3, domain="brain-regeneration.com", title="Brain Regeneration", mode="auto")


async def test_decision_f_across_every_method_legacy(mock_gregory):
	await _scan_every_method(mock_gregory, site_id=7, domain="encefalites.pt", title="Encefalites", mode="legacy")
