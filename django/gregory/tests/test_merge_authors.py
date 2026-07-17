from django.test import TestCase
from django.core.management import call_command
from django.core.management.base import CommandError
from io import StringIO
import uuid

from gregory.models import Articles, Authors


class MergeAuthorsCommandTest(TestCase):
	"""Basic tests for the merge_authors command"""

	def test_command_help_text(self):
		"""Test that command help is properly defined"""
		from gregory.management.commands.merge_authors import Command

		cmd = Command()
		# Test the help text is properly set
		self.assertIn("Merges authors with the same ORCID", cmd.help)
		self.assertIn("http and https", cmd.help)

		# Test that arguments are properly configured
		parser = cmd.create_parser("merge_authors", "merge_authors")
		self.assertIsNotNone(parser)

		# Test dry-run argument exists
		help_text = parser.format_help()
		self.assertIn("--dry-run", help_text)
		self.assertIn("--keep-author", help_text)
		self.assertIn("--force", help_text)

	def test_merge_authors_nonexistent_orcid(self):
		"""Test handling of non-existent ORCID"""
		unique_id = str(uuid.uuid4())[:8]
		nonexistent_orcid = f"0000-0000-{unique_id[:4]}-{unique_id[4:]}"

		out = StringIO()
		call_command("merge_authors", nonexistent_orcid, stdout=out)
		self.assertIn("No authors found with ORCID", out.getvalue())

	def test_command_imports_successfully(self):
		"""Test that the command can be imported without errors"""
		from gregory.management.commands.merge_authors import Command

		cmd = Command()
		self.assertIn("Merges authors with the same ORCID", cmd.help)

	def test_normalize_orcid_function(self):
		"""Test the ORCID normalization function"""
		from gregory.management.commands.merge_authors import Command

		cmd = Command()

		# Test with HTTPS URL
		orcid_id, http_var, https_var = cmd.normalize_orcid(
			"https://orcid.org/0000-0000-1234-5678"
		)
		self.assertEqual(orcid_id, "0000-0000-1234-5678")
		self.assertEqual(http_var, "http://orcid.org/0000-0000-1234-5678")
		self.assertEqual(https_var, "https://orcid.org/0000-0000-1234-5678")

		# Test with HTTP URL
		orcid_id, http_var, https_var = cmd.normalize_orcid(
			"http://orcid.org/0000-0000-1234-5678"
		)
		self.assertEqual(orcid_id, "0000-0000-1234-5678")
		self.assertEqual(http_var, "http://orcid.org/0000-0000-1234-5678")
		self.assertEqual(https_var, "https://orcid.org/0000-0000-1234-5678")

		# Test with just the ID
		orcid_id, http_var, https_var = cmd.normalize_orcid("0000-0000-1234-5678")
		self.assertEqual(orcid_id, "0000-0000-1234-5678")
		self.assertEqual(http_var, "http://orcid.org/0000-0000-1234-5678")
		self.assertEqual(https_var, "https://orcid.org/0000-0000-1234-5678")

		# Test with ORCID ending in X
		orcid_id, http_var, https_var = cmd.normalize_orcid("0000-0000-1234-567X")
		self.assertEqual(orcid_id, "0000-0000-1234-567X")
		self.assertEqual(http_var, "http://orcid.org/0000-0000-1234-567X")
		self.assertEqual(https_var, "https://orcid.org/0000-0000-1234-567X")

		# Test with empty or invalid input
		orcid_id, http_var, https_var = cmd.normalize_orcid("")
		self.assertIsNone(orcid_id)
		self.assertIsNone(http_var)
		self.assertIsNone(https_var)

		orcid_id, http_var, https_var = cmd.normalize_orcid("invalid-orcid")
		self.assertEqual(orcid_id, "invalid-orcid")
		self.assertEqual(http_var, "invalid-orcid")
		self.assertEqual(https_var, "invalid-orcid")

		# Test with a lowercase check digit - should canonicalize to uppercase
		orcid_id, http_var, https_var = cmd.normalize_orcid("0000-0000-1234-567x")
		self.assertEqual(orcid_id, "0000-0000-1234-567X")
		self.assertEqual(http_var, "http://orcid.org/0000-0000-1234-567X")
		self.assertEqual(https_var, "https://orcid.org/0000-0000-1234-567X")

	def test_orcid_search_variants_covers_common_storage_forms(self):
		"""Lookups must match legacy trailing-slash and scheme-less storage forms"""
		from gregory.management.commands.merge_authors import Command

		cmd = Command()
		orcid_id = "0000-0000-1234-5678"
		variants = cmd.orcid_search_variants(orcid_id)

		self.assertIn(orcid_id, variants)
		self.assertIn(f"{orcid_id}/", variants)
		self.assertIn(f"orcid.org/{orcid_id}", variants)
		self.assertIn(f"orcid.org/{orcid_id}/", variants)
		self.assertIn(f"www.orcid.org/{orcid_id}", variants)
		self.assertIn(f"http://orcid.org/{orcid_id}/", variants)
		self.assertIn(f"https://orcid.org/{orcid_id}/", variants)
		self.assertIn(f"http://www.orcid.org/{orcid_id}/", variants)
		self.assertIn(f"https://www.orcid.org/{orcid_id}/", variants)

	def test_empty_orcid_handling(self):
		"""Test that command properly handles empty and whitespace-only ORCID"""
		# Test with whitespace only - this should trigger our "ORCID cannot be empty" check
		with self.assertRaises(CommandError) as cm:
			call_command("merge_authors", "   ")
		self.assertIn("ORCID cannot be empty", str(cm.exception))

		# Test with no argument at all - this behavior varies between direct command vs call_command
		# Both error messages are acceptable as they indicate missing/invalid input
		with self.assertRaises(CommandError):
			call_command("merge_authors")

	def test_force_flag_handling(self):
		"""Test that the force flag is properly handled"""
		from gregory.management.commands.merge_authors import Command

		cmd = Command()

		# Test that force flag is properly configured
		parser = cmd.create_parser("merge_authors", "merge_authors")
		help_text = parser.format_help()
		self.assertIn("--force", help_text)
		self.assertIn("Skip confirmation prompt", help_text)

	def test_invalid_orcid_format_handling(self):
		"""Test handling of invalid ORCID format"""
		# Note: The current implementation handles malformed ORCIDs gracefully
		# by treating them as-is, so this test verifies that behavior
		out = StringIO()
		call_command("merge_authors", "not-a-valid-orcid", stdout=out)
		self.assertIn("No authors found with ORCID", out.getvalue())


class MergeAuthorsOrcidStorageTest(TestCase):
	"""The merged author must end up with the bare ORCID ID, not a URL"""

	ORCID_ID = "0000-0002-1825-0097"

	def test_merge_stores_bare_orcid(self):
		kept = Authors.objects.create(
			given_name="Ana",
			family_name="Silva",
			ORCID=f"https://orcid.org/{self.ORCID_ID}",
		)
		duplicate = Authors.objects.create(
			given_name="A.",
			family_name="Silva",
			ORCID=self.ORCID_ID,
		)
		article = Articles.objects.create(
			title="Paper", link="https://example.com/paper"
		)
		article.authors.add(kept)

		out = StringIO()
		call_command("merge_authors", self.ORCID_ID, "--force", stdout=out)

		kept.refresh_from_db()
		self.assertEqual(kept.ORCID, self.ORCID_ID)
		self.assertFalse(
			Authors.objects.filter(author_id=duplicate.author_id).exists()
		)
		self.assertIn(kept, article.authors.all())

	def test_merge_transfers_articles_and_stores_bare_orcid(self):
		kept = Authors.objects.create(
			given_name="Ana",
			family_name="Silva",
			ORCID=f"http://orcid.org/{self.ORCID_ID}",
		)
		duplicate = Authors.objects.create(
			given_name="A.",
			family_name="Silva",
			ORCID=f"https://orcid.org/{self.ORCID_ID}",
		)
		kept_article = Articles.objects.create(
			title="Kept paper", link="https://example.com/kept"
		)
		kept_article.authors.add(kept)
		transferred_article = Articles.objects.create(
			title="Transferred paper", link="https://example.com/transferred"
		)
		transferred_article.authors.add(duplicate)

		out = StringIO()
		call_command(
			"merge_authors",
			self.ORCID_ID,
			"--force",
			"--keep-author",
			str(kept.author_id),
			stdout=out,
		)

		kept.refresh_from_db()
		self.assertEqual(kept.ORCID, self.ORCID_ID)
		self.assertFalse(
			Authors.objects.filter(author_id=duplicate.author_id).exists()
		)
		self.assertIn(kept, transferred_article.authors.all())

	def test_single_author_orcid_is_normalized(self):
		author = Authors.objects.create(
			given_name="Ana",
			family_name="Silva",
			ORCID=f"https://orcid.org/{self.ORCID_ID}",
		)

		out = StringIO()
		call_command("merge_authors", self.ORCID_ID, stdout=out)

		author.refresh_from_db()
		self.assertEqual(author.ORCID, self.ORCID_ID)
		self.assertIn("ORCID normalized to", out.getvalue())

	def test_single_author_dry_run_keeps_stored_orcid(self):
		stored = f"https://orcid.org/{self.ORCID_ID}"
		author = Authors.objects.create(
			given_name="Ana", family_name="Silva", ORCID=stored
		)

		out = StringIO()
		call_command("merge_authors", self.ORCID_ID, "--dry-run", stdout=out)

		author.refresh_from_db()
		self.assertEqual(author.ORCID, stored)
		self.assertIn("ORCID would be normalized to", out.getvalue())

	def test_single_author_matches_trailing_slash_and_lowercase_input(self):
		"""A trailing-slash URL in storage must be found by a lowercase check-digit query"""
		orcid_id = "0000-0002-1825-009X"
		author = Authors.objects.create(
			given_name="Ana",
			family_name="Silva",
			ORCID=f"https://orcid.org/{orcid_id}/",
		)

		out = StringIO()
		call_command("merge_authors", "0000-0002-1825-009x", stdout=out)

		author.refresh_from_db()
		self.assertEqual(author.ORCID, orcid_id)
		self.assertIn("ORCID normalized to", out.getvalue())

	def test_merge_dry_run_makes_no_changes(self):
		kept = Authors.objects.create(
			given_name="Ana",
			family_name="Silva",
			ORCID=f"https://orcid.org/{self.ORCID_ID}",
		)
		duplicate = Authors.objects.create(
			given_name="A.",
			family_name="Silva",
			ORCID=self.ORCID_ID,
		)

		out = StringIO()
		call_command("merge_authors", self.ORCID_ID, "--dry-run", stdout=out)

		kept.refresh_from_db()
		self.assertEqual(kept.ORCID, f"https://orcid.org/{self.ORCID_ID}")
		self.assertTrue(
			Authors.objects.filter(author_id=duplicate.author_id).exists()
		)
		self.assertIn("DRY RUN - No changes will be made", out.getvalue())
