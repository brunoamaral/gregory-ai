"""
The Authors admin change form shows openalex_id and name_key read-only.

Run with:
    docker exec gregory python manage.py test gregory.tests.test_admin_authors_readonly_ids
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from gregory.models import Authors

User = get_user_model()


class AuthorsAdminReadonlyIdsTest(TestCase):
	def setUp(self):
		self.author = Authors.objects.create(given_name="Belgin", family_name="Koçer", openalex_id="A5084674707")
		self.superuser = User.objects.create_superuser(username="admin", email="admin@example.com", password="pw")
		self.client.force_login(self.superuser)
		self.url = reverse("admin:gregory_authors_change", args=[self.author.pk])

	def test_change_form_shows_both_values_read_only(self):
		response = self.client.get(self.url)

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "A5084674707")
		self.assertContains(response, "belgin|kocer")
		form = response.context["adminform"].form
		self.assertNotIn("openalex_id", form.fields)
		self.assertNotIn("name_key", form.fields)

	def test_posting_the_form_cannot_change_them(self):
		response = self.client.get(self.url)
		data = {
			k: v for k, v in response.context["adminform"].form.initial.items() if v is not None and not isinstance(v, list)
		}
		for formset in response.context["inline_admin_formsets"]:
			mgmt = formset.formset.management_form
			for name, field in mgmt.fields.items():
				data[f"{mgmt.prefix}-{name}"] = mgmt.initial.get(name, field.initial) or 0
		data.update({"openalex_id": "A1", "name_key": "x|y"})

		response = self.client.post(self.url, data)

		self.assertEqual(response.status_code, 302, getattr(response.context, "get", lambda *_: None)("errors"))
		self.author.refresh_from_db()
		self.assertEqual(self.author.openalex_id, "A5084674707")
		self.assertEqual(self.author.name_key, "belgin|kocer")
