"""Institution admin: read-only and superuser-only."""

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from gregory.models import Authors, Institution


class InstitutionAdminTests(TestCase):
	@classmethod
	def setUpTestData(cls):
		cls.institution = Institution.objects.create(
			openalex_id="I2801081054", name="The Francis Crick Institute", ror="04tnbqb63", country_code="GB"
		)
		cls.superuser = User.objects.create_superuser("root", "root@example.org", "pw")
		cls.staff = User.objects.create_user("staff", "staff@example.org", "pw", is_staff=True)

	def test_superuser_can_list_search_and_view_but_not_change(self):
		self.client.force_login(self.superuser)
		url = reverse("admin:gregory_institution_changelist")
		self.assertContains(self.client.get(url), "The Francis Crick Institute")
		self.assertContains(self.client.get(url, {"q": "04tnbqb63"}), "The Francis Crick Institute")
		self.assertNotContains(self.client.get(url, {"q": "nothing like it"}), "Francis Crick")
		change = reverse("admin:gregory_institution_change", args=[self.institution.pk])
		self.assertEqual(self.client.get(change).status_code, 200)
		self.assertEqual(self.client.post(change, {"name": "Renamed"}).status_code, 403)
		self.assertEqual(self.client.get(reverse("admin:gregory_institution_add")).status_code, 403)
		delete = reverse("admin:gregory_institution_delete", args=[self.institution.pk])
		self.assertEqual(self.client.post(delete, {"post": "yes"}).status_code, 403)
		self.institution.refresh_from_db()
		self.assertEqual(self.institution.name, "The Francis Crick Institute")

	def test_staff_without_superuser_gets_403_everywhere(self):
		self.client.force_login(self.staff)
		for name, args in (
			("changelist", []),
			("add", []),
			("change", [self.institution.pk]),
			("delete", [self.institution.pk]),
		):
			response = self.client.get(reverse(f"admin:gregory_institution_{name}", args=args))
			self.assertEqual(response.status_code, 403, name)

	def test_the_author_form_does_not_offer_the_openalex_id(self):
		author = Authors.objects.create(given_name="Jane", family_name="Smith", openalex_id="A1")
		self.client.force_login(self.superuser)
		response = self.client.get(reverse("admin:gregory_authors_change", args=[author.pk]))
		self.assertEqual(response.status_code, 200)
		self.assertNotContains(response, "openalex_id")
