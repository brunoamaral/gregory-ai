from django.contrib.sites.models import Site
from django.core.management.base import BaseCommand, CommandError

from sitesettings.mcp_defaults import DEFAULT_MCP_PROMPTS
from sitesettings.models import SiteMcpPrompt


class Command(BaseCommand):
	help = (
		"Gives a site the same three built-in MCP prompts a new tenant starts "
		"with (see sitesettings.mcp_defaults.DEFAULT_MCP_PROMPTS). Never "
		"updates an existing prompt row, so an edited prompt survives a "
		"re-run. Does NOT touch CustomSetting.mcp_enabled -- switching a site "
		"on stays a deliberate act in the admin."
	)

	def add_arguments(self, parser):
		parser.add_argument(
			"--site",
			type=int,
			required=True,
			help="Site ID to seed prompts for.",
		)
		parser.add_argument(
			"--dry-run",
			action="store_true",
			help="Show what would be created without writing anything.",
		)

	def handle(self, *args, **options):
		site_id = options["site"]
		dry_run = options["dry_run"]

		try:
			Site.objects.get(pk=site_id)
		except Site.DoesNotExist:
			raise CommandError(f"No site with id={site_id}.")

		existing_names = set(
			SiteMcpPrompt.objects.filter(site_id=site_id).values_list("name", flat=True)
		)

		for prompt in DEFAULT_MCP_PROMPTS:
			if prompt["name"] in existing_names:
				self.stdout.write(f"kept {prompt['name']}")
				continue

			if dry_run:
				self.stdout.write(f"would create {prompt['name']}")
				continue

			SiteMcpPrompt.objects.get_or_create(
				site_id=site_id,
				name=prompt["name"],
				defaults={
					"title": prompt["title"],
					"description": prompt["description"],
					"template": prompt["template"],
					"arguments": prompt["arguments"],
					"ordering": prompt["ordering"],
				},
			)
			self.stdout.write(f"created {prompt['name']}")
