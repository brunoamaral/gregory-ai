"""
Unit tests for the Verboser utility.

This module contains tests that verify the behavior of the Verboser class
at different verbosity levels, ensuring messages are correctly filtered
and styled.
"""
import io
from unittest.mock import patch

import pytest

from gregory.utils.verboser import Verboser, VerbosityLevel


class TestVerboser:
    """Tests for the Verboser class."""
    
    def test_verbosity_levels(self):
        """Test that verbosity levels are correctly defined."""
        assert VerbosityLevel.QUIET == 0
        assert VerbosityLevel.PROGRESS == 1
        assert VerbosityLevel.WARNINGS == 2
        assert VerbosityLevel.SUMMARY == 3
    
    def test_info_message_filtering(self):
        """Test that info messages are filtered correctly by verbosity level."""
        # Create string buffers for capturing output
        stdout = io.StringIO()
        stderr = io.StringIO()
        
        # Test with QUIET level
        verboser = Verboser(level=VerbosityLevel.QUIET, stdout=stdout, stderr=stderr, use_styling=False)
        verboser.info("This should not appear")
        assert stdout.getvalue() == ""
        
        # Test with PROGRESS level
        stdout = io.StringIO()
        verboser = Verboser(level=VerbosityLevel.PROGRESS, stdout=stdout, stderr=stderr, use_styling=False)
        verboser.info("This should appear")
        assert stdout.getvalue() == "This should appear"
    
    def test_warning_message_filtering(self):
        """Test that warning messages are filtered correctly by verbosity level."""
        stdout = io.StringIO()
        
        # PROGRESS level should not show warnings by default
        verboser = Verboser(level=VerbosityLevel.PROGRESS, stdout=stdout, use_styling=False)
        verboser.warn("This should not appear")
        assert stdout.getvalue() == ""
        
        # WARNINGS level should show warnings
        stdout = io.StringIO()
        verboser = Verboser(level=VerbosityLevel.WARNINGS, stdout=stdout, use_styling=False)
        verboser.warn("This should appear")
        assert stdout.getvalue() == "This should appear"
    
    def test_summary_filtering(self):
        """Test that summary is filtered correctly by verbosity level."""
        stdout = io.StringIO()
        
        # WARNINGS level should not show summary by default
        verboser = Verboser(level=VerbosityLevel.WARNINGS, stdout=stdout, use_styling=False)
        verboser.summary("This is a summary table")
        assert stdout.getvalue() == ""
        
        # SUMMARY level should show the summary
        stdout = io.StringIO()
        verboser = Verboser(level=VerbosityLevel.SUMMARY, stdout=stdout, use_styling=False)
        verboser.summary("This is a summary table")
        assert "This is a summary table" in stdout.getvalue()
    
    def test_error_shown_at_progress_level(self):
        """Test that error messages are shown even at PROGRESS level."""
        stderr = io.StringIO()
        
        verboser = Verboser(level=VerbosityLevel.PROGRESS, stderr=stderr, use_styling=False)
        verboser.error("This is an error")
        assert stderr.getvalue() == "This is an error"
    
    def test_ansi_styling(self):
        """Test that ANSI styling is applied when enabled."""
        stdout = io.StringIO()
        
        # With styling enabled
        verboser = Verboser(level=VerbosityLevel.PROGRESS, stdout=stdout, use_styling=True)
        verboser.success("Success message")
        
        # Check for ANSI color codes in the output
        output = stdout.getvalue()
        assert '\033[32m' in output  # Green color code
        assert '\033[0m' in output   # Reset color code
        
        # With styling disabled
        stdout = io.StringIO()
        verboser = Verboser(level=VerbosityLevel.PROGRESS, stdout=stdout, use_styling=False)
        verboser.success("Success message")
        
        # Check that no ANSI codes are present
        output = stdout.getvalue()
        assert '\033[32m' not in output
        assert '\033[0m' not in output
        assert output == "Success message"
        
    def test_custom_min_level(self):
        """Test using custom minimum levels for different message types."""
        stdout = io.StringIO()
        
        # Create a verboser with QUIET level
        verboser = Verboser(level=VerbosityLevel.QUIET, stdout=stdout, use_styling=False)
        
        # Test with default min_level (should not appear)
        verboser.info("This should not appear")
        assert stdout.getvalue() == ""
        
        # Test with custom min_level=QUIET (should appear)
        verboser.info("This should appear", min_level=VerbosityLevel.QUIET)
        assert stdout.getvalue() == "This should appear"
