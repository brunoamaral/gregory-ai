"""
Tests for the versioning module.
"""
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from unittest import mock

from django.test import TestCase

from gregory.utils.versioning import make_version_path


class VersioningTestCase(TestCase):
    """Test case for the versioning module."""
    
    def setUp(self):
        """Create a temporary directory for testing."""
        self.temp_dir = tempfile.mkdtemp()
        # Mock datetime to return a fixed date
        self.patcher = mock.patch('gregory.utils.versioning.datetime')
        self.mock_datetime = self.patcher.start()
        self.mock_datetime.now.return_value = datetime(2025, 5, 18)  # YYYY-MM-DD
    
    def tearDown(self):
        """Clean up temporary directory after testing."""
        self.patcher.stop()
        shutil.rmtree(self.temp_dir)
    
    def test_make_version_path_creates_directory(self):
        """Test that make_version_path creates the directory structure correctly."""
        path = make_version_path(self.temp_dir, 'team1', 'covid', 'pubmed_bert')
        
        # Check the path is correct
        expected_path = Path(self.temp_dir) / 'team1' / 'covid' / 'pubmed_bert' / '20250518'
        self.assertEqual(path, expected_path)
        
        # Check the directory was created
        self.assertTrue(path.exists())
        self.assertTrue(path.is_dir())
    
    def test_make_version_path_increments_suffix_when_exists(self):
        """Test that make_version_path adds suffixes when directories already exist."""
        # Create the first version directory
        first_path = make_version_path(self.temp_dir, 'team2', 'diabetes', 'lstm')
        
        # Create a second version - should add _2 suffix
        second_path = make_version_path(self.temp_dir, 'team2', 'diabetes', 'lstm')
        expected_second_path = Path(self.temp_dir) / 'team2' / 'diabetes' / 'lstm' / '20250518_2'
        self.assertEqual(second_path, expected_second_path)
        
        # Create a third version - should add _3 suffix
        third_path = make_version_path(self.temp_dir, 'team2', 'diabetes', 'lstm')
        expected_third_path = Path(self.temp_dir) / 'team2' / 'diabetes' / 'lstm' / '20250518_3'
        self.assertEqual(third_path, expected_third_path)
    
    def test_make_version_path_handles_different_algorithms(self):
        """Test that make_version_path handles different algorithms for the same team/subject."""
        # Create directories for different algorithms
        path1 = make_version_path(self.temp_dir, 'team3', 'cancer', 'pubmed_bert')
        path2 = make_version_path(self.temp_dir, 'team3', 'cancer', 'lgbm_tfidf')
        path3 = make_version_path(self.temp_dir, 'team3', 'cancer', 'lstm')
        
        # Check each algorithm gets its own directory
        self.assertEqual(path1, Path(self.temp_dir) / 'team3' / 'cancer' / 'pubmed_bert' / '20250518')
        self.assertEqual(path2, Path(self.temp_dir) / 'team3' / 'cancer' / 'lgbm_tfidf' / '20250518')
        self.assertEqual(path3, Path(self.temp_dir) / 'team3' / 'cancer' / 'lstm' / '20250518')
    
    def test_make_version_path_handles_string_path(self):
        """Test that make_version_path works with string paths."""
        path = make_version_path(str(self.temp_dir), 'team4', 'alzheimer', 'pubmed_bert')
        
        expected_path = Path(self.temp_dir) / 'team4' / 'alzheimer' / 'pubmed_bert' / '20250518'
        self.assertEqual(path, expected_path)
        
        # Check the directory was created
        self.assertTrue(path.exists())
        self.assertTrue(path.is_dir())
    
    def test_make_version_path_skips_existing_suffix(self):
        """Test that make_version_path skips an existing _2 suffix and creates _3."""
        # Create the base version directory
        os.makedirs(os.path.join(self.temp_dir, 'team5', 'parkinsons', 'lstm', '20250518'))
        
        # Create the _2 version directory
        os.makedirs(os.path.join(self.temp_dir, 'team5', 'parkinsons', 'lstm', '20250518_2'))
        
        # Call make_version_path - should create _3
        path = make_version_path(self.temp_dir, 'team5', 'parkinsons', 'lstm')
        expected_path = Path(self.temp_dir) / 'team5' / 'parkinsons' / 'lstm' / '20250518_3'
        self.assertEqual(path, expected_path)
