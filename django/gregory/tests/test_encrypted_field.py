from django.test import TestCase
from django.conf import settings
from django.db import models
from django.core.exceptions import ImproperlyConfigured
from cryptography.fernet import Fernet
import base64

from gregory.models import TeamCredentials, Team, get_fernet, EncryptedTextField


class TemporaryModelWithEncryptedField(models.Model):
    """Temporary model for testing EncryptedTextField."""
    encrypted_data = EncryptedTextField()
    
    class Meta:
        app_label = 'gregory'  # This ensures the model is part of the gregory app
        managed = False  # This model won't actually be created in the database


class EncryptedTextFieldTests(TestCase):
    """Test cases for the EncryptedTextField."""
    
    def setUp(self):
        """Set up test data."""
        # Create a Fernet instance for direct encryption/decryption verification
        self.fernet = get_fernet()
    
    def test_get_fernet_function(self):
        """Test that the get_fernet function returns a valid Fernet instance."""
        fernet = get_fernet()
        self.assertIsInstance(fernet, Fernet)
        
        # Test with missing settings
        original_key = settings.FERNET_SECRET_KEY
        try:
            # Temporarily remove the key from settings
            delattr(settings, 'FERNET_SECRET_KEY')
            with self.assertRaises(ValueError):
                get_fernet()
        finally:
            # Restore the key
            settings.FERNET_SECRET_KEY = original_key
    
    def test_encryption_decryption(self):
        """Test direct encryption and decryption using the field's methods."""
        # Create a field instance
        field = EncryptedTextField()
        
        # Test string to encrypt
        test_string = "sensitive-data-123"
        
        # Encrypt the string
        encrypted_value = field.get_prep_value(test_string)
        
        # Verify it's not the original
        self.assertNotEqual(encrypted_value, test_string)
        
        # Decrypt and verify it matches the original
        decrypted_value = field.from_db_value(encrypted_value, None, None)
        self.assertEqual(decrypted_value, test_string)
    
    def test_non_string_encryption(self):
        """Test that non-string values raise an error."""
        temp_model = TemporaryModelWithEncryptedField()
        
        # Try to assign a non-string value
        temp_model.encrypted_data = 12345
        
        # This should raise a ValueError when preparing the value for the database
        with self.assertRaises(ValueError):
            # Get the field and directly call get_prep_value on the non-string value
            field = temp_model._meta.get_field('encrypted_data')
            field.get_prep_value(temp_model.encrypted_data)
    
    def test_none_value_handling(self):
        """Test that None values are handled correctly."""
        # Create a field instance
        field = EncryptedTextField()
        
        # Test with None value
        prep_value = field.get_prep_value(None)
        self.assertIsNone(prep_value)
        
        # Test decryption of None
        from_db_value = field.from_db_value(None, None, None)
        self.assertIsNone(from_db_value)
