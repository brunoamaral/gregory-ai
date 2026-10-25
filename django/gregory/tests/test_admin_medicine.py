"""The Medicine admin is read-only, and every staff user can browse it.

Medicines are public EMA data with no team or organisation, so there is nothing to scope.
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from gregory.models import Medicine, MedicineEvent

User = get_user_model()


class MedicineAdminTests(TestCase):
	@classmethod
	def setUpTestData(cls):
		cls.medicine = Medicine.objects.create(
			ema_product_number="EMEA/H/C/004043",
			name="Ocrevus",
			inn="ocrelizumab",
			substances=["ocrelizumab"],
			status="Authorised",
			authorisation_date=date(2018, 1, 8),
			latest_ec_decision_date=date(2026, 10, 15),
			url="https://www.ema.europa.eu/en/medicines/human/EPAR/ocrevus",
		)
		MedicineEvent.objects.create(
			medicine=cls.medicine,
			kind=MedicineEvent.Kind.AUTHORISED,
			event_date=date(2018, 1, 8),
			basis=MedicineEvent.Basis.RECONSTRUCTED,
		)
		MedicineEvent.objects.create(
			medicine=cls.medicine,
			kind=MedicineEvent.Kind.EC_DECISION,
			event_date=date(2026, 10, 15),
			basis=MedicineEvent.Basis.RECONSTRUCTED,
		)
		# Staff with no model permissions at all.
		cls.staff = User.objects.create_user("staff", "staff@example.org", "x", is_staff=True)
		cls.outsider = User.objects.create_user("plain", "plain@example.org", "x")

	def test_staff_user_without_permissions_sees_the_list_and_the_page(self):
		self.client.force_login(self.staff)
		response = self.client.get(reverse("admin:gregory_medicine_changelist"))
		self.assertContains(response, "Ocrevus")
		response = self.client.get(
			reverse("admin:gregory_medicine_change", args=[self.medicine.pk])
		)
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "Latest EC decision")
		self.assertContains(response, 'href="https://www.ema.europa.eu/en/medicines/human/EPAR/ocrevus"')

	def test_events_are_listed_newest_first(self):
		self.client.force_login(self.staff)
		response = self.client.get(
			reverse("admin:gregory_medicine_change", args=[self.medicine.pk])
		)
		inline = next(
			i for i in response.context["inline_admin_formsets"] if i.formset.model is MedicineEvent
		)
		self.assertEqual(
			[form.instance.kind for form in inline.formset.forms],
			[MedicineEvent.Kind.EC_DECISION, MedicineEvent.Kind.AUTHORISED],
		)

	def test_nothing_can_be_added_changed_or_deleted(self):
		self.client.force_login(self.staff)
		self.assertEqual(self.client.get(reverse("admin:gregory_medicine_add")).status_code, 403)
		response = self.client.post(
			reverse("admin:gregory_medicine_change", args=[self.medicine.pk]),
			{"name": "Changed"},
		)
		self.assertEqual(response.status_code, 403)
		response = self.client.post(
			reverse("admin:gregory_medicine_delete", args=[self.medicine.pk]), {"post": "yes"}
		)
		self.assertEqual(response.status_code, 403)
		self.medicine.refresh_from_db()
		self.assertEqual(self.medicine.name, "Ocrevus")

	def test_a_non_staff_user_is_sent_to_login(self):
		self.client.force_login(self.outsider)
		response = self.client.get(reverse("admin:gregory_medicine_changelist"))
		self.assertEqual(response.status_code, 302)

	def test_search_by_product_number(self):
		self.client.force_login(self.staff)
		response = self.client.get(
			reverse("admin:gregory_medicine_changelist"), {"q": "004043"}
		)
		self.assertContains(response, "Ocrevus")
		response = self.client.get(reverse("admin:gregory_medicine_changelist"), {"q": "zzz"})
		self.assertNotContains(response, "Ocrevus")
