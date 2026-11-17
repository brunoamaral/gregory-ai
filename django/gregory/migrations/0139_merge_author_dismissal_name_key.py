from django.db import migrations


class Migration(migrations.Migration):
	"""Joins the two 0138 migrations that landed in parallel (#990 and #991)."""

	dependencies = [
		("gregory", "0138_author_merge_dismissal"),
		("gregory", "0138_authors_name_key"),
	]

	operations = []
