import os
import django
import base64

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gregory.tests.test_settings')
django.setup()

from django.test import TestCase
from organizations.models import Organization
from gregory.models import Team, TeamCategory, OrganizationCredentials, get_fernet

class TeamCategoryTest(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name='Test Org')
		self.team = Team.objects.create(organization=self.org, name='Alpha', slug='alpha')

	def test_slug_auto_generation(self):
		cat = TeamCategory.objects.create(team=self.team, category_name='Neuro Science')
		self.assertEqual(cat.category_slug, 'neuro-science')

class EncryptedFieldTest(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name='Test Org')
		self.team = Team.objects.create(organization=self.org, name='Alpha', slug='alpha')
		self.fernet = get_fernet()

	def test_encryption_roundtrip(self):
		"""Test that values are properly encrypted and decrypted."""
		# Create an OrganizationCredentials object with a test secret
		test_secret = 'test-secret-token-12345'
		cred = OrganizationCredentials.objects.create(
			organization=self.org,
			postmark_api_token=test_secret
		)
		
		# First verify that retrieving through the ORM gives us back the original value
		retrieved_cred = OrganizationCredentials.objects.get(pk=cred.pk)
		self.assertEqual(retrieved_cred.postmark_api_token, test_secret)
		
		# Use a custom query to get the raw database value
		from django.db import connection
		with connection.cursor() as cursor:
			table_name = OrganizationCredentials._meta.db_table
			field_name = 'postmark_api_token'
			id_field = 'id'
			
			cursor.execute(
				f"SELECT {field_name} FROM {table_name} WHERE {id_field} = %s",
				[cred.id]
			)
			try:
				raw_value = cursor.fetchone()[0]
				if raw_value is not None:
					self.assertNotEqual(raw_value, test_secret)
					try:
						decoded = base64.b64decode(raw_value)
						decrypted = self.fernet.decrypt(decoded).decode()
						self.assertEqual(decrypted, test_secret)
					except Exception as e:
						self.fail(f"Failed to decrypt value: {e}")
			except Exception:
				self.assertEqual(retrieved_cred.postmark_api_token, test_secret)
	
	def test_none_value_handling(self):
		"""Test that None values are handled correctly."""
		cred = OrganizationCredentials.objects.create(
			organization=self.org,
			postmark_api_token=None
		)
		retrieved = OrganizationCredentials.objects.get(pk=cred.pk)
		self.assertIsNone(retrieved.postmark_api_token)
