from django.test import TestCase
from django.core.management import call_command
from django.core.management.base import CommandError
from io import StringIO
import uuid


class MergeAuthorsCommandTest(TestCase):
    """Basic tests for the merge_authors command"""

    def test_command_help_text(self):
        """Test that command help is properly defined"""
        from gregory.management.commands.merge_authors import Command
        cmd = Command()
        # Test the help text is properly set
        self.assertIn('Merges authors with the same ORCID', cmd.help)
        self.assertIn('http and https', cmd.help)
        
        # Test that arguments are properly configured
        parser = cmd.create_parser('merge_authors', 'merge_authors')
        self.assertIsNotNone(parser)
        
        # Test dry-run argument exists
        help_text = parser.format_help()
        self.assertIn('--dry-run', help_text)
        self.assertIn('--keep-author', help_text)
        self.assertIn('--force', help_text)

    def test_merge_authors_nonexistent_orcid(self):
        """Test handling of non-existent ORCID"""
        unique_id = str(uuid.uuid4())[:8]
        nonexistent_orcid = f"0000-0000-{unique_id[:4]}-{unique_id[4:]}"
        
        out = StringIO()
        call_command('merge_authors', nonexistent_orcid, stdout=out)
        self.assertIn('No authors found with ORCID', out.getvalue())

    def test_command_imports_successfully(self):
        """Test that the command can be imported without errors"""
        from gregory.management.commands.merge_authors import Command
        cmd = Command()
        self.assertIn('Merges authors with the same ORCID', cmd.help)

    def test_normalize_orcid_function(self):
        """Test the ORCID normalization function"""
        from gregory.management.commands.merge_authors import Command
        cmd = Command()
        
        # Test with HTTPS URL
        orcid_id, http_var, https_var = cmd.normalize_orcid("https://orcid.org/0000-0000-1234-5678")
        self.assertEqual(orcid_id, "0000-0000-1234-5678")
        self.assertEqual(http_var, "http://orcid.org/0000-0000-1234-5678")
        self.assertEqual(https_var, "https://orcid.org/0000-0000-1234-5678")
        
        # Test with HTTP URL
        orcid_id, http_var, https_var = cmd.normalize_orcid("http://orcid.org/0000-0000-1234-5678")
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

    def test_empty_orcid_handling(self):
        """Test that command properly handles empty and whitespace-only ORCID"""
        # Test with whitespace only - this should trigger our "ORCID cannot be empty" check
        with self.assertRaises(CommandError) as cm:
            call_command('merge_authors', '   ')
        self.assertIn('ORCID cannot be empty', str(cm.exception))
        
        # Test with no argument at all - this behavior varies between direct command vs call_command
        # Both error messages are acceptable as they indicate missing/invalid input
        with self.assertRaises(CommandError):
            call_command('merge_authors')

    def test_force_flag_handling(self):
        """Test that the force flag is properly handled"""
        from gregory.management.commands.merge_authors import Command
        cmd = Command()
        
        # Test that force flag is properly configured
        parser = cmd.create_parser('merge_authors', 'merge_authors')
        help_text = parser.format_help()
        self.assertIn('--force', help_text)
        self.assertIn('Skip confirmation prompt', help_text)

    def test_invalid_orcid_format_handling(self):
        """Test handling of invalid ORCID format"""
        # Note: The current implementation handles malformed ORCIDs gracefully
        # by treating them as-is, so this test verifies that behavior
        out = StringIO()
        call_command('merge_authors', 'not-a-valid-orcid', stdout=out)
        self.assertIn('No authors found with ORCID', out.getvalue())
