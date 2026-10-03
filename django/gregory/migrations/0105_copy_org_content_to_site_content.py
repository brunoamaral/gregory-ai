from django.db import migrations


def copy_org_content_to_sites(apps, schema_editor):
	"""Copy every ArticleOrgContent row to each site its organisation owns.

	Editorial content was per organisation; it is per site now. Copying to
	every site keeps what each site shows today -- copying only to the
	default site would hide the content on an organisation's other sites.
	An organisation that owns no site has nothing to show it on, so its rows
	stay in ArticleOrgContent only. A set-based INSERT ... SELECT: the table
	can hold hundreds of thousands of rows, and an empty database is just a
	no-op. ON CONFLICT makes a re-run harmless.
	"""
	ArticleOrgContent = apps.get_model("gregory", "ArticleOrgContent")
	ArticleSiteContent = apps.get_model("gregory", "ArticleSiteContent")
	OrganizationSite = apps.get_model("gregory", "OrganizationSite")

	qn = schema_editor.connection.ops.quote_name
	schema_editor.execute(
		f"""
		INSERT INTO {qn(ArticleSiteContent._meta.db_table)}
			(article_id, site_id, takeaways, summary_plain_english, created_at, updated_at)
		SELECT aoc.article_id, os.site_id, aoc.takeaways, aoc.summary_plain_english,
			aoc.created_at, aoc.updated_at
		FROM {qn(ArticleOrgContent._meta.db_table)} aoc
		JOIN {qn(OrganizationSite._meta.db_table)} os
			ON os.organization_id = aoc.organization_id
		ON CONFLICT (article_id, site_id) DO NOTHING
		"""
	)


class Migration(migrations.Migration):

	dependencies = [
		("gregory", "0104_article_site_content"),
	]

	operations = [
		# Reverse is a no-op: ArticleOrgContent is untouched, so nothing is
		# lost by leaving the copies in place (reversing 0104 drops the table).
		migrations.RunPython(copy_org_content_to_sites, migrations.RunPython.noop),
	]
