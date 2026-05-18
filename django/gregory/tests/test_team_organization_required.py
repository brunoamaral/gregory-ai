"""
Regression guard: Team.organization must never be NULL.

If someone re-adds null=True to Team.organization the full_clean() call below
will stop raising ValidationError and this test will fail.
"""
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gregory.tests.test_settings')

import django
django.setup()

from django.core.exceptions import ValidationError
from django.test import TestCase
from gregory.models import Team


class TeamOrganizationRequiredTest(TestCase):
	def test_team_without_organization_fails_validation(self):
		team = Team(name='orphan', slug='orphan')
		with self.assertRaises(ValidationError) as ctx:
			team.full_clean()
		self.assertIn('organization', ctx.exception.message_dict)
